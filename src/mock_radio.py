import logging
import zigpy.application
import zigpy.types as t

logger = logging.getLogger("coordinator.mock_radio")

class DummyApplication(zigpy.application.ControllerApplication):
    """
    A simulated Zigbee radio application for high-availability testing.
    
    This class overrides core zigpy ControllerApplication methods to
    simulate a functional radio without physical hardware.
    """

    async def startup(self, auto_form=False):
        """
        Initialize the dummy radio and set up the in-memory network state.

        Args:
            auto_form (bool): Whether to automatically form a new network
                if one does not exist. Defaults to False.
        """
        logger.info("Starting Dummy Radio...")
        # Create a dummy database file so the sync routine doesn't complain
        db_path = self.config.get("database_path", "zigbee_devices.db")
        import os
        if not os.path.exists(db_path):
            with open(db_path, "wb") as f:
                f.write(b"")
                
        # Initialize an in-memory network state
        self.state.network_info.pan_id = t.PanId(0x1234)
        self.state.network_info.extended_pan_id = t.ExtendedPanId(t.EUI64.convert("00:11:22:33:44:55:66:77"))
        self.state.network_info.nwk_update_id = 0
        self.state.network_info.channel = 11
        
        from zigpy.state import Key
        key = Key()
        key.key = t.KeyData(b'\x01' * 16)
        key.seq_num = 0
        self.state.network_info.network_key = key
        
        # Add a mock IEEE address so the node_info is complete
        self.state.node_info.ieee = t.EUI64.convert("00:11:22:33:44:55:66:88")
        
        logger.info(f"Dummy Radio started. PAN: {self.state.network_info.pan_id}, ExtPAN: {self.state.network_info.extended_pan_id}")

    async def send_packet(self, packet):
        """
        Simulate sending a Zigbee packet over the air.

        Args:
            packet: The payload of the packet to send.
        """
        logger.info(f"Dummy Radio sending packet: {packet}")

    async def permit_ncp(self, time_s=254):
        """
        Simulate permitting the Network Co-Processor (NCP) to allow
        new devices to join the network.

        Args:
            time_s (int): Duration in seconds to permit joining. Defaults to 254.
        """
        logger.info(f"Dummy Radio permit_ncp for {time_s} seconds")

    async def add_endpoint(self, *args, **kwargs):
        pass

    async def connect(self, *args, **kwargs):
        pass

    async def disconnect(self, *args, **kwargs):
        pass

    async def force_remove(self, *args, **kwargs):
        pass

    async def load_network_info(self, *args, **kwargs):
        pass

    async def permit_with_link_key(self, *args, **kwargs):
        pass

    async def reset_network_info(self, *args, **kwargs):
        pass

    async def start_network(self, *args, **kwargs):
        pass

    async def write_network_info(self, *args, **kwargs):
        pass
