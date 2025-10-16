"""
Native WebSocket Handler for Border Router Communication

This module provides a native WebSocket handler (without Socket.IO)
for ESP32 Border Router clients using esp_websocket_client.

The ESP32 client sends/receives plain JSON messages, so we cannot use
Flask-SocketIO which wraps messages in Socket.IO protocol.
"""

import json
import time
import logging
import queue
import threading
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

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

    def __init__(self, border_router_manager, br_auth_enabled=True):
        """
        Initialize the native WebSocket handler

        Args:
            border_router_manager: BorderRouterManager instance
            br_auth_enabled: Enable token authentication
        """
        self.border_router_manager = border_router_manager
        self.br_auth_enabled = br_auth_enabled
        self.active_connections: Dict[str, any] = {}  # {br_id: ws_connection}
        self.message_queues: Dict[str, queue.Queue] = {}  # {br_id: Queue()} for thread-safe message sending
        self.tx_threads: Dict[str, threading.Thread] = {}  # {br_id: Thread} dedicated TX threads
        self.ipv6_mapping: Dict[str, Dict] = {}  # {ipv6: {'node_name': str, 'br_id': str, 'last_seen': float}}
        logger.info("🔧 Native WebSocket handler initialized (TX thread pattern)")

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
            logger.debug(f"🔓 Authentication disabled, accepting BR {br_id}")
            return True

        # Import here to avoid circular dependency
        from lib.br_auth import verify_br_token

        if verify_br_token(br_id, auth_token):
            logger.info(f"✅ BR {br_id} authenticated successfully")
            return True
        else:
            logger.error(f"❌ BR {br_id} authentication failed")
            return False

    def resolve_ipv6_to_node_name(self, ipv6: str) -> Optional[str]:
        """
        Resolve IPv6 address to node name using config/adresses.json

        Args:
            ipv6: IPv6 address (e.g., "fd78:8e78:3bfe:1:1234:5678:90ab:cdef")

        Returns:
            node_name (e.g., "n01") or None if not found
        """
        logger.debug(f"🔍 Resolving IPv6 → node_name: {ipv6}")

        try:
            import json
            with open('config/adresses.json', 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Search for matching IPv6 (case-insensitive comparison)
            ipv6_lower = ipv6.lower()
            total_nodes = len(config.get('nodes', {}))
            logger.debug(f"   📁 Loaded {total_nodes} nodes from config")

            for node_name, node_data in config.get('nodes', {}).items():
                node_ipv6 = node_data.get('address', '').lower()
                if node_ipv6 == ipv6_lower:
                    logger.info(f"   ✅ MATCH: {ipv6} → {node_name}")
                    return node_name

            # Not found in config
            logger.warning(f"   ❌ NO MATCH: IPv6 {ipv6} not found in adresses.json ({total_nodes} nodes checked)")
            return None

        except FileNotFoundError:
            logger.error("❌ config/adresses.json not found")
            return None
        except Exception as e:
            logger.error(f"❌ Error resolving IPv6: {e}")
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

            logger.warning(f"⚠️ Node {node_name} not found in adresses.json")
            return None

        except Exception as e:
            logger.error(f"❌ Error resolving node name: {e}")
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
        logger.debug(f"📍 Mapping updated: {ipv6} → {node_name} → {br_id}")

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

        logger.warning(f"⚠️ No BR mapping found for node {node_name}")
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
                logger.debug(f"📤 Queue: Sent message to BR {br_id} ({msg_queue.qsize()} remaining)")
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"❌ Error sending queued message to BR {br_id}: {e}")

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
        logger.info(f"📤 TX thread started for BR {br_id}")

        try:
            while True:
                # Block until message available (or None sentinel for shutdown)
                message = self.message_queues[br_id].get()

                # Check for shutdown sentinel
                if message is None:
                    logger.info(f"🛑 TX thread received shutdown signal for BR {br_id}")
                    break

                # Send message to Border Router
                try:
                    ws.send(message)
                    logger.info(f"📤 TX→BR {br_id}: Sent {len(message)} bytes")
                    logger.debug(f"   Content: {message[:200]}...")  # Log first 200 chars
                except Exception as e:
                    logger.error(f"❌ TX thread failed to send to BR {br_id}: {e}")
                    # Don't break - try to send remaining messages
                    # The RX thread will handle connection cleanup

        except Exception as e:
            logger.error(f"❌ TX thread crashed for BR {br_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info(f"📤 TX thread stopped for BR {br_id}")

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

        logger.info(f"📡 New WebSocket connection from BR {br_id}")

        # Validate required parameters
        if not br_id or not auth_token:
            logger.error("❌ Missing br_id or auth_token in connection URL")
            error_msg = json.dumps({
                'type': 'error',
                'message': 'Missing br_id or auth_token'
            })
            ws.send(error_msg)
            return

        # Authenticate
        if not self.authenticate_br(br_id, auth_token):
            logger.error(f"❌ Authentication failed for BR {br_id}")
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
            logger.error(f"❌ Failed to register BR {br_id}")
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
        logger.info(f"✅ TX thread started for BR {br_id}")

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

        logger.info(f"✅ Border Router {br_id} connected and registered")

        # Enter message processing loop (RX only - TX is handled by dedicated thread)
        try:
            while True:
                # Receive incoming message (blocking)
                message = ws.receive()

                if message is None:
                    # Connection closed
                    logger.info(f"🔌 BR {br_id} closed connection")
                    break

                # Process received message
                self.handle_message(br_id, message, ws)

        except Exception as e:
            logger.error(f"❌ Error in WebSocket RX loop for BR {br_id}: {e}")

        finally:
            # Signal TX thread to shutdown (send None sentinel)
            if br_id in self.message_queues:
                logger.info(f"🛑 Signaling TX thread shutdown for BR {br_id}")
                self.message_queues[br_id].put(None)

            # Wait for TX thread to finish (with timeout)
            if br_id in self.tx_threads:
                tx_thread = self.tx_threads[br_id]
                tx_thread.join(timeout=2.0)
                if tx_thread.is_alive():
                    logger.warning(f"⚠️ TX thread for BR {br_id} did not stop in time")
                else:
                    logger.info(f"✅ TX thread for BR {br_id} stopped cleanly")
                del self.tx_threads[br_id]

            # Cleanup: unregister BR and remove from active connections
            self.border_router_manager.unregister_br(br_id)
            if br_id in self.active_connections:
                del self.active_connections[br_id]
            if br_id in self.message_queues:
                del self.message_queues[br_id]
            logger.warning(f"⚠️ Border Router {br_id} disconnected")

    def handle_message(self, br_id: str, message: str, ws):
        """
        Process incoming message from Border Router

        Args:
            br_id: Border Router ID
            message: JSON message string
            ws: WebSocket connection object
        """
        # 📥 LOG: Trame RAW reçue du BR
        logger.error(f"📥 PYTHON←BR: Received WebSocket message from BR {br_id}:")
        logger.error(f"   RAW JSON ({len(message)} bytes): {message}")

        try:
            # Parse JSON
            data = json.loads(message)
            msg_type = data.get('type')

            logger.error(f"   ✅ JSON parsed successfully")
            logger.error(f"   Message type: {msg_type}")

            if not msg_type:
                logger.error(f"❌ Message from BR {br_id} missing 'type' field")
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

            else:
                logger.warning(f"⚠️ Unknown message type from BR {br_id}: {msg_type}")

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON from BR {br_id}: {e}")
            logger.error(f"📩 Trame complète reçue: {message}")
        except Exception as e:
            logger.error(f"❌ Error processing message from BR {br_id}: {e}")

    def handle_heartbeat(self, br_id: str, data: dict, ws):
        """
        Process heartbeat message from Border Router

        Args:
            br_id: Border Router ID
            data: Heartbeat data (timestamp, nodes_count, status)
            ws: WebSocket connection
        """
        nodes_count = data.get('nodes_count', 0)
        timestamp = data.get('timestamp', 0)
        status = data.get('status', 'unknown')

        # 🔍 Log detailed heartbeat info
        logger.info(f"💓 HEARTBEAT from BR {br_id}:")
        logger.info(f"   📊 Nodes count: {nodes_count}")
        logger.info(f"   ⏱️  Timestamp: {timestamp}s")
        logger.info(f"   ✅ Status: {status}")

        # Get current mapping for this BR
        br_nodes = [ipv6 for ipv6, mapping in self.ipv6_mapping.items() if mapping['br_id'] == br_id]
        logger.info(f"   🗺️  Known nodes in mapping: {len(br_nodes)}")
        for ipv6 in br_nodes:
            mapping = self.ipv6_mapping[ipv6]
            logger.info(f"      - {mapping['node_name']} @ {ipv6}")

        # Update heartbeat in manager
        self.border_router_manager.update_heartbeat(br_id, nodes_count)

        # Send heartbeat acknowledgment
        ack_msg = json.dumps({
            'type': 'heartbeat_ack',
            'timestamp': time.time(),
            'server_status': 'ok'
        })
        ws.send(ack_msg)

    def handle_node_event_with_ipv6(self, br_id: str, data: dict):
        """
        Process node event with IPv6 source from Border Router

        Args:
            br_id: Border Router ID
            data: Event data with source_ipv6, event_type, payload
        """
        # 📦 LOG: Extraction des champs
        logger.error(f"📦 PYTHON: Processing node_event from BR {br_id}")
        logger.error(f"   Full event data: {json.dumps(data, indent=2)}")

        source_ipv6 = data.get('source_ipv6')
        source_rloc = data.get('source_rloc')  # RLOC optionnel pour référence
        event_type = data.get('event_type')
        payload = data.get('payload', {})

        logger.error(f"   🌐 Extracted fields:")
        logger.error(f"      source_ipv6: {source_ipv6}")
        if source_rloc:
            logger.error(f"      source_rloc: {source_rloc} (for reference)")
        logger.error(f"      event_type: {event_type}")
        logger.error(f"      payload: {json.dumps(payload)}")

        if not source_ipv6 or not event_type:
            logger.error(f"❌ Invalid node_event from BR {br_id}: missing source_ipv6 or event_type")
            return

        # 🆕 Détecter si c'est un NOUVEAU node (première fois qu'on le voit)
        is_new_node = source_ipv6 not in self.ipv6_mapping

        # Resolve IPv6 to node name
        logger.error(f"   🔍 Resolving IPv6 to node name...")
        node_name = self.resolve_ipv6_to_node_name(source_ipv6)
        if not node_name:
            logger.warning(f"⚠️ Unknown node IPv6: {source_ipv6} (event: {event_type})")
            # Create temporary name for unknown nodes
            node_name = f"unknown-{source_ipv6[-8:]}"
            logger.error(f"   🏷️  Generated temporary name: {node_name}")
        else:
            logger.error(f"   ✅ Resolved to known node: {node_name}")

        # Update IPv6 mapping
        self.update_ipv6_mapping(source_ipv6, node_name, br_id)
        logger.error(f"   📍 Mapping updated: {source_ipv6} → {node_name} → {br_id}")

        # 🆕 Émettre événement Socket.IO si c'est un nouveau node
        if is_new_node and _socketio:
            logger.error(f"   🎉 NEW NODE DETECTED! Emitting 'node_update' event to web clients")
            _socketio.emit('node_update', {
                'node_name': node_name,
                'ipv6': source_ipv6,
                'br_id': br_id,
                'timestamp': time.time()
            }, namespace='/')
            logger.info(f"✨ New active node: {node_name} ({source_ipv6}) via {br_id}")

            # 🔄 Déclencher un scan CoAP en arrière-plan pour enrichir la topologie
            if _topology_refresh_callback:
                import threading
                logger.info(f"🔍 Déclenchement scan CoAP pour enrichir topologie...")
                thread = threading.Thread(target=_topology_refresh_callback)
                thread.daemon = True
                thread.start()
                logger.info(f"✅ Scan CoAP démarré en arrière-plan")

        # Increment event counter
        self.border_router_manager.increment_event_counter(br_id)

        # 🔍 DEBUG: Vérifier si coap_server existe
        logger.error(f"   🔍 DEBUG: event_type={event_type}, coap_server={'EXISTS' if _coap else 'IS NONE'}")

        # Route to appropriate handler based on event type
        if event_type == 'ble_beacon' and _coap:
            logger.error(f"   ✅ Calling coap_server.handle_ble_event_from_br() with payload: {payload}")
            _coap.handle_ble_event_from_br({
                'node': node_name,
                'br_id': br_id,
                'payload': payload  # Passer le payload complet
            })
        elif event_type == 'ble_beacon' and not _coap:
            logger.error(f"   ❌ CANNOT call handler: coap_server is None!")

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

        logger.info(f"📨 Node event from BR {br_id}: {node_name} ({source_ipv6}) - {event_type}")

    def handle_node_discovered(self, br_id: str, data: dict):
        """
        Handle node discovery announcement from Border Router

        Args:
            br_id: Border Router ID
            data: Discovery data with source_ipv6
        """
        source_ipv6 = data.get('source_ipv6')

        logger.info(f"🆔 NODE DISCOVERED from BR {br_id}:")
        logger.info(f"   🌐 Source IPv6: {source_ipv6}")

        if not source_ipv6:
            logger.error(f"❌ Invalid node_discovered from BR {br_id}: missing source_ipv6")
            return

        # Resolve IPv6 to node name
        node_name = self.resolve_ipv6_to_node_name(source_ipv6)
        if not node_name:
            logger.info(f"   ⚠️  Unknown node (not in config)")
            node_name = f"unknown-{source_ipv6[-8:]}"
            logger.info(f"   🏷️  Generated temporary name: {node_name}")
        else:
            logger.info(f"   ✅ Known node: {node_name}")

        # Update mapping
        self.update_ipv6_mapping(source_ipv6, node_name, br_id)
        logger.info(f"   📍 Mapping registered: {node_name} via BR {br_id}")

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
            logger.error(f"❌ Invalid node_event from BR {br_id}: missing node or event_type")
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

        logger.info(f"📨 Node event from BR {br_id}: {node_name} - {event_type}")

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
            logger.error(f"❌ Command response from BR {br_id} missing request_id")
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

        logger.info(f"📨 Command response from BR {br_id}: {request_id} - {status}")

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

        logger.info(f"🗺️ Topology update from BR {br_id}: {len(node_names)} nodes")

        # Notify web clients
        if _socketio:
            _socketio.emit('topology_update', {
                'br_id': br_id,
                'nodes_count': len(node_names),
                'timestamp': time.time()
            }, namespace='/')

    def handle_scan_node_result(self, br_id: str, data: dict):
        """
        Process scan_node result from Border Router

        This handler receives network topology information for a scanned node.
        The results are aggregated to build the complete network topology.

        Args:
            br_id: Border Router ID
            data: Scan result data with target_ipv6, node_name, request_id, success, network_info
        """
        target_ipv6 = data.get('target_ipv6')
        node_name = data.get('node_name')
        request_id = data.get('request_id')
        success = data.get('success', False)
        network_info = data.get('network_info', {})
        error = data.get('error')

        logger.info(f"📊 SCAN RESULT from BR {br_id}:")
        logger.info(f"   Node: {node_name} ({target_ipv6})")
        logger.info(f"   Request ID: {request_id}")
        logger.info(f"   Success: {success}")

        if not success:
            logger.error(f"   ❌ Scan failed: {error}")
            return

        # Log network info
        logger.info(f"   Network Info:")
        logger.info(f"      RLOC16: {network_info.get('rloc16')}")
        logger.info(f"      Role: {network_info.get('role')}")
        logger.info(f"      Parent: {network_info.get('parent')}")
        logger.info(f"      Neighbors: {len(network_info.get('neighbors', []))}")

        # TODO: Aggregate results and build topology
        # For now, just emit to web clients
        if _socketio:
            _socketio.emit('scan_node_result', {
                'br_id': br_id,
                'node_name': node_name,
                'target_ipv6': target_ipv6,
                'request_id': request_id,
                'success': success,
                'network_info': network_info,
                'timestamp': time.time()
            }, namespace='/')

        logger.info(f"✅ Scan result processed for {node_name}")

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
            logger.error(f"❌ Cannot send command to BR {br_id}: not connected")
            return False

        ws = self.active_connections[br_id]

        try:
            # Add 'type' field if not present
            if 'type' not in command_data:
                command_data['type'] = 'command'

            # Send JSON message
            message = json.dumps(command_data)
            ws.send(message)

            logger.debug(f"📤 Command sent to BR {br_id}: {command_data.get('command')}")
            return True

        except Exception as e:
            logger.error(f"❌ Error sending command to BR {br_id}: {e}")
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
            logger.error(f"❌ Cannot resolve node {node_name} to IPv6")
            return False

        # Find which BR manages this node
        br_id = self.get_br_for_node(node_name)
        if not br_id:
            logger.error(f"❌ No BR mapping for {node_name} ({ipv6})")
            return False

        # Check if BR is connected
        if br_id not in self.active_connections:
            logger.error(f"❌ BR {br_id} not connected")
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
            logger.info(f"📤 Command sent to {node_name} ({ipv6}) via {br_id}: {command_type} - {payload}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send command: {e}")
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
            logger.error(f"❌ BR {br_id} not connected (available: {list(self.active_connections.keys())})")
            return False

        # Check if queue exists
        if br_id not in self.message_queues:
            logger.error(f"❌ No message queue for BR {br_id}")
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
            logger.info(f"🔍 Scan enqueued: {node_name} → {target_ipv6} via BR {br_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to enqueue scan for {node_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
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

        logger.debug(f"🔍 Active nodes: {len(active_nodes)}/{len(self.ipv6_mapping)} (timeout: {timeout_seconds}s)")
        return active_nodes
