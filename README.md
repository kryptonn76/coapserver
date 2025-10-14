# LuxNavix CoAP Server

Serveur CoAP pour contrôler les nodes ESP32 OpenThread avec interface web de gestion.

## Fonctionnalités

- **Contrôle audio** : Lecture de messages vocaux et musiques sur les nodes ESP32
- **Cartographie réseau** : Scan automatique et visualisation de la topologie OpenThread
- **Interface web** : Contrôle et monitoring en temps réel via Flask/SocketIO
- **Intégration ThingsBoard** : Télémétrie et tracking de localisation (optionnel)
- **Gestion des beacons BLE** : Suivi et analyse des badges de localisation

## Architecture

```
coapserver/
├── server.py              # Serveur principal Flask + CoAP
├── config/
│   └── adresses.json     # Configuration des nodes ESP32
├── data/
│   ├── audio_catalog_esp32.json    # Catalogue audio (259 messages + 95 musiques)
│   ├── audio_mapping.json          # Mapping noms courts FAT32
│   └── music_tracks_mapping.json   # Noms des morceaux musicaux
├── lib/
│   ├── audio_library.py           # Gestion de la bibliothèque audio
│   ├── network_topology.py        # Modèle de topologie OpenThread
│   ├── ot_network_mapper.py       # Scanner réseau Thread (parallélisé)
│   └── thingsboard_loc_tracker.py # Client WebSocket ThingsBoard
├── static/
│   ├── audio_library.css
│   └── audio_library.js
└── templates/
    ├── index.html           # Dashboard principal
    ├── audio_library.html   # Interface catalogue audio
    ├── network_map.html     # Carte du réseau Thread
    └── ...
```

## Installation

### Prérequis

- Python 3.8+
- Accès réseau IPv6 au réseau OpenThread

### Installation des dépendances

```bash
# Créer un environnement virtuel
python3 -m venv venv
source venv/bin/activate  # Sur Windows: venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt
```

### Configuration

1. Copier le fichier d'exemple :
```bash
cp .env.example .env
```

2. Éditer `.env` avec vos paramètres :
```bash
# ThingsBoard (optionnel)
TB_URL=https://platform.tamtamdeals.com
TB_USERNAME=your@email.com
TB_PASSWORD=your_password

# Serveur
COAP_PORT=5683
WEB_PORT=5001
```

3. Configurer les adresses des nodes dans `config/adresses.json` :
```json
{
  "nodes": {
    "n01": {
      "address": "fd78:8e78:3bfe:1:xxxx:xxxx:xxxx:xxxx",
      "role": "leader"
    }
  }
}
```

## Utilisation

### Démarrage du serveur

```bash
python3 server.py
```

Le serveur démarre sur :
- **Web interface** : http://localhost:5001
- **CoAP server** : udp://[::]:5683

### Interfaces web disponibles

- `/` : Dashboard principal
- `/audio-library` : Bibliothèque audio (354 fichiers)
- `/network-map` : Carte réseau OpenThread
- `/beacons` : Suivi des beacons BLE
- `/devices` : Gestion des devices

### Catalogue audio

Le système gère **354 fichiers audio** :
- **259 messages vocaux** : Messages PTI, navigation, instructions, etc.
- **95 pistes musicales** : 5 albums (Californication, Drukqs, etc.)

#### Utilisation de l'interface audio

1. Sélectionner un node cible dans le menu déroulant
2. Parcourir les catégories ou utiliser la recherche
3. Cliquer sur "▶️ Jouer" pour lancer un message
4. Ajuster le volume avec le slider
5. Utiliser "⏹️ Stop" pour arrêter la lecture

#### Albums musicaux

Pour les albums, deux options :
- **"▶️ Tout jouer"** : Lance le premier morceau de l'album
- **"📋 Voir morceaux"** : Affiche la liste pour sélection individuelle

## Architecture réseau

### Scan du réseau (parallélisé)

Le scanner interroge tous les nodes en **parallèle** via `asyncio.gather()` :
- Avant : 6 nodes × 2s timeout = **12 secondes**
- Après : timeout max = **2 secondes** ⚡

```python
# Les requêtes CoAP sont envoyées simultanément
tasks = [query_node(ip) for ip in addresses]
results = await asyncio.gather(*tasks)
```

### Protocoles utilisés

- **CoAP** (UDP/IPv6) : Communication avec les ESP32
- **WebSocket** : Temps réel pour l'interface web
- **HTTP/REST** : API ThingsBoard (si activé)

## API

### Endpoints audio

```bash
# Lire un message
POST /api/audio/play
{
  "node": "n01",
  "message_id": 341
}

# Arrêter la lecture
POST /api/audio/stop
{
  "node": "n01"
}

# Ajuster le volume
POST /api/audio/volume
{
  "node": "n01",
  "volume": 75
}

# Obtenir le catalogue
GET /api/audio/catalog
```

### Endpoints réseau

```bash
# Topologie du réseau
GET /api/topology

# Forcer un refresh du scan
POST /api/topology/refresh
```

## Développement

### Structure du code

Le serveur est organisé en modules :
- **CoAPServer** : Gestion des requêtes CoAP vers les ESP32
- **NodeRegistry** : Enregistrement des nodes actifs
- **AudioLibrary** : Catalogue des 354 fichiers audio
- **NetworkTopology** : Modèle du réseau Thread
- **OpenThreadScanner** : Scan parallélisé des nodes

### Ajout d'un nouveau message audio

1. Ajouter le fichier WAV sur la carte SD (nom court FAT32)
2. Mettre à jour `data/audio_mapping.json`
3. Régénérer le catalogue :
```bash
python3 generate_python_catalog.py
```

### Debug

Logs détaillés disponibles dans la console :
```
✓ Catalogue audio chargé: 354 messages
📋 Scanning adresses connues...
  ✓ Node actif: fd78:8e78:3bfe:1:xxxx
🗺️  Construction de la topologie...
 * Running on http://0.0.0.0:5001
```

## Dépannage

### Le serveur ne démarre pas

- Vérifier que le port 5001 n'est pas déjà utilisé
- Installer toutes les dépendances : `pip install -r requirements.txt`

### Les nodes ne sont pas détectés

- Vérifier la connectivité IPv6 : `ping6 fd78:8e78:3bfe:1:xxxx`
- Vérifier `config/adresses.json`
- Augmenter le timeout de scan dans `lib/ot_network_mapper.py`

### L'audio ne joue pas

- Vérifier que le node est bien connecté au réseau Thread
- Vérifier que les fichiers audio existent sur la carte SD
- Vérifier les chemins dans `data/audio_catalog_esp32.json`

## Licence

Propriétaire - LuxNavix / TamTamDeals

## Contact

Pour toute question : lilian@tamtamdeals.com
