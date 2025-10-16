# Question pour ChatGPT: Problème Persistant de Scan Réseau

## Contexte

J'ai un système de scan orchestré pour réseau mesh OpenThread avec:
- **Python Flask Server** (port 5001)
- **Dual WebSocket System**:
  - Socket.IO pour web browsers
  - Native WebSocket (Flask-Sock) pour ESP32 Border Routers
- **Border Routers ESP32-C6** qui utilisent `esp_websocket_client`
- **Nodes ESP32-C6** avec service CoAP `/network-info`

**Architecture complète**: Voir fichier `ARCHITECTURE_NETWORK_SCAN.md` pour détails complets.

---

## Le Problème

Lorsque je clique sur le bouton "Refresh" dans l'interface web pour déclencher un scan réseau, **les commandes n'arrivent jamais au Border Router**.

### Logs Observés

#### Python Server:
```
15:34:50 [INFO] __main__: 🔍 Démarrage du scan orchestré de tous les nodes...
15:34:50 [INFO] __main__: 📋 Nodes à scanner: 16
15:34:50 [INFO] __main__:    • gateway @ fde7:cfa3:40ca:73b5:b63a:45ff:fe18:2384
15:34:50 [INFO] __main__:    • d2C @ fd78:8e78:3bfe:1:f2a0:91f8:bb:b8dc
... (liste des 16 nodes)
```

**⚠️ MAIS: Aucun log du type:**
```
[INFO] lib.native_websocket_handler: 🔍 Scan command enqueued for BR BR-001, node d2C (...)
```

#### Border Router:
```
I (506635) cloud_ws: 📩 Received message from server (0 bytes)
```

Le BR reçoit des "messages" mais avec **0 bytes** de contenu.

---

## Fixes Déjà Appliqués

### Fix #1: Protocol Mismatch
**Fichier**: `native_websocket_handler.py:832`

Changé de:
```python
scan_msg = {
    'type': 'scan_node',  # ❌ Mauvais champ
    ...
}
```

À:
```python
scan_msg = {
    'command': 'scan_node',  # ✅ Correct
    'target_ipv6': target_ipv6,
    'node_name': node_name,
    'request_id': request_id
}
```

### Fix #2: Thread Safety avec Message Queue
**Fichier**: `native_websocket_handler.py`

Implémenté système de queue pour éviter cross-thread `ws.send()`:

```python
class NativeWebSocketHandler:
    def __init__(self):
        self.message_queues = {}  # {br_id: Queue()}

    def handle_connection(self, ws, environ):
        br_id = parse_br_id(environ)
        self.message_queues[br_id] = queue.Queue()

        while True:
            # Process outgoing queue
            self._process_outgoing_queue(br_id, ws)

            # Receive incoming
            message = ws.receive()
            self.handle_message(br_id, message, ws)

    def _process_outgoing_queue(self, br_id, ws):
        queue = self.message_queues[br_id]
        while not queue.empty():
            message = queue.get_nowait()
            ws.send(message)

    def send_scan_node_command(self, br_id, target_ipv6, node_name, request_id):
        scan_msg = {
            'command': 'scan_node',
            'target_ipv6': target_ipv6,
            'node_name': node_name,
            'request_id': request_id
        }

        # Enqueue for thread-safe sending
        message = json.dumps(scan_msg)
        self.message_queues[br_id].put(message)
        logger.info(f"🔍 Scan command enqueued for BR {br_id}, node {node_name}")
```

---

## Code de l'Orchestrateur (server.py)

```python
def scan_all_nodes_via_brs():
    """
    Scan orchestré de tous les nodes via les Border Routers
    """
    global network_topology_data
    logger.info("🔍 Démarrage du scan orchestré de tous les nodes...")

    try:
        # 1. Charger liste nodes depuis config/adresses.json
        nodes_to_scan = {}
        try:
            with open(ADDRESSES_FILE, 'r') as f:
                data = json.load(f)
                nodes = data.get('nodes', {})
                for node_name, node_data in nodes.items():
                    if isinstance(node_data, dict):
                        ipv6 = node_data.get('address')
                        if ipv6:
                            nodes_to_scan[node_name] = ipv6
                    else:
                        nodes_to_scan[node_name] = node_data
        except Exception as e:
            logger.error(f"❌ Erreur chargement adresses: {e}")
            return {'success': False, 'error': str(e)}

        if not nodes_to_scan:
            logger.warning("⚠️ Aucun node trouvé")
            return {'success': False, 'error': 'No nodes found'}

        logger.info(f"📋 Nodes à scanner: {len(nodes_to_scan)}")
        for node_name, ipv6 in nodes_to_scan.items():
            logger.info(f"   • {node_name} @ {ipv6}")

        # 2. Pour chaque node, envoyer commande scan au BR
        scan_requests = []
        for node_name, ipv6 in nodes_to_scan.items():
            # Trouver le BR pour ce node
            br_id = border_router_manager.get_br_for_node(node_name)

            if not br_id:
                logger.warning(f"⚠️ Aucun BR pour {node_name}, tentative BR disponible")
                active_brs = border_router_manager.get_active_border_routers()
                if active_brs:
                    br_id = active_brs[0]['br_id']
                else:
                    logger.error(f"❌ Aucun BR actif pour {node_name}")
                    continue

            # Envoyer commande scan_node via WebSocket
            request_id = str(uuid.uuid4())
            success = native_ws_handler.send_scan_node_command(
                br_id=br_id,
                target_ipv6=ipv6,
                node_name=node_name,
                request_id=request_id
            )

            if success:
                scan_requests.append({
                    'node_name': node_name,
                    'request_id': request_id,
                    'br_id': br_id,
                    'status': 'pending'
                })

        logger.info(f"📤 {len(scan_requests)} commandes de scan envoyées")

        # TODO: Attendre réponses et agréger
        return {
            'success': True,
            'nodes_scanned': len(scan_requests),
            'scan_requests': scan_requests
        }

    except Exception as e:
        logger.error(f"❌ Erreur scan: {e}")
        return {'success': False, 'error': str(e)}
```

---

## Observations

### 1. Logs Manquants
Je ne vois **JAMAIS** ce log apparaître:
```python
logger.info(f"🔍 Scan command enqueued for BR {br_id}, node {node_name}")
```

Cela signifie que soit:
- `send_scan_node_command()` n'est pas appelée
- Elle retourne False avant le `queue.put()`
- Une exception silencieuse se produit

### 2. Messages "0 bytes"
Le BR continue de recevoir des messages vides, ce qui suggère que:
- Le WebSocket handler appelle `ws.send()` avec une string vide
- Ou `_process_outgoing_queue()` envoie des messages malformés

### 3. Aucune Erreur Python
Aucun traceback ou erreur visible dans les logs Python, ce qui rend le debug difficile.

---

## Questions Spécifiques

### Question 1: Pourquoi send_scan_node_command() ne log-t-il pas?

Le code devrait logger **avant** de retourner:

```python
def send_scan_node_command(self, br_id, target_ipv6, node_name, request_id):
    # Checks...
    if br_id not in self.active_connections:
        logger.error(f"❌ Cannot send scan command to BR {br_id}: not connected")
        return False

    if br_id not in self.message_queues:
        logger.error(f"❌ No message queue for BR {br_id}")
        return False

    # Build message
    scan_msg = {...}

    try:
        message = json.dumps(scan_msg)
        self.message_queues[br_id].put(message)
        logger.info(f"🔍 Scan command enqueued...")  # ← CE LOG N'APPARAIT PAS!
        return True
    except Exception as e:
        logger.error(f"❌ Failed to enqueue: {e}")
        return False
```

**Hypothèses:**
- A. La fonction retourne False aux checks (mais devrait logger l'erreur)
- B. Exception dans `json.dumps()` ou `queue.put()` (mais devrait être catchée)
- C. La fonction n'est jamais appelée du tout

Comment puis-je debugger pourquoi ce log n'apparaît jamais?

### Question 2: Comment le BR peut-il recevoir "0 bytes"?

Si aucun message n'est enqueued (pas de logs), comment le BR peut-il recevoir quoi que ce soit?

Possibilités:
- D'autres messages sont enqueued ailleurs dans le code?
- Heartbeat ACK vides?
- Un autre thread envoie des messages vides?

### Question 3: Architecture Alternative?

Devrais-je:
- A. Ajouter plus de logging à TOUS les niveaux (entry/exit de chaque fonction)
- B. Utiliser asyncio au lieu de threading + queue
- C. Remplacer Flask-Sock par une autre librairie
- D. Introduire Redis comme message broker

### Question 4: Comment forcer le flush de la queue?

Actuellement, `_process_outgoing_queue()` est appelé avant chaque `ws.receive()`.

Mais si `ws.receive()` bloque longtemps, les messages restent en queue sans être envoyés.

Devrais-je:
- Utiliser `ws.receive(timeout=0.1)` pour forcer un flush régulier?
- Créer un thread dédié qui flush la queue toutes les 100ms?

---

## Code Complet Disponible

Tous les fichiers sont disponibles dans le repository:

**Python:**
- `server.py` (ligne ~2800: scan_all_nodes_via_brs)
- `lib/native_websocket_handler.py` (classe NativeWebSocketHandler)
- `lib/border_router_manager.py`

**C (ESP32 BR):**
- `cloud_websocket_client.c` (handle_server_command)
- `coap_proxy.c` (coap_proxy_scan_node)

**Architecture complète:**
- `ARCHITECTURE_NETWORK_SCAN.md` (1400+ lignes)

---

## Ce que Je Cherche

1. **Diagnostic**: Pourquoi les logs de `send_scan_node_command()` n'apparaissent-ils jamais?

2. **Solution immédiate**: Comment faire en sorte que les messages scan_node arrivent au BR avec le bon contenu (non vide)?

3. **Architecture**: Y a-t-il un pattern plus robuste pour ce use case (orchestration de commandes asynchrones via WebSocket depuis HTTP endpoint)?

4. **Debug strategy**: Quelle approche recommandez-vous pour tracer le problème?

---

## Attentes de Réponse

Merci de:
1. Identifier la cause probable du problème
2. Suggérer du code de debug à ajouter
3. Proposer une solution de fix
4. Évaluer si l'architecture actuelle est viable ou s'il faut une refonte

**Contexte complet disponible dans `ARCHITECTURE_NETWORK_SCAN.md`**.
