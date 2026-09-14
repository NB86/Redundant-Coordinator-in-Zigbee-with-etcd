"""
A real Zigbee end device made from a second EmberZNet NCP.

The NCP firmware is the full EmberZNet stack behind an EZSP interface, so a second simulated
NCP can be told to *join* the coordinator's network instead of forming one. This host drives it
over bellows' EZSP layer:

  * registers a Home Automation "temperature sensor" endpoint (Basic + Temperature Measurement),
  * scans for a joinable PAN and joins it as an end device with the well-known link key,
  * sends a temperature attribute report to the coordinator every REPORT_INTERVAL seconds and
    logs whether the coordinator acknowledged it (APS ack), which is the communication check
    used for the failover test,
  * answers the coordinator's ZCL Read Attributes / Configure Reporting so zigpy can
    initialize the device,

Environment variables:
  ZIGBEE_DEVICE    NCP to use (default: socket://127.0.0.1:24852)
  ZIGBEE_CHANNEL   channel to scan (default: 15)
  REPORT_INTERVAL  seconds between reports (default: 5)
  FAILURES_BEFORE_REJOIN  undelivered reports tolerated before rejoining (default: 4)
"""
import asyncio
import logging
import os
import random
import struct
import time

import bellows.types as t
from bellows.ezsp import EZSP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("end_device")

DEVICE_PATH = os.environ.get("ZIGBEE_DEVICE", "socket://127.0.0.1:24852")
CHANNEL = int(os.environ.get("ZIGBEE_CHANNEL", "15"))
REPORT_INTERVAL = float(os.environ.get("REPORT_INTERVAL", "5"))
# Consecutive undelivered reports before the device gives up on its parent and rejoins.
# A coordinator failover takes about a minute; each failed report costs ~15 s of APS retries.
FAILURES_BEFORE_REJOIN = int(os.environ.get("FAILURES_BEFORE_REJOIN", "4"))

HA_PROFILE = 0x0104
BASIC_CLUSTER = 0x0000
TEMPERATURE_CLUSTER = 0x0402
TEMPERATURE_SENSOR_DEVICE = 0x0302
ENDPOINT = 1
COORDINATOR_NWK = 0x0000
WELL_KNOWN_LINK_KEY = b"ZigBeeAlliance09"

# ZCL global commands
ZCL_READ_ATTRIBUTES = 0x00
ZCL_READ_ATTRIBUTES_RSP = 0x01
ZCL_CONFIGURE_REPORTING = 0x06
ZCL_CONFIGURE_REPORTING_RSP = 0x07
ZCL_REPORT_ATTRIBUTES = 0x0A
ZCL_DEFAULT_RESPONSE = 0x0B

def zcl_string(text: str) -> bytes:
    data = text.encode()
    return bytes([len(data)]) + data


# attribute id -> (zcl type, encoded value)
BASIC_ATTRIBUTES = {
    0x0000: (0x20, bytes([8])),                    # ZCLVersion
    0x0004: (0x42, zcl_string("Renode/Silabs")),   # ManufacturerName
    0x0005: (0x42, zcl_string("EFR32MG24-NCP")),   # ModelIdentifier
    0x0007: (0x30, bytes([3])),                    # PowerSource: battery
}


def zcl_header(frame_control: int, seq: int, command: int) -> bytes:
    return bytes([frame_control, seq, command])


class EndDevice:
    def __init__(self):
        self.ezsp = None
        self.node_id = None
        self.network_up = asyncio.Event()
        self.found = []
        self.pending = {}  # messageTag -> future(EmberStatus)
        self.tag = 0
        self.zcl_seq = 0
        self.report_seq = 0
        self.delivered = 0
        self.failed = 0
        self.consecutive_failures = 0
        self.recover_needed = asyncio.Event()
        self.recovering = False
        self.rejoins = 0

    # ------------------------------------------------------------------ EZSP callbacks
    def on_callback(self, frame_name, args):
        if frame_name == "stackStatusHandler":
            (status,) = args
            logger.info(f"Stack status: {status.name}")
            if status == t.EmberStatus.NETWORK_UP:
                self.network_up.set()
            elif status in (t.EmberStatus.NETWORK_DOWN, t.EmberStatus.JOIN_FAILED):
                self.network_up.clear()
                if not self.recovering:  # the stack reports NETWORK_DOWN while it rejoins
                    self.recover_needed.set()
        elif frame_name == "networkFoundHandler":
            network, lqi, rssi = args
            logger.info(
                f"Found PAN 0x{network.panId:04X} ext {network.extendedPanId} ch {network.channel} "
                f"allowingJoin={bool(network.allowingJoin)} lqi={lqi} rssi={rssi}"
            )
        elif frame_name == "messageSentHandler":
            msg_type, dest, aps, tag, status, contents = args
            fut = self.pending.pop(tag, None)
            if fut and not fut.done():
                fut.set_result(status)
        elif frame_name == "incomingMessageHandler":
            msg_type, aps, lqi, rssi, sender, binding, addr_index, contents = args
            asyncio.get_running_loop().create_task(self.handle_incoming(aps, sender, bytes(contents)))

    # ------------------------------------------------------------------ incoming ZCL
    async def handle_incoming(self, aps, sender, data):
        if aps.profileId != HA_PROFILE or len(data) < 3:
            return
        frame_control = data[0]
        manufacturer_specific = frame_control & 0x04
        hdr_len = 5 if manufacturer_specific else 3
        seq, command = data[hdr_len - 2], data[hdr_len - 1]
        payload = data[hdr_len:]
        cluster_specific = frame_control & 0x03 == 1
        logger.info(
            f"<- from 0x{sender:04X} cluster 0x{aps.clusterId:04X} "
            f"{'cluster' if cluster_specific else 'global'} cmd 0x{command:02X} payload {payload.hex()}"
        )
        if cluster_specific:
            return
        if command == ZCL_READ_ATTRIBUTES:
            rsp = b""
            for (attr,) in struct.iter_unpack("<H", payload[: len(payload) // 2 * 2]):
                table = BASIC_ATTRIBUTES if aps.clusterId == BASIC_CLUSTER else {}
                if aps.clusterId == TEMPERATURE_CLUSTER and attr == 0x0000:
                    table = {0x0000: (0x29, struct.pack("<h", self.current_temperature()))}
                if attr in table:
                    zcl_type, value = table[attr]
                    rsp += struct.pack("<HBB", attr, 0x00, zcl_type) + value
                else:
                    rsp += struct.pack("<HB", attr, 0x86)  # UNSUPPORTED_ATTRIBUTE
            await self.send(aps.clusterId, zcl_header(0x18, seq, ZCL_READ_ATTRIBUTES_RSP) + rsp, sender, aps.sourceEndpoint)
        elif command == ZCL_CONFIGURE_REPORTING:
            await self.send(aps.clusterId, zcl_header(0x18, seq, ZCL_CONFIGURE_REPORTING_RSP) + b"\x00", sender, aps.sourceEndpoint)
        elif command in (ZCL_READ_ATTRIBUTES_RSP, ZCL_DEFAULT_RESPONSE, ZCL_CONFIGURE_REPORTING_RSP):
            pass
        elif not frame_control & 0x10:
            await self.send(aps.clusterId, zcl_header(0x18, seq, ZCL_DEFAULT_RESPONSE) + bytes([command, 0x81]), sender, aps.sourceEndpoint)

    # ------------------------------------------------------------------ sending
    async def send(self, cluster, payload, dest=COORDINATOR_NWK, dst_ep=ENDPOINT, timeout=15):
        """Unicast an APS message and return the delivery status reported by the NCP."""
        self.tag = (self.tag % 255) + 1
        tag = self.tag
        aps = t.EmberApsFrame(
            profileId=HA_PROFILE, clusterId=cluster, sourceEndpoint=ENDPOINT, destinationEndpoint=dst_ep,
            options=t.EmberApsOption.APS_OPTION_RETRY | t.EmberApsOption.APS_OPTION_ENABLE_ROUTE_DISCOVERY,
            groupId=0, sequence=0,
        )
        fut = asyncio.get_running_loop().create_future()
        self.pending[tag] = fut
        (status, seq) = await self.ezsp.sendUnicast(
            type=t.EmberOutgoingMessageType.OUTGOING_DIRECT, indexOrDestination=dest,
            apsFrame=aps, messageTag=tag, messageContents=payload,
        )
        if status != t.EmberStatus.SUCCESS:
            self.pending.pop(tag, None)
            return status
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(tag, None)
            return t.EmberStatus.DELIVERY_FAILED

    def current_temperature(self) -> int:
        return int(random.uniform(18.0, 26.0) * 100)  # hundredths of a degree

    async def report_loop(self):
        while True:
            await asyncio.sleep(REPORT_INTERVAL)
            if not self.network_up.is_set():
                logger.warning("Network is down, skipping report")
                continue
            temp = self.current_temperature()
            self.zcl_seq = (self.zcl_seq + 1) % 256
            self.report_seq += 1
            payload = zcl_header(0x18, self.zcl_seq, ZCL_REPORT_ATTRIBUTES) + struct.pack("<HBh", 0x0000, 0x29, temp)
            t0 = time.time()
            status = await self.send(TEMPERATURE_CLUSTER, payload)
            ok = status == t.EmberStatus.SUCCESS
            if ok:
                self.delivered += 1
                self.consecutive_failures = 0
            else:
                self.failed += 1
                self.consecutive_failures += 1
            logger.log(
                logging.INFO if ok else logging.WARNING,
                f"-> report #{self.report_seq} temperature {temp / 100:.2f} C to coordinator: "
                f"{'DELIVERED' if ok else 'FAILED (' + status.name + ')'} in {time.time() - t0:.2f}s "
                f"[delivered {self.delivered}, failed {self.failed}, rejoins {self.rejoins}]",
            )
            if self.consecutive_failures >= FAILURES_BEFORE_REJOIN:
                logger.warning(f"{FAILURES_BEFORE_REJOIN} consecutive delivery failures: the parent is not answering, recovering")
                self.consecutive_failures = 0
                self.recover_needed.set()

    async def recovery_loop(self):
        """
        What a real end device does when it loses its parent: NWK rejoin (keeps the network
        key and the short address), and after repeated failures a fresh association.
        """
        while True:
            await self.recover_needed.wait()
            self.recover_needed.clear()
            self.recovering = True
            for attempt in range(1, 3):
                if self.network_up.is_set() and attempt > 1:
                    break
                self.network_up.clear()
                (status,) = await self.ezsp.findAndRejoinNetwork(
                    haveCurrentNetworkKey=True, channelMask=t.Channels.from_channel_list([CHANNEL]))
                logger.warning(f"REJOIN attempt {attempt}: findAndRejoinNetwork -> {status.name}")
                try:
                    await asyncio.wait_for(self.network_up.wait(), 30)
                    logger.info("REJOIN succeeded, network is up again")
                    self.rejoins += 1
                    break
                except asyncio.TimeoutError:
                    logger.warning("REJOIN did not complete")
            if not self.network_up.is_set():
                logger.warning("Rejoin failed twice: leaving and joining again (new association)")
                try:
                    await self.ezsp.leaveNetwork()
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"leaveNetwork: {e}")
                await asyncio.sleep(2)
                await self.scan_and_join()
                self.rejoins += 1
                (self.node_id,) = await self.ezsp.getNodeId()
                logger.info(f"Re-associated as nwk 0x{self.node_id:04X}")
            self.recovering = False
            self.recover_needed.clear()
            self.consecutive_failures = 0

    # ------------------------------------------------------------------ joining
    async def set_security_state(self):
        """Well-known Zigbee 3.0 install: join with ZigBeeAlliance09 as the TC link key."""
        security = t.EmberInitialSecurityState(
            bitmask=(t.EmberInitialSecurityBitmask.HAVE_PRECONFIGURED_KEY
                     | t.EmberInitialSecurityBitmask.TRUST_CENTER_GLOBAL_LINK_KEY
                     | t.EmberInitialSecurityBitmask.REQUIRE_ENCRYPTED_KEY),
            preconfiguredKey=t.KeyData(WELL_KNOWN_LINK_KEY),
            networkKey=t.KeyData(bytes(16)), networkKeySequenceNumber=0,
            preconfiguredTrustCenterEui64=t.EUI64.convert("00:00:00:00:00:00:00:00"),
        )
        (status,) = await self.ezsp.setInitialSecurityState(state=security)
        logger.info(f"setInitialSecurityState: {status.name}")

    async def scan_and_join(self):
        while True:
            # bellows collects the networkFoundHandler callbacks until scanCompleteHandler
            try:
                results = await self.ezsp.startScan(
                    scanType=t.EzspNetworkScanType.ACTIVE_SCAN,
                    channelMask=t.Channels.from_channel_list([CHANNEL]), duration=3,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Active scan failed: {e}; retrying in 10 s")
                await asyncio.sleep(10)
                continue
            self.found = [network for (network, lqi, rssi) in results]
            logger.info(f"Active scan on channel {CHANNEL}: {len(self.found)} network(s) found")
            joinable = [n for n in self.found if n.allowingJoin]
            if not joinable:
                logger.info("No network permitting joins yet (is the coordinator up and permitting?); retrying in 10 s")
                await asyncio.sleep(10)
                continue
            network = joinable[0]
            params = t.EmberNetworkParameters(
                extendedPanId=network.extendedPanId, panId=network.panId, radioTxPower=8,
                radioChannel=network.channel, joinMethod=t.EmberJoinMethod.USE_MAC_ASSOCIATION,
                nwkManagerId=0, nwkUpdateId=network.nwkUpdateId,
                channels=t.Channels.from_channel_list([network.channel]),
            )
            self.network_up.clear()
            await self.set_security_state()
            (status,) = await self.ezsp.joinNetwork(nodeType=t.EmberNodeType.END_DEVICE, parameters=params)
            logger.info(f"joinNetwork(END_DEVICE, PAN 0x{network.panId:04X}): {status.name}")
            try:
                await asyncio.wait_for(self.network_up.wait(), 60)
                return
            except asyncio.TimeoutError:
                logger.warning("Join did not complete, retrying")

    async def run(self):
        self.ezsp = EZSP({"path": DEVICE_PATH, "baudrate": 115200, "flow_control": None})
        await self.ezsp.connect(use_thread=False)
        self.ezsp.add_callback(self.on_callback)
        logger.info(f"Connected to NCP {DEVICE_PATH}, EZSP v{self.ezsp.ezsp_version}")

        for config_id, value in ((t.EzspConfigId.CONFIG_STACK_PROFILE, 2), (t.EzspConfigId.CONFIG_SECURITY_LEVEL, 5)):
            (status,) = await self.ezsp.setConfigurationValue(configId=config_id, value=value)
        (status,) = await self.ezsp.addEndpoint(
            endpoint=ENDPOINT, profileId=HA_PROFILE, deviceId=TEMPERATURE_SENSOR_DEVICE, deviceVersion=0,
            inputClusterCount=2, outputClusterCount=0, inputClusterList=[BASIC_CLUSTER, TEMPERATURE_CLUSTER],
            outputClusterList=[],
        )
        logger.info(f"addEndpoint: {status.name}")
        await self.set_security_state()

        (status,) = await self.ezsp.networkInit(networkInitBitmask=t.EmberNetworkInitBitmask.NETWORK_INIT_NO_OPTIONS)
        logger.info(f"networkInit: {status.name}")
        if status == t.EmberStatus.SUCCESS:
            await asyncio.wait_for(self.network_up.wait(), 30)
            logger.info("Rejoined the network stored in the NCP")
        else:
            await self.scan_and_join()

        (self.node_id,) = await self.ezsp.getNodeId()
        (ieee,) = await self.ezsp.getEui64()
        (st, node_type, params) = await self.ezsp.getNetworkParameters()
        logger.info(
            f"JOINED as {node_type.name} nwk 0x{self.node_id:04X} ieee {ieee} on PAN 0x{params.panId:04X} "
            f"channel {params.radioChannel}"
        )
        asyncio.get_running_loop().create_task(self.recovery_loop())
        await self.report_loop()


if __name__ == "__main__":
    try:
        asyncio.run(EndDevice().run())
    except KeyboardInterrupt:
        logger.info("Shutting down end device...")
