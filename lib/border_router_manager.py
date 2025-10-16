#!/usr/bin/env python3
"""
Border Router Manager - Gestion des Border Routers via WebSocket
Permet la connexion de plusieurs Border Routers ESP32 au serveur cloud
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
import json
import logging

logger = logging.getLogger(__name__)


class BorderRouterManager:
    """
    Gère les connexions WebSocket des Border Routers
    - Enregistrement/désenregistrement
    - Heartbeat monitoring
    - Routage des commandes vers le bon BR
    - État et statistiques
    """

    def __init__(self, heartbeat_timeout: int = 30):
        """
        Args:
            heartbeat_timeout: Timeout en secondes avant de considérer un BR offline
        """
        self.border_routers: Dict[str, Dict] = {}  # {br_id: {info}}
        self.sid_to_br: Dict[str, str] = {}        # {socket_id: br_id}
        self.node_to_br: Dict[str, str] = {}       # {node_name: br_id}
        self.lock = threading.RLock()  # RLock = Reentrant Lock (évite deadlock)
        self.heartbeat_timeout = heartbeat_timeout

        # Démarrer le thread de monitoring
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self._monitor_heartbeats, daemon=True)
        self.monitor_thread.start()

    def register_br(self, br_id: str, sid: str, network_prefix: str = "", nodes: List[str] = None) -> bool:
        """
        Enregistre un nouveau Border Router

        Args:
            br_id: Identifiant unique du BR (ex: "BR-001")
            sid: Socket ID de la connexion WebSocket
            network_prefix: Préfixe réseau Thread (ex: "fd78:8e78:3bfe:1::/64")
            nodes: Liste des nodes gérés par ce BR

        Returns:
            True si enregistrement réussi
        """
        with self.lock:
            # Vérifier si le BR existe déjà (reconnexion)
            if br_id in self.border_routers:
                old_sid = self.border_routers[br_id]['sid']
                if old_sid in self.sid_to_br:
                    del self.sid_to_br[old_sid]
                logger.info(f"🔄 Border Router {br_id} reconnecté (ancien sid: {old_sid})")

            # Enregistrer le BR
            self.border_routers[br_id] = {
                'sid': sid,
                'br_id': br_id,
                'network_prefix': network_prefix,
                'nodes': nodes or [],
                'status': 'online',
                'connected_at': datetime.now(),
                'last_heartbeat': datetime.now(),
                'heartbeat_count': 0,
                'nodes_count': len(nodes) if nodes else 0,
                'commands_sent': 0,
                'events_received': 0
            }

            self.sid_to_br[sid] = br_id

            # Mettre à jour le mapping node → BR
            if nodes:
                for node_name in nodes:
                    self.node_to_br[node_name] = br_id

            logger.info(f"✅ Border Router {br_id} enregistré (sid: {sid}, nodes: {len(nodes) if nodes else 0})")
            return True

    def unregister_br(self, br_id: str) -> bool:
        """
        Désenregistre un Border Router (déconnexion)

        Args:
            br_id: Identifiant du BR

        Returns:
            True si désenregistrement réussi
        """
        with self.lock:
            if br_id not in self.border_routers:
                return False

            br_info = self.border_routers[br_id]
            sid = br_info['sid']

            # Retirer du mapping sid → br
            if sid in self.sid_to_br:
                del self.sid_to_br[sid]

            # Retirer les nodes du mapping
            nodes = br_info.get('nodes', [])
            for node_name in nodes:
                if self.node_to_br.get(node_name) == br_id:
                    del self.node_to_br[node_name]

            # Marquer comme offline (garder en mémoire pour les stats)
            self.border_routers[br_id]['status'] = 'offline'
            self.border_routers[br_id]['disconnected_at'] = datetime.now()

            logger.warning(f"⚠️ Border Router {br_id} déconnecté")
            return True

    def update_heartbeat(self, br_id: str, nodes_count: int = None) -> bool:
        """
        Met à jour le heartbeat d'un BR

        Args:
            br_id: Identifiant du BR
            nodes_count: Nombre de nodes actifs (optionnel)

        Returns:
            True si mise à jour réussie
        """
        with self.lock:
            if br_id not in self.border_routers:
                logger.warning(f"Heartbeat reçu pour BR inconnu: {br_id}")
                return False

            self.border_routers[br_id]['last_heartbeat'] = datetime.now()
            self.border_routers[br_id]['heartbeat_count'] += 1

            if nodes_count is not None:
                self.border_routers[br_id]['nodes_count'] = nodes_count

            # Remettre online si était offline
            if self.border_routers[br_id]['status'] == 'offline':
                self.border_routers[br_id]['status'] = 'online'
                logger.info(f"✅ Border Router {br_id} rétabli")

            return True

    def get_br_for_node(self, node_name: str) -> Optional[str]:
        """
        Trouve le BR gérant un node spécifique

        Args:
            node_name: Nom du node (ex: "n01")

        Returns:
            br_id si trouvé, None sinon
        """
        with self.lock:
            br_id = self.node_to_br.get(node_name)

            # Vérifier que le BR est online
            if br_id and self.is_br_online(br_id):
                return br_id

            return None

    def get_br_sid(self, br_id: str) -> Optional[str]:
        """
        Récupère le socket ID d'un BR

        Args:
            br_id: Identifiant du BR

        Returns:
            socket_id si trouvé et online, None sinon
        """
        with self.lock:
            if br_id in self.border_routers and self.border_routers[br_id]['status'] == 'online':
                return self.border_routers[br_id]['sid']
            return None

    def is_br_online(self, br_id: str) -> bool:
        """
        Vérifie si un BR est online

        Args:
            br_id: Identifiant du BR

        Returns:
            True si online
        """
        with self.lock:
            if br_id not in self.border_routers:
                return False

            br_info = self.border_routers[br_id]

            # Vérifier le statut ET le dernier heartbeat
            if br_info['status'] != 'online':
                return False

            time_since_heartbeat = (datetime.now() - br_info['last_heartbeat']).total_seconds()
            return time_since_heartbeat < self.heartbeat_timeout

    def get_active_border_routers(self) -> List[Dict]:
        """
        Récupère la liste des Border Routers actifs (online)

        Returns:
            Liste de dicts avec les infos des BRs online
        """
        with self.lock:
            active_brs = []
            for br_id, br_info in self.border_routers.items():
                # Vérifier online sans rappeler is_br_online() (évite deadlock)
                if br_info['status'] == 'online':
                    time_since_heartbeat = (datetime.now() - br_info['last_heartbeat']).total_seconds()
                    if time_since_heartbeat < self.heartbeat_timeout:
                        active_brs.append({
                            'br_id': br_id,
                            'sid': br_info['sid'],
                            'network_prefix': br_info.get('network_prefix', ''),
                            'nodes': br_info.get('nodes', []),
                            'nodes_count': br_info.get('nodes_count', 0),
                            'connected_at': br_info['connected_at'],
                            'last_heartbeat': br_info['last_heartbeat']
                        })
            return active_brs

    def get_all_brs_status(self) -> Dict[str, Dict]:
        """
        Récupère le statut de tous les BR

        Returns:
            Dict avec le statut de chaque BR
        """
        with self.lock:
            status = {}
            for br_id, br_info in self.border_routers.items():
                time_since_heartbeat = (datetime.now() - br_info['last_heartbeat']).total_seconds()

                status[br_id] = {
                    'br_id': br_id,
                    'status': 'online' if self.is_br_online(br_id) else 'offline',
                    'network_prefix': br_info.get('network_prefix', ''),
                    'nodes': br_info.get('nodes', []),
                    'nodes_count': br_info.get('nodes_count', 0),
                    'connected_at': br_info['connected_at'].isoformat(),
                    'last_heartbeat': br_info['last_heartbeat'].isoformat(),
                    'heartbeat_count': br_info.get('heartbeat_count', 0),
                    'time_since_heartbeat': round(time_since_heartbeat, 1),
                    'commands_sent': br_info.get('commands_sent', 0),
                    'events_received': br_info.get('events_received', 0)
                }

            return status

    def get_statistics(self) -> Dict:
        """
        Récupère les statistiques globales

        Returns:
            Dict avec les statistiques
        """
        with self.lock:
            total_brs = len(self.border_routers)
            online_brs = sum(1 for br_id in self.border_routers if self.is_br_online(br_id))
            total_nodes = len(self.node_to_br)

            total_commands = sum(br['commands_sent'] for br in self.border_routers.values())
            total_events = sum(br['events_received'] for br in self.border_routers.values())

            return {
                'total_border_routers': total_brs,
                'online_border_routers': online_brs,
                'offline_border_routers': total_brs - online_brs,
                'total_nodes': total_nodes,
                'total_commands_sent': total_commands,
                'total_events_received': total_events,
                'heartbeat_timeout': self.heartbeat_timeout
            }

    def increment_command_counter(self, br_id: str):
        """Incrémente le compteur de commandes envoyées"""
        with self.lock:
            if br_id in self.border_routers:
                self.border_routers[br_id]['commands_sent'] += 1

    def increment_event_counter(self, br_id: str):
        """Incrémente le compteur d'événements reçus"""
        with self.lock:
            if br_id in self.border_routers:
                self.border_routers[br_id]['events_received'] += 1

    def update_nodes_list(self, br_id: str, nodes: List[str]):
        """
        Met à jour la liste des nodes gérés par un BR

        Args:
            br_id: Identifiant du BR
            nodes: Liste des nodes
        """
        with self.lock:
            if br_id not in self.border_routers:
                return

            # Retirer l'ancien mapping
            old_nodes = self.border_routers[br_id].get('nodes', [])
            for node_name in old_nodes:
                if self.node_to_br.get(node_name) == br_id:
                    del self.node_to_br[node_name]

            # Ajouter le nouveau mapping
            for node_name in nodes:
                self.node_to_br[node_name] = br_id

            self.border_routers[br_id]['nodes'] = nodes
            self.border_routers[br_id]['nodes_count'] = len(nodes)

            logger.info(f"📋 BR {br_id}: Liste de nodes mise à jour ({len(nodes)} nodes)")

    def _monitor_heartbeats(self):
        """Thread de monitoring des heartbeats"""
        while self.monitoring_active:
            try:
                with self.lock:
                    now = datetime.now()
                    for br_id, br_info in self.border_routers.items():
                        if br_info['status'] == 'online':
                            time_since_heartbeat = (now - br_info['last_heartbeat']).total_seconds()

                            if time_since_heartbeat > self.heartbeat_timeout:
                                br_info['status'] = 'offline'
                                br_info['disconnected_at'] = now
                                logger.error(f"❌ Border Router {br_id} timeout (pas de heartbeat depuis {time_since_heartbeat:.0f}s)")

                time.sleep(5)  # Vérifier toutes les 5 secondes

            except Exception as e:
                logger.error(f"Erreur dans monitor_heartbeats: {e}")
                time.sleep(5)

    def stop(self):
        """Arrête le monitoring"""
        self.monitoring_active = False
        if self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2)

    def __del__(self):
        """Cleanup"""
        self.stop()
