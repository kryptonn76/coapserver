# Image de base Python optimisée pour la production
FROM python:3.11-slim

# Métadonnées
LABEL maintainer="LuxNavix Team"
LABEL description="LuxNavix CoAP Server - Serveur de contrôle pour réseau ESP32 OpenThread"
LABEL version="1.0"

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Créer un utilisateur non-root pour la sécurité
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Installer les dépendances système nécessaires
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de dépendances
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY --chown=appuser:appuser . .

# Créer les répertoires nécessaires avec les bonnes permissions
RUN mkdir -p config data logs && \
    chown -R appuser:appuser /app

# Passer à l'utilisateur non-root
USER appuser

# Exposer les ports
# Port 5001: Interface web Flask
# Port 5683: Serveur CoAP (UDP)
EXPOSE 5001 5683/udp

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5001/api/health || exit 1

# Commande de démarrage
CMD ["python3", "server.py"]
