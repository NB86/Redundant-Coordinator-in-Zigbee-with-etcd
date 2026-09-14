# High Availability Zigbee Coordinator with etcd

A resilient, multi-coordinator Zigbee network architecture designed to eliminate the Single Point of Failure (SPOF) of the Zigbee Coordinator. Using distributed consensus via `etcd` for leader election and state synchronization, this system allows a standby coordinator to seamlessly take over an existing Zigbee network without dropping device sessions or losing cryptographic state.

The project features a full emulation stack running **unmodified Silicon Labs EmberZNet NCP firmware** (EFR32MG24) inside the [Renode](https://renode.io) hardware emulator over a simulated 802.15.4 wireless medium, paired with an autonomous Zigbee end device and an optional in-memory mock radio for rapid testing.

---

## 📚 Project Documentation

The project documentation has been organized into dedicated guides according to submission requirements:

| Document | Purpose & Audience |
| :--- | :--- |
| 📖 **[User Guide](USER_GUIDE.md)** | Step-by-step instructions for installation, deployment, configuration, running the simulation, testing failover, and cleanup. |
| 🛠️ **[Developer's Guide](DEVELOPER_GUIDE.md)** | Detailed high-level design, system architecture, module descriptions, sequence diagrams, inter-component interactions, and build/extension tooling. |

---

## ⚡ Quick Start

### 1. Prerequisites
- **Python 3.11+** (added to PATH)
- **Git**
- **Docker Desktop** (running)
- **WSL2 with Ubuntu** (for Renode NCP emulation, or use `set ZIGBEE_RADIO=mock` to skip)

### 2. Setup (One-Time)
Run the automated setup script to configure the Python virtual environment and download Renode inside WSL:
```cmd
setup.bat
```

### 3. Run Simulation
Launch the entire HA stack (etcd, Renode NCPs, Active Coordinator, Standby Coordinator, and End Device):
```cmd
run.bat
```

### 4. Test Failover
1. In the **Active Coordinator** window, press `Ctrl + C` to kill the active leader.
2. Watch the **Standby Coordinator** window detect lease expiration, acquire the lock, restore NVRAM/device database from `etcd`, and become the new Leader.
3. Observe the **Zigbee End Device** rejoin and resume sending acknowledged temperature telemetry.

For complete details, see the **[User Guide](USER_GUIDE.md)**.

---

## 📂 Repository Structure

```text
├── USER_GUIDE.md                # End-user guide: installation, running & failover testing
├── DEVELOPER_GUIDE.md           # Developer guide: architecture, modules & internal workflows
├── README.md                    # Repository landing page and quick overview
├── src/
│   ├── coordinator.py           # HA Coordinator daemon (election, sync, failover)
│   ├── radio.py                 # Radio abstraction layer (bellows / Renode vs. mock)
│   ├── end_device_sim.py        # Autonomous Zigbee End Device simulation (EZSP client)
│   └── mock_radio.py            # In-memory mock radio implementation
├── sim/
│   ├── firmware/mg24_ncp.out    # Precompiled EFR32MG24 EmberZNet NCP binary
│   ├── renode/twonode.resc      # Renode simulation script for dual NCPs on 802.15.4
│   ├── renode/brd4186c_userdata_mem.repl  # Platform variant with flash user data
│   ├── renode/make_userdata.py  # User-data flash image generator
│   ├── setup_wsl.sh             # WSL environment setup script for Renode
│   ├── run_ncps.sh              # Headless Renode launcher script
│   └── tools/ash_test.py        # Standalone ASH/EZSP link diagnostics
├── docker-compose.yml           # Containerized etcd v3 service definition
├── requirements.txt             # Python dependencies
├── setup.bat                    # One-click Windows setup script
└── run.bat                      # One-click Windows multi-process launch script
```

---

## ⚖️ License
Academic and educational project developed for the Technion - Israel Institute of Technology.
