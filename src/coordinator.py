import asyncio
import os
import json
import logging
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor

import etcd3
import zigpy.backups

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("coordinator")

import radio

ETCD_DB_KEY = "/zigbee/state/database"
ETCD_NVRAM_KEY = "/zigbee/state/nvram"


def snapshot_database(db_path: str) -> bytes | None:
    """
    Return a consistent copy of the zigpy sqlite database as bytes, or None if it does not exist.

    zigpy keeps the database in WAL mode, so copying the file directly would miss the
    write-ahead log; sqlite's online backup API produces a self-contained snapshot instead.
    """
    if not os.path.exists(db_path):
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp_path)


def restore_database(db_path: str, db_bytes: bytes) -> None:
    """Replace the local zigpy database with the snapshot taken from etcd."""
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except FileNotFoundError:
            pass
    with open(db_path, "wb") as f:
        f.write(db_bytes)


PERMIT_JOIN_SECONDS = 254


class ZigbeeEvents:
    """
    zigpy listener: logs joins and attribute reports from real devices.
    """

    def __init__(self, node):
        self.node = node
        self.watched = set()

    # --- application level events
    def device_joined(self, device):
        logger.info(f"Device JOINED: nwk 0x{device.nwk:04X} ieee {device.ieee}")

    def device_initialized(self, device):
        logger.info(f"Device initialized: nwk 0x{device.nwk:04X} ieee {device.ieee} "
                    f"model={device.model!r} manufacturer={device.manufacturer!r}")
        self.watch(device)

    def device_left(self, device):
        logger.warning(f"Device LEFT: nwk 0x{device.nwk:04X} ieee {device.ieee}")

    def watch(self, device):
        """
        Attach cluster listeners to every server cluster of the device. Idempotent per cluster
        object: zigpy may rebuild a device's endpoints (for example after it rejoins), so this is
        called again on every sync cycle to pick up the new objects.
        """
        if device.nwk == 0x0000:
            return
        for ep_id, ep in device.endpoints.items():
            if ep_id == 0:
                continue
            for cluster in ep.in_clusters.values():
                if id(cluster) not in self.watched:
                    events = ClusterEvents(self.node, device, cluster)
                    # Reports arrive as "attribute_report"; "attribute_updated" covers reads/quirks.
                    cluster.on_event("attribute_report", events.on_attribute)
                    cluster.on_event("attribute_updated", events.on_attribute)
                    self.watched.add(id(cluster))


class ClusterEvents:
    """Receives zigpy's attribute events for one cluster of one device."""

    def __init__(self, node, device, cluster):
        self.node, self.device, self.cluster = node, device, cluster

    def on_attribute(self, event):
        # event: zigpy AttributeReportedEvent / AttributeUpdatedEvent
        name = event.attribute_name or f"0x{event.attribute_id:04X}"
        shown = f"{event.value / 100:.2f} C" if self.cluster.cluster_id == 0x0402 and event.attribute_id == 0 else event.value
        logger.info(f"<- REPORT from 0x{self.device.nwk:04X} {self.cluster.ep_attribute}.{name} = {shown}")


# --- 2.2 Main Async Loop & Leader Election ---
class CoordinatorHA:
    """
    High-Availability Coordinator node for Zigbee.

    Handles leader election via etcd locks and drives the Zigbee radio (a real EmberZNet NCP
    simulated in Renode through bellows, or the mock radio) while holding
    the leader lock.
    """

    def __init__(self):
        """
        Initialize the coordinator instance, setting up the etcd client.
        """
        self.pid = os.getpid()
        self.is_leader = False
        self.app = None

        # Initialize etcd3 client
        self.etcd = etcd3.client(host='127.0.0.1', port=2379)
        self.lock = self.etcd.lock('/zigbee/leader/lock', ttl=10)

        # Executor for blocking etcd3 calls
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.sync_task = None
        self.keepalive_task = None
        self.lease_lost = False
        self.permit_task = None
        self.events = None

    async def lease_keepalive(self):
        """
        Keep the leader lease alive independently of the election loop.

        Bringing up the real radio (bellows connect, network formation or restore) takes
        longer than the lock TTL, so the lease must be refreshed while that is in progress.
        If etcd reports the lease as expired, leadership is flagged as lost.
        """
        while self.is_leader:
            try:
                responses = await self._run_blocking(self.lock.refresh)
                if not responses or responses[0].TTL <= 0:
                    raise RuntimeError("etcd reports the leader lease as expired")
            except Exception as e:
                logger.error(f"Lost the leader lease: {e}")
                self.lease_lost = True
                return
            await asyncio.sleep(2)

    async def _run_blocking(self, func, *args, **kwargs):
        """
        Run a synchronous blocking function in the thread pool executor.

        Args:
            func (callable): The blocking function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            Any: The result of the blocking function.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, lambda: func(*args, **kwargs))

    async def perform_failover(self):
        """
        Attempt to recover state from etcd if available.
        Returns True if recovery was successful, False if no state exists (fresh start).
        Raises if state exists but could not be applied to the radio.
        """
        logger.info("Checking etcd for existing network state...")
        db_bytes, _ = await self._run_blocking(self.etcd.get, ETCD_DB_KEY)
        nvram_bytes, _ = await self._run_blocking(self.etcd.get, ETCD_NVRAM_KEY)

        if db_bytes is None or nvram_bytes is None:
            logger.info("No existing state found in etcd. Starting fresh network.")
            return False

        logger.info("Found existing state in etcd. Initiating Failover & Recovery...")

        # 1. Restore the zigpy device database before the application opens it
        restore_database(radio.DB_PATH, db_bytes)

        # 2. Fetch NVRAM JSON and convert it to a zigpy NetworkBackup
        backup_obj = zigpy.backups.NetworkBackup.from_dict(json.loads(nvram_bytes.decode("utf-8")))
        old_counter = backup_obj.network_info.network_key.tx_counter
        logger.info(
            f"Restoring PAN {backup_obj.network_info.pan_id} on channel "
            f"{backup_obj.network_info.channel}; nwk frame counter {old_counter} "
            f"-> {old_counter + radio.FRAME_COUNTER_SAFETY_MARGIN} (safety margin)"
        )

        # 3. Write the network settings into the radio and start it (no new network is formed)
        self.app = await radio.create_application(radio.build_config())
        await radio.start_restored(self.app, backup_obj)
        expected = old_counter + radio.FRAME_COUNTER_SAFETY_MARGIN
        actual = self.app.state.network_info.network_key.tx_counter
        if actual < expected:
            # Seen with the Renode-simulated NCP: the counter written before formNetwork does not
            # survive the NCP reset bellows performs afterwards, so the stack restarts it from its
            # last persisted token. Devices would then drop our frames as replays, so try to set
            # it again now that no further reset follows.
            logger.warning(f"NCP reports nwk frame counter {actual} after restore, expected >= {expected}")
            actual = await radio.force_frame_counter(self.app, expected)
        if actual < expected:
            logger.warning(f"frame counter continuity is NOT guaranteed on this NCP ({actual} < {expected})")
        else:
            logger.info(f"NCP nwk frame counter after restore: {actual} (>= {expected})")

        logger.info(f"FAILOVER COMPLETE: node_{self.pid} is the new leader.")
        return True

    async def data_sync_routine(self):
        """
        Background task running on the leader node to synchronize
        Zigbee network state and database to the etcd cluster.
        """
        while self.is_leader:
            try:
                if self.events is not None:
                    for device in list(self.app.devices.values()):
                        self.events.watch(device)

                # 1. Create NVRAM backup (network settings, keys, frame counters) as JSON
                backup = await self.app.backups.create_backup()
                backup_json = json.dumps(backup.as_dict(), default=str)
                await self._run_blocking(self.etcd.put, ETCD_NVRAM_KEY, backup_json)

                # 2. Snapshot the device database and write it to etcd
                db_bytes = await self._run_blocking(snapshot_database, radio.DB_PATH)
                if db_bytes is not None:
                    await self._run_blocking(self.etcd.put, ETCD_DB_KEY, db_bytes)
                    logger.info(
                        f"Synced NVRAM (frame counter {backup.network_info.network_key.tx_counter}) "
                        f"and DB ({len(db_bytes)} bytes) to etcd."
                    )
                else:
                    logger.warning(f"Database file {radio.DB_PATH} not found. DB sync skipped.")

            except Exception as e:
                logger.error(f"Error during data sync: {e}")

            await asyncio.sleep(5)

    async def become_leader(self):
        """Bring up the radio, recovering the network from etcd when state exists."""
        recovered = await self.perform_failover()
        if not recovered:
            # Fresh start: use the network the NCP already has, or form a new one
            self.app = await radio.create_application(radio.build_config())
            await radio.start_fresh(self.app)
        ni = self.app.state.network_info
        logger.info(
            f"Radio up: PAN {ni.pan_id}, ExtPAN {ni.extended_pan_id}, channel {ni.channel}, "
            f"coordinator IEEE {self.app.state.node_info.ieee}"
        )
        self.events = ZigbeeEvents(self)
        self.app.add_listener(self.events)
        for device in list(self.app.devices.values()):
            if device.nwk != 0x0000:
                logger.info(f"Known device from database: nwk 0x{device.nwk:04X} ieee {device.ieee} model={device.model!r}")
                self.events.watch(device)
        self.permit_task = asyncio.create_task(self.permit_join_routine())

    async def permit_join_routine(self):
        """Keep the network open for joins while this node is the leader."""
        while self.is_leader:
            try:
                await self.app.permit(PERMIT_JOIN_SECONDS)
                logger.info(f"Permit join open for {PERMIT_JOIN_SECONDS} s")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"permit join failed: {e}")
            await asyncio.sleep(PERMIT_JOIN_SECONDS - 30)

    async def step_down(self):
        """Stop being the leader: stop syncing and release the radio for the next leader."""
        self.is_leader = False
        if self.keepalive_task:
            self.keepalive_task.cancel()
            self.keepalive_task = None
        if self.sync_task:
            self.sync_task.cancel()
            self.sync_task = None
        if self.permit_task:
            self.permit_task.cancel()
            self.permit_task = None
        app, self.app = self.app, None
        await radio.stop(app)

    async def run(self):
        """
        Start the main asynchronous loop for leader election.

        Continuously attempts to acquire or refresh the etcd lock.
        If successful, transitions to or maintains LEADER state, otherwise
        transitions to STANDBY state.
        """
        logger.info(f"Node {self.pid} starting Leader Election loop (radio backend: {radio.RADIO_BACKEND})...")

        while True:
            try:
                if not self.is_leader:
                    # Attempt to acquire the lock
                    # timeout=0: try once and return. With a positive timeout python-etcd3 waits on
                    # a watch that can hang forever after the key is deleted; the loop polls anyway.
                    acquired = await self._run_blocking(self.lock.acquire, timeout=0)
                    if acquired:
                        logger.info(f"Node {self.pid} ACQUIRED lock! Transitioning to LEADER.")
                        self.is_leader = True
                        self.lease_lost = False
                        self.keepalive_task = asyncio.create_task(self.lease_keepalive())

                        # 1+2. Start the radio: Failover & Recovery, or fresh start
                        await self.become_leader()
                        if self.lease_lost:
                            raise RuntimeError("leader lease expired while starting the radio")

                        # 3. Start background task for Data Sync
                        self.sync_task = asyncio.create_task(self.data_sync_routine())
                    else:
                        logger.debug(f"Node {self.pid} is STANDBY.")

                if self.is_leader:
                    if self.lease_lost:
                        raise RuntimeError("leader lease expired")

            except Exception as e:
                logger.error(f"Error in election loop: {e}")
                was_leader = self.is_leader
                await self.step_down()
                if was_leader:
                    try:
                        await self._run_blocking(self.lock.release)
                    except Exception as release_err:  # noqa: BLE001
                        logger.warning(f"Could not release leader lock: {release_err}")

            # Sleep and retry acquiring the lock / heartbeat
            await asyncio.sleep(3)

    async def shutdown(self):
        """Disconnect from the radio on exit. The etcd lock is left to expire (crash semantics)."""
        await self.step_down()


if __name__ == "__main__":
    node = CoordinatorHA()

    async def main():
        try:
            await node.run()
        finally:
            await node.shutdown()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
