"""
Radio backend selection for the HA coordinator.

Two backends are supported, chosen with the ZIGBEE_RADIO environment variable:

  bellows (default)  Real EmberZNet NCP firmware running in Renode, reached over a TCP socket
                     (see README, "Simulated NCP"). Uses the bellows zigpy radio library, exactly
                     as it would with a physical EFR32 NCP dongle.
  mock               The in-process DummyApplication from mock_radio.py (no NCP needed).

Environment variables:
  ZIGBEE_RADIO    bellows | mock                 (default: bellows)
  ZIGBEE_DEVICE   serial path or socket://host:port for bellows
                                                (default: socket://127.0.0.1:24842)
  ZIGBEE_CHANNEL  channel used when a new network is formed
                                                (default: 15; skips the energy scan)
  ZIGBEE_DB       zigpy sqlite database path     (default: zigbee_devices.db)
"""
import logging
import os

import zigpy.application
import zigpy.backups

logger = logging.getLogger("coordinator.radio")

RADIO_BACKEND = os.environ.get("ZIGBEE_RADIO", "bellows").lower()
DEVICE_PATH = os.environ.get("ZIGBEE_DEVICE", "socket://127.0.0.1:24842")
CHANNEL = int(os.environ.get("ZIGBEE_CHANNEL", "15"))
DB_PATH = os.environ.get("ZIGBEE_DB", "zigbee_devices.db")

# Added to the network key's outgoing frame counter when a standby node takes over, so frames it
# sends are never rejected as replays by devices that already saw the old leader's counter.
FRAME_COUNTER_SAFETY_MARGIN = 2500


def build_config() -> dict:
    """zigpy/bellows application configuration for the selected backend."""
    if RADIO_BACKEND == "mock":
        return {"device": {"path": "dummy"}, "database_path": DB_PATH}
    return {
        "device": {"path": DEVICE_PATH, "baudrate": 115200, "flow_control": None},
        "database_path": DB_PATH,
        "network": {"channel": CHANNEL},
    }


async def create_application(config: dict) -> zigpy.application.ControllerApplication:
    """Create the application object with its database loaded, radio not yet started."""
    if RADIO_BACKEND == "mock":
        from mock_radio import DummyApplication

        app = DummyApplication(config)
        if not hasattr(app, "backups"):
            app.backups = zigpy.backups.BackupManager(app)
        return app

    from bellows.zigbee.application import ControllerApplication

    logger.info("Using bellows radio on %s", DEVICE_PATH)
    return await ControllerApplication.new(config, auto_form=False, start_radio=False)


async def start_fresh(app: zigpy.application.ControllerApplication) -> None:
    """Connect to the radio and join the network it already has, or form a new one."""
    await app.startup(auto_form=True)


async def start_restored(
    app: zigpy.application.ControllerApplication, backup: zigpy.backups.NetworkBackup
) -> None:
    """Connect to the radio, write the network settings taken over from etcd, then start."""
    if RADIO_BACKEND == "mock":
        await app.backups.restore_backup(backup, counter_increment=FRAME_COUNTER_SAFETY_MARGIN)
        await app.startup(auto_form=False)
        return

    try:
        await app.connect()
        await app.backups.restore_backup(backup, counter_increment=FRAME_COUNTER_SAFETY_MARGIN)
        await app.initialize(auto_form=False)
    except Exception:
        await app.shutdown(db=False)
        raise


async def stop(app: zigpy.application.ControllerApplication | None) -> None:
    """Disconnect from the radio and close the database; never raises."""
    if app is None:
        return
    try:
        await app.shutdown()
    except Exception as e:  # noqa: BLE001
        logger.warning("Error while shutting down the radio: %r", e)


async def force_frame_counter(app, value: int) -> int:
    """
    Try to set the NWK outgoing frame counter of a running (joined) bellows NCP and return the
    counter the stack reports afterwards. Experimental: the EZSP documentation only guarantees
    the value can be set while not joined.
    """
    if RADIO_BACKEND == "mock":
        return app.state.network_info.network_key.tx_counter
    import bellows.types as t

    ezsp = app._ezsp
    (status,) = await ezsp.setValue(
        valueId=t.EzspValueId.VALUE_NWK_FRAME_COUNTER, value=t.uint32_t(value).serialize()
    )
    logger.info(f"setValue(NWK_FRAME_COUNTER={value}) while joined: {status.name}")
    await app.load_network_info(load_devices=False)
    return app.state.network_info.network_key.tx_counter
