# Architecture du Scan Réseau OpenThread

**Documentation complète du système de scan orchestré pour réseau mesh OpenThread**

Date: 2025-10-16
Auteur: Documentation technique générée suite à implémentation

---

## Table des Matières

1. [Vue d'Ensemble](#1-vue-densemble)
2. [Architecture Dual WebSocket](#2-architecture-dual-websocket)
3. [Composants Détaillés](#3-composants-détaillés)
4. [Flux de Scan Complet](#4-flux-de-scan-complet)
5. [Protocoles de Communication](#5-protocoles-de-communication)
6. [Bugs Rencontrés et Solutions](#6-bugs-rencontrés-et-solutions)
7. [Points d'Attention](#7-points-dattention-pour-développement-futur)
8. [Code Snippets Critiques](#8-code-snippets-critiques)
9. [Questions pour ChatGPT](#9-questions-pour-chatgpt)

---

## 1. Vue d'Ensemble

### 1.1 Objectif du Système

Permettre le scan automatique de la topologie d'un réseau mesh OpenThread composé de:
- **Border Routers (BR)**: Passerelles entre WiFi et réseau Thread
- **Nodes**: Dispositifs ESP32-C6 formant le mesh Thread

Le système construit une carte complète du réseau en interrogeant chaque node pour obtenir:
- Son rôle (Leader, Router, End Device)
- Son parent
- Ses voisins
- Son RLOC16 et autres métadonnées

### 1.2 Architecture Globale

```
┌─────────────────────────────────────────────────────────────────┐
│                      Web Browser (Client)                        │
│                  http://localhost:5001/network-map               │
│                                                                   │
│  [Refresh Button] ──► Socket.IO emit('trigger_scan')            │
└───────────────────────────────┬─────────────────────────────────┘
                                │ Socket.IO
                                │ (Flask-SocketIO)
┌───────────────────────────────▼─────────────────────────────────┐
│                     Python Flask Server                          │
│                        (port 5001)                               │
│                                                                   │
│  ┌─────────────────────┐    ┌────────────────────────────────┐ │
│  │   Socket.IO         │    │   Native WebSocket Handler     │ │
│  │   (Web Clients)     │    │   (Border Routers)             │ │
│  │   Flask-SocketIO    │    │   Flask-Sock                   │ │
│  └─────────────────────┘    └────────────────────────────────┘ │
│             │                            │                       │
│             │                            │ Native WS             │
│  ┌──────────▼────────────────────────────▼──────────────────┐  │
│  │         scan_all_nodes_via_brs()                         │  │
│  │         (Orchestrateur central)                          │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │ Native WebSocket
                                │ ws://server:5001/ws/br
┌───────────────────────────────▼─────────────────────────────────┐
│                  Border Router (ESP32-C6)                        │
│                   cloud_websocket_client.c                       │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  WebSocket Client (esp_websocket_client)                   │ │
│  │  - Authentification                                        │ │
│  │  - Réception commandes {'command':'scan_node'}            │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│  ┌────────────────────▼───────────────────────────────────────┐ │
│  │  CoAP Proxy (coap_proxy.c)                                │ │
│  │  - coap_proxy_scan_node()                                 │ │
│  │  - Envoie GET /network-info                               │ │
│  └────────────────────┬───────────────────────────────────────┘ │
└────────────────────────┼────────────────────────────────────────┘
                         │ CoAP/UDP
                         │ (IPv6 Thread network)
┌────────────────────────▼────────────────────────────────────────┐
│                    Node (ESP32-C6)                               │
│                 network_info_service.c                           │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  CoAP Server                                               │ │
│  │  GET /network-info                                         │ │
│  │  → JSON response:                                          │ │
│  │    {role, rloc16, parent, neighbors[], children[]}        │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Dual WebSocket

### 2.1 Pourquoi Deux Systèmes WebSocket Distincts?

**Point critique**: Le serveur Python utilise **DEUX systèmes WebSocket différents** qui coexistent:

#### System 1: Socket.IO (Flask-SocketIO)
- **Usage**: Communication avec navigateurs web
- **Protocol**: Socket.IO (encapsulation sur WebSocket)
- **Port**: 5001
- **Namespace**: `/` (racine)
- **Features**:
  - Auto-reconnexion
  - Fallback HTTP polling
  - Events nommés
  - Compatible avec tous les navigateurs

#### System 2: Native WebSocket (Flask-Sock)
- **Usage**: Communication avec Border Routers ESP32
- **Protocol**: WebSocket natif (RFC 6455)
- **Endpoint**: `/ws/br`
- **Library ESP32**: `esp_websocket_client`
- **Format**: JSON pur, pas d'encapsulation Socket.IO

### 2.2 Pourquoi Cette Séparation?

```python
# ESP32 ne peut PAS parler Socket.IO
# esp_websocket_client envoie du JSON pur: {"type":"heartbeat",...}
# Socket.IO encapsule: 42["heartbeat",{...}]
# → Incompatible!
```

**Tentative initiale échouée**: Utiliser Socket.IO pour les BR
- Socket.IO ajoute des préfixes ("42[..."]") que l'ESP32 ne comprend pas
- L'ESP32 ne peut pas parser ces messages encapsulés
- Solution: Créer un endpoint WebSocket natif séparé

### 2.3 Coexistence dans Flask

**server.py:**
```python
from flask import Flask
from flask_socketio import SocketIO  # Pour web
from flask_sock import Sock           # Pour BR

app = Flask(__name__)
socketio = SocketIO(app)  # System 1: Socket.IO
sock = Sock(app)           # System 2: Native WebSocket

# Socket.IO endpoint (automatic)
@socketio.on('connect')
def handle_web_connect():
    # Clients web se connectent automatiquement

# Native WebSocket endpoint (manuel)
@sock.route('/ws/br')
def border_router_websocket(ws):
    native_ws_handler.handle_connection(ws, request.environ)
```

**IMPORTANT**: Les deux systèmes sont **complètement indépendants**:
- Socket.IO ne voit pas les messages des BR
- Native WS ne voit pas les messages web
- Ils partagent seulement le même port Flask (5001)

---

## 3. Composants Détaillés

### 3.1 Serveur Python

#### server.py
**Responsabilités:**
- Initialiser Flask, Socket.IO, Flask-Sock
- Définir endpoint `/api/trigger_scan` (HTTP REST)
- Héberger `scan_all_nodes_via_brs()` (orchestrateur principal)
- Router les events Socket.IO depuis le web

**Code clé:**
```python
# server.py:48
logger = logging.getLogger(__name__)  # Bug fix #3

@app.route('/api/trigger_scan', methods=['POST'])
def trigger_network_scan():
    """Endpoint appelé par le bouton Refresh"""
    result = scan_all_nodes_via_brs()
    return jsonify(result)

def scan_all_nodes_via_brs():
    """Orchestrateur central du scan"""
    # 1. Charger liste nodes depuis config/adresses.json
    nodes_to_scan = load_nodes_from_config()

    # 2. Pour chaque node, trouver son BR
    for node_name, ipv6 in nodes_to_scan.items():
        br_id = border_router_manager.get_br_for_node(node_name)

        # 3. Envoyer commande scan_node au BR
        request_id = str(uuid.uuid4())
        native_ws_handler.send_scan_node_command(
            br_id=br_id,
            target_ipv6=ipv6,
            node_name=node_name,
            request_id=request_id
        )

    # 4. Attendre réponses asynchrones (TODO: timeout, aggregation)
```

#### lib/native_websocket_handler.py
**Responsabilités:**
- Gérer connexions WebSocket natives des BR
- Authentifier les BR
- **Envoyer commandes aux BR de manière thread-safe** (via queue)
- Recevoir réponses des BR
- Router les events vers Socket.IO pour le web

**Architecture thread-safe:**
```python
class NativeWebSocketHandler:
    def __init__(self):
        self.active_connections = {}  # {br_id: ws_connection}
        self.message_queues = {}      # {br_id: Queue()} ← Solution Bug #2

    def handle_connection(self, ws, environ):
        """Thread dédié pour chaque BR (bloquant sur ws.receive())"""
        br_id = parse_br_id(environ)

        # Créer queue pour ce BR
        self.message_queues[br_id] = queue.Queue()

        while True:
            # Envoyer messages en attente dans la queue
            self._process_outgoing_queue(br_id, ws)

            # Recevoir message du BR (bloquant)
            message = ws.receive()
            self.handle_message(br_id, message, ws)

    def send_scan_node_command(self, br_id, target_ipv6, node_name, request_id):
        """Appelé depuis thread HTTP → Enqueue message"""
        scan_msg = {
            'command': 'scan_node',  # Fix Bug #1
            'target_ipv6': target_ipv6,
            'node_name': node_name,
            'request_id': request_id
        }

        # Thread-safe: ajouter à la queue
        self.message_queues[br_id].put(json.dumps(scan_msg))

    def _process_outgoing_queue(self, br_id, ws):
        """Appelé depuis thread WebSocket handler"""
        queue = self.message_queues[br_id]
        while not queue.empty():
            message = queue.get_nowait()
            ws.send(message)  # Safe: même thread que ws.receive()
```

#### lib/ot_network_mapper.py
**Responsabilités:**
- Agréger les réponses de scan
- Construire l'objet NetworkTopology
- Calculer les relations parent/enfant

*(Non modifié dans cette implémentation - utilise l'ancien système CoAP direct)*

---

### 3.2 Border Router (ESP32-C6)

#### cloud_websocket_client.c
**Responsabilités:**
- Se connecter au serveur Python via WebSocket natif
- Envoyer heartbeats périodiques
- **Recevoir commandes du serveur** (scan_node, send_coap, etc.)
- Router les commandes vers coap_proxy

**Code clé:**
```c
// cloud_websocket_client.c:266
static void handle_server_command(const char *data, int len) {
    cJSON *json = cJSON_ParseWithLength(data, len);
    const char *command = cJSON_GetStringValue(
        cJSON_GetObjectItem(json, "command")  // ← Cherche "command"
    );

    // Handle "scan_node" command
    if (strcmp(command, "scan_node") == 0) {
        const char *target_ipv6 = cJSON_GetStringValue(
            cJSON_GetObjectItem(json, "target_ipv6")
        );
        const char *node_name = cJSON_GetStringValue(
            cJSON_GetObjectItem(json, "node_name")
        );
        const char *request_id = cJSON_GetStringValue(
            cJSON_GetObjectItem(json, "request_id")
        );

        ESP_LOGI(TAG, "🔍 scan_node: %s → %s", node_name, target_ipv6);

        // Appel asynchrone
        esp_err_t err = coap_proxy_scan_node(
            target_ipv6, node_name, request_id
        );

        if (err != ESP_OK) {
            // Envoyer erreur immédiate au serveur
            send_scan_error(target_ipv6, node_name, request_id);
        }
    }
}
```

#### coap_proxy.c
**Responsabilités:**
- Recevoir commandes scan_node depuis cloud_websocket_client
- **Envoyer requête CoAP GET /network-info au node**
- Attendre réponse asynchrone
- **Renvoyer résultat au serveur Python via WebSocket**

**Code clé:**
```c
// coap_proxy.c:853 - Fonction principale
esp_err_t coap_proxy_scan_node(const char *target_ipv6,
                               const char *node_name,
                               const char *request_id)
{
    // 1. Allouer contexte pour callback async
    scan_node_context_t *ctx = malloc(sizeof(scan_node_context_t));
    strncpy(ctx->request_id, request_id, sizeof(ctx->request_id));
    strncpy(ctx->node_name, node_name, sizeof(ctx->node_name));
    strncpy(ctx->target_ipv6, target_ipv6, sizeof(ctx->target_ipv6));

    // 2. Créer message CoAP GET
    otMessage *message = otCoapNewMessage(instance, NULL);
    otCoapMessageInit(message, OT_COAP_TYPE_CONFIRMABLE, OT_COAP_CODE_GET);
    otCoapMessageAppendUriPathOptions(message, "network-info");

    // 3. Envoyer avec callback asynchrone
    otError error = otCoapSendRequest(
        instance, message, &messageInfo,
        handle_scan_node_response,  // ← Callback
        ctx                         // ← Context
    );

    return (error == OT_ERROR_NONE) ? ESP_OK : ESP_FAIL;
}

// coap_proxy.c:715 - Callback asynchrone
static void handle_scan_node_response(void *context,
                                       otMessage *message,
                                       const otMessageInfo *messageInfo,
                                       otError result)
{
    scan_node_context_t *ctx = (scan_node_context_t *)context;

    if (result != OT_ERROR_NONE) {
        // Timeout ou erreur → envoyer échec au serveur
        char error_msg[512];
        snprintf(error_msg, sizeof(error_msg),
                "{\"type\":\"scan_node_result\","
                "\"target_ipv6\":\"%s\","
                "\"node_name\":\"%s\","
                "\"request_id\":\"%s\","
                "\"success\":false,"
                "\"error\":\"CoAP timeout\"}",
                ctx->target_ipv6, ctx->node_name, ctx->request_id);

        cloud_ws_send_message(error_msg);
        free(ctx);
        return;
    }

    // Extraire payload JSON de la réponse CoAP
    uint16_t length = otMessageGetLength(message) - otMessageGetOffset(message);
    char *network_info_json = malloc(length + 1);
    otMessageRead(message, otMessageGetOffset(message), network_info_json, length);
    network_info_json[length] = '\0';

    // Construire message de succès
    char *result_msg = malloc(length + 512);
    snprintf(result_msg, length + 512,
            "{\"type\":\"scan_node_result\","
            "\"target_ipv6\":\"%s\","
            "\"node_name\":\"%s\","
            "\"request_id\":\"%s\","
            "\"success\":true,"
            "\"network_info\":%s}",  // ← JSON imbriqué
            ctx->target_ipv6, ctx->node_name, ctx->request_id,
            network_info_json);

    // Envoyer au serveur Python
    cloud_ws_send_message(result_msg);

    free(result_msg);
    free(network_info_json);
    free(ctx);
}
```

---

### 3.3 Node (ESP32-C6)

#### network_info_service.c
**Responsabilités:**
- Exposer service CoAP `/network-info`
- Répondre aux requêtes GET avec informations réseau

**Code clé:**
```c
// Réponse JSON typique:
{
  "role": "router",
  "rloc16": "0xa000",
  "ext_addr": "7aeb6e45c8970785",
  "parent": {
    "rloc16": "0x7000",
    "rssi": -45
  },
  "children": [],
  "neighbors": [
    {
      "rloc16": "0x7000",
      "rssi": -42,
      "lqi": 3
    }
  ]
}
```

---

### 3.4 Web Interface

#### templates/network_map.html
**Responsabilités:**
- Bouton "Refresh" pour déclencher scan
- Écouter events Socket.IO pour mises à jour temps réel
- Afficher topologie (TODO: visualisation graphique)

**Code clé:**
```javascript
// Socket.IO connection (pas Native WebSocket!)
const socket = io();

// Bouton Refresh
document.getElementById('refreshBtn').onclick = function() {
    fetch('/api/trigger_scan', {method: 'POST'})
        .then(response => response.json())
        .then(data => console.log('Scan started:', data));
};

// Recevoir résultats en temps réel
socket.on('scan_node_result', function(data) {
    console.log('Node scanned:', data.node_name, data.network_info);
    // TODO: Mettre à jour visualisation
});
```

---

## 4. Flux de Scan Complet

### 4.1 Séquence Détaillée

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌────────┐
│ Browser │     │ Python  │     │   BR    │     │  Node  │
│  (Web)  │     │ Server  │     │ (ESP32) │     │(ESP32) │
└────┬────┘     └────┬────┘     └────┬────┘     └────┬───┘
     │               │               │               │
     │ 1. Click      │               │               │
     │  "Refresh"    │               │               │
     ├──────────────►│               │               │
     │ Socket.IO     │               │               │
     │ trigger_scan  │               │               │
     │               │               │               │
     │               │ 2. HTTP POST  │               │
     │               │ /api/trigger_ │               │
     │               │ scan          │               │
     │               │ (internal)    │               │
     │               │               │               │
     │               │ 3. For each   │               │
     │               │    node:      │               │
     │               │ send_scan_    │               │
     │               │ node_command()│               │
     │               │               │               │
     │               │ 4. Enqueue    │               │
     │               │ message in    │               │
     │               │ Queue         │               │
     │               │               │               │
     │               │               │               │
     │               ├──────────────►│ 5. Native WS │
     │               │ {"command":   │ (Flask-Sock) │
     │               │  "scan_node", │               │
     │               │  "target_ipv6"│               │
     │               │  "..."        │               │
     │               │ }             │               │
     │               │               │               │
     │               │               │ 6. CoAP GET  │
     │               │               │ /network-info│
     │               │               ├─────────────►│
     │               │               │ (UDP/IPv6)   │
     │               │               │              │
     │               │               │              │ 7. Query
     │               │               │              │ OpenThread
     │               │               │              │ API
     │               │               │              │
     │               │               │ 8. CoAP RSP │
     │               │               │ 2.05 Content│
     │               │               │◄─────────────┤
     │               │               │ {role,rloc16│
     │               │               │  parent,...} │
     │               │               │              │
     │               │               │              │
     │               │ 9. Native WS  │              │
     │               │◄──────────────┤              │
     │               │ {"type":      │              │
     │               │  "scan_node_  │              │
     │               │   result",    │              │
     │               │  "success":   │              │
     │               │   true,       │              │
     │               │  "network_    │              │
     │               │   info":{...} │              │
     │               │ }             │              │
     │               │               │              │
     │ 10. Socket.IO │               │              │
     │◄──────────────┤               │              │
     │ scan_node_    │               │              │
     │ result event  │               │              │
     │               │               │              │
     │ 11. Update UI │               │              │
     │               │               │              │
```

### 4.2 Timing et Parallélisme

**Caractéristiques:**
- Scans **parallèles** pour tous les nodes (non-bloquant)
- Timeout par node: ~5 secondes (CoAP retries)
- Agrégation côté Python après réception de toutes les réponses
- Total pour 16 nodes: ~6 secondes (vs 48s en séquentiel)

---

## 5. Protocoles de Communication

### 5.1 Socket.IO (Web ↔ Python)

**Events utilisés:**

```javascript
// Client → Server
socket.emit('trigger_scan');  // Déclencher scan manuel

// Server → Client
socket.on('scan_node_result', (data) => {
  // data = {node_name, network_info, success, ...}
});

socket.on('topology_update', (data) => {
  // Topologie complète reconstruite
});
```

**Format:**
- Encapsulation Socket.IO: `42["event_name", {...data...}]`
- Auto-reconnexion
- Namespaces, rooms (non utilisés actuellement)

---

### 5.2 Native WebSocket (Python ↔ BR)

**URL de connexion:**
```
ws://192.168.1.150:5001/ws/br?br_id=BR-001&auth_token=xxx&network_prefix=fd78::/64
```

**Messages Python → BR:**

```json
{
  "command": "scan_node",
  "target_ipv6": "fd78:8e78:3bfe:1:5339:695e:8f14:acf6",
  "node_name": "d4E",
  "request_id": "a3f2c1b5-..."
}
```

**Messages BR → Python:**

```json
// Heartbeat (toutes les 10s)
{
  "type": "heartbeat",
  "br_id": "BR-001",
  "timestamp": 12345,
  "nodes_count": 2,
  "status": "online"
}

// Résultat de scan
{
  "type": "scan_node_result",
  "target_ipv6": "fd78:8e78:3bfe:1:5339:695e:8f14:acf6",
  "node_name": "d4E",
  "request_id": "a3f2c1b5-...",
  "success": true,
  "network_info": {
    "role": "router",
    "rloc16": "0xa000",
    "parent": {"rloc16": "0x7000"},
    "neighbors": [...]
  },
  "error": null
}

// En cas d'erreur
{
  "type": "scan_node_result",
  "success": false,
  "error": "CoAP timeout: node not reachable"
}
```

**ATTENTION**:
- Champ `"command"` pour Python → BR
- Champ `"type"` pour BR → Python
- **Ce n'est PAS un oubli**, c'est une convention différente par direction

---

### 5.3 CoAP (BR ↔ Node)

**Requête:**
```
GET coap://[fd78:8e78:3bfe:1:5339:695e:8f14:acf6]:5683/network-info
Type: CON (Confirmable)
```

**Réponse:**
```
2.05 Content
Content-Format: application/json

{
  "role": "router",
  "rloc16": "0xa000",
  "ext_addr": "5339695e8f14acf6",
  "parent": {
    "rloc16": "0x7000",
    "rssi": -45
  },
  "children": [],
  "neighbors": [
    {
      "rloc16": "0x7000",
      "rssi": -42,
      "lqi": 3,
      "link_quality_in": 3
    }
  ]
}
```

---

## 6. Bugs Rencontrés et Solutions

### 6.1 Bug #1: Protocol Mismatch (Python → BR)

#### Symptômes
```
BR logs: "📩 Received message from server (0 bytes)"
```
Le BR recevait des messages mais la longueur était 0.

#### Cause Racine

**Python envoyait:**
```python
scan_msg = {
    'type': 'scan_node',  # ← Mauvais champ
    'target_ipv6': '...',
    'node_name': '...',
    'request_id': '...'
}
```

**BR C attendait:**
```c
const char *command = cJSON_GetStringValue(
    cJSON_GetObjectItem(json, "command")  // ← Cherche "command"
);

if (strcmp(command, "scan_node") == 0) {
    // ...
}
```

Le BR ne trouvait pas le champ `"command"`, donc `command == NULL`, et ne rentrait jamais dans le `if`.

#### Solution

**Fichier**: `native_websocket_handler.py:832`

**Avant:**
```python
scan_msg = {
    'type': 'scan_node',  # ❌ Wrong
    ...
}
```

**Après:**
```python
scan_msg = {
    'command': 'scan_node',  # ✅ Fixed
    ...
}
```

**Commentaire ajouté:**
```python
# IMPORTANT: Use 'command' field, not 'type', to match BR handler
```

---

### 6.2 Bug #2: Thread Safety (Flask-Sock WebSocket)

#### Symptômes
Même après fix du Bug #1, les messages arrivaient toujours vides (0 bytes) au BR.

#### Cause Racine

**Architecture threading de Flask:**

```
┌──────────────────────────┐
│  Flask Process           │
│                          │
│  ┌────────────────────┐  │
│  │ HTTP Request       │  │
│  │ Thread             │  │
│  │ /api/trigger_scan  │  │
│  │                    │  │
│  │ ├─► send_scan_    │  │
│  │ │    node_command()│  │
│  │ │                  │  │
│  │ │    ws.send() ────┼──┼──► ❌ Cross-thread!
│  │ │                  │  │
│  └─┴──────────────────┘  │
│                          │
│  ┌────────────────────┐  │
│  │ WebSocket Handler  │  │
│  │ Thread (Flask-Sock)│  │
│  │                    │  │
│  │ while True:        │  │
│  │   msg = ws.receive│  │ ← Bloque ici
│  │   handle(msg)      │  │
│  └────────────────────┘  │
└──────────────────────────┘
```

**Problème**: `ws.send()` appelé depuis le thread HTTP, mais `ws` est géré par le thread WebSocket handler.

**Flask-Sock/simple-websocket n'est PAS thread-safe** pour les appels cross-thread à `ws.send()`.

#### Solution: Message Queue System

**Architecture corrigée:**

```python
class NativeWebSocketHandler:
    def __init__(self):
        self.message_queues = {}  # {br_id: Queue()}

    def handle_connection(self, ws, environ):
        """Thread WebSocket handler (dédié par BR)"""
        br_id = extract_br_id(environ)

        # Créer queue pour ce BR
        self.message_queues[br_id] = queue.Queue()

        while True:
            # 1. Vérifier queue et envoyer messages en attente
            self._process_outgoing_queue(br_id, ws)  # ✅ Safe

            # 2. Recevoir message du BR (bloquant)
            message = ws.receive()
            self.handle_message(br_id, message, ws)

    def _process_outgoing_queue(self, br_id, ws):
        """Envoyer tous les messages en attente (non-bloquant)"""
        queue = self.message_queues[br_id]

        while not queue.empty():
            message = queue.get_nowait()
            ws.send(message)  # ✅ Safe: même thread que ws.receive()

    def send_scan_node_command(self, br_id, ...):
        """Appelé depuis thread HTTP"""
        scan_msg = {...}
        message = json.dumps(scan_msg)

        # Ajouter à la queue (thread-safe)
        self.message_queues[br_id].put(message)  # ✅ Safe
        # Ne PAS appeler ws.send() directement!
```

**Principe:**
1. Thread HTTP → Enqueue message dans `Queue()` (thread-safe)
2. Thread WebSocket handler → Déqueue et envoie via `ws.send()` (safe)
3. `Queue()` fait office de pont thread-safe entre les deux threads

**Fichiers modifiés:**
- `native_websocket_handler.py`:
  - Ligne 14: `import queue`
  - Ligne 66: `self.message_queues = {}`
  - Lignes 219-251: `_process_outgoing_queue()`
  - Ligne 320: Créer queue à la connexion
  - Ligne 339: Appel `_process_outgoing_queue()` avant chaque `receive()`
  - Ligne 360: Cleanup queue à la déconnexion
  - Lignes 889-893: `send_scan_node_command()` utilise `queue.put()`

---

### 6.3 Bug #3: Logger Non Défini

#### Symptômes
```python
NameError: name 'logger' is not defined
  File "server.py", line 1930, in get_topology
```

#### Cause
`logging` module importé mais instance `logger` jamais créée.

#### Solution
**Fichier**: `server.py:48`

```python
import logging

# Créer logger pour ce module
logger = logging.getLogger(__name__)
```

---

## 7. Points d'Attention pour Développement Futur

### 7.1 Thread Safety

**RÈGLE ABSOLUE**: Ne **JAMAIS** appeler `ws.send()` depuis un thread différent du thread WebSocket handler.

**Mauvais:**
```python
def some_http_endpoint():
    ws = native_ws_handler.active_connections['BR-001']
    ws.send("message")  # ❌ DANGER!
```

**Bon:**
```python
def some_http_endpoint():
    native_ws_handler.send_scan_node_command(...)  # ✅ Utilise queue
```

### 7.2 Dual WebSocket System

**Toujours se rappeler:**
- Socket.IO pour **web browser**
- Native WebSocket pour **ESP32 BR**
- **Pas d'interopérabilité** entre les deux

**Ne PAS faire:**
```python
# ❌ Envoyer à un BR via Socket.IO
socketio.emit('scan_node', {...}, room=br_id)  # Ne marchera jamais!
```

### 7.3 Protocol Fields

**Python → BR**: Toujours utiliser `"command"`
**BR → Python**: Toujours utiliser `"type"`

Cette asymétrie est **intentionnelle** (legacy code du BR).

### 7.4 Timeouts et Error Handling

**Actuellement manquant:**
- Timeout global du scan (si un node ne répond jamais)
- Retry logic pour nodes injoignables
- Agrégation partielle des résultats

**TODO:**
```python
async def scan_all_nodes_via_brs():
    scan_results = {}

    async with timeout(30):  # 30s max total
        for node in nodes:
            try:
                result = await scan_node_async(node, timeout=5)
                scan_results[node] = result
            except TimeoutError:
                scan_results[node] = {'success': False, 'error': 'timeout'}

    return build_topology_from_partial_results(scan_results)
```

---

## 8. Code Snippets Critiques

### 8.1 Message Queue System (Python)

```python
# native_websocket_handler.py
import queue

class NativeWebSocketHandler:
    def __init__(self):
        self.message_queues: Dict[str, queue.Queue] = {}

    def handle_connection(self, ws, environ):
        br_id = parse_br_id(environ)
        self.message_queues[br_id] = queue.Queue()

        try:
            while True:
                self._process_outgoing_queue(br_id, ws)
                message = ws.receive()
                if message is None:
                    break
                self.handle_message(br_id, message, ws)
        finally:
            del self.message_queues[br_id]

    def _process_outgoing_queue(self, br_id, ws):
        queue = self.message_queues[br_id]
        while not queue.empty():
            try:
                message = queue.get_nowait()
                ws.send(message)
            except queue.Empty:
                break

    def send_scan_node_command(self, br_id, target_ipv6, node_name, request_id):
        scan_msg = {
            'command': 'scan_node',
            'target_ipv6': target_ipv6,
            'node_name': node_name,
            'request_id': request_id
        }
        self.message_queues[br_id].put(json.dumps(scan_msg))
```

### 8.2 BR Command Handler (C)

```c
// cloud_websocket_client.c
static void handle_server_command(const char *data, int len) {
    cJSON *json = cJSON_ParseWithLength(data, len);
    const char *command = cJSON_GetStringValue(cJSON_GetObjectItem(json, "command"));

    if (strcmp(command, "scan_node") == 0) {
        const char *target_ipv6 = cJSON_GetStringValue(cJSON_GetObjectItem(json, "target_ipv6"));
        const char *node_name = cJSON_GetStringValue(cJSON_GetObjectItem(json, "node_name"));
        const char *request_id = cJSON_GetStringValue(cJSON_GetObjectItem(json, "request_id"));

        esp_err_t err = coap_proxy_scan_node(target_ipv6, node_name, request_id);

        if (err != ESP_OK) {
            char error_response[512];
            snprintf(error_response, sizeof(error_response),
                    "{\"type\":\"scan_node_result\","
                    "\"target_ipv6\":\"%s\","
                    "\"node_name\":\"%s\","
                    "\"request_id\":\"%s\","
                    "\"success\":false,"
                    "\"error\":\"Failed to initiate CoAP scan\"}",
                    target_ipv6, node_name, request_id);
            cloud_ws_send_message(error_response);
        }
    }

    cJSON_Delete(json);
}
```

### 8.3 BR CoAP Proxy (C)

```c
// coap_proxy.c
typedef struct {
    char request_id[64];
    char node_name[32];
    char target_ipv6[48];
} scan_node_context_t;

esp_err_t coap_proxy_scan_node(const char *target_ipv6,
                               const char *node_name,
                               const char *request_id)
{
    scan_node_context_t *ctx = malloc(sizeof(scan_node_context_t));
    strncpy(ctx->request_id, request_id, sizeof(ctx->request_id) - 1);
    strncpy(ctx->node_name, node_name, sizeof(ctx->node_name) - 1);
    strncpy(ctx->target_ipv6, target_ipv6, sizeof(ctx->target_ipv6) - 1);

    otMessage *message = otCoapNewMessage(instance, NULL);
    otCoapMessageInit(message, OT_COAP_TYPE_CONFIRMABLE, OT_COAP_CODE_GET);
    otCoapMessageAppendUriPathOptions(message, "network-info");

    otMessageInfo messageInfo;
    memset(&messageInfo, 0, sizeof(messageInfo));
    otIp6AddressFromString(target_ipv6, &messageInfo.mPeerAddr);
    messageInfo.mPeerPort = OT_DEFAULT_COAP_PORT;

    otError error = otCoapSendRequest(instance, message, &messageInfo,
                                      handle_scan_node_response, ctx);

    return (error == OT_ERROR_NONE) ? ESP_OK : ESP_FAIL;
}

static void handle_scan_node_response(void *context,
                                       otMessage *message,
                                       const otMessageInfo *messageInfo,
                                       otError result)
{
    scan_node_context_t *ctx = (scan_node_context_t *)context;

    if (result != OT_ERROR_NONE) {
        // Send error to cloud
        char error_msg[512];
        snprintf(error_msg, sizeof(error_msg),
                "{\"type\":\"scan_node_result\","
                "\"success\":false,"
                "\"error\":\"CoAP timeout\"}");
        cloud_ws_send_message(error_msg);
        free(ctx);
        return;
    }

    uint16_t length = otMessageGetLength(message) - otMessageGetOffset(message);
    char *network_info_json = malloc(length + 1);
    otMessageRead(message, otMessageGetOffset(message), network_info_json, length);
    network_info_json[length] = '\0';

    char *result_msg = malloc(length + 512);
    snprintf(result_msg, length + 512,
            "{\"type\":\"scan_node_result\","
            "\"target_ipv6\":\"%s\","
            "\"node_name\":\"%s\","
            "\"request_id\":\"%s\","
            "\"success\":true,"
            "\"network_info\":%s}",
            ctx->target_ipv6, ctx->node_name, ctx->request_id, network_info_json);

    cloud_ws_send_message(result_msg);

    free(result_msg);
    free(network_info_json);
    free(ctx);
}
```

---

## 9. Questions pour ChatGPT

### 9.1 Optimisation du Système de Queue

**Contexte:** Actuellement, `_process_outgoing_queue()` est appelé avant chaque `ws.receive()` dans une boucle bloquante.

**Question:**
> Notre implémentation actuelle utilise `queue.Queue()` pour la thread safety entre thread HTTP et thread WebSocket. Existe-t-il des alternatives plus performantes ou idiomatiques en Python pour ce use case?
>
> Architecture actuelle:
> - Thread WS handler bloque sur `ws.receive()`
> - Avant chaque receive, on vide la queue avec `queue.get_nowait()`
> - Thread HTTP fait `queue.put()` depuis n'importe où
>
> Y a-t-il un meilleur pattern? (gevent, eventlet, asyncio?)

### 9.2 Alternative à Flask-Sock

**Contexte:** Flask-Sock utilise `simple-websocket` qui n'est pas thread-safe nativement.

**Question:**
> Existe-t-il une librairie WebSocket pour Flask qui soit nativement thread-safe et supporte les WebSockets natifs (non Socket.IO)?
>
> Requis:
> - Compatible ESP32 `esp_websocket_client`
> - Thread-safe pour envoi depuis threads différents
> - Coexiste avec Flask-SocketIO dans la même app
>
> Options considérées: websockets, wsproto, ws4py?

### 9.3 Gestion d'Erreurs et Timeouts

**Question:**
> Comment implémenter un système de timeout robuste pour le scan orchestré?
>
> Contraintes:
> - 16+ nodes à scanner en parallèle
> - Timeout par node: 5s
> - Timeout global: 30s
> - Agrégation partielle des résultats (ne pas bloquer sur nodes morts)
> - WebSocket asynchrone (pas de await direct)
>
> Faut-il migrer vers `async/await` ou garder l'approche callback?

### 9.4 Performance avec 50+ Nodes

**Question:**
> Avec notre architecture actuelle, quelles sont les limites de scalabilité?
>
> - Python: 50+ `queue.Queue()` actives
> - BR: 50+ requêtes CoAP asynchrones simultanées
> - Mémoire ESP32: Contextes malloc pour chaque scan
>
> Optimisations suggérées?

### 9.5 Architecture Alternative: Message Broker

**Question:**
> Serait-il plus robuste d'introduire un message broker (Redis, RabbitMQ)?
>
> Architecture proposée:
> - Python publie commandes dans Redis queue
> - Worker threads consomment et envoient via WebSocket
> - Résultats publiés dans Redis pub/sub
> - Agrégateur subscribe et construit topologie
>
> Avantages/inconvénients vs notre système actuel?

---

## Conclusion

Ce document constitue une référence complète pour comprendre l'architecture du système de scan réseau OpenThread. Les trois bugs principaux ont été identifiés et résolus:

1. **Protocol mismatch** (field naming)
2. **Thread safety** (message queue system)
3. **Logger initialization**

Le système fonctionne mais nécessite encore:
- Timeouts robustes
- Agrégation complète des résultats
- Gestion des nodes déconnectés
- Optimisation pour grand nombre de nodes

**Pour questions techniques**: Utiliser ce document comme contexte pour ChatGPT.

---

**Fichiers de référence:**
- Python: `server.py`, `lib/native_websocket_handler.py`
- BR: `cloud_websocket_client.c`, `coap_proxy.c`, `coap_proxy.h`
- Node: `network_info_service.c`
- Web: `templates/network_map.html`
- Doc: `ESP32_BR_NETWORK_SCAN.md` (scan orchestration flow)

**Dernière mise à jour**: 2025-10-16
