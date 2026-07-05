import asyncio
import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import etcd3
from gmqtt import Client as MQTTClient
import zigpy.application
import zigpy.backups
import zigpy.types as t

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("coordinator")

from mock_radio import DummyApplication


# --- 2.2 Main Async Loop & Leader Election ---
class CoordinatorHA:
    """
    High-Availability Coordinator node for Zigbee.

    Handles leader election via etcd locks, connects to an MQTT broker
    to publish state, and starts the Zigbee dummy radio when holding
    the leader lock.
    """

    def __init__(self):
        """
        Initialize the coordinator instance, setting up etcd and MQTT clients.
        """
        self.pid = os.getpid()
        self.is_leader = False
        self.app = None
        
        # Initialize etcd3 client
        self.etcd = etcd3.client(host='127.0.0.1', port=2379)
        self.lock = self.etcd.lock('/zigbee/leader/lock', ttl=10)
        
        # Initialize MQTT client
        self.mqtt = MQTTClient(f"coordinator-{self.pid}")
        
        # Executor for blocking etcd3 calls
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.sync_task = None
        
    async def connect_mqtt(self):
        """
        Connect the MQTT client to the local broker asynchronously.
        """
        await self.mqtt.connect("127.0.0.1", 1883)
        logger.info(f"Node {self.pid} connected to MQTT.")

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
        """
        logger.info("Checking etcd for existing network state...")
        db_bytes, _ = await self._run_blocking(self.etcd.get, '/zigbee/state/database')
        nvram_bytes, _ = await self._run_blocking(self.etcd.get, '/zigbee/state/nvram')
        
        if db_bytes is not None and nvram_bytes is not None:
            logger.info("Found existing state in etcd. Initiating Failover & Recovery...")
            
            # 1. Write DB bytes to local file
            with open("zigbee_devices.db", "wb") as f:
                f.write(db_bytes)
                
            # 2. Fetch NVRAM JSON
            nvram_json = nvram_bytes.decode('utf-8')
            backup_dict = json.loads(nvram_json)
            
            # 3. Convert JSON to zigpy.backups.NetworkBackup
            if hasattr(zigpy.backups.NetworkBackup, 'from_dict'):
                backup_obj = zigpy.backups.NetworkBackup.from_dict(backup_dict)
            else:
                backup_obj = zigpy.backups.NetworkBackup(**backup_dict)
            
            # 4. Safety Margin: Add 2500 to the nwk_frame_counter
            if hasattr(backup_obj, 'network_info') and hasattr(backup_obj.network_info, 'nwk_frame_counter'):
                logger.info(f"Old nwk_frame_counter: {backup_obj.network_info.nwk_frame_counter}")
                backup_obj.network_info.nwk_frame_counter += 2500
                logger.info(f"New nwk_frame_counter: {backup_obj.network_info.nwk_frame_counter}")
            
            # Ensure BackupManager exists
            if not hasattr(self.app, 'backups'):
                self.app.backups = zigpy.backups.BackupManager(self.app)
                
            # 5. Restore backup
            try:
                await self.app.backups.restore_backup(backup_obj)
            except Exception as e:
                logger.error(f"Failed to restore backup: {e}")
                return False
            
            # 6. Call startup with auto_form=False
            await self.app.startup(auto_form=False)
            
            # 7. Publish MQTT alert
            alert_payload = {"alert": "FAILOVER_OCCURRED", "new_leader": f"node_{self.pid}"}
            self.mqtt.publish("zigbee_ha/alerts", json.dumps(alert_payload))
            logger.info("Failover complete.")
            return True
        else:
            logger.info("No existing state found in etcd. Starting fresh network.")
            return False

    async def data_sync_routine(self):
        """
        Background task running on the leader node to synchronize
        Zigbee network state and database to the etcd cluster.
        """
        while self.is_leader:
            try:
                # 1. Create NVRAM backup and convert to JSON
                if self.app and hasattr(self.app, 'backups'):
                    backup = await self.app.backups.create_backup()
                    
                    if hasattr(backup, 'as_dict'):
                        backup_json = json.dumps(backup.as_dict(), default=str)
                    else:
                        backup_json = json.dumps(backup, default=str)
                    
                    # 3. Write NVRAM to etcd
                    await self._run_blocking(self.etcd.put, '/zigbee/state/nvram', backup_json)
                
                # 2. Read the local database as raw bytes
                db_path = "zigbee_devices.db"
                if os.path.exists(db_path):
                    with open(db_path, "rb") as f:
                        db_bytes = f.read()
                    
                    # 3. Write database to etcd
                    await self._run_blocking(self.etcd.put, '/zigbee/state/database', db_bytes)
                    logger.info("Successfully synced NVRAM and DB to etcd.")
                else:
                    logger.warning(f"Database file {db_path} not found. DB sync skipped.")

            except Exception as e:
                logger.error(f"Error during data sync: {e}")
            
            await asyncio.sleep(5)

    async def run(self):
        """
        Start the main asynchronous loop for leader election.

        Continuously attempts to acquire or refresh the etcd lock.
        If successful, transitions to or maintains LEADER state, otherwise
        transitions to STANDBY state. Emits MQTT status updates on each iteration.
        """
        await self.connect_mqtt()
        logger.info(f"Node {self.pid} starting Leader Election loop...")
        
        while True:
            try:
                if not self.is_leader:
                    # Attempt to acquire the lock
                    acquired = await self._run_blocking(self.lock.acquire)
                    if acquired:
                        logger.info(f"Node {self.pid} ACQUIRED lock! Transitioning to LEADER.")
                        self.is_leader = True
                        
                        # 1. Instantiate the DummyApplication
                        config = {
                            "device": {"path": "dummy"}, 
                            "database_path": "zigbee_devices.db"
                        }
                        self.app = DummyApplication(config)
                        
                        # Ensure BackupManager exists BEFORE attempting failover or sync
                        if not hasattr(self.app, 'backups'):
                            self.app.backups = zigpy.backups.BackupManager(self.app)
                        
                        # 2. Task 2.4: Attempt Failover & Recovery
                        recovered = await self.perform_failover()
                        if not recovered:
                            # Fresh start
                            await self.app.startup(auto_form=True)
                        
                        # 3. Publish MQTT heartbeat
                        self.mqtt.publish(f"zigbee_ha/node_{self.pid}/status", json.dumps({"state": "ACTIVE"}))
                        
                        # 4. Start background task for Data Sync
                        self.sync_task = asyncio.create_task(self.data_sync_routine())
                    else:
                        logger.debug(f"Node {self.pid} is STANDBY.")
                        # 2. Publish MQTT heartbeat (Standby)
                        self.mqtt.publish(f"zigbee_ha/node_{self.pid}/status", json.dumps({"state": "STANDBY"}))
                
                if self.is_leader:
                    # Refresh lock to keep TTL alive
                    await self._run_blocking(self.lock.refresh)
                    self.mqtt.publish(f"zigbee_ha/node_{self.pid}/status", json.dumps({"state": "ACTIVE"}))

            except Exception as e:
                logger.error(f"Error in election loop: {e}")
                self.is_leader = False
                if self.sync_task:
                    self.sync_task.cancel()
            
            # Sleep and retry acquiring the lock / heartbeat
            await asyncio.sleep(3)

if __name__ == "__main__":
    node = CoordinatorHA()
    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
