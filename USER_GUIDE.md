# User Guide: High Availability Zigbee Coordinator Simulation

This guide explains how to install, configure, deploy, run, and test the **High Availability (HA) Zigbee Coordinator Simulation** as an end user.

---

## 1. Prerequisites

Before running the project, ensure the following software is installed on your Windows machine:

1. **Python 3.11+**:
   - Download from [python.org](https://www.python.org/) and ensure **"Add python.exe to PATH"** is checked during installation.
2. **Git**:
   - Required for cloning the repository and fetching dependencies.
3. **Docker Desktop**:
   - Required for hosting the distributed `etcd` key-value store.
   - Ensure Docker Desktop is started and running in the background.
4. **WSL2 (Windows Subsystem for Linux)** *(Optional, for full Renode emulation)*:
   - Install Ubuntu via PowerShell:
     ```powershell
     wsl --install -d Ubuntu
     ```
   - Standard Ubuntu utilities (`python3`, `curl`, `tar`) are sufficient.
   - *Note: If you do not have WSL2, you can run the entire simulation using the built-in mock radio (`ZIGBEE_RADIO=mock`), requiring only Python and Docker.*

---

## 2. Installation & Setup

Setup is automated via the provided `setup.bat` script:

1. Open a terminal or file explorer in the root of this project repository.
2. Double-click `setup.bat` (or run `./setup.bat` in your terminal).
3. The setup script will:
   - Create a Python virtual environment (`venv/`).
   - Upgrade `pip` and install all Python dependencies from `requirements.txt`.
   - Automatically download the portable **Renode** emulator into WSL (approximately 63 MB, one-time download).
   - Configure emulated platform definitions and generate the flash user-data image for the Silicon Labs EFR32MG24 NCP.
4. Setup is fully idempotent and only needs to be completed once.

---

## 3. Configuration

The simulation uses sensible default settings, but can be customized via environment variables:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `ZIGBEE_RADIO` | `bellows` | Set to `bellows` for full Renode NCP emulation, or `mock` for lightweight in-process dummy radio. |
| `ZIGBEE_DEVICE` | `socket://127.0.0.1:24842` | Serial socket address for the coordinator NCP (port 24852 for end device). |
| `ZIGBEE_CHANNEL` | `15` | Zigbee 802.15.4 radio channel (11–26). |
| `ZIGBEE_DB` | `zigbee_devices.db` | Path to the SQLite device database (managed automatically per node in `run.bat`). |
| `REPORT_INTERVAL` | `5` | Interval in seconds between temperature sensor reports. |
| `FAILURES_BEFORE_REJOIN` | `4` | Consecutive unacknowledged reports before the end device initiates a network rejoin. |
| `WSL_DISTRO` | `Ubuntu` | Target WSL distribution name for running Renode. |

To override any variable before running, set it in your terminal. For example, to run in mock radio mode:
```cmd
set ZIGBEE_RADIO=mock
run.bat
```

---

## 4. Running the Simulation

Launch the entire high-availability environment with a single click:

1. Verify that **Docker Desktop** is running.
2. Double-click **`run.bat`** (or execute `.\run.bat` in CMD / PowerShell).
3. The script will automatically orchestrate all services:
   - **etcd Cluster**: Starts a lightweight etcd v3 container in Docker.
   - **Renode NCPs (WSL)**: Spawns the Renode emulator running two EFR32MG24 NCPs connected via a virtual 802.15.4 wireless medium:
     - Coordinator NCP: listening on TCP `127.0.0.1:24842`.
     - End Device NCP: listening on TCP `127.0.0.1:24852`.
     - Renode Monitor: telnet accessible on port `24999`.
   - **Active Coordinator**: Boots as the primary coordinator, acquires the `etcd` distributed lock, assumes the **LEADER** role, opens network permit-joins, and continuously backs up state to `etcd`.
   - **Standby Coordinator**: Boots as a secondary coordinator, detects an active leader, and remains in **STANDBY** mode monitoring the heartbeat.
   - **Zigbee End Device**: Boots an autonomous Zigbee temperature sensor that scans for the coordinator's network, joins, and transmits periodic telemetry.

---

## 5. Testing High Availability & Failover

To observe automatic failover in action:

1. Arrange your terminal windows side-by-side so you can see:
   - **Active Coordinator**
   - **Standby Coordinator**
   - **Zigbee End Device**
2. Observe steady-state operation:
   - The Active Coordinator logs periodic database and NVRAM state syncs to `etcd`.
   - The End Device reports temperature measurements (`<- REPORT from 0x... temperature.measured_value = 22.40 C`) marked as `DELIVERED`.
3. **Simulate a Crash**:
   - Focus the **Active Coordinator** window and press `Ctrl + C` (or close the window) to simulate an abrupt failure.
4. **Observe Standby Takeover**:
   - Within ~10 seconds, the active leader's etcd lease expires.
   - The **Standby Coordinator** immediately acquires the leader lock, transitions to **LEADER**, retrieves the latest network parameters and device database from `etcd`, applies frame counter continuity protection, restores the radio state, and logs `FAILOVER COMPLETE`.
5. **Observe End Device Continuity**:
   - While the coordinator is restarting, the end device logs report delivery failures.
   - Once the new leader is ready and the device performs a quick NWK rejoin (preserving its short address and encryption keys), reports immediately resume and are once again acknowledged (`DELIVERED`).

---

## 6. Shutting Down & Cleanup

When you are finished testing:

1. Close the Python terminal windows (`Active Coordinator`, `Standby Coordinator`, `Zigbee End Device`).
2. Close the `Renode NCPs (WSL)` window.
3. Stop the Docker services:
   ```cmd
   docker-compose down
   ```
4. *(Optional)* To completely wipe the persisted network state in `etcd` and start from a blank slate:
   ```cmd
   docker-compose down -v
   ```
