# CLAUDE.md - Contexte d'exécution du serveur CoAP

Ce fichier fournit le contexte complet pour Claude Code lors du travail sur ce projet.

---

## Vue d'ensemble du projet

**LuxNavix CoAP Server** est un serveur Python Flask qui contrôle un réseau de nodes ESP32-C6 via le protocole CoAP sur OpenThread (réseau mesh IPv6). Le système gère la lecture de messages audio et de musique sur les nodes, le monitoring du réseau Thread, et le tracking de localisation via beacons BLE.

---

## Architecture système

### Stack technologique

```
┌─────────────────────────────────────────────────────────┐
│  Interface Web (Flask + SocketIO)                       │
│  - HTML/CSS/JS responsive                               │
│  - WebSocket temps réel                                 │
│  - Contrôle audio, topologie, beacons                   │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTP/WebSocket
┌──────────────────▼──────────────────────────────────────┐
│  Serveur Python (server.py)                             │
│  - Flask app (port 5001)                                │
│  - CoAP client UDP (port 5683)                          │
│  - Scan réseau Thread (parallélisé)                     │
│  - Gestion catalogue audio (354 fichiers)               │
└──────────────────┬──────────────────────────────────────┘
                   │ CoAP/IPv6
┌──────────────────▼──────────────────────────────────────┐
│  Réseau OpenThread (mesh IPv6)                          │
│  - Border Router (ESP32-C6)                             │
│  - Nodes ESP32-C6 (lecteurs audio)                      │
│  - Préfixe: fd78:8e78:3bfe:1::/64                       │
└─────────────────────────────────────────────────────────┘
```

### Composants principaux

1. **server.py** (2867 lignes)
   - Serveur Flask avec SocketIO
   - Client CoAP pour communication ESP32
   - Scanner réseau OpenThread (parallélisé)
   - Endpoints API REST
   - Gestion WebSocket temps réel

2. **lib/audio_library.py**
   - Catalogue de 354 fichiers audio (259 vocaux + 95 musicaux)
   - Recherche full-text
   - Gestion des catégories et albums
   - API pour interface web

3. **lib/ot_network_mapper.py**
   - Scan réseau Thread en parallèle (asyncio.gather)
   - Interrogation CoAP des nodes
   - Construction topologie réseau
   - Optimisé : 6 nodes en 2s au lieu de 12s

4. **lib/network_topology.py**
   - Modèle de données réseau Thread
   - Relations parent/enfant, leader/router
   - Export Graphviz, JSON
   - Calcul distances en sauts

5. **lib/thingsboard_loc_tracker.py**
   - Client WebSocket ThingsBoard
   - Réception télémétrie temps réel
   - Tracking de localisation
   - Optionnel (non critique)

---

## Catalogue audio

### Structure des fichiers

**Total : 354 fichiers audio**

**Messages vocaux (259 fichiers) :**
- Catégorie 1 : Alertes PTI & Urgences (16)
- Catégorie 2 : Sécurité & Évacuation (19)
- Catégorie 3 : Navigation Indoor (56)
- Catégorie 4 : Opérations Techniques (54)
- Catégorie 5 : Temps & Unités (57)
- Catégorie 6 : Instructions & Consignes (41)
- Catégorie 7 : Système & Statut (16)

**Pistes musicales (95 fichiers) - 5 albums :**
- Album 1 : Aphex Twin - Drukqs (30 morceaux, ID 260-289)
- Album 2 : Gotan Project - La Revancha del Tango (11, ID 290-300)
- Album 3 : Moby - Essentials (24, ID 301-324)
- Album 4 : Thievery Corporation - Mirror Conspiracy (15, ID 325-339)
- Album 5 : Red Hot Chili Peppers - Californication (15, ID 340-354)

### Système de nommage FAT32

**Sur la carte SD (noms courts FAT32) :**
```
/sdcard/audio/
├── d-001/           # alertes_pti
│   ├── f-001.wav    # Message 1
│   ├── f-002.wav
│   └── ...
├── d-004/           # music
│   ├── d-011/       # Aphex Twin
│   │   ├── t-001.wav
│   │   └── ...
│   ├── d-015/       # Californication
│   │   ├── t-001.wav    # "01 - Around the World"
│   │   ├── t-002.wav    # "02 - Parallel Universe"
│   │   └── ...
```

**Mapping Python (noms lisibles) :**
- `data/audio_catalog_esp32.json` : Catalogue complet pour serveur
- `data/audio_mapping.json` : Table de correspondance carte SD
- `data/music_tracks_mapping.json` : Noms de morceaux musicaux

**Exemple :**
```json
// audio_catalog_esp32.json
{
  "id": 341,
  "description": "02 - Parallel Universe",
  "path": "/sdcard/audio/d-004/d-015/t-002.wav",
  "category": "[1999] Californication"
}
```

### Flux de lecture audio

```
1. User clique "▶️ Jouer" sur interface web
2. JavaScript envoie POST /api/audio/play {"node": "n01", "message_id": 341}
3. Serveur Python lit audio_catalog_esp32.json
4. Serveur envoie CoAP "play:341" au node n01
5. ESP32 lit son catalogue C (audio_catalog.c)
6. ESP32 ouvre /sdcard/audio/d-004/d-015/t-002.wav
7. ESP32 decode WAV/MP3 et envoie vers I2S (MAX98357A)
8. 🔊 Audio joué sur le haut-parleur
```

---

## Protocole CoAP

### Format des messages

**Requêtes envoyées aux ESP32 :**
```
play:341         # Jouer le message ID 341
stop             # Arrêter la lecture
volume:75        # Régler le volume à 75%
led:on           # Allumer la LED
led:off          # Éteindre la LED
battery          # Demander l'état de la batterie
```

**Ressources CoAP des ESP32 :**
- `/button` : Événements bouton (POST)
- `/battery` : État batterie (POST automatique toutes les 60s)
- `/led` : Contrôle LED (GET/PUT)
- `/network-info` : Informations réseau Thread (GET)

### Structure paquet CoAP

```python
# Header CoAP GET NON-confirmable
header = struct.pack('!BBH',
    0x50,  # Version=1, Type=NON (1), Token Length=0
    0x01,  # Code=GET (0.01)
    message_id)

# Option Uri-Path (Delta=11)
option = bytes([0xB0 + len(uri)]) + uri.encode('utf-8')

packet = header + option
```

---

## Réseau OpenThread

### Topologie type

```
┌─────────────────┐
│  Border Router  │ (ESP32-C6, RLOC16: 0x0400)
│   (Leader)      │ - Connecté Ethernet/WiFi
│  fd78:...::1    │ - Routage IPv6 vers Internet
└────────┬────────┘
         │ Thread mesh
    ┌────┴────┬────────┬────────┐
    │         │        │        │
┌───▼───┐ ┌──▼──┐  ┌──▼──┐  ┌──▼──┐
│Router │ │Router│ │Router│ │Child│
│ n01   │ │ n02  │ │ n03  │ │ n04 │
└───────┘ └──────┘ └──────┘ └─────┘
```

### Scan réseau (optimisé)

**Problème initial :** Scan séquentiel = 6 nodes × 2s = 12 secondes

**Solution :** Scan parallèle avec `asyncio.gather()`

```python
# lib/ot_network_mapper.py (ligne 173-195)
async def scan_known_addresses(self) -> Set[str]:
    # Créer toutes les tâches en parallèle
    tasks = [self.query_node(ipv6) for ipv6 in self.known_addresses]

    # Exécuter toutes les requêtes simultanément
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Traiter les résultats
    for ipv6, result in zip(self.known_addresses, results):
        if result:
            discovered.add(ipv6)
```

**Résultat :** 6 nodes scannés en 2 secondes (timeout max) au lieu de 12s

### Format network-info

```json
{
  "role": "leader",
  "rloc16": "0x0400",
  "ext_addr": "0a:1b:2c:3d:4e:5f:6a:7b",
  "network_name": "LuxNavix",
  "partition_id": "0x12345678",
  "parent": "0xfffe",
  "children": ["0x0401", "0x0402"],
  "neighbors": ["0x0800", "0x0c00"]
}
```

---

## Configuration

### Fichiers de configuration

**config/adresses.json** - Nodes connus du réseau
```json
{
  "nodes": {
    "n01": {
      "address": "fd78:8e78:3bfe:1:xxxx:xxxx:xxxx:xxxx",
      "role": "leader"
    },
    "n02": {
      "address": "fd78:8e78:3bfe:1:yyyy:yyyy:yyyy:yyyy",
      "role": "router"
    }
  }
}
```

**.env** - Variables d'environnement (créer depuis .env.example)
```bash
# ThingsBoard (optionnel)
TB_URL=https://platform.tamtamdeals.com
TB_USERNAME=user@example.com
TB_PASSWORD=your_password

# Serveur
COAP_PORT=5683
WEB_PORT=5001
SECRET_KEY=your-secret-key-here

# Debug
DEBUG=False
LOG_LEVEL=INFO
```

---

## API REST

### Endpoints audio

**POST /api/audio/play** - Lire un message
```json
{
  "node": "n01",
  "message_id": 341
}
```

**POST /api/audio/stop** - Arrêter la lecture
```json
{
  "node": "n01"
}
```

**POST /api/audio/volume** - Régler le volume
```json
{
  "node": "n01",
  "volume": 75
}
```

**GET /api/audio/catalog** - Obtenir le catalogue complet
```json
{
  "success": true,
  "statistics": {
    "total_messages": 354,
    "categories_count": 12
  },
  "categories": {
    "[1999] Californication": {
      "description": "🎵 Album musical",
      "count": 15,
      "messages": [...]
    }
  }
}
```

**GET /api/audio/search?q=évacuation** - Rechercher des messages
```json
{
  "success": true,
  "count": 19,
  "results": [...]
}
```

### Endpoints réseau

**GET /api/topology** - Topologie du réseau
```json
{
  "nodes": [
    {
      "name": "n01",
      "rloc16": "0x0400",
      "role": "leader",
      "ext_addr": "...",
      "ipv6": "fd78:8e78:3bfe:1:...",
      "children": [...],
      "neighbors": [...]
    }
  ],
  "statistics": {
    "total_nodes": 4,
    "leaders": 1,
    "routers": 2,
    "children": 1
  }
}
```

**POST /api/topology/refresh** - Forcer un refresh du scan

---

## Interface web

### Pages disponibles

1. **/** (index.html) - Dashboard principal
   - Vue d'ensemble du système
   - Statistiques réseau
   - Liens vers toutes les interfaces

2. **/audio-library** (audio_library.html) - Bibliothèque audio
   - 20 messages instantanés (accès rapide)
   - 7 catégories vocales
   - 5 albums musicaux
   - Recherche full-text
   - Contrôle volume
   - Bouton stop

3. **/network-map** (network_map.html) - Carte réseau
   - Visualisation graphique de la topologie
   - Relations parent/enfant
   - Statut des nodes
   - Métriques réseau

4. **/beacons** (beacons.html) - Tracking beacons BLE
   - Liste des badges détectés
   - Historique des positions
   - Analyse RSSI

5. **/ble-debug** (ble_debug.html) - Debug BLE
   - Scan en direct
   - Logs des trames
   - Statistiques

6. **/audio-control** (audio_control.html) - Contrôle audio simple
   - Interface compacte
   - Lecture rapide

7. **/devices** (devices.html) - Gestion devices
   - Configuration nodes
   - Paramètres réseau

### Composants JavaScript

**static/audio_library.js** - Gestion interface audio
```javascript
// Fonctions principales
loadNodes()              // Charge la liste des nodes
loadInstantMessages()    // Charge les 20 messages prioritaires
loadCategories()         // Charge toutes les catégories (filtre albums)
loadMusicAlbums()        // Charge les albums musicaux séparément
playMessage(button)      // Joue un message
playAlbum(albumKey)      // Joue un album complet
toggleAlbumTracks()      // Affiche/cache la liste des morceaux
stopAudio()              // Arrête la lecture
setupVolumeControl()     // Gestion du slider volume
setupSearch()            // Recherche avec debounce 300ms
connectWebSocket()       // WebSocket temps réel
```

**WebSocket events :**
```javascript
socket.on('audio_playback', (data) => {
  updateStatus(`▶️ ${data.node}: ${data.description}`);
});

socket.on('topology_update', () => {
  loadNodes();  // Recharger la liste des nodes
});

socket.on('node_update', () => {
  loadNodes();
});
```

---

## Développement

### Structure des modules

```python
# server.py - Serveur principal
class CoAPServer:
    def send_coap_message(node_ipv6: str, command: str)
    def create_coap_request(uri: str) -> bytes
    def parse_coap_response(data: bytes) -> tuple

class NodeRegistry:
    def register_node(name: str, ipv6: str, role: str)
    def get_node(name: str) -> dict
    def get_all_addresses() -> List[str]

# lib/audio_library.py
class AudioLibrary:
    def get_instant_messages(count: int) -> List[Dict]
    def get_category(category: str) -> Dict
    def search(keywords: str) -> List[Dict]
    def get_message_by_id(msg_id: int) -> Optional[Dict]

# lib/ot_network_mapper.py
class OpenThreadScanner:
    async def query_node(ipv6: str) -> dict
    async def scan_known_addresses() -> Set[str]
    async def build_topology()

# lib/network_topology.py
class NetworkTopology:
    def add_node_from_network_info(ipv6: str, info: dict)
    def calculate_hop_distances()
    def export_graphviz(filename: str)
    def to_json() -> str
```

### Ajout d'une nouvelle fonctionnalité

1. **Nouvelle ressource CoAP :**
   - Ajouter endpoint dans `server.py`
   - Implémenter dans firmware ESP32

2. **Nouveau message audio :**
   - Copier fichier WAV sur carte SD (nom court FAT32)
   - Mettre à jour `data/audio_mapping.json`
   - Régénérer catalogue Python : `generate_python_catalog.py`
   - Recompiler firmware ESP32 avec nouveau `audio_catalog.c`

3. **Nouvelle page web :**
   - Créer template HTML dans `templates/`
   - Ajouter route Flask dans `server.py`
   - Créer CSS/JS dans `static/` si nécessaire

### Tests

**Test du serveur :**
```bash
cd /Users/lilianbrun/work/ttd/luxnavix/coapserver
python3 server.py
```

**Test des imports :**
```python
from lib.audio_library import audio_lib
from lib.network_topology import NetworkTopology
from lib.ot_network_mapper import OpenThreadScanner

# Vérifier le catalogue
print(f"Messages: {audio_lib.get_statistics()['total_messages']}")
```

**Test API :**
```bash
# Catalogue
curl http://localhost:5001/api/audio/catalog | jq '.statistics'

# Lecture
curl -X POST http://localhost:5001/api/audio/play \
  -H "Content-Type: application/json" \
  -d '{"node": "n01", "message_id": 341}'

# Stop
curl -X POST http://localhost:5001/api/audio/stop \
  -H "Content-Type: application/json" \
  -d '{"node": "n01"}'
```

---

## Dépannage

### Le serveur ne démarre pas

**Erreur : "Address already in use"**
```bash
# Trouver le processus sur le port 5001
lsof -i :5001
# Tuer le processus
kill -9 <PID>
```

**Erreur : "No module named 'lib.audio_library'"**
```bash
# Vérifier que vous êtes dans le bon répertoire
pwd  # Doit afficher: /Users/lilianbrun/work/ttd/luxnavix/coapserver

# Vérifier que lib/ existe
ls -la lib/
```

**Erreur : "Catalogue non trouvé"**
```bash
# Vérifier que data/ contient les fichiers
ls -la data/
# Doit contenir: audio_catalog_esp32.json, audio_mapping.json, music_tracks_mapping.json
```

### Les nodes ne sont pas détectés

**Vérifier la connectivité IPv6 :**
```bash
# Ping direct
ping6 fd78:8e78:3bfe:1:xxxx:xxxx:xxxx:xxxx

# Vérifier le routage
ip -6 route show
```

**Vérifier adresses.json :**
```bash
cat config/adresses.json
# Les adresses doivent être à jour
```

**Logs du scan :**
```
📋 Scanning adresses connues...
  ✓ Node actif: fd78:8e78:3bfe:1:xxxx
  ⚠️  Node inactif: fd78:8e78:3bfe:1:yyyy  # Normal si node éteint
```

### L'audio ne joue pas

**Vérifier le node :**
```bash
# Envoyer une commande test
curl -X POST http://localhost:5001/api/audio/play \
  -H "Content-Type: application/json" \
  -d '{"node": "n01", "message_id": 1}'

# Observer les logs du serveur
# Doit afficher: "▶️ Lecture sur n01: ..."
```

**Vérifier le firmware ESP32 :**
- Carte SD insérée et montée
- Fichiers audio présents (vérifier avec `ls` sur ESP32)
- I2S configuré correctement
- Amplificateur MAX98357A alimenté

### Scan réseau lent

**Si le scan prend >5 secondes :**
- Vérifier que `lib/ot_network_mapper.py` utilise `asyncio.gather()`
- Réduire le nombre d'adresses dans `config/adresses.json`
- Augmenter le timeout si réseau lent : `SCAN_TIMEOUT = 3`

---

## Performance

### Métriques attendues

- **Démarrage serveur :** ~5 secondes (avec scan réseau)
- **Scan réseau (6 nodes) :** 2 secondes (parallélisé)
- **Latence audio :** <100ms (commande → lecture)
- **Catalogue audio :** 354 messages en <50ms
- **WebSocket :** <10ms latence

### Optimisations appliquées

1. **Scan réseau parallèle :**
   - Avant : 12s (séquentiel)
   - Après : 2s (parallel)
   - Gain : 6x plus rapide

2. **Cache catalogue audio :**
   - Chargement unique au démarrage
   - Recherche en mémoire (pas de lecture fichier)

3. **WebSocket pour temps réel :**
   - Pas de polling HTTP
   - Push instantané des événements

4. **Queue SocketIO :**
   - Buffer 200 événements
   - Thread worker dédié
   - Pas de blocage

---

## Sécurité

### Considérations

1. **Pas d'authentification par défaut**
   - Ajouter Flask-Login pour production
   - Protéger les endpoints sensibles

2. **CoAP non chiffré**
   - Utiliser DTLS pour production
   - Réseau Thread déjà sécurisé (AES-128)

3. **Variables d'environnement**
   - Ne jamais commiter `.env`
   - Utiliser des secrets forts
   - Changer `SECRET_KEY` en production

4. **Injection CoAP**
   - Validation des commandes
   - Whitelist des nodes autorisés

---

## Maintenance

### Logs

**Emplacement :** Console standard output

**Niveaux de log :**
```python
logging.basicConfig(
    level=logging.INFO,  # ou DEBUG pour plus de détails
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

**Messages importants :**
```
✓ Catalogue audio chargé: 354 messages
📋 Scanning adresses connues...
🗺️  Construction de la topologie...
 * Running on http://0.0.0.0:5001
```

### Backup

**Données critiques à sauvegarder :**
- `config/adresses.json` : Configuration nodes
- `data/*.json` : Catalogues audio
- `.env` : Variables d'environnement

**Backup automatique :**
```bash
#!/bin/bash
tar -czf backup-$(date +%Y%m%d).tar.gz config/ data/ .env
```

### Mises à jour

**Catalogue audio :**
```bash
# Après modification des fichiers sur carte SD
python3 generate_python_catalog.py
# Redémarrer le serveur
```

**Dépendances Python :**
```bash
pip install --upgrade -r requirements.txt
```

**Firmware ESP32 :**
- Recompiler avec `idf.py build`
- Flasher avec `idf.py flash`
- Ne pas oublier de mettre à jour `audio_catalog.c`

---

## Historique des versions

### v1.0.0 (2025-10-14)
- ✅ Migration vers projet standalone
- ✅ Scan réseau parallélisé (6x plus rapide)
- ✅ Catalogue audio complet (354 fichiers)
- ✅ Interface web albums musicaux
- ✅ Bouton stop ajouté
- ✅ Documentation complète

### Prochaines fonctionnalités

- [ ] Authentification utilisateur
- [ ] Playlist audio (lecture séquentielle)
- [ ] Mode aléatoire (shuffle)
- [ ] Historique des lectures
- [ ] Export statistiques
- [ ] API GraphQL

---

## Ressources

### Documentation ESP32

- ESP-IDF: https://docs.espressif.com/projects/esp-idf/
- OpenThread: https://openthread.io/
- CoAP: https://datatracker.ietf.org/doc/html/rfc7252

### Python

- Flask: https://flask.palletsprojects.com/
- Flask-SocketIO: https://flask-socketio.readthedocs.io/
- asyncio: https://docs.python.org/3/library/asyncio.html

### Protocoles

- Thread: https://www.threadgroup.org/
- CoAP: https://coap.technology/
- IPv6: https://www.rfc-editor.org/rfc/rfc8200

---

## Contact

**Projet :** LuxNavix CoAP Server
**Auteur :** Lilian Brun
**Email :** lilian@tamtamdeals.com
**Entreprise :** TamTamDeals

---

**Dernière mise à jour :** 2025-10-14
