#!/usr/bin/env python3
"""
Border Router Authentication Module
Gestion de l'authentification des Border Routers via tokens
"""

import json
import hashlib
import hmac
import time
from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

# Chemin vers le fichier de configuration
BR_CONFIG_FILE = Path(__file__).parent.parent / 'config' / 'border_routers.json'


class BRAuthManager:
    """Gère l'authentification des Border Routers"""

    def __init__(self, config_file: Path = BR_CONFIG_FILE):
        """
        Args:
            config_file: Chemin vers le fichier de configuration des BR
        """
        self.config_file = config_file
        self.config_cache = None
        self.cache_timestamp = 0
        self.cache_ttl = 60  # Recharger la config toutes les 60 secondes

    def load_config(self, force_reload: bool = False) -> Dict:
        """
        Charge la configuration des BR depuis le fichier JSON

        Args:
            force_reload: Force le rechargement même si le cache est valide

        Returns:
            Dict de configuration
        """
        current_time = time.time()

        # Utiliser le cache si valide
        if not force_reload and self.config_cache and (current_time - self.cache_timestamp) < self.cache_ttl:
            return self.config_cache

        try:
            if not self.config_file.exists():
                logger.warning(f"Fichier de configuration BR non trouvé: {self.config_file}")
                return {'border_routers': {}}

            with open(self.config_file, 'r') as f:
                config = json.load(f)

            self.config_cache = config
            self.cache_timestamp = current_time

            logger.debug(f"Configuration BR chargée: {len(config.get('border_routers', {}))} BR définis")
            return config

        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON dans {self.config_file}: {e}")
            return {'border_routers': {}}
        except Exception as e:
            logger.error(f"Erreur chargement config BR: {e}")
            return {'border_routers': {}}

    def verify_br_token(self, br_id: str, token: str) -> bool:
        """
        Vérifie le token d'authentification d'un Border Router

        Args:
            br_id: Identifiant du BR (ex: "BR-001")
            token: Token d'authentification

        Returns:
            True si le token est valide
        """
        if not br_id or not token:
            logger.warning("BR ID ou token manquant")
            return False

        config = self.load_config()
        br_config = config.get('border_routers', {}).get(br_id)

        if not br_config:
            logger.warning(f"Border Router inconnu: {br_id}")
            return False

        expected_token = br_config.get('auth_token')
        if not expected_token:
            logger.error(f"Pas de token configuré pour BR {br_id}")
            return False

        # Comparaison sécurisée (timing attack resistant)
        is_valid = hmac.compare_digest(token, expected_token)

        if is_valid:
            logger.info(f"✅ Authentification réussie pour BR {br_id}")
        else:
            logger.warning(f"❌ Authentification échouée pour BR {br_id}")

        return is_valid

    def get_br_config(self, br_id: str) -> Optional[Dict]:
        """
        Récupère la configuration complète d'un BR

        Args:
            br_id: Identifiant du BR

        Returns:
            Dict de configuration ou None si non trouvé
        """
        config = self.load_config()
        return config.get('border_routers', {}).get(br_id)

    def get_br_nodes(self, br_id: str) -> list:
        """
        Récupère la liste des nodes configurés pour un BR

        Args:
            br_id: Identifiant du BR

        Returns:
            Liste des nodes (peut être vide)
        """
        br_config = self.get_br_config(br_id)
        if br_config:
            return br_config.get('nodes', [])
        return []

    def get_br_network_prefix(self, br_id: str) -> str:
        """
        Récupère le préfixe réseau Thread d'un BR

        Args:
            br_id: Identifiant du BR

        Returns:
            Préfixe réseau (vide si non trouvé)
        """
        br_config = self.get_br_config(br_id)
        if br_config:
            return br_config.get('network_prefix', '')
        return ''

    def get_all_br_ids(self) -> list:
        """
        Récupère la liste de tous les BR configurés

        Returns:
            Liste des br_id
        """
        config = self.load_config()
        return list(config.get('border_routers', {}).keys())

    def is_br_configured(self, br_id: str) -> bool:
        """
        Vérifie si un BR est configuré

        Args:
            br_id: Identifiant du BR

        Returns:
            True si le BR est dans la configuration
        """
        return self.get_br_config(br_id) is not None

    def generate_token(self, length: int = 32) -> str:
        """
        Génère un token aléatoire sécurisé

        Args:
            length: Longueur du token

        Returns:
            Token hex
        """
        import secrets
        return secrets.token_hex(length)

    def add_br_to_config(self, br_id: str, auth_token: str, network_prefix: str = "",
                        location: str = "", nodes: list = None) -> bool:
        """
        Ajoute un nouveau BR à la configuration (utilitaire)

        Args:
            br_id: Identifiant du BR
            auth_token: Token d'authentification
            network_prefix: Préfixe réseau Thread
            location: Localisation physique
            nodes: Liste des nodes

        Returns:
            True si ajout réussi
        """
        try:
            config = self.load_config(force_reload=True)

            if 'border_routers' not in config:
                config['border_routers'] = {}

            config['border_routers'][br_id] = {
                'auth_token': auth_token,
                'network_prefix': network_prefix,
                'location': location,
                'nodes': nodes or []
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            # Invalider le cache
            self.config_cache = None

            logger.info(f"✅ Border Router {br_id} ajouté à la configuration")
            return True

        except Exception as e:
            logger.error(f"Erreur ajout BR à la configuration: {e}")
            return False


# Instance globale (singleton)
_auth_manager = None


def get_auth_manager() -> BRAuthManager:
    """
    Récupère l'instance singleton du gestionnaire d'authentification

    Returns:
        BRAuthManager instance
    """
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = BRAuthManager()
    return _auth_manager


# Fonctions utilitaires (raccourcis)

def verify_br_token(br_id: str, token: str) -> bool:
    """Raccourci pour vérifier un token"""
    return get_auth_manager().verify_br_token(br_id, token)


def get_br_config(br_id: str) -> Optional[Dict]:
    """Raccourci pour récupérer la config d'un BR"""
    return get_auth_manager().get_br_config(br_id)


def get_br_nodes(br_id: str) -> list:
    """Raccourci pour récupérer les nodes d'un BR"""
    return get_auth_manager().get_br_nodes(br_id)


def generate_br_token() -> str:
    """Génère un nouveau token pour un BR"""
    return get_auth_manager().generate_token()
