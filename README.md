# High Availability Zigbee Coordinator Simulation

This project simulates a High Availability (HA) Zigbee network with redundant coordinators. It uses `etcd` for leader election and persistent state synchronization, so that if the active Zigbee coordinator crashes, a standby coordinator takes over the network without losing its state.

The Zigbee radio is a **real, unmodified Silicon Labs EmberZNet NCP firmware** (EFR32MG24, `zigbee_ncp_uart_hardware` sample, EZSP v13) running in the [Renode](https://renode.io) emulator. The coordinators talk to it with the standard [bellows](https://github.com/zigpy/bellows) zigpy radio library over a TCP socket, exactly as they would with a physical NCP dongle. A **second simulated NCP on the same emulated 802.15.4 medium acts as a real Zigbee end device** (a temperature sensor) that joins the coordinator's network and keeps reporting across a failover. A mock in-process radio is available for runs without the emulator.

## Project Structure
```text
├── src/
│   ├── coordinator.py           # The HA Coordinator logic (Leader Election, Sync, Failover)
│   ├── radio.py                 # Radio backend selection: bellows (Renode NCP) or mock
│   ├── end_device_sim.py        # A Zigbee end device: second NCP joining the network, sending reports
│   └── mock_radio.py            # A simulated Zigbee radio to avoid needing the emulator
├── sim/
│   ├── firmware/mg24_ncp.out    # EmberZNet NCP firmware (Simplicity Studio, GSDK 4.4.1) for both NCPs
│   ├── renode/twonode.resc      # Renode script: two NCPs on one 802.15.4 medium
│   ├── renode/brd4186c_userdata_mem.repl, make_userdata.py   # platform variant + user-data image
│   ├── setup_wsl.sh             # Installs Renode + platform files inside WSL (called by setup.bat)
│   ├── run_ncps.sh              # Starts the two NCPs headless (called by run.bat)
│   └── tools/ash_test.py        # Stand-alone ASH/EZSP link check for one NCP
├── docker-compose.yml           # etcd
├── setup.bat                    # One-time setup script (Python venv + simulator in WSL)
├── run.bat                      # One-click launch script
└── requirements.txt             # Python dependencies
```

## Prerequisites
1. **Python 3.11+** installed and added to your system PATH, plus **git** (a dependency is installed from GitHub).
2. **Docker Desktop** installed and running (for etcd).
3. **WSL2 with an Ubuntu distro** (`wsl --install -d Ubuntu`) with `python3`, `curl` and `tar` (all present on a stock Ubuntu). `setup.bat` installs Renode into it automatically. Without WSL, set `ZIGBEE_RADIO=mock` to use the mock radio.

## Setup
1. Clone the repository and double-click `setup.bat` (or run it from a terminal to watch the output).
2. It creates the Python virtual environment, installs the dependencies, then inside WSL downloads the Renode portable build (about 63 MB, once, into `~/renode_portable`), installs the platform files it needs and generates the NCP user-data image.
3. Run it again at any time; every step is idempotent.

Options via environment variables: `WSL_DISTRO` (default `Ubuntu`), `RENODE_DIR` and `RENODE_URL` (where Renode is installed and downloaded from; an existing Renode 1.15.3 or newer with the Silicon Labs EFR32MG24 platform can be reused), `ZIGBEE_RADIO=mock` to skip the simulator entirely.

## Running the Simulation
1. Ensure **Docker Desktop** is running.
2. Double-click `run.bat`. It starts etcd, the two simulated NCPs in a "Renode NCPs (WSL)" window, and three terminal windows:
   - **Active Coordinator:** The first node to boot up, which becomes the LEADER. It connects to the NCP through bellows, forms a Zigbee network (or adopts the one the NCP already has), opens it for joining and syncs its state to etcd every 5 seconds.
   - **Standby Coordinator:** The second node, which becomes the STANDBY backup.
   - **Zigbee End Device:** The second NCP joining the network and reporting temperatures. Every report logs whether the coordinator acknowledged it (`DELIVERED` / `FAILED`).

The leader logs joins (`Device JOINED`), device discovery, and every received report (`<- REPORT from 0xD293 temperature.measured_value = 22.40 C`).

## Testing Failover
1. Arrange the windows so you can see the Active Coordinator, the Standby Coordinator and the Zigbee End Device.
2. In the **Active Coordinator** window press `Ctrl+C` (simulating a crash).
3. **Standby Coordinator:** when the leader's lock expires (10 s) it acquires the lock, restores the network settings (PAN, keys, frame counter with a safety margin) and the device database from etcd, writes them into the NCP and logs `FAILOVER COMPLETE`. This takes about a minute.
4. **Zigbee End Device:** its reports fail while no coordinator is running, the new leader starts receiving them as soon as it is up, and after the device's NWK rejoin (same address, same keys, no re-association) every report is acknowledged again.

Measured with the simulated NCPs: lock takeover 11 s, radio restored 55–60 s after the crash, acknowledged reports resume 1.5–3 minutes after the crash depending on `FAILURES_BEFORE_REJOIN`.

### Known limitation
The NCP firmware was built without the *Zigbee Token Interface* component, so bellows cannot read the NCP's child table into the backup (`Failed to read NVRAM child info ... LIBRARY_NOT_PRESENT`). After a failover the new coordinator does not know the end device as its child until the device rejoins: reports still arrive, but acknowledgements and commands towards the device fail until then. Rebuilding the NCP with that component (and replacing `sim/firmware/mg24_ncp.out`) should remove the rejoin step.

## Configuration
Environment variables read by `src/radio.py` and `src/end_device_sim.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `ZIGBEE_RADIO` | `bellows` | `bellows` for the Renode NCP, `mock` for the in-process dummy radio (coordinator) |
| `ZIGBEE_DEVICE` | `socket://127.0.0.1:24842` (coordinator), `socket://127.0.0.1:24852` (end device) | Serial path or socket of the NCP |
| `ZIGBEE_CHANNEL` | `15` | Channel used when forming / scanning |
| `ZIGBEE_DB` | `zigbee_devices.db` | zigpy device database (each node needs its own; `run.bat` sets one per node) |
| `REPORT_INTERVAL` | `5` | End device: seconds between reports |
| `FAILURES_BEFORE_REJOIN` | `4` | End device: undelivered reports tolerated before it rejoins |

## About the simulation
`sim/renode/twonode.resc` documents the simulation-side adjustments Renode's stock EFR32MG24 platform needs for this firmware (serial core scheduling, the flash user-data page as plain memory, a reset-cause hook, a `reset` macro standing in for the missing bootloader, and a CPU speed setting). The firmware itself is unmodified. The Renode monitor is reachable on telnet port `24999` while the NCPs run. To check one NCP's link by hand from WSL: `python3 sim/tools/ash_test.py 24842` should end with `RESULT: PASS`.

## Cleaning Up
1. Close the Python windows and the "Renode NCPs (WSL)" window.
2. Run `docker-compose down` to stop etcd (`docker-compose down -v` also wipes the stored network state).

The NCPs keep their networks in simulated flash only while Renode is running; restarting it gives you blank NCPs, and the next leader restores the network from etcd.
