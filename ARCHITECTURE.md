# Architecture du Serveur CoAP LuxNavix

## Vue d'ensemble

Ce document décrit l'architecture refactorisée du serveur CoAP LuxNavix. Le refactoring a permis de réduire la taille du fichier `server.py` de ~3500 lignes en extrayant les responsabilités dans des modules dédiés.

## Structure des répertoires

```
coapserver/
├── server.py                    # Point d'entrée principal (Flask app + CoAPServer)
├── lib/                         # Modules refactorisés
│   ├── coap/                    # Protocole CoAP
│   │   ├── __init__.py
│   │   ├── protocol.py          # Parse/création paquets CoAP
│   │   └── client.py            # Client CoAP
│   ├── tracking/                # Tracking et triangulation
│   │   ├── __init__.py
│   │   └── badge_tracker.py     # Suivi séquence badges BLE
│   ├── registry.py              # Registre des nodes ESP32
│   ├── thingsboard_client.py    # Client ThingsBoard (REST + WS)
│   ├── thingsboard_loc_tracker.py  # Tracking localisation ThingsBoard
│   ├── audio_library.py         # Catalogue audio (354 fichiers)
│   ├── ot_network_mapper.py     # Scanner réseau OpenThread
│   ├── network_topology.py      # Modèle topologie réseau
│   └── ...                      # Autres modules existants
└── config/
    └── adresses.json            # Configuration nodes
```

## Modules refactorisés

### 1. lib/coap/protocol.py

**Responsabilité**: Manipulation du protocole CoAP (RFC 7252)

**Fonctions exportées**:
- `parse_coap_packet(data)` - Parse un paquet CoAP brut
- `create_coap_response(message_id, code)` - Crée une réponse ACK
- `create_coap_post_packet(uri_path, payload)` - Crée un paquet POST

**Usage**:
```python
from lib.coap.protocol import parse_coap_packet, create_coap_response

packet = parse_coap_packet(raw_data)
response = create_coap_response(packet['message_id'])
```

### 2. lib/coap/client.py

**Responsabilité**: Client CoAP pour communication avec les nodes ESP32

**Classe**: `CoAPClient`

**Méthodes**:
- `send_post(address, uri_path, payload, verbose=True)` - Envoie un POST CoAP

**Usage**:
```python
from lib.coap.client import CoAPClient

client = CoAPClient()
success = client.send_post("fd78::1234", "led", "red:on")
```

### 3. lib/registry.py

**Responsabilité**: Gestion du registre des nodes ESP32

**Classe**: `NodeRegistry`

**Méthodes principales**:
- `load()` / `save()` - Persistance JSON
- `get_all_addresses()` - Liste des adresses IPv6
- `get_node_by_address(address)` - Recherche par adresse
- `get_nodes_sorted_by_order()` - Tri par ordre de routage
- `get_connected_nodes(node_name)` - Liste des nodes connexes

**Format de données** (config/adresses.json):
```json
{
  "nodes": {
    "n01": {
      "address": "fd78:8e78:3bfe:1::1234",
      "ordre": 1,
      "connexes": ["n02", "n03"]
    }
  }
}
```

### 4. lib/tracking/badge_tracker.py

**Responsabilité**: Tracking qualité des badges BLE (séquence po1→po2→...→po9→po0)

**Classe**: `BadgeTracker`

**Méthodes**:
- `check_sequence(new_code, timestamp)` - Vérifie continuité séquence
- `_calculate_gap(old_code, new_code)` - Calcule frames manquées
- `get_stats()` - Statistiques (taux succès, runtime, frames perdues)

**Usage**:
```python
from lib.tracking import BadgeTracker

tracker = BadgeTracker(addr="AA:BB:CC:DD:EE:FF")
is_valid, gap = tracker.check_sequence("po5", time.time())
stats = tracker.get_stats()  # {'success_rate': 98.5, 'missed': 12, ...}
```

### 5. lib/thingsboard_client.py

**Responsabilité**: Client ThingsBoard pour télémétrie et tracking localisation

**Classe**: `ThingsBoardClient`

**Paramètres constructeur**:
- `tb_config` - Dict avec url, username, password
- `socketio` - Instance Flask-SocketIO pour événements temps réel
- `on_telemetry_update` - Callback pour mises à jour télémétrie
- `on_location_change` - Callback pour changement de zone

**Méthodes principales**:
- `connect()` - Connexion REST + WebSocket
- `disconnect()` - Déconnexion propre
- `send_battery_telemetry(node_name, voltage, percentage)` - Envoie télémétrie batterie
- `refresh_asset_cache()` - Cache des assets/devices
- `_handle_loc_update(...)` - Handler WebSocket pour loc_code

**Architecture**:
- REST API: Authentification, requêtes CRUD
- WebSocket: Réception temps réel des `loc_code` via `ThingsBoardLocTracker`
- Token JWT: Auto-renouvellement (durée vie 15 min)

**Usage**:
```python
from lib.thingsboard_client import ThingsBoardClient

tb = ThingsBoardClient(
    tb_config={'url': '...', 'username': '...', 'password': '...'},
    socketio=socketio,
    on_telemetry_update=handle_telemetry,
    on_location_change=handle_location
)
tb.connect()
tb.send_battery_telemetry("n01", 3.7, 85)
```

## Modifications dans server.py

### Imports ajoutés (lignes 59-64)

```python
from lib.registry import NodeRegistry
from lib.thingsboard_client import ThingsBoardClient
from lib.tracking.badge_tracker import BadgeTracker
from lib.coap.client import CoAPClient
from lib.coap.protocol import parse_coap_packet, create_coap_response, create_coap_post_packet
```

### CoAPServer.__init__ modifié (ligne 154)

Le constructeur accepte maintenant des paramètres optionnels pour l'injection de dépendances:

```python
def __init__(self, socketio_instance=None, tb_config=None):
    # Fallback vers les globales si non fournis
    sio = socketio_instance if socketio_instance is not None else socketio
    tb_cfg = tb_config if tb_config is not None else TB_CONFIG

    self.thingsboard = ThingsBoardClient(
        tb_config=tb_cfg,
        socketio=sio,
        on_telemetry_update=self.handle_tb_telemetry_update,
        on_location_change=self.handle_location_change
    )
```

### Appels méthodes CoAP mis à jour

Les appels aux méthodes CoAP ont été modifiés pour utiliser les fonctions importées:

```python
# Avant
packet = self.parse_coap_packet(data)
response = self.create_coap_response(packet['message_id'])

# Après
packet = parse_coap_packet(data)
response = create_coap_response(packet['message_id'])
```

## Gains du refactoring

### Réduction de taille

- **server.py**: ~3500 lignes → ~2800 lignes (**-700 lignes**, -20%)
- Classes extraites: ~500 lignes réparties dans les modules

### Amélioration maintenabilité

✅ **Séparation des responsabilités**: Chaque module a un rôle clair
✅ **Testabilité**: Modules indépendants plus faciles à tester
✅ **Réutilisabilité**: CoAPClient, NodeRegistry utilisables ailleurs
✅ **Lisibilité**: Code organisé par domaine fonctionnel

### Conservation compatibilité

✅ **Pas de breaking changes**: Le code client reste identique
✅ **Fallback vers globales**: Injection optionnelle des dépendances
✅ **Tests non impactés**: Interface publique inchangée

## Diagramme de dépendances

```
server.py (Flask app + CoAPServer)
    │
    ├─→ lib/coap/protocol.py (parse/create CoAP packets)
    ├─→ lib/coap/client.py → protocol.py
    ├─→ lib/registry.py (NodeRegistry)
    ├─→ lib/thingsboard_client.py
    │       ├─→ lib/thingsboard_loc_tracker.py (WebSocket)
    │       └─→ SocketIO (événements temps réel)
    └─→ lib/tracking/badge_tracker.py (BadgeTracker)
```

## Prochaines étapes (optionnel)

### Refactoring phase 2 (non critique)

Si nécessaire, d'autres modules peuvent être extraits:

1. **lib/events/** - Handlers événements (boutons, batterie, BLE)
2. **lib/led/** - Contrôleur LED
3. **lib/web/** - Routes Flask et API REST
4. **lib/cli/** - Commandes CLI

Cependant, ces extractions sont optionnelles car:
- server.py est maintenant ~2800 lignes (taille gérable)
- Les modules extraits couvrent les duplications principales
- Les autres fonctionnalités sont bien organisées dans server.py

## Tests

Pour valider le refactoring:

```bash
# Tester imports
python3 -c "from lib.registry import NodeRegistry; print('✓ NodeRegistry')"
python3 -c "from lib.thingsboard_client import ThingsBoardClient; print('✓ ThingsBoardClient')"
python3 -c "from lib.tracking import BadgeTracker; print('✓ BadgeTracker')"
python3 -c "from lib.coap.protocol import parse_coap_packet; print('✓ CoAP protocol')"
python3 -c "from lib.coap.client import CoAPClient; print('✓ CoAPClient')"

# Lancer le serveur
python3 server.py
```

## Notes techniques

### Thread-safety

- `NodeRegistry`: Utilise `threading.Lock()` pour les opérations critiques
- `ThingsBoardClient`: WebSocket géré dans thread séparé

### Gestion erreurs

- Tous les modules utilisent try/except avec logs clairs
- ThingsBoard: Auto-reconnexion en cas d'expiration token
- CoAP: Gestion timeout et erreurs réseau

### Configuration

- Variables d'environnement: `TB_URL`, `TB_USERNAME`, `TB_PASSWORD`
- Fichier config: `config/adresses.json` (NodeRegistry)
- Constantes: `COAP_PORT=5683`, `BR_HEARTBEAT_TIMEOUT`, etc.

---

**Version**: 1.0
**Date**: 2025-10-16
**Auteur**: Refactoring automatisé Claude Code
