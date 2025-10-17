# LuxNavix CoAP Server - Déploiement Docker

Guide complet pour déployer le serveur CoAP LuxNavix en production avec Docker.

---

## Table des matières

1. [Prérequis](#prérequis)
2. [Configuration rapide](#configuration-rapide)
3. [Build et démarrage](#build-et-démarrage)
4. [Configuration avancée](#configuration-avancée)
5. [Commandes utiles](#commandes-utiles)
6. [Monitoring et logs](#monitoring-et-logs)
7. [Troubleshooting](#troubleshooting)
8. [Production et sécurité](#production-et-sécurité)

---

## Prérequis

- **Docker** >= 20.10
- **Docker Compose** >= 2.0
- **Accès réseau** aux ports 5001 (HTTP) et 5683 (CoAP/UDP)

Installation Docker :
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Vérifier l'installation
docker --version
docker compose version
```

---

## Configuration rapide

### 1. Créer le fichier d'environnement

```bash
cp .env.example .env
```

### 2. Éditer `.env` avec vos valeurs

**Variables obligatoires :**
```bash
# ThingsBoard
TB_URL=https://platform.tamtamdeals.com
TB_USERNAME=votre@email.com
TB_PASSWORD=votre_mot_de_passe

# Border Router WebSocket
USE_WEBSOCKET_BR=true
BR_AUTH_ENABLED=true
```

**Variables optionnelles (VPN/WireGuard) :**
```bash
WG_ENDPOINT=votre.ip.publique:51820
WG_BORDER_ROUTER_PUBLIC_KEY=votre_clé_publique_br
WG_SERVER_PRIVATE_KEY=votre_clé_privée_serveur
```

### 3. Générer une clé secrète sécurisée

```bash
python3 -c "import secrets; print(f'SECRET_KEY={secrets.token_hex(32)}')" >> .env
```

---

## Build et démarrage

### Démarrage rapide avec Docker Compose

```bash
# Build de l'image
docker compose build

# Démarrer le serveur
docker compose up -d

# Vérifier le statut
docker compose ps
```

### Build manuel (sans Docker Compose)

```bash
# Build de l'image
docker build -t luxnavix/coap-server:latest .

# Lancer le conteneur
docker run -d \
  --name luxnavix-coap-server \
  --restart unless-stopped \
  -p 5001:5001 \
  -p 5683:5683/udp \
  --env-file .env \
  -v $(pwd)/config:/app/config:rw \
  -v $(pwd)/data:/app/data:rw \
  -v $(pwd)/logs:/app/logs:rw \
  luxnavix/coap-server:latest
```

---

## Configuration avancée

### Volumes Docker

Les volumes suivants sont montés pour la persistance :

| Volume local | Volume container | Usage |
|--------------|------------------|-------|
| `./config` | `/app/config` | Configurations JSON (addresses, border routers) |
| `./data` | `/app/data` | Données audio, mappings |
| `./logs` | `/app/logs` | Logs applicatifs |

### Limites de ressources

Par défaut, le conteneur utilise :
- **CPU** : Max 1 core, Min 0.5 core
- **RAM** : Max 512MB, Min 256MB

Pour modifier ces limites, éditez `docker-compose.yml` :

```yaml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 1G
    reservations:
      cpus: '1.0'
      memory: 512M
```

### Réseau et ports

**Ports exposés :**
- `5001/tcp` : Interface web Flask (API REST, WebSocket)
- `5683/udp` : Serveur CoAP

Pour changer les ports externes :
```yaml
ports:
  - "8080:5001"      # Interface web sur port 8080
  - "6683:5683/udp"  # CoAP sur port 6683
```

---

## Commandes utiles

### Gestion du conteneur

```bash
# Voir les logs en temps réel
docker compose logs -f

# Voir uniquement les logs récents
docker compose logs --tail=100

# Redémarrer le serveur
docker compose restart

# Arrêter le serveur
docker compose stop

# Arrêter et supprimer le conteneur
docker compose down

# Rebuild complet (après modification du code)
docker compose down
docker compose build --no-cache
docker compose up -d
```

### Inspection et debug

```bash
# Entrer dans le conteneur (shell)
docker compose exec coap-server sh

# Vérifier les variables d'environnement
docker compose exec coap-server env

# Vérifier l'état du health check
docker inspect --format='{{.State.Health.Status}}' luxnavix-coap-server

# Voir les processus en cours
docker compose top

# Voir les stats de ressources
docker stats luxnavix-coap-server
```

### Nettoyage

```bash
# Supprimer le conteneur et les volumes
docker compose down -v

# Nettoyer les images inutilisées
docker image prune -a

# Nettoyage complet du système Docker
docker system prune -a --volumes
```

---

## Monitoring et logs

### Health check

Le conteneur effectue automatiquement un health check toutes les 30 secondes.

**⚠️ Note importante :** Le health check vérifie l'endpoint `/api/health`. Si ce endpoint n'existe pas dans votre application, vous devez soit :

1. Ajouter cet endpoint dans `server.py` :
```python
@app.route('/api/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': time.time()}), 200
```

2. OU modifier le health check dans le `Dockerfile` et `docker-compose.yml` :
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:5001/"]
```

### Logs structurés

```bash
# Logs avec timestamps
docker compose logs -f --timestamps

# Logs d'un service spécifique
docker compose logs -f coap-server

# Sauvegarder les logs dans un fichier
docker compose logs > logs/docker-$(date +%Y%m%d).log
```

### Surveillance des ressources

```bash
# Monitoring en temps réel
docker stats luxnavix-coap-server --no-stream

# Utilisation disque
docker system df
```

---

## Troubleshooting

### Le conteneur ne démarre pas

```bash
# Vérifier les logs d'erreur
docker compose logs coap-server

# Vérifier la configuration
docker compose config

# Valider le .env
cat .env
```

### Problèmes de permissions

Si vous avez des erreurs de permissions sur les volumes :

```bash
# Donner les permissions
chmod -R 755 config data logs

# OU créer les dossiers avec les bonnes permissions
mkdir -p config data logs
```

### Le serveur est inaccessible

```bash
# Vérifier que les ports sont bien exposés
docker compose ps

# Vérifier les connexions réseau
docker compose exec coap-server netstat -tuln

# Tester l'interface web
curl http://localhost:5001/
```

### Problèmes de connectivité CoAP (UDP)

```bash
# Vérifier le port UDP
sudo netstat -uln | grep 5683

# Tester avec un client CoAP externe
# (installer coap-client: apt install libcoap2-bin)
coap-client -m get coap://localhost:5683/
```

---

## Production et sécurité

### Checklist de sécurité

- [ ] **SECRET_KEY** généré avec `secrets.token_hex(32)` (≠ valeur par défaut)
- [ ] **Credentials ThingsBoard** dans `.env` (pas dans le code)
- [ ] **Firewall** : Limiter l'accès aux ports 5001 et 5683
- [ ] **HTTPS** : Utiliser un reverse proxy (Nginx, Traefik) pour le port 5001
- [ ] **Monitoring** : Mettre en place des alertes sur le health check
- [ ] **Backups** : Sauvegarder régulièrement `config/` et `data/`

### Reverse proxy avec Nginx

Exemple de configuration Nginx pour exposer l'interface web en HTTPS :

```nginx
server {
    listen 443 ssl http2;
    server_name coap.votredomaine.com;

    ssl_certificate /etc/letsencrypt/live/votredomaine.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/votredomaine.com/privkey.pem;

    location / {
        proxy_pass http://localhost:5001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Sauvegardes automatiques

Script de backup :

```bash
#!/bin/bash
# backup.sh
BACKUP_DIR="/var/backups/luxnavix"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
tar -czf $BACKUP_DIR/coap-server-$DATE.tar.gz \
    config/ data/ .env

# Garder uniquement les 7 derniers backups
find $BACKUP_DIR -name "coap-server-*.tar.gz" -mtime +7 -delete
```

Ajouter dans crontab :
```bash
# Backup quotidien à 3h du matin
0 3 * * * /chemin/vers/backup.sh
```

### Mise à jour en production

```bash
# 1. Sauvegarder
./backup.sh

# 2. Arrêter le conteneur
docker compose stop

# 3. Récupérer la dernière version du code
git pull

# 4. Rebuild
docker compose build --no-cache

# 5. Redémarrer
docker compose up -d

# 6. Vérifier
docker compose logs -f --tail=50
```

---

## Support

Pour les problèmes ou questions :
- Vérifier les logs : `docker compose logs -f`
- Consulter `ARCHITECTURE.md` pour comprendre le système
- Vérifier la configuration dans `CLAUDE.md`

---

**LuxNavix CoAP Server v1.0**
*Containerized for production with Docker*
