# 🔍 FLUX COMPLET BLE BEACON - De bout en bout

Documentation du flux de données BLE depuis la réception Border Router jusqu'à l'affichage web.

---

## 📡 ÉTAPE 1: ESP32 Node → Border Router (CoAP)

**Fichier**: `/Users/lilianbrun/esp2/esp-idf/examples/openthread/ot_cli_lux/main/coap_beacon_service.c`

**Fonction**: `coap_send_ble_beacon()` - ligne 277

**Payload envoyé** (lignes 321-330):
```c
// Build complete JSON payload: code, node_ipv6, ble_addr, and rssi
char addr_str[18];
snprintf(addr_str, sizeof(addr_str), "%02X:%02X:%02X:%02X:%02X:%02X",
         addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);

char payload[MAX_PAYLOAD_LEN];
int payload_len = snprintf(payload, sizeof(payload),
                           "{\"code\":\"%s\",\"node_ipv6\":\"%s\",\"ble_addr\":\"%s\",\"rssi\":%d}",
                           code, s_node_ipv6, addr_str, rssi);
```

**Exemple de payload CoAP**:
```json
{
  "code": "po5",
  "node_ipv6": "fd78:8e78:3bfe:1:5339:695e:8f14:acf6",
  "ble_addr": "EF:05:AB:1A:CB:A5",
  "rssi": -46
}
```

**URI CoAP**: `coap://[BR_IPv6]:5683/ble-beacon`

---

## 📡 ÉTAPE 2: Border Router → Python Server (WebSocket)

**Protocole**: Native WebSocket (pas Socket.IO)

**Message WebSocket envoyé par BR**:
```json
{
  "type": "node_event",
  "source_ipv6": "fd78:8e78:3bfe:1:5339:695e:8f14:acf6",
  "source_rloc": "0xc401",
  "event_type": "ble_beacon",
  "payload": {
    "code": "po5",
    "node_ipv6": "fd78:8e78:3bfe:1:5339:695e:8f14:acf6",
    "ble_addr": "EF:05:AB:1A:CB:A5",
    "rssi": -46
  }
}
```

**Note importante**:
- `type: "node_event"` → Type de message WebSocket
- `event_type: "ble_beacon"` → Type d'événement node (underscore, pas hyphen!)

---

## 🐍 ÉTAPE 3: Python - Réception WebSocket

**Fichier**: `lib/native_websocket_handler.py`

### 3.1 Reception du message

**Fonction**: `handle_message()` - ligne 294

**Code** (lignes 303-313):
```python
# 📥 LOG: Trame RAW reçue du BR
logger.error(f"📥 PYTHON←BR: Received WebSocket message from BR {br_id}:")
logger.error(f"   RAW JSON ({len(message)} bytes): {message}")

try:
    # Parse JSON
    data = json.loads(message)
    msg_type = data.get('type')

    logger.error(f"   ✅ JSON parsed successfully")
    logger.error(f"   Message type: {msg_type}")
```

**Log attendu**:
```
📥 PYTHON←BR: Received WebSocket message from BR BR-001:
   RAW JSON (269 bytes): {"type":"node_event","source_ipv6":"fd78:...","event_type":"ble_beacon",...}
   ✅ JSON parsed successfully
   Message type: node_event
```

### 3.2 Routing selon type

**Code** (lignes 319-325):
```python
# Route to appropriate handler
if msg_type == 'heartbeat':
    self.handle_heartbeat(br_id, data, ws)

elif msg_type == 'node_event':
    # New: Handle node_event with source_ipv6 field
    self.handle_node_event_with_ipv6(br_id, data)
```

**Action**: Redirige vers `handle_node_event_with_ipv6()`

---

## 🐍 ÉTAPE 4: Python - Traitement node_event

**Fichier**: `lib/native_websocket_handler.py`

**Fonction**: `handle_node_event_with_ipv6()` - ligne 383

### 4.1 Extraction des champs

**Code** (lignes 391-405):
```python
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
```

**Log attendu**:
```
📦 PYTHON: Processing node_event from BR BR-001
   Full event data: {
     "type": "node_event",
     "source_ipv6": "fd78:...",
     "event_type": "ble_beacon",
     "payload": {...}
   }
   🌐 Extracted fields:
      source_ipv6: fd78:8e78:3bfe:1:5339:695e:8f14:acf6
      source_rloc: 0xc401 (for reference)
      event_type: ble_beacon
      payload: {"code":"po5","node_ipv6":"fd78:...","ble_addr":"EF:05:AB:1A:CB:A5","rssi":-46}
```

### 4.2 Résolution IPv6 → node_name

**Code** (lignes 414-423):
```python
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
```

**Utilise**: `config/adresses.json` pour la résolution

**Log attendu**:
```
   🔍 Resolving IPv6 to node name...
   ✅ MATCH: fd78:8e78:3bfe:1:5339:695e:8f14:acf6 → d4E
   ✅ Resolved to known node: d4E
```

### 4.3 Routing selon event_type

**Code** (lignes 447-453):
```python
# Route to appropriate handler based on event type
if event_type == 'ble_beacon' and coap_server:
    coap_server.handle_ble_event_from_br({
        'node': node_name,
        'br_id': br_id,
        'payload': payload  # Passer le payload complet
    })
```

**⚠️ CRITIQUE**: Vérifie `event_type == 'ble_beacon'` (underscore!)

**Action**: Appelle `server.py::handle_ble_event_from_br()`

---

## 🐍 ÉTAPE 5: Python - Handler BLE

**Fichier**: `server.py`

**Fonction**: `handle_ble_event_from_br()` - ligne 917

### 5.1 Extraction payload

**Code** (lignes 921-928):
```python
br_id = data.get('br_id')
node_name = data.get('node')
payload = data.get('payload', {})

# Récupérer l'adresse BLE et le RSSI depuis le payload
ble_addr = payload.get('ble_addr', '')
rssi = payload.get('rssi', 0)
code = payload.get('code', '')
```

**Log attendu** (ligne 930):
```
📡 BLE beacon depuis BR BR-001, node d4E: EF:05:AB:1A:CB:A5 (RSSI: -46, code: po5)
```

### 5.2 Stockage historique

**Code** (lignes 932-947):
```python
# Stocker la détection
detection_data = {
    'node': node_name,
    'br_id': br_id,
    'ble_addr': ble_addr,
    'rssi': rssi,
    'code': code,
    'timestamp': datetime.now().isoformat()
}

# Ajouter à l'historique
self.ble_history.append(detection_data)

# Limiter l'historique à 1000 entrées
if len(self.ble_history) > 1000:
    self.ble_history.pop(0)
```

### 5.3 Émission WebSocket #1 - ble_beacon

**Code** (lignes 953-954):
```python
# Émettre via WebSocket
socketio.emit('ble_beacon', detection_data)
```

**Payload émis**:
```json
{
  "node": "d4E",
  "br_id": "BR-001",
  "ble_addr": "EF:05:AB:1A:CB:A5",
  "rssi": -46,
  "code": "po5",
  "timestamp": "2025-10-16T08:00:00.123456"
}
```

### 5.4 Émission WebSocket #2 - ble_frame (pour debug)

**Code** (lignes 956-966):
```python
# Émettre aussi ble_frame pour la page debug
frame_data = {
    'router': node_name,
    'code': code,
    'badge_addr': ble_addr,
    'rssi': rssi if rssi else 0,
    'timestamp': datetime.now().isoformat()
}
logger.info(f"📤 Émission WebSocket 'ble_frame': {frame_data}")
socketio.emit('ble_frame', frame_data)
logger.info(f"✅ WebSocket 'ble_frame' émis avec succès")
```

**Log attendu**:
```
📤 Émission WebSocket 'ble_frame': {'router': 'd4E', 'code': 'po5', 'badge_addr': 'EF:05:AB:1A:CB:A5', 'rssi': -46, 'timestamp': '2025-10-16T08:00:00.123456'}
✅ WebSocket 'ble_frame' émis avec succès
```

**Payload émis**:
```json
{
  "router": "d4E",
  "code": "po5",
  "badge_addr": "EF:05:AB:1A:CB:A5",
  "rssi": -46,
  "timestamp": "2025-10-16T08:00:00.123456"
}
```

---

## 🌐 ÉTAPE 6: Page Web - Réception

**Fichier**: `templates/ble_debug.html`

### 6.1 Connexion Socket.IO

**Code** (lignes 321-323):
```javascript
// Connect to Socket.IO server
const socket = io();
```

### 6.2 Écoute événement ble_frame

**Code** (lignes 325-327):
```javascript
socket.on('ble_frame', (data) => {
    addFrame(data);
});
```

### 6.3 Affichage dans tableau

**Fonction**: `addFrame(data)` - lignes 253-280

**Code extrait**:
```javascript
function addFrame(data) {
    const tbody = document.getElementById('frames-tbody');

    // Create new row
    const row = document.createElement('tr');
    row.className = 'frame-row new';

    // Format timestamp
    const timestamp = new Date(data.timestamp);

    row.innerHTML = `
        <td>${formatTime(timestamp)}</td>
        <td class="router-cell">${data.router}</td>
        <td class="code-cell">${data.code}</td>
        <td class="addr-cell">${data.badge_addr}</td>
        <td class="rssi-cell">${data.rssi} dBm</td>
    `;

    // Insert at top
    tbody.insertBefore(row, tbody.firstChild);

    // Update stats
    updateStats();
}
```

**Colonnes affichées**:
1. **Heure** - Timestamp formaté
2. **Routeur** - Node name (ex: "d4E")
3. **Badge Code** - Code beacon (ex: "po5")
4. **Badge ID** - Adresse BLE (ex: "EF:05:AB:1A:CB:A5")
5. **RSSI** - Signal strength (ex: "-46 dBm")

---

## 🚨 PROBLÈMES RÉSOLUS

### ❌ Problème #1: coap_server is None (CRITIQUE)

**Symptôme**: Handler ne peut pas être appelé car `coap_server` est None

**Logs**:
```
✅ CoAP Server créé: <__main__.CoAPServer object at 0x1114ed7b0>
...
❌ CANNOT call handler: coap_server is None!
```

**Cause racine**: Module Python `server.py` importé plusieurs fois par les workers Flask
- Premier import: Dans `main()`, crée `CoAPServer` avec succès
- Second import: Worker Flask réimporte le module, réinitialise `coap_server = None`

**Preuve**: Configuration banner imprimé 2 fois dans les logs

**Solution** (server.py lignes 2228-2238):
```python
# Module-level dict that persists across reimports (by Flask workers)
# This solves the "coap_server is None" issue when workers reimport the module
_server_instances = {}

def get_coap_server():
    """Retourne l'instance du serveur CoAP (créée dans main())

    Utilise _server_instances dict qui persiste à travers les imports de modules.
    Cela résout le problème de module importé plusieurs fois par les workers Flask.
    """
    return _server_instances.get('coap_server')
```

**Guard contre double initialisation** (server.py lignes 3411-3416):
```python
# Check if already initialized (module reimport scenario by Flask workers)
if _server_instances.get('coap_server'):
    print("⚠️  Module reimport détecté - CoAP Server existe déjà, réutilisation de l'instance")
    print(f"   Instance existante: {_server_instances['coap_server']} (id={id(_server_instances['coap_server'])})")
    coap_server = _server_instances['coap_server']
    return
```

**Stockage** (server.py lignes 3438-3447):
```python
# CRITIQUE: Stocker dans _server_instances dict pour persister à travers tous les imports
# Cela résout le problème de module importé plusieurs fois par les workers Flask
_server_instances['coap_server'] = coap_server

# Aussi stocker dans app.config pour accès via contexte Flask
app.config['COAP_SERVER'] = coap_server

print(f"✅ CoAP Server créé: {coap_server} (id={id(coap_server)})")
print(f"   Stocké dans _server_instances: {_server_instances.get('coap_server')}")
print(f"   Stocké dans app.config: {app.config.get('COAP_SERVER')}")
```

**Usage** (native_websocket_handler.py ligne 448):
```python
# Get coap_server instance dynamically (resolves double-import issue)
coap_server_instance = server.get_coap_server()

if event_type == 'ble_beacon' and coap_server_instance:
    coap_server_instance.handle_ble_event_from_br({...})
```

**Résultat**: Python module cache préserve `_server_instances` dict même lors de réimports. Premier import crée instance, second import détecte et réutilise. Tous les workers accèdent à la même instance via `get_coap_server()`.

---

### ❌ Problème #2: Handler Legacy

**Fichier**: `lib/native_websocket_handler.py`

**Fonction**: `handle_node_event()` - ligne 523 (LEGACY, NON UTILISÉ)

**Code problématique** (ligne 561):
```python
elif event_type == 'ble-beacon' and coap_server:  # ❌ HYPHEN au lieu d'underscore!
    coap_server.handle_ble_event_from_br({
        'node': node_name,
        'br_id': br_id,
        'ble_addr': payload.get('ble_addr'),
        'rssi': payload.get('rssi'),
        'code': payload.get('code')
    })
```

**Note**: Ce handler n'est normalement PAS appelé car le Border Router envoie `type: "node_event"` qui est géré par `handle_node_event_with_ipv6()`.

### ✅ Solution: Handler actif

Le handler actif `handle_node_event_with_ipv6()` ligne 448 utilise correctement:
```python
if event_type == 'ble_beacon' and coap_server_instance:  # ✅ UNDERSCORE correct!
```

---

## 🔍 VÉRIFICATIONS À FAIRE

### 1. Vérifier les logs Python

Chercher dans les logs du serveur:
```bash
grep "📥 PYTHON←BR" server.log
grep "event_type: ble_beacon" server.log
grep "📤 Émission WebSocket 'ble_frame'" server.log
```

### 2. Vérifier la console web

Ouvrir `/ble_debug` et la console (F12):
- [ ] "Connected to server" apparaît
- [ ] Événements Socket.IO reçus
- [ ] Fonction `addFrame()` appelée

### 3. Vérifier le payload Border Router

Dans les logs, chercher la trame RAW:
```
📥 PYTHON←BR: Received WebSocket message from BR BR-001:
   RAW JSON (269 bytes): {"type":"node_event","event_type":"ble_beacon",...}
```

**Vérifier**:
- [ ] `"type": "node_event"` ✅
- [ ] `"event_type": "ble_beacon"` ✅ (underscore, pas hyphen!)
- [ ] Payload contient `ble_addr` et `rssi`

---

## ✅ CHECKLIST DE DÉPLOIEMENT

### ESP32 Node
- [ ] `coap_beacon_service.c` modifié (lignes 321-330)
- [ ] Firmware recompilé avec `idf.py build`
- [ ] Firmware flashé avec `idf.py flash`
- [ ] Node redémarré

### Python Server
- [ ] `native_websocket_handler.py` ligne 448: `event_type == 'ble_beacon'`
- [ ] `server.py` lignes 925-927: extraction ble_addr et rssi
- [ ] `server.py` lignes 964-966: logs d'émission présents
- [ ] Serveur redémarré

### Tests
- [ ] Border Router connecté (log "✅ Border Router BR-001 connected")
- [ ] Node ESP32 envoie beacons CoAP
- [ ] Python reçoit événements node_event
- [ ] Logs "📤 Émission WebSocket 'ble_frame'" apparaissent
- [ ] Page `/ble_debug` affiche les frames

---

## 📊 RÉSUMÉ DU FLUX

```
┌──────────────┐  CoAP    ┌──────────────┐  WebSocket  ┌──────────────┐  Socket.IO  ┌──────────────┐
│  ESP32 Node  │ ───────> │ Border Router│ ──────────> │ Python Server│ ──────────> │   Web Page   │
│  (nRF52840)  │          │   (ESP32-C6) │             │  (Flask)     │             │ (ble_debug)  │
└──────────────┘          └──────────────┘             └──────────────┘             └──────────────┘
      ↓                          ↓                           ↓                            ↓
  ble_addr                  node_event                 ble_frame                    Tableau HTML
   + rssi                + source_ipv6              + badge_addr                   avec colonnes
   + code                + event_type               + rssi, code                   RSSI, Code, Addr
```

**Durée totale estimée**: < 100ms (CoAP 20ms + WebSocket 30ms + Socket.IO 20ms)

---

**Document créé le**: 2025-10-16
**Dernière mise à jour**: 2025-10-16 08:00 UTC
