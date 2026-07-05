import asyncio
import logging
import random
import json
import os
from gmqtt import Client as MQTTClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sensor_sim")

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
TOPIC = "zigbee_ha/sensor/traffic"

async def simulate_traffic():
    """
    Simulates Zigbee sensor traffic.
    
    In a real system, an incoming physical Zigbee radio frame would 
    increase the nwk_frame_counter on the Coordinator. Here, we publish 
    simulated traffic to MQTT to visually represent that activity.
    """
    client = MQTTClient(f"sensor_sim-{os.getpid()}")
    
    try:
        await client.connect(MQTT_BROKER, MQTT_PORT)
        logger.info("Connected to MQTT broker.")
        
        frame_seq = 0
        while True:
            # Simulate a random delay between sensor events
            await asyncio.sleep(random.uniform(2.0, 5.0))
            
            frame_seq += 1
            payload = {
                "event": "sensor_data",
                "sensor_id": f"sensor_{random.randint(1, 5)}",
                "value": round(random.uniform(10.0, 99.0), 2),
                "sequence": frame_seq
            }
            
            logger.info(f"Simulating inbound Zigbee frame: {payload}")
            logger.info("--> This event theoretically increments the Active Leader's nwk_frame_counter.")
            
            client.publish(TOPIC, json.dumps(payload))
            
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in sensor simulator: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    logger.info("Starting Traffic Generator (Sensor Simulator)...")
    try:
        asyncio.run(simulate_traffic())
    except KeyboardInterrupt:
        logger.info("Shutting down sensor simulator...")
