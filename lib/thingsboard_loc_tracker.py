#!/usr/bin/env python3
"""
WebSocket client optimisé pour le suivi loc_code en temps réel
Basé sur le format fonctionnel de websocket_solution.py
"""

import json
import websocket
import ssl
import threading
import time
from datetime import datetime
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


class ThingsBoardLocTracker:
    """Client WebSocket pour suivi de localisation (loc_code) en temps réel"""
    
    def __init__(self, url: str, token: str, on_loc_update=None):
        """
        Initialise le tracker de localisation
        
        Args:
            url: URL de ThingsBoard (ex: https://platform.tamtamdeals.com)
            token: Token JWT pour l'authentification
            on_loc_update: Callback appelé pour chaque mise à jour loc_code
                          Format: on_loc_update(device_id, device_name, loc_code, timestamp)
        """
        self.tb_url = url
        self.token = token
        self.on_loc_update = on_loc_update
        
        # Construire l'URL WebSocket
        parsed = urlparse(url)
        ws_protocol = "wss" if parsed.scheme == "https" else "ws"
        self.ws_url = f"{ws_protocol}://{parsed.netloc}/api/ws"
        
        # État de connexion
        self.ws = None
        self.connected = False
        self.ws_thread = None
        
        # Tracking des devices
        self.devices = {}  # device_id -> device_info
        self.cmd_to_device = {}  # cmdId -> device_id
        self.loc_updates = {}  # device_id -> list of updates
        self.next_cmd_id = 10  # Commencer à 10 pour éviter conflit avec authCmd
        
    def on_open(self, ws):
        """WebSocket ouvert - Envoyer authCmd ET cmds ensemble"""
        logger.info(f"WebSocket connecté à {datetime.now().strftime('%H:%M:%S')}")
        self.connected = True
        
        # Si des devices sont déjà configurés, s'y abonner
        if self.devices:
            self._subscribe_to_all_devices()
            
    def on_message(self, ws, message):
        """Message reçu du WebSocket"""
        try:
            data = json.loads(message)
            
            # Debug: afficher tous les messages avec données
            if data.get("data") and len(str(data["data"])) > 2:
                logger.debug(f"Message avec données: cmdId={data.get('cmdId')}, keys={list(data['data'].keys()) if isinstance(data['data'], dict) else 'not-dict'}")
            
            # Chercher loc_code dans les données
            if data.get("data") and data["data"].get("loc_code"):
                # Identifier le device via cmdId
                cmd_id = data.get("cmdId")
                device_id = self.cmd_to_device.get(cmd_id)
                
                # Si pas de mapping cmdId, essayer d'autres méthodes
                if not device_id:
                    # Méthode 1: Chercher device_id dans les données
                    if data["data"].get("device_id"):
                        # Le device_id dans les données pourrait être le nom ou l'ID
                        potential_id = data["data"]["device_id"]
                        
                        # Si c'est une liste, prendre le premier élément
                        if isinstance(potential_id, list):
                            if len(potential_id) > 0:
                                potential_id = potential_id[0]
                                # Si c'est encore une liste (nested), prendre le premier
                                if isinstance(potential_id, list) and len(potential_id) > 0:
                                    potential_id = potential_id[0]
                            else:
                                potential_id = None
                        
                        # Seulement si on a une string valide
                        if potential_id and isinstance(potential_id, str):
                            # Chercher si c'est un ID connu
                            if potential_id in self.devices:
                                device_id = potential_id
                                logger.debug(f"Device identifié via device_id dans data: {device_id}")
                            else:
                                # Chercher si c'est un nom
                                for dev_id, dev_info in self.devices.items():
                                    if dev_info.get('name') == potential_id:
                                        device_id = dev_id
                                        logger.debug(f"Device identifié via nom dans data: {potential_id} -> {device_id}")
                                        break
                    
                    # Méthode 2: Fallback - utiliser un device par défaut
                    if not device_id:
                        if len(self.devices) == 1:
                            # Un seul device configuré
                            device_id = list(self.devices.keys())[0]
                            logger.debug(f"Utilisation du seul device configuré: {device_id}")
                        elif len(self.devices) > 1:
                            # Plusieurs devices - essayer DALKIA_4 en priorité
                            for dev_id, dev_info in self.devices.items():
                                if dev_info.get('name') == 'DALKIA_4':
                                    device_id = dev_id
                                    logger.debug(f"Multiple devices, utilisation de DALKIA_4 par défaut")
                                    break
                            
                            # Sinon prendre le premier
                            if not device_id:
                                device_id = list(self.devices.keys())[0]
                                logger.debug(f"Multiple devices, utilisation du premier: {self.devices[device_id].get('name')}")
                
                if device_id:
                    device_info = self.devices.get(device_id, {})
                    device_name = device_info.get('name', 'Unknown')
                    
                    # Parser la valeur loc_code
                    loc_data = data["data"]["loc_code"]
                    loc_code, timestamp = self._parse_loc_code(loc_data)
                    
                    # Sauvegarder l'update
                    if device_id not in self.loc_updates:
                        self.loc_updates[device_id] = []
                    
                    self.loc_updates[device_id].append({
                        'value': loc_code,
                        'timestamp': timestamp or datetime.now(),
                        'device_name': device_name
                    })
                    
                    # Appeler le callback si défini
                    if self.on_loc_update:
                        logger.debug(f"Calling on_loc_update callback for {device_name}")
                        try:
                            self.on_loc_update(device_id, device_name, loc_code, timestamp)
                        except Exception as e:
                            logger.error(f"Error in on_loc_update callback: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                    else:
                        logger.debug(f"No on_loc_update callback defined")
                    
                    logger.debug(f"LOC_CODE Update - {device_name}: {loc_code}")
                    
            # Réponse d'authentification/souscription
            elif "subscriptionId" in data or "errorCode" in data:
                if "errorCode" in data:
                    if data["errorCode"] == 0:
                        logger.debug("Souscription réussie")
                    else:
                        logger.warning(f"Erreur souscription: {data.get('errorMsg', 'Unknown')}")
                        
        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON: {e}")
        except Exception as e:
            logger.error(f"Erreur traitement message: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            
    def on_error(self, ws, error):
        """Erreur WebSocket"""
        logger.error(f"Erreur WebSocket: {error}")
        
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket fermé"""
        logger.info(f"WebSocket fermé: {close_status_code} - {close_msg}")
        self.connected = False
        
    def _parse_loc_code(self, loc_data):
        """
        Parse la valeur loc_code selon différents formats possibles
        
        Returns:
            tuple: (loc_code_value, timestamp)
        """
        timestamp = None
        value = None
        
        if isinstance(loc_data, list) and len(loc_data) > 0:
            if isinstance(loc_data[0], list):
                # Format [[timestamp, value]]
                if len(loc_data[0]) >= 2:
                    timestamp = datetime.fromtimestamp(loc_data[0][0] / 1000)
                    value = loc_data[0][1]
            else:
                # Format [value]
                value = loc_data[0]
        else:
            # Valeur directe
            value = loc_data
            
        return value, timestamp
        
    def _subscribe_to_all_devices(self):
        """S'abonner à tous les devices configurés"""
        if not self.ws or not self.connected:
            return
            
        # Construire les commandes pour tous les devices
        cmds = []
        for device_id, device_info in self.devices.items():
            cmd_id = self.next_cmd_id
            self.next_cmd_id += 1
            
            cmds.append({
                "entityType": "DEVICE",
                "entityId": device_id,
                "scope": "LATEST_TELEMETRY",
                "cmdId": cmd_id,
                "type": "TIMESERIES"  # MANDATORY!
            })
            
            self.cmd_to_device[cmd_id] = device_id
            
            if device_id not in self.loc_updates:
                self.loc_updates[device_id] = []
                
        # Format EXACT : authCmd ET cmds dans le MÊME message
        message = {
            "authCmd": {
                "cmdId": 0,
                "token": self.token
            },
            "cmds": cmds
        }
        
        msg_str = json.dumps(message)
        logger.info(f"Souscription à {len(cmds)} devices")
        logger.debug(f"Message: {msg_str[:200]}...")
        
        self.ws.send(msg_str)
        
    def connect(self):
        """Établir la connexion WebSocket"""
        logger.info(f"Connexion WebSocket à {self.ws_url}")
        
        sslopt = {"cert_reqs": ssl.CERT_NONE} if self.ws_url.startswith("wss") else None
        
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # Lancer dans un thread séparé
        self.ws_thread = threading.Thread(target=lambda: self.ws.run_forever(sslopt=sslopt))
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
        # Attendre la connexion (max 10 secondes)
        timeout = time.time() + 10
        while not self.connected and time.time() < timeout:
            time.sleep(0.1)
            
        return self.connected
        
    def disconnect(self):
        """Fermer la connexion WebSocket"""
        if self.ws:
            self.ws.close()
            
    def add_device(self, device_id: str, device_name: str):
        """
        Ajouter un device à surveiller
        
        Args:
            device_id: ID ThingsBoard du device
            device_name: Nom du device pour l'affichage
        """
        self.devices[device_id] = {
            'name': device_name,
            'id': device_id
        }
        
        # Note: La souscription se fera dans on_open() quand le WebSocket sera connecté
            
    def _subscribe_single_device(self, device_id: str, device_name: str):
        """S'abonner à un seul device"""
        if not self.ws or not self.connected:
            return
            
        cmd_id = self.next_cmd_id
        self.next_cmd_id += 1
        
        # Format avec auth et cmd ensemble
        message = {
            "authCmd": {
                "cmdId": 0,
                "token": self.token
            },
            "cmds": [{
                "entityType": "DEVICE",
                "entityId": device_id,
                "scope": "LATEST_TELEMETRY",
                "cmdId": cmd_id,
                "type": "TIMESERIES"
            }]
        }
        
        self.cmd_to_device[cmd_id] = device_id
        
        if device_id not in self.loc_updates:
            self.loc_updates[device_id] = []
            
        self.ws.send(json.dumps(message))
        logger.info(f"Souscription ajoutée pour {device_name}")
        
    def set_devices(self, devices_list):
        """
        Configurer la liste complète des devices à surveiller
        
        Args:
            devices_list: Liste de dict avec 'id' et 'name'
        """
        self.devices = {}
        for device in devices_list:
            self.devices[device['id']] = {
                'name': device['name'],
                'id': device['id']
            }
            
        # Si déjà connecté, s'abonner
        if self.connected:
            self._subscribe_to_all_devices()
            
    def get_statistics(self):
        """Obtenir les statistiques de suivi"""
        stats = {
            'total_devices': len(self.devices),
            'devices_with_updates': len([d for d in self.loc_updates if self.loc_updates[d]]),
            'total_updates': sum(len(updates) for updates in self.loc_updates.values()),
            'devices': {}
        }
        
        for device_id, updates in self.loc_updates.items():
            device_name = self.devices.get(device_id, {}).get('name', 'Unknown')
            stats['devices'][device_name] = {
                'count': len(updates),
                'last_value': updates[-1]['value'] if updates else None,
                'last_time': updates[-1]['timestamp'] if updates else None
            }
            
        return stats
        
    def get_device_updates(self, device_id: str):
        """Obtenir toutes les mises à jour d'un device spécifique"""
        return self.loc_updates.get(device_id, [])