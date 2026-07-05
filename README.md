# High Availability Zigbee Coordinator Simulation

This project simulates a High Availability (HA) Zigbee network using multiple redundant coordinators. It uses `etcd` for persistent state synchronization and leader election, ensuring that if the active Zigbee coordinator crashes, a standby coordinator can instantly take over the network without any data loss.

The simulation also publishes fake sensor traffic and coordinator alerts to an MQTT broker, which can be visualized in real-time on a Home Assistant dashboard.

## Project Structure
```text
├── src/
│   ├── coordinator.py       # The HA Coordinator logic (Leader Election & Sync)
│   ├── mock_radio.py        # A simulated Zigbee radio to avoid needing physical hardware
│   └── sensor_sim.py        # Generates fake MQTT sensor data for visualization
├── ha_config/               # Home Assistant configuration files (sensors, dashboard)
├── docker-compose.yml       # Infrastructure setup (Home Assistant, Mosquitto MQTT, etcd)
├── setup.bat                # One-time setup script
├── run.bat                  # One-click launch script
└── requirements.txt         # Python dependencies
```

## Prerequisites
1. **Python 3.11+** installed and added to your system PATH.
2. **Docker Desktop** installed and running.

## Setup Instructions
Before running the simulation for the first time, you need to install the Python dependencies.

1. Double-click the `setup.bat` file.
2. Wait for it to create the virtual environment and install the required packages.
3. You only need to run this script **once**.

## Running the Simulation

You can launch the entire simulation automatically using the run script!

1. Ensure **Docker Desktop** is open and running in the background.
2. Double-click the `run.bat` file.
3. The script will automatically:
   - Start your backend infrastructure via Docker (`etcd`, Mosquitto, and Home Assistant).
   - Open three separate terminal windows for you:
     - **Active Coordinator:** The first node to boot up, which will become the LEADER.
     - **Standby Coordinator:** The second node to boot up, which will become the STANDBY backup.
     - **Sensor Traffic Sim:** A script generating fake temperature readings for the Zigbee sensors.

## Visualizing in Home Assistant

1. Open your web browser and go to `http://localhost:8123`.
2. Follow the setup wizard to create an account (you can skip the location/timezone steps).
3. Connect the MQTT Broker:
   - Go to **Settings** -> **Devices & Services** -> **Add Integration**.
   - Search for **MQTT**.
   - In the broker field, type: `mosquitto-ha`.
   - Leave the port as `1883` and leave the username/password blank.
   - Click **Submit**.
4. Restart Home Assistant (Settings -> System -> Restart).
5. Go to your **Overview** dashboard, click the **Pencil icon** (Edit), and add an **Entities card**.
6. Search for `Zigbee` and add `Sensor 1` through `Sensor 5`, plus the `Coordinator Alert` sensor.

## Testing Failover

To test the high availability of the system:
1. Arrange your terminal windows so you can see both the Active Coordinator and the Standby Coordinator.
2. Go to the **Active Coordinator** window and press `Ctrl+C` to kill it (simulating a crash).
3. Watch the **Standby Coordinator** window—it will immediately detect the missing heartbeat, acquire the lock, restore the network state from `etcd`, and become the new Leader!
4. Check your Home Assistant dashboard—the **Coordinator Alert** sensor will instantly update to show a failover occurred!

## Cleaning Up

To stop the simulation:
1. Close the three Python terminal windows.
2. Open a terminal in the project folder and run `docker-compose down` to stop the Docker containers.
*Note: If you want to wipe the `etcd` database and start a completely fresh network, run `docker-compose down -v` to delete the storage volumes.*
