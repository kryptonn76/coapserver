"""
Native WebSocket Handler for Border Router Communication

This module provides a native WebSocket handler (without Socket.IO)
for ESP32 Border Router clients using esp_websocket_client.

The ESP32 client sends/receives plain JSON messages, so we cannot use
Flask-SocketIO which wraps messages in Socket.IO protocol.
"""

import json
import time
# logging removed - using print() instead to avoid circular import issues
import queue
import threading
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

# logger removed - using print() for all logging

# Import Network Topology Aggregator for Network Diagnostic events
from lib.network_topology_aggregator import NetworkTopologyAggregator

# Références injectées par server.py (évite l'import circulaire)
_app = _socketio = _coap = _border_router_manager = _topology_refresh_callback = None

def init(app, socketio, coap_server, border_router_manager, topology_refresh_callback=None):
    """
    Initialize handler with references from main server

    This avoids circular imports and ensures we use the SAME socketio instance
    that's running the server (not a phantom copy from reimported module).

    Args:
        app: Flask app instance
        socketio: Flask-SocketIO instance (THE REAL ONE from __main__)
        coap_server: CoAPServer instance
        border_router_manager: BorderRouterManager instance
        topology_refresh_callback: Callback to trigger CoAP topology scan (optional)
    """
    global _app, _socketio, _coap, _border_router_manager, _topology_refresh_callback
    _app, _socketio, _coap, _border_router_manager = app, socketio, coap_server, border_router_manager
    _topology_refresh_callback = topology_refresh_callback
    print(f"✅ native_websocket_handler.init() called")
    print(f"   socketio id: {id(_socketio)}")
    print(f"   topology_refresh_callback: {'SET' if _topology_refresh_callback else 'NOT SET'}")
    print(f"   module: {__name__}")


class NativeWebSocketHandler:
    """
    Handles native WebSocket connections from ESP32 Border Routers

    This class manages the WebSocket communication protocol between
    the cloud server and Border Router clients, using plain JSON
    messages without Socket.IO encapsulation.
    """

    def __init__(self, border_router_manager, br_auth_enabled=True, mesh_local_prefix="fd00::/8"):
        """
        Initialize the native WebSocket handler

        Args:
            border_router_manager: BorderRouterManager instance
            br_auth_enabled: Enable token authentication
            mesh_local_prefix: Thread Mesh-Local prefix for ML-EID extraction (default: fd00::/8)
        """
        self.border_router_manager = border_router_manager
        self.br_auth_enabled = br_auth_enabled
        self.active_connections: Dict[str, any] = {}  # {br_id: ws_connection}
        self.message_queues: Dict[str, queue.Queue] = {}  # {br_id: Queue()} for thread-safe message sending
        self.tx_threads: Dict[str, threading.Thread] = {}  # {br_id: Thread} dedicated TX threads
        self.ipv6_mapping: Dict[str, Dict] = {}  # {ipv6: {'node_name': str, 'br_id': str, 'last_seen': float}}

        # Network Topology Aggregator for Network Diagnostic events
        self.topology_aggregator = NetworkTopologyAggregator(mesh_local_prefix)

        print("🔧 Native WebSocket handler initialized (TX thread pattern + Network Diagnostic)")

    def parse_connection_params(self, environ) -> Dict[str, str]:
        """
        Parse query parameters from WebSocket connection URL

        Args:
            environ: WSGI environ dictionary

        Returns:
            Dictionary with br_id, auth_token, network_prefix
        """
        query_string = environ.get('QUERY_STRING', '')
        params = parse_qs(query_string)

        # Extract parameters (parse_qs returns lists)
        return {
            'br_id': params.get('br_id', [''])[0],
            'auth_token': params.get('auth_token', [''])[0],
            'network_prefix': params.get('network_prefix', [''])[0]
        }

    def authenticate_br(self, br_id: str, auth_token: str) -> bool:
        """
        Authenticate Border Router connection

        Args:
            br_id: Border Router ID
            auth_token: Authentication token

        Returns:
            True if authentication succeeds
        """
        if not self.br_auth_enabled:
            print(f"🔓 Authentication disabled, accepting BR {br_id}")
            return True

        # Import here to avoid circular dependency
        from lib.br_auth import verify_br_token

        if verify_br_token(br_id, auth_token):
            print(f"✅ BR {br_id} authenticated successfully")
            return True
        else:
            print(f"❌ BR {br_id} authentication failed")
            return False

    def extract_rloc16_from_rloc_ipv6(self, ipv6: str) -> Optional[str]:
        """
        Extract RLOC16 from IPv6 RLOC address

        RLOC (Routing Locator) addresses have a specific pattern in the Interface Identifier (last 64 bits):
        Pattern: 00:00:00:ff:fe:00:XX:XX where XX:XX is the RLOC16

        Args:
            ipv6: IPv6 address (e.g., "fdc7:4097:c896:f63b:0:ff:fe00:c400")

        Returns:
            RLOC16 as string (e.g., "0xc400") or None if not a RLOC address
        """
        try:
            import ipaddress

            # Parse IPv6
            ipv6_obj = ipaddress.IPv6Address(ipv6)
            ipv6_int = int(ipv6_obj)

            # Extract IID (last 64 bits)
            iid = ipv6_int & ((1 << 64) - 1)  # Mask to get last 64 bits

            # Convert IID to bytes (8 bytes)
            iid_bytes = iid.to_bytes(8, 'big')

            # Check RLOC pattern: 00:00:00:ff:fe:00:xx:xx
            if (iid_bytes[0] == 0x00 and
                iid_bytes[1] == 0x00 and
                iid_bytes[2] == 0x00 and
                iid_bytes[3] == 0xff and
                iid_bytes[4] == 0xfe and
                iid_bytes[5] == 0x00):

                # Extract RLOC16 (last 2 bytes)
                rloc16_high = iid_bytes[6]
                rloc16_low = iid_bytes[7]
                rloc16 = (rloc16_high << 8) | rloc16_low

                rloc16_str = f"0x{rloc16:04x}"
                print(f"   🔍 Detected RLOC address, extracted RLOC16: {rloc16_str}")
                return rloc16_str

            # Not a RLOC address
            return None

        except Exception as e:
            print(f"❌ Error extracting RLOC16 from {ipv6}: {e}")
            return None

    def resolve_ipv6_to_node_name(self, ipv6: str) -> Optional[str]:
        """
        Resolve IPv6 address to node name using config/adresses.json and Network Diagnostic topology

        Strategy:
        1. First, try to match IPv6 in adresses.json (ML-EID)
        2. If not found, check if it's a RLOC address
        3. If RLOC, extract RLOC16 and search in Network Diagnostic topology
        4. If found in topology, try to resolve business name from ML-EID

        Args:
            ipv6: IPv6 address (e.g., "fd78:8e78:3bfe:1:1234:5678:90ab:cdef" or RLOC)

        Returns:
            node_name (e.g., "n01") or None if not found
        """
        print(f"🔍 Resolving IPv6 → node_name: {ipv6}")

        try:
            import json
            with open('config/adresses.json', 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Search for matching IPv6 (case-insensitive comparison)
            ipv6_lower = ipv6.lower()
            total_nodes = len(config.get('nodes', {}))
            print(f"   📁 Loaded {total_nodes} nodes from config")

            for node_name, node_data in config.get('nodes', {}).items():
                node_ipv6 = node_data.get('address', '').lower()
                if node_ipv6 == ipv6_lower:
                    print(f"   ✅ MATCH: {ipv6} → {node_name}")
                    return node_name

            # Not found in config - try alternative resolution methods
            print(f"   ❌ NO MATCH in adresses.json ({total_nodes} nodes checked)")

            # Try 1: Check if it's a mesh-local EID by searching in topology
            topology = self.topology_aggregator.get_topology()
            for node in topology.get('nodes', []):
                # Check if this IPv6 is one of the node's MLEIDs
                node_mleids = [mleid.lower() for mleid in node.get('mleids', [])]
                if ipv6_lower in node_mleids:
                    print(f"   🔍 Found mesh-local EID in topology, resolving to business name...")

                    # Try to find the Thread global address (fd78:...) for this node in adresses.json
                    # by checking all MLEIDs of this node
                    for mleid in node.get('mleids', []):
                        for biz_name, biz_data in config.get('nodes', {}).items():
                            if biz_data.get('address', '').lower() == mleid.lower():
                                print(f"   ✅ Mesh-local→Business name: {ipv6} → {biz_name}")
                                return biz_name

                    print(f"   ⚠️  Mesh-local EID found in topology but no business name match")

            # Try 2: Check if it's a RLOC address
            rloc16 = self.extract_rloc16_from_rloc_ipv6(ipv6)
            if rloc16:
                print(f"   🔍 Trying to resolve via Network Diagnostic topology with RLOC16: {rloc16}")

                # Search in Network Diagnostic topology
                for node in topology.get('nodes', []):
                    # Check if RLOC16 matches
                    if rloc16.lower() in [r.lower() for r in node.get('rloc16s', [])]:
                        print(f"   ✅ Found node in topology with RLOC16 {rloc16}")

                        # Try to resolve business name from ML-EID
                        mleids = node.get('mleids', [])
                        if mleids:
                            ml_eid = mleids[0]  # Use first ML-EID
                            print(f"   🔍 Resolving ML-EID {ml_eid} to business name...")

                            for biz_name, biz_data in config.get('nodes', {}).items():
                                if biz_data.get('address', '').lower() == ml_eid.lower():
                                    print(f"   ✅ RLOC→ML-EID→Business name: {rloc16} → {ml_eid} → {biz_name}")
                                    return biz_name

                            print(f"   ⚠️  ML-EID {ml_eid} not in config, using RLOC16 as name")
                            return rloc16  # Use RLOC16 as fallback name

                        # No ML-EID, use RLOC16 as name
                        print(f"   ⚠️  No ML-EID found, using RLOC16 as name")
                        return rloc16

                print(f"   ❌ RLOC16 {rloc16} not found in Network Diagnostic topology")

            return None

        except FileNotFoundError:
            print("❌ config/adresses.json not found")
            return None
        except Exception as e:
            print(f"❌ Error resolving IPv6: {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def resolve_node_name_to_ipv6(self, node_name: str) -> Optional[str]:
        """
        Resolve node name to IPv6 using config/adresses.json

        Args:
            node_name: Node name (e.g., "n01")

        Returns:
            IPv6 address or None if not found
        """
        try:
            import json
            with open('config/adresses.json', 'r', encoding='utf-8') as f:
                config = json.load(f)

            node_data = config.get('nodes', {}).get(node_name)
            if node_data:
                return node_data.get('address')

            print(f"⚠️ Node {node_name} not found in adresses.json")
            return None

        except Exception as e:
            print(f"❌ Error resolving node name: {e}")
            return None

    def resolve_extaddr_to_ml_eid(self, ext_addr: str) -> Optional[tuple]:
        """
        Resolve Extended Address to ML-EID using config/adresses.json

        The ML-EID's last 64 bits (Interface Identifier) are derived from the Extended Address
        by flipping the U/L bit (bit 1 of the first byte). This method matches Extended Addresses
        to ML-EIDs by comparing the IID portion.

        Args:
            ext_addr: Extended Address as hex string (e.g., "0123456789abcdef")

        Returns:
            Tuple of (ml_eid, node_name) or None if not found
        """
        try:
            # Remove colons if present and convert to lowercase
            ext_addr_clean = ext_addr.replace(':', '').lower()

            if len(ext_addr_clean) != 16:
                print(f"⚠️ Invalid Extended Address length: {ext_addr} (expected 16 hex chars)")
                return None

            # Convert Extended Address to IID (flip U/L bit - bit 1 of first byte)
            ext_bytes = bytes.fromhex(ext_addr_clean)
            iid_bytes = bytearray(ext_bytes)
            iid_bytes[0] ^= 0x02  # Flip U/L bit

            # Convert IID to lowercase hex string for comparison
            iid_hex = iid_bytes.hex().lower()

            print(f"🔍 Resolving ExtAddr → ML-EID: {ext_addr}")
            print(f"   IID (with flipped bit): {iid_hex}")

            # Load addresses from config
            import json
            with open('config/adresses.json', 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Search for matching ML-EID (check if last 64 bits match IID)
            for node_name, node_data in config.get('nodes', {}).items():
                ml_eid = node_data.get('address', '').lower()

                # Extract last 64 bits (IID) from ML-EID
                # ML-EID format: fd78:8e78:3bfe:1:IIII:IIII:IIII:IIII
                # Remove colons and get last 16 hex chars
                ml_eid_clean = ml_eid.replace(':', '')
                if len(ml_eid_clean) >= 32:
                    ml_eid_iid = ml_eid_clean[-16:]  # Last 64 bits

                    if ml_eid_iid == iid_hex:
                        print(f"   ✅ MATCH: {ext_addr} → {ml_eid} ({node_name})")
                        return (ml_eid, node_name)

            print(f"   ❌ NO MATCH: ExtAddr {ext_addr} not found in adresses.json")
            return None

        except FileNotFoundError:
            print("❌ config/adresses.json not found")
            return None
        except Exception as e:
            print(f"❌ Error resolving ExtAddr: {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def calculate_linklocal_from_extaddr(self, ext_addr: str) -> Optional[str]:
        """
        Calculate link-local IPv6 address from Extended Address (EUI-64)

        Link-local addresses are always reachable for direct neighbors (1-hop).
        The IID (Interface Identifier) is derived by flipping the U/L bit
        (bit 1 of the first byte) of the Extended Address.

        Args:
            ext_addr: Extended Address as hex string (e.g., "0123456789abcdef" or "01:23:45:67:89:ab:cd:ef")

        Returns:
            Link-local IPv6 address (e.g., "fe80::0323:4567:89ab:cdef") or None if invalid
        """
        try:
            # Remove colons if present and convert to lowercase
            ext_addr_clean = ext_addr.replace(':', '').lower()

            if len(ext_addr_clean) != 16:
                print(f"⚠️ Invalid Extended Address length: {ext_addr} (expected 16 hex chars)")
                return None

            # Convert Extended Address to bytes
            ext_bytes = bytes.fromhex(ext_addr_clean)

            # Flip U/L bit (bit 1 of first byte) to get IID
            iid_bytes = bytearray(ext_bytes)
            iid_bytes[0] ^= 0x02  # Flip U/L bit

            # Build link-local address: fe80::<IID>
            # Format: fe80::XXXX:XXXX:XXXX:XXXX
            ll_addr = f"fe80::{iid_bytes[0]:02x}{iid_bytes[1]:02x}:{iid_bytes[2]:02x}{iid_bytes[3]:02x}:{iid_bytes[4]:02x}{iid_bytes[5]:02x}:{iid_bytes[6]:02x}{iid_bytes[7]:02x}"

            print(f"🔗 ExtAddr {ext_addr} → Link-Local {ll_addr}")
            return ll_addr

        except Exception as e:
            print(f"❌ Error calculating link-local from ExtAddr {ext_addr}: {e}")
            return None

    def update_ipv6_mapping(self, ipv6: str, node_name: str, br_id: str):
        """
        Update dynamic IPv6 → node → BR mapping

        Args:
            ipv6: IPv6 address
            node_name: Node name
            br_id: Border Router ID
        """
        self.ipv6_mapping[ipv6] = {
            'node_name': node_name,
            'br_id': br_id,
            'last_seen': time.time()
        }
        print(f"📍 Mapping updated: {ipv6} → {node_name} → {br_id}")

    def get_br_for_node(self, node_name: str) -> Optional[str]:
        """
        Get Border Router ID for a given node

        Args:
            node_name: Node name

        Returns:
            BR ID or None if not found
        """
        # First, try to find in IPv6 mapping
        for ipv6, mapping in self.ipv6_mapping.items():
            if mapping['node_name'] == node_name:
                return mapping['br_id']

        # If not found in mapping, resolve IPv6 and check
        ipv6 = self.resolve_node_name_to_ipv6(node_name)
        if ipv6 and ipv6 in self.ipv6_mapping:
            return self.ipv6_mapping[ipv6]['br_id']

        print(f"⚠️ No BR mapping found for node {node_name}")
        return None

    def _process_outgoing_queue(self, br_id: str, ws) -> int:
        """
        Process pending outgoing messages from queue (thread-safe sending)

        This method is called from the WebSocket handler thread to send
        any messages that were queued by other threads (e.g., HTTP request threads).

        DEPRECATED: This method is no longer used in the TX thread pattern.
        Messages are now sent by a dedicated TX thread (_tx_thread_worker).

        Args:
            br_id: Border Router ID
            ws: WebSocket connection object

        Returns:
            Number of messages sent
        """
        if br_id not in self.message_queues:
            return 0

        msg_queue = self.message_queues[br_id]
        sent_count = 0

        # Process all pending messages (non-blocking)
        while not msg_queue.empty():
            try:
                message = msg_queue.get_nowait()
                ws.send(message)
                sent_count += 1
                print(f"📤 Queue: Sent message to BR {br_id} ({msg_queue.qsize()} remaining)")
            except queue.Empty:
                break
            except Exception as e:
                print(f"❌ Error sending queued message to BR {br_id}: {e}")

        return sent_count

    def _tx_thread_worker(self, br_id: str, ws):
        """
        Dedicated TX thread worker for sending messages to Border Router

        This thread continuously monitors the message queue and sends messages
        immediately when they become available. It blocks on queue.get() until
        a message is available or a shutdown sentinel (None) is received.

        This pattern solves the race condition where messages were enqueued but
        not sent because ws.receive() blocked the main loop.

        Args:
            br_id: Border Router ID
            ws: WebSocket connection object
        """
        print(f"📤 TX thread started for BR {br_id}")

        try:
            while True:
                # Block until message available (or None sentinel for shutdown)
                message = self.message_queues[br_id].get()

                # Check for shutdown sentinel
                if message is None:
                    print(f"🛑 TX thread received shutdown signal for BR {br_id}")
                    break

                # Send message to Border Router
                try:
                    ws.send(message)
                    print(f"📤 TX→BR {br_id}: Sent {len(message)} bytes")
                    print(f"   Content: {message[:200]}...")  # Log first 200 chars
                except Exception as e:
                    print(f"❌ TX thread failed to send to BR {br_id}: {e}")
                    # Don't break - try to send remaining messages
                    # The RX thread will handle connection cleanup

        except Exception as e:
            print(f"❌ TX thread crashed for BR {br_id}: {e}")
            import traceback
            print(traceback.format_exc())

        print(f"📤 TX thread stopped for BR {br_id}")

    def handle_connection(self, ws, environ):
        """
        Handle incoming WebSocket connection from Border Router

        This is the main entry point for WebSocket connections.
        It parses connection params, authenticates, and enters the
        message processing loop.

        Args:
            ws: WebSocket connection object
            environ: WSGI environ dictionary
        """
        # Parse connection parameters
        params = self.parse_connection_params(environ)
        br_id = params['br_id']
        auth_token = params['auth_token']
        network_prefix = params['network_prefix']

        print(f"📡 New WebSocket connection from BR {br_id}")

        # Validate required parameters
        if not br_id or not auth_token:
            print("❌ Missing br_id or auth_token in connection URL")
            error_msg = json.dumps({
                'type': 'error',
                'message': 'Missing br_id or auth_token'
            })
            ws.send(error_msg)
            return

        # Authenticate
        if not self.authenticate_br(br_id, auth_token):
            print(f"❌ Authentication failed for BR {br_id}")
            error_msg = json.dumps({
                'type': 'error',
                'message': 'Authentication failed'
            })
            ws.send(error_msg)
            return

        # Get BR configuration (nodes list)
        from lib.br_auth import get_br_config
        br_config = get_br_config(br_id)
        nodes = br_config.get('nodes', []) if br_config else []

        # Register BR in the manager
        # Note: For native WebSocket, we use br_id as sid (no separate session ID)
        success = self.border_router_manager.register_br(
            br_id=br_id,
            sid=br_id,  # Use br_id as session identifier
            network_prefix=network_prefix,
            nodes=nodes
        )

        if not success:
            print(f"❌ Failed to register BR {br_id}")
            error_msg = json.dumps({
                'type': 'error',
                'message': 'Failed to register Border Router'
            })
            ws.send(error_msg)
            return

        # Store active connection
        self.active_connections[br_id] = ws

        # Create message queue for this BR (thread-safe communication)
        self.message_queues[br_id] = queue.Queue()

        # Start dedicated TX thread for sending messages
        tx_thread = threading.Thread(
            target=self._tx_thread_worker,
            args=(br_id, ws),
            name=f"TX-{br_id}",
            daemon=True
        )
        tx_thread.start()
        self.tx_threads[br_id] = tx_thread
        print(f"✅ TX thread started for BR {br_id}")

        # Send connection confirmation
        connected_msg = json.dumps({
            'type': 'connected',
            'status': 'ok',
            'br_id': br_id,
            'server_time': time.time(),
            'nodes': nodes,
            'message': 'Border Router connected successfully'
        })
        ws.send(connected_msg)

        print(f"✅ Border Router {br_id} connected and registered")

        # Enter message processing loop (RX only - TX is handled by dedicated thread)
        try:
            while True:
                # Receive incoming message (blocking)
                message = ws.receive()

                if message is None:
                    # Connection closed
                    print(f"🔌 BR {br_id} closed connection")
                    break

                # Process received message
                self.handle_message(br_id, message, ws)

        except Exception as e:
            print(f"❌ Error in WebSocket RX loop for BR {br_id}: {e}")

        finally:
            # Signal TX thread to shutdown (send None sentinel)
            if br_id in self.message_queues:
                print(f"🛑 Signaling TX thread shutdown for BR {br_id}")
                self.message_queues[br_id].put(None)

            # Wait for TX thread to finish (with timeout)
            if br_id in self.tx_threads:
                tx_thread = self.tx_threads[br_id]
                tx_thread.join(timeout=2.0)
                if tx_thread.is_alive():
                    print(f"⚠️ TX thread for BR {br_id} did not stop in time")
                else:
                    print(f"✅ TX thread for BR {br_id} stopped cleanly")
                del self.tx_threads[br_id]

            # Cleanup: unregister BR and remove from active connections
            self.border_router_manager.unregister_br(br_id)
            if br_id in self.active_connections:
                del self.active_connections[br_id]
            if br_id in self.message_queues:
                del self.message_queues[br_id]
            print(f"⚠️ Border Router {br_id} disconnected")

    def handle_message(self, br_id: str, message: str, ws):
        """
        Process incoming message from Border Router

        Args:
            br_id: Border Router ID
            message: JSON message string
            ws: WebSocket connection object
        """
        # 🔍 DEBUG: Log raw message received (ultra-verbose for debugging)
        print(f"🔍 DEBUG: Received raw message from BR {br_id}")
        print(f"   Length: {len(message)} bytes")
        print(f"   First 300 chars: {message[:300]}...")

        try:
            # Parse JSON
            data = json.loads(message)
            msg_type = data.get('type')

            # 🔍 DEBUG: Log extracted message type
            print(f"📥 BR {br_id}: {msg_type} ({len(message)} bytes)")
            print(f"   🔍 DEBUG: msg_type='{msg_type}' (type={type(msg_type).__name__})")

            if not msg_type:
                print(f"❌ Message from BR {br_id} missing 'type' field")
                print(f"   Full message: {message}")
                return

            # Route to appropriate handler
            if msg_type == 'heartbeat':
                self.handle_heartbeat(br_id, data, ws)

            elif msg_type == 'node_event':
                # New: Handle node_event with source_ipv6 field
                self.handle_node_event_with_ipv6(br_id, data)

            elif msg_type == 'node_discovered':
                # New: Handle node discovery announcement
                self.handle_node_discovered(br_id, data)

            elif msg_type == 'command_response':
                self.handle_command_response(br_id, data)

            elif msg_type == 'topology_update':
                self.handle_topology_update(br_id, data)

            elif msg_type == 'scan_node_result':
                # New: Handle scan_node result for topology discovery
                self.handle_scan_node_result(br_id, data)

            elif msg_type == 'diagnostic_node':
                # Network Diagnostic: Node discovery via multicast ff03::1
                print(f"   ✅ DEBUG: Routing to handle_diagnostic_node()")
                self.handle_diagnostic_node(br_id, data)

            elif msg_type == 'diagnostic_link':
                # Network Diagnostic: Router↔Router link metrics
                print(f"   ✅ DEBUG: Routing to handle_diagnostic_link()")
                self.handle_diagnostic_link(br_id, data)

            elif msg_type == 'diagnostic_child':
                # Network Diagnostic: Parent↔Child link metrics
                print(f"   ✅ DEBUG: Routing to handle_diagnostic_child()")
                self.handle_diagnostic_child(br_id, data)

            else:
                print(f"⚠️ Unknown message type from BR {br_id}: {msg_type}")
                print(f"   Available handlers: heartbeat, node_event, node_discovered, command_response, topology_update, scan_node_result, diagnostic_node, diagnostic_link, diagnostic_child")

        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON from BR {br_id}: {e}")
            print(f"📩 Trame complète reçue: {message}")
        except Exception as e:
            print(f"❌ Error processing message from BR {br_id}: {e}")
            import traceback
            print(traceback.format_exc())

    def handle_heartbeat(self, br_id: str, data: dict, ws):
        """
        Process heartbeat message from Border Router

        Args:
            br_id: Border Router ID
            data: Heartbeat data (timestamp, nodes_count, status)
            ws: WebSocket connection
        """
        nodes_count = data.get('nodes_count', 0)

        # Auto-register BR on first heartbeat if not already registered
        if br_id not in self.active_connections:
            print(f"📝 Auto-registering BR {br_id} from heartbeat")
            self.active_connections[br_id] = ws

            # Create message queue if doesn't exist
            if br_id not in self.message_queues:
                self.message_queues[br_id] = queue.Queue()

                # Start dedicated TX thread for sending messages
                tx_thread = threading.Thread(
                    target=self._tx_thread_worker,
                    args=(br_id, ws),
                    name=f"TX-{br_id}",
                    daemon=True
                )
                tx_thread.start()
                self.tx_threads[br_id] = tx_thread
                print(f"✅ TX thread started for BR {br_id} (late registration)")

            # Register in border router manager if not already done
            if not self.border_router_manager.is_br_registered(br_id):
                self.border_router_manager.register_br(
                    br_id=br_id,
                    sid=br_id,
                    network_prefix=data.get('network_prefix', 'fd78:8e78:3bfe:1::/64'),
                    nodes=[]
                )
                print(f"✅ BR {br_id} registered in manager (auto-registration)")

        # Update heartbeat in manager
        self.border_router_manager.update_heartbeat(br_id, nodes_count)

        # Send heartbeat acknowledgment
        ack_msg = json.dumps({
            'type': 'heartbeat_ack',
            'timestamp': time.time(),
            'server_status': 'ok'
        })
        ws.send(ack_msg)

        # Refresh gateway timestamp to keep it online while BR is connected
        # Gateway is the BR itself and doesn't send CoAP events, so we update its last_seen on each heartbeat
        for ipv6, mapping in self.ipv6_mapping.items():
            if mapping['node_name'] == 'gateway' and mapping['br_id'] == br_id:
                mapping['last_seen'] = time.time()
                break

        print(f"💓 BR {br_id}: {nodes_count} nodes")

    def handle_node_event_with_ipv6(self, br_id: str, data: dict):
        """
        Process node event with IPv6 source from Border Router

        Args:
            br_id: Border Router ID
            data: Event data with source_ipv6, event_type, payload
        """
        # 📦 LOG: Extraction des champs
        print(f"📦 PYTHON: Processing node_event from BR {br_id}")
        print(f"   Full event data: {json.dumps(data, indent=2)}")

        source_ipv6 = data.get('source_ipv6')
        source_rloc = data.get('source_rloc')  # RLOC optionnel pour référence
        event_type = data.get('event_type')
        payload = data.get('payload', {})

        print(f"   🌐 Extracted fields:")
        print(f"      source_ipv6: {source_ipv6}")
        if source_rloc:
            print(f"      source_rloc: {source_rloc} (for reference)")
        print(f"      event_type: {event_type}")
        print(f"      payload: {json.dumps(payload)}")

        if not source_ipv6 or not event_type:
            print(f"❌ Invalid node_event from BR {br_id}: missing source_ipv6 or event_type")
            return

        # 🆕 Détecter si c'est un NOUVEAU node (première fois qu'on le voit)
        is_new_node = source_ipv6 not in self.ipv6_mapping

        # Resolve IPv6 to node name
        print(f"   🔍 Resolving IPv6 to node name...")
        node_name = self.resolve_ipv6_to_node_name(source_ipv6)
        if not node_name:
            print(f"⚠️ Unknown node IPv6: {source_ipv6} (event: {event_type})")
            # Create temporary name for unknown nodes
            node_name = f"unknown-{source_ipv6[-8:]}"
            print(f"   🏷️  Generated temporary name: {node_name}")
        else:
            print(f"   ✅ Resolved to known node: {node_name}")

        # Update IPv6 mapping
        self.update_ipv6_mapping(source_ipv6, node_name, br_id)
        print(f"   📍 Mapping updated: {source_ipv6} → {node_name} → {br_id}")

        # 🆕 Émettre événement Socket.IO si c'est un nouveau node
        if is_new_node and _socketio:
            print(f"   🎉 NEW NODE DETECTED! Emitting 'node_update' event to web clients")
            _socketio.emit('node_update', {
                'node_name': node_name,
                'ipv6': source_ipv6,
                'br_id': br_id,
                'timestamp': time.time()
            }, namespace='/')
            print(f"✨ New active node: {node_name} ({source_ipv6}) via {br_id}")

            # 🔄 Déclencher un scan CoAP en arrière-plan pour enrichir la topologie
            if _topology_refresh_callback:
                import threading
                print(f"🔍 Déclenchement scan CoAP pour enrichir topologie...")
                thread = threading.Thread(target=_topology_refresh_callback)
                thread.daemon = True
                thread.start()
                print(f"✅ Scan CoAP démarré en arrière-plan")

        # Increment event counter
        self.border_router_manager.increment_event_counter(br_id)

        # 🔍 DEBUG: Vérifier si coap_server existe
        print(f"   🔍 DEBUG: event_type={event_type}, coap_server={'EXISTS' if _coap else 'IS NONE'}")

        # Route to appropriate handler based on event type
        if event_type == 'ble_beacon' and _coap:
            print(f"   ✅ Calling coap_server.handle_ble_event_from_br() with payload: {payload}")
            _coap.handle_ble_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'payload': payload  # Passer le payload complet
            })
        elif event_type == 'ble_beacon' and not _coap:
            print(f"   ❌ CANNOT call handler: coap_server is None!")

        elif event_type == 'button' and _coap:
            _coap.handle_button_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'payload': payload
            })

        elif event_type == 'battery' and _coap:
            _coap.handle_battery_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'voltage': payload.get('voltage_mv'),
                'percentage': payload.get('percentage')
            })

        # Emit to web clients via Socket.IO
        if _socketio:
            _socketio.emit('node_event', {
                'node': node_name,
                'br_id': br_id,
                'ipv6': source_ipv6,
                'event_type': event_type,
                'payload': payload,
                'timestamp': time.time()
            }, namespace='/')

        print(f"📨 Node event from BR {br_id}: {node_name} ({source_ipv6}) - {event_type}")

    def handle_node_discovered(self, br_id: str, data: dict):
        """
        Handle node discovery announcement from Border Router

        Args:
            br_id: Border Router ID
            data: Discovery data with source_ipv6
        """
        source_ipv6 = data.get('source_ipv6')

        print(f"🆔 NODE DISCOVERED from BR {br_id}:")
        print(f"   🌐 Source IPv6: {source_ipv6}")

        if not source_ipv6:
            print(f"❌ Invalid node_discovered from BR {br_id}: missing source_ipv6")
            return

        # Resolve IPv6 to node name
        node_name = self.resolve_ipv6_to_node_name(source_ipv6)
        if not node_name:
            print(f"   ⚠️  Unknown node (not in config)")
            node_name = f"unknown-{source_ipv6[-8:]}"
            print(f"   🏷️  Generated temporary name: {node_name}")
        else:
            print(f"   ✅ Known node: {node_name}")

        # Update mapping
        self.update_ipv6_mapping(source_ipv6, node_name, br_id)
        print(f"   📍 Mapping registered: {node_name} via BR {br_id}")

        # Emit to web interface
        if _socketio:
            _socketio.emit('node_discovered', {
                'node_name': node_name,
                'ipv6': source_ipv6,
                'br_id': br_id,
                'timestamp': time.time()
            }, namespace='/')

    def handle_node_event(self, br_id: str, data: dict):
        """
        Process node event from Border Router

        Args:
            br_id: Border Router ID
            data: Event data (node, event_type, payload)
        """
        node_name = data.get('node')
        event_type = data.get('event_type')
        payload = data.get('payload', {})

        if not node_name or not event_type:
            print(f"❌ Invalid node_event from BR {br_id}: missing node or event_type")
            return

        # Increment event counter
        self.border_router_manager.increment_event_counter(br_id)

        # Route to appropriate handler based on event type
        if event_type == 'button' and _coap:
            _coap.handle_button_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'payload': payload
            })

        elif event_type == 'battery' and _coap:
            _coap.handle_battery_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'voltage': payload.get('voltage'),
                'percentage': payload.get('percentage')
            })

        elif event_type == 'ble-beacon' and _coap:
            _coap.handle_ble_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'ble_addr': payload.get('ble_addr'),
                'rssi': payload.get('rssi'),
                'code': payload.get('code')
            })

        # Emit to web clients via Socket.IO
        if _socketio:
            _socketio.emit('node_event', {
                'node': node_name,
                'br_id': br_id,
                'event_type': event_type,
                'payload': payload,
                'timestamp': time.time()
            }, namespace='/')

        print(f"📨 Node event from BR {br_id}: {node_name} - {event_type}")

    def handle_command_response(self, br_id: str, data: dict):
        """
        Process command response from Border Router

        Args:
            br_id: Border Router ID
            data: Response data (request_id, node, status, result, error)
        """
        request_id = data.get('request_id')
        node_name = data.get('node')
        status = data.get('status')
        result = data.get('result', {})
        error = data.get('error')

        if not request_id:
            print(f"❌ Command response from BR {br_id} missing request_id")
            return

        # Notify web clients via Socket.IO
        if _socketio:
            _socketio.emit('command_completed', {
                'request_id': request_id,
                'node': node_name,
                'br_id': br_id,
                'status': status,
                'result': result,
                'error': error,
                'timestamp': time.time()
            }, namespace='/')

        print(f"📨 Command response from BR {br_id}: {request_id} - {status}")

    def handle_topology_update(self, br_id: str, data: dict):
        """
        Process topology update from Border Router

        Args:
            br_id: Border Router ID
            data: Topology data (nodes list)
        """
        nodes = data.get('nodes', [])

        # Extract node names
        node_names = [n.get('name') for n in nodes if n.get('name')]

        # Update nodes list in manager
        self.border_router_manager.update_nodes_list(br_id, node_names)

        print(f"🗺️ Topology update from BR {br_id}: {len(node_names)} nodes")

        # Notify web clients
        if _socketio:
            _socketio.emit('topology_update', {
                'br_id': br_id,
                'nodes_count': len(node_names),
                'timestamp': time.time()
            }, namespace='/')

    def handle_diagnostic_node(self, br_id: str, data: dict):
        """
        Process Network Diagnostic node event from Border Router

        Receives node information from multicast ff03::1 diagnostic queries.
        Aggregates asynchronously without timeout.

        Args:
            br_id: Border Router ID
            data: Node event with keys:
                  - partition (int): Thread partition ID
                  - ext_addr (str): Extended Address (EUI-64)
                  - rloc16 (str): Routing Locator
                  - role (str): router/reed/child/leader
                  - ipv6_list (list): All IPv6 addresses
                  - is_br (bool, optional): True if this node is the Border Router itself
        """
        # 🔍 DEBUG: Confirmation that handler is called
        print(f"🔍 DEBUG: handle_diagnostic_node() CALLED for BR {br_id}")
        print(f"   Data keys: {list(data.keys())}")
        print(f"   partition: {data.get('partition')}")
        print(f"   ext_addr: {data.get('ext_addr')}")
        print(f"   rloc16: {data.get('rloc16')}")
        print(f"   role: {data.get('role')}")
        print(f"   is_br: {data.get('is_br', False)}")
        print(f"   ipv6_list length: {len(data.get('ipv6_list', []))}")

        # Check if this node is the Border Router itself
        is_border_router = data.get('is_br', False)
        if is_border_router:
            print(f"🌐 Network Diagnostic: BORDER ROUTER self-diagnostic from BR {br_id}")
        else:
            print(f"📡 Network Diagnostic: Node from BR {br_id}")

        # Upsert to topology aggregator
        print(f"   🔄 Calling topology_aggregator.upsert_node()...")
        self.topology_aggregator.upsert_node(data, br_id)

        # Try to resolve business name from ML-EID
        node_name = None  # Initialize to track resolved name
        mleids = self.topology_aggregator.extract_mleids(data.get('ipv6_list', []))
        if mleids:
            # Use first ML-EID for business name lookup
            ml_eid = mleids[0]
            node_name = self.resolve_ipv6_to_node_name(ml_eid)

            if node_name:
                # Update mapping for commands/events
                self.update_ipv6_mapping(ml_eid, node_name, br_id)
                if is_border_router:
                    print(f"   ✅ Border Router enriched: {data.get('ext_addr', '')[:8]}... → {node_name} (BR-{br_id})")
                else:
                    print(f"   ✅ Enriched: {data.get('ext_addr', '')[:8]}... → {node_name}")

                # Update name→RLOC16 mapping for badge positioning
                rloc16 = data.get('rloc16')
                if _coap and rloc16:
                    _coap.name_to_rloc16[node_name] = rloc16
                    print(f"   📍 Badge mapping: {node_name} → {rloc16}")

        # Emit to web clients with enriched business name
        if _socketio:
            event_data = {
                'br_id': br_id,
                **data,
                'mleids': mleids,
                'is_br': is_border_router,
                'timestamp': time.time()
            }

            # Add resolved business name if available
            if node_name:
                event_data['business_name'] = node_name
                print(f"   📡 Emitting diagnostic_node with business_name: {node_name}")

            _socketio.emit('diagnostic_node', event_data, namespace='/')

    def handle_diagnostic_link(self, br_id: str, data: dict):
        """
        Process Network Diagnostic router link event from Border Router

        Receives router↔router link metrics from meshdiag routerneighbortable.

        Args:
            br_id: Border Router ID
            data: Link event with keys:
                  - a_rloc16 (str): First router RLOC16
                  - b_rloc16 (str): Second router RLOC16
                  - avg_rssi (int): Average RSSI
                  - last_rssi (int): Last RSSI
                  - lqi (int): Link Quality Indicator
                  - margin_db (int): Link margin in dB
                  - frame_err (float): Frame error rate
                  - msg_err (float): Message error rate
        """
        print(f"📶 Network Diagnostic: Router link from BR {br_id}")
        print(f"   {data.get('a_rloc16')} ↔ {data.get('b_rloc16')} RSSI={data.get('avg_rssi')}")

        # Upsert to topology aggregator
        self.topology_aggregator.upsert_router_link(data)

        # Emit to web clients
        if _socketio:
            _socketio.emit('diagnostic_link', {
                'br_id': br_id,
                **data,
                'timestamp': time.time()
            }, namespace='/')

    def handle_diagnostic_child(self, br_id: str, data: dict):
        """
        Process Network Diagnostic child event from Border Router

        Receives parent↔child link metrics from meshdiag childtable/childip6.

        Args:
            br_id: Border Router ID
            data: Child event with keys:
                  - parent_rloc16 (str): Parent router RLOC16
                  - child_rloc16 (str): Child RLOC16
                  - child_ext_addr (str): Child Extended Address
                  - child_mleids (list): Child ML-EID addresses
                  - partition (int): Partition ID
                  - avg_rssi (int): Average RSSI
                  - last_rssi (int): Last RSSI
                  - lqi (int): Link Quality
                  - mode (str): Child mode (rx-on/mtd/sed)
                  - version (int): Thread version
        """
        print(f"👶 Network Diagnostic: Child from BR {br_id}")
        print(f"   {data.get('parent_rloc16')} → {data.get('child_rloc16')} RSSI={data.get('avg_rssi')}")

        # Upsert to topology aggregator (also creates child node if ext_addr present)
        self.topology_aggregator.upsert_child_link(data, br_id)

        # Try to resolve child business name from ML-EID
        child_mleids = data.get('child_mleids', [])
        if child_mleids:
            ml_eid = child_mleids[0]
            node_name = self.resolve_ipv6_to_node_name(ml_eid)

            if node_name:
                # Update mapping for commands/events
                self.update_ipv6_mapping(ml_eid, node_name, br_id)
                print(f"   ✅ Child enriched: {data.get('child_ext_addr', '')[:8]}... → {node_name}")

        # Emit to web clients
        if _socketio:
            _socketio.emit('diagnostic_child', {
                'br_id': br_id,
                **data,
                'timestamp': time.time()
            }, namespace='/')

    def handle_scan_node_result(self, br_id: str, data: dict):
        """
        Process scan_node result from Border Router

        This handler receives network topology information for a scanned node.
        The results are aggregated to build the complete network topology.

        Args:
            br_id: Border Router ID
            data: Scan result data with target_ipv6, source_ipv6, source_rloc, node_name, request_id, success, network_info
        """
        target_ipv6 = data.get('target_ipv6')  # Address used to scan (may be RLOC)
        source_ipv6 = data.get('source_ipv6')  # Node's ML-EID (from network_info)
        source_rloc = data.get('source_rloc')  # Node's RLOC (constructed by BR)
        node_name_br = data.get('node_name')   # Generic name from BR (e.g., "node_c001")
        request_id = data.get('request_id')
        success = data.get('success', False)
        network_info = data.get('network_info', {})
        error = data.get('error')

        if not success:
            print(f"❌ Scan {node_name_br}: {error}")
            return

        # Use source_ipv6 (ML-EID) for enrichment - this is the stable address
        print(f"   📍 Node ML-EID: {source_ipv6}")
        if source_rloc:
            print(f"   📍 Node RLOC: {source_rloc}")

        # Enrich with business name from adresses.json using source_ipv6 (ML-EID)
        node_name_business = self.resolve_ipv6_to_node_name(source_ipv6)

        if node_name_business:
            # Found in config - use business name
            print(f"✅ Enriched: {node_name_br} → {node_name_business} (from adresses.json)")
            node_name = node_name_business
            # Update mapping with ML-EID
            self.update_ipv6_mapping(source_ipv6, node_name_business, br_id)
        else:
            # Not in config - keep BR's generic name
            print(f"⚠️ Not in config: {node_name_br} ML-EID={source_ipv6} - keeping generic name")
            node_name = node_name_br

        # Log success concisely
        role = network_info.get('role', 'unknown')
        rloc = network_info.get('rloc16', '?')
        children = network_info.get('children', [])
        neighbors = network_info.get('neighbors', [])
        print(f"✅ Scan {node_name}: {role} ({rloc}) - {len(children)} children, {len(neighbors)} neighbors")

        # Discover and scan children/neighbors using LINK-LOCAL addresses
        # Link-local addresses (fe80::<IID>) are always reachable for direct neighbors (1-hop)
        # This is more reliable than RLOC which may not be routable yet
        discovered_nodes = []

        print(f"   🔍 Starting recursive neighbor discovery via link-local addresses")

        # Process children (if this node is a router/leader)
        for child in children:
            rloc16 = child.get('rloc16', '')
            ext_addr = child.get('ext_addr', '')

            if ext_addr:
                # Calculate link-local address from Extended Address
                link_local = self.calculate_linklocal_from_extaddr(ext_addr)

                if link_local:
                    discovered_nodes.append({
                        'ipv6': link_local,
                        'rloc16': rloc16,
                        'ext_addr': ext_addr,
                        'type': 'child',
                        'discovery_method': 'link-local'
                    })
                    print(f"   🔍 Discovered child: ExtAddr={ext_addr} → Link-Local {link_local}")
                else:
                    print(f"   ⚠️ Failed to calculate link-local for child ExtAddr={ext_addr}")

        # Process neighbors (other routers)
        for neighbor in neighbors:
            # Skip if neighbor is marked as a child (already processed above)
            if neighbor.get('is_child', False):
                continue

            rloc16 = neighbor.get('rloc16', '')
            ext_addr = neighbor.get('ext_addr', '')

            if ext_addr:
                # Calculate link-local address from Extended Address
                link_local = self.calculate_linklocal_from_extaddr(ext_addr)

                if link_local:
                    discovered_nodes.append({
                        'ipv6': link_local,
                        'rloc16': rloc16,
                        'ext_addr': ext_addr,
                        'type': 'neighbor',
                        'discovery_method': 'link-local'
                    })
                    print(f"   🔍 Discovered neighbor: ExtAddr={ext_addr} → Link-Local {link_local}")
                else:
                    print(f"   ⚠️ Failed to calculate link-local for neighbor ExtAddr={ext_addr}")

        # Initiate scans for discovered nodes using LINK-LOCAL addresses
        if discovered_nodes:
            print(f"   📡 Initiating scans for {len(discovered_nodes)} discovered nodes via link-local...")
            import uuid
            for node_info in discovered_nodes:
                scan_request_id = str(uuid.uuid4())
                # Use generic name for now (will be enriched after scan with ml_eid)
                node_name_temp = f"node_{node_info['rloc16'].replace('0x', '')}"

                success = self.send_scan_node_command(
                    br_id=br_id,
                    target_ipv6=node_info['ipv6'],  # Use link-local address
                    node_name=node_name_temp,
                    request_id=scan_request_id
                )
                if success:
                    print(f"      ✅ Scan queued: {node_name_temp} via {node_info['discovery_method']} {node_info['ipv6']} ({node_info['type']})")
                else:
                    print(f"      ❌ Failed to queue scan: {node_name_temp}")

        # Emit to web clients (with enriched business name)
        if _socketio:
            _socketio.emit('scan_node_result', {
                'br_id': br_id,
                'node_name': node_name,  # Business name (n01, n02) or generic (node_c001)
                'node_name_br': node_name_br,  # Original BR name for reference
                'target_ipv6': target_ipv6,
                'request_id': request_id,
                'success': success,
                'network_info': network_info,
                'timestamp': time.time()
            }, namespace='/')

    def send_command(self, br_id: str, command_data: dict) -> bool:
        """
        Send command to Border Router

        Args:
            br_id: Border Router ID
            command_data: Command to send (type, target_node, request_id, payload)

        Returns:
            True if command was sent successfully
        """
        if br_id not in self.active_connections:
            print(f"❌ Cannot send command to BR {br_id}: not connected")
            return False

        ws = self.active_connections[br_id]

        try:
            # Add 'type' field if not present
            if 'type' not in command_data:
                command_data['type'] = 'command'

            # Send JSON message
            message = json.dumps(command_data)
            ws.send(message)

            print(f"📤 Command sent to BR {br_id}: {command_data.get('command')}")
            return True

        except Exception as e:
            print(f"❌ Error sending command to BR {br_id}: {e}")
            return False

    def send_command_to_node(self, node_name: str, command_type: str, payload: str) -> bool:
        """
        Send command to node via Border Router using IPv6 routing

        Args:
            node_name: Node name (e.g., "n01")
            command_type: Command type ("audio", "led")
            payload: Command payload (e.g., "play:341", "red:on")

        Returns:
            True if command was sent successfully
        """
        # Resolve node name to IPv6
        ipv6 = self.resolve_node_name_to_ipv6(node_name)
        if not ipv6:
            print(f"❌ Cannot resolve node {node_name} to IPv6")
            return False

        # Find which BR manages this node
        br_id = self.get_br_for_node(node_name)
        if not br_id:
            print(f"❌ No BR mapping for {node_name} ({ipv6})")
            return False

        # Check if BR is connected
        if br_id not in self.active_connections:
            print(f"❌ BR {br_id} not connected")
            return False

        # Build command message for BR
        import uuid
        command_msg = {
            'command': 'send_coap',
            'target_ipv6': ipv6,
            'command_type': command_type,
            'payload': payload,
            'request_id': str(uuid.uuid4())
        }

        # Send to BR
        try:
            ws = self.active_connections[br_id]
            message = json.dumps(command_msg)
            ws.send(message)
            print(f"📤 Command sent to {node_name} ({ipv6}) via {br_id}: {command_type} - {payload}")
            return True
        except Exception as e:
            print(f"❌ Failed to send command: {e}")
            return False

    def send_scan_node_command(self, br_id: str, target_ipv6: str, node_name: str, request_id: str) -> bool:
        """
        Send scan_node command to Border Router for network topology discovery

        This method enqueues a command message to be sent by the BR handler thread.
        The BR acts as a transparent proxy: WebSocket ← Python → BR → CoAP → Node

        Args:
            br_id: Border Router ID
            target_ipv6: Target node IPv6 address
            node_name: Node name (for logging)
            request_id: Unique request identifier

        Returns:
            True if command was enqueued successfully
        """
        # Check if BR is connected
        if br_id not in self.active_connections:
            print(f"❌ BR {br_id} not connected (available: {list(self.active_connections.keys())})")
            return False

        # Check if queue exists
        if br_id not in self.message_queues:
            print(f"❌ No message queue for BR {br_id}")
            return False

        # Build scan_node command message
        # IMPORTANT: Use 'command' field, not 'type', to match BR handler
        scan_msg = {
            'command': 'scan_node',
            'target_ipv6': target_ipv6,
            'node_name': node_name,
            'request_id': request_id
        }

        # Enqueue message for thread-safe sending
        try:
            message = json.dumps(scan_msg)
            msg_queue = self.message_queues[br_id]
            msg_queue.put(message)
            print(f"🔍 Scan enqueued: {node_name} → {target_ipv6} via BR {br_id}")
            return True
        except Exception as e:
            print(f"❌ Failed to enqueue scan for {node_name}: {e}")
            import traceback
            print(traceback.format_exc())
            return False

    def send_scan_all_command(self, br_id: str, request_id: str) -> bool:
        """
        Send scan_all_nodes command to Border Router for dynamic network discovery

        This method asks the BR to discover all nodes in its Thread network and scan each one.
        The BR will iterate through its children and neighbors, get their IPv6 addresses,
        and scan each node automatically.

        Args:
            br_id: Border Router ID
            request_id: Unique request identifier for tracking

        Returns:
            True if command was enqueued successfully
        """
        # Check if BR is connected
        if br_id not in self.active_connections:
            print(f"❌ BR {br_id} not connected (available: {list(self.active_connections.keys())})")
            return False

        # Check if queue exists
        if br_id not in self.message_queues:
            print(f"❌ No message queue for BR {br_id}")
            return False

        # Build scan_all_nodes command message
        scan_msg = {
            'command': 'scan_all_nodes',
            'request_id': request_id
        }

        # Enqueue message for thread-safe sending
        try:
            message = json.dumps(scan_msg)
            msg_queue = self.message_queues[br_id]
            msg_queue.put(message)
            print(f"🔍 Scan all nodes enqueued for BR {br_id} (request_id: {request_id})")
            return True
        except Exception as e:
            print(f"❌ Failed to enqueue scan_all for BR {br_id}: {e}")
            import traceback
            print(traceback.format_exc())
            return False

    def is_br_connected(self, br_id: str) -> bool:
        """
        Check if Border Router is connected

        Args:
            br_id: Border Router ID

        Returns:
            True if BR has active WebSocket connection
        """
        return br_id in self.active_connections

    def get_active_connections_count(self) -> int:
        """
        Get count of active WebSocket connections

        Returns:
            Number of active connections
        """
        return len(self.active_connections)

    def get_active_br_ids(self) -> list:
        """
        Get list of connected Border Router IDs

        Returns:
            List of br_id strings
        """
        return list(self.active_connections.keys())

    def get_active_nodes(self, timeout_seconds: int = 60) -> list:
        """
        Get list of active nodes based on last_seen timestamp

        Args:
            timeout_seconds: Maximum time since last event (default: 60s)

        Returns:
            List of dicts with node info: [{'name': str, 'ipv6': str, 'br_id': str, 'last_seen': float}]
        """
        current_time = time.time()
        active_nodes = []

        for ipv6, mapping in self.ipv6_mapping.items():
            time_since_last_seen = current_time - mapping['last_seen']

            if time_since_last_seen <= timeout_seconds:
                active_nodes.append({
                    'name': mapping['node_name'],
                    'ipv6': ipv6,
                    'br_id': mapping['br_id'],
                    'last_seen': mapping['last_seen'],
                    'seconds_ago': int(time_since_last_seen)
                })

        print(f"🔍 Active nodes: {len(active_nodes)}/{len(self.ipv6_mapping)} (timeout: {timeout_seconds}s)")
        return active_nodes

    def get_network_topology(self) -> dict:
        """
        Get aggregated network topology from Network Diagnostic events

        Returns:
            Dictionary with nodes, router_links, child_links, and stats
            Enriched with business names from adresses.json where available
        """
        topology = self.topology_aggregator.get_topology()

        # Enrich nodes with business names
        for node in topology['nodes']:
            if node['mleids']:
                # Try to resolve business name from first ML-EID
                ml_eid = node['mleids'][0]
                business_name = self.resolve_ipv6_to_node_name(ml_eid)
                if business_name:
                    node['business_name'] = business_name

        return topology
