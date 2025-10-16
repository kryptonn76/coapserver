"""
Client ThingsBoard pour télémétrie et WebSocket
"""
import time
import requests
from datetime import datetime

# Import ThingsBoard (optionnel)
try:
    from tb_rest_client.rest_client_ce import RestClientCE
    from tb_rest_client.rest import ApiException
    TB_AVAILABLE = True
except ImportError:
    print("⚠️  Module tb_rest_client non disponible - ThingsBoard désactivé")
    RestClientCE = None
    ApiException = None
    TB_AVAILABLE = False

# Import ThingsBoard Location Tracker (optionnel)
try:
    from lib.thingsboard_loc_tracker import ThingsBoardLocTracker
except ImportError as e:
    print(f"⚠️  Module thingsboard_loc_tracker non disponible: {e}")
    ThingsBoardLocTracker = None


class ThingsBoardClient:
    """Client ThingsBoard pour envoyer la télémétrie et recevoir les mises à jour"""

    def __init__(self, tb_config, socketio, on_telemetry_update=None, on_location_change=None):
        """
        Args:
            tb_config: Dict avec url, username, password
            socketio: Instance Socket.IO pour émettre des événements
            on_telemetry_update: Callback pour mises à jour télémétrie
            on_location_change: Callback pour changement de zone
        """
        self.tb_config = tb_config
        self.socketio = socketio
        self.client = None
        self.customer_id = None
        self.connected = False
        self.asset_cache = {}  # Cache des assets par nom
        self.asset_id_to_name = {}  # Mapping inverse ID → nom
        self.device_cache = {}  # Cache des devices par nom
        self.device_id_to_name = {}  # Mapping inverse device ID → nom
        self.device_loc_code = {}  # Stockage des valeurs loc_code par device
        self.last_loc_code = None  # Dernière localisation globale
        self.token_timestamp = 0  # Timestamp de la dernière connexion
        self.token_lifetime = 900  # Durée de vie du token en secondes (15 min)
        self.ws_client = None  # Client WebSocket
        self.on_telemetry_update = on_telemetry_update  # Callback pour les mises à jour
        self.on_location_change = on_location_change  # Callback pour changement de zone

    def connect(self) -> bool:
        """Se connecter à ThingsBoard (REST + WebSocket)"""
        if not TB_AVAILABLE:
            print("⚠️ ThingsBoard: Module non disponible")
            return False

        if not self.tb_config['username'] or not self.tb_config['password']:
            print("⚠️ ThingsBoard: Credentials non configurés")
            return False

        try:
            print("🌐 Connexion à ThingsBoard...")
            self.client = RestClientCE(base_url=self.tb_config['url'])
            self.client.login(username=self.tb_config['username'], password=self.tb_config['password'])

            # Obtenir les infos utilisateur
            user = self.client.get_user()
            print(f"✅ ThingsBoard: Connecté en tant que {user.email}")

            # Stocker customer ID pour CUSTOMER_USER
            if hasattr(user, 'customer_id') and user.customer_id:
                self.customer_id = user.customer_id.id

            self.connected = True
            self.token_timestamp = time.time()  # Enregistrer le timestamp de connexion
            self.refresh_asset_cache()

            # Connexion WebSocket pour les mises à jour en temps réel
            self._connect_websocket()

            return True

        except Exception as e:
            print(f"❌ ThingsBoard: Erreur connexion: {e}")
            self.connected = False
            return False

    def _connect_websocket(self):
        """Établir la connexion WebSocket pour recevoir les mises à jour"""
        if not ThingsBoardLocTracker:
            return

        try:
            # Récupérer le token JWT
            token = self.client.configuration.api_key['X-Authorization'].replace('Bearer ', '')

            # Créer le client WebSocket avec callback pour loc_code
            self.ws_client = ThingsBoardLocTracker(
                url=self.tb_config['url'],
                token=token,
                on_loc_update=self._handle_loc_update
            )

            # Préparer la liste des devices AVANT la connexion
            devices_list = []
            for device_name, device_id in self.device_cache.items():
                devices_list.append({
                    'id': device_id,
                    'name': device_name
                })
                self.device_id_to_name[device_id] = device_name

            # Configurer les devices AVANT de se connecter
            if devices_list:
                print(f"📡 Configuration de {len(devices_list)} devices pour le suivi loc_code...")
                self.ws_client.set_devices(devices_list)

            # Se connecter (cela déclenchera les souscriptions)
            if self.ws_client.connect():
                print("✅ WebSocket ThingsBoard connecté")

                # Afficher les devices surveillés
                for device in devices_list:
                    if 'DALKIA' in device['name']:
                        print(f"   🎯 {device['name']} (Badge)")
                    else:
                        print(f"   📱 {device['name']}")
            else:
                print("⚠️ Impossible d'établir la connexion WebSocket")

        except Exception as e:
            print(f"❌ Erreur connexion WebSocket: {e}")

    def _handle_loc_update(self, device_id, device_name, loc_code, timestamp):
        """Handler pour les mises à jour loc_code reçues via WebSocket"""
        try:
            # Détecter si la localisation a changé
            location_changed = False
            if self.last_loc_code and self.last_loc_code != loc_code:
                location_changed = True
                print(f"\n🔄 CHANGEMENT DE ZONE: {self.last_loc_code} → {loc_code}")

            # Stocker la valeur loc_code
            self.device_loc_code[device_name] = {
                'value': loc_code,
                'timestamp': timestamp.timestamp() * 1000 if timestamp else time.time() * 1000,
                'device_id': device_id
            }

            # Mettre à jour la dernière localisation
            self.last_loc_code = loc_code

            # Si changement de zone, appeler le callback
            if location_changed and self.on_location_change:
                print(f"   🔴 Clignotement LED rouge pour node: {loc_code}")
                self.on_location_change(loc_code)

            # Émettre via Socket.IO pour l'interface web
            self.socketio.emit('loc_code_update', {
                'device': device_name,
                'loc_code': loc_code,
                'timestamp': timestamp.isoformat() if timestamp else datetime.now().isoformat(),
                'device_id': device_id,
                'location_changed': location_changed
            })

            # Appeler le callback si défini
            if self.on_telemetry_update:
                self.on_telemetry_update(device_name, {'loc_code': loc_code})

        except Exception as e:
            print(f"❌ Erreur traitement loc_code: {e}")

    def disconnect(self):
        """Se déconnecter (REST + WebSocket)"""
        # Déconnexion WebSocket
        if self.ws_client:
            try:
                self.ws_client.disconnect()
                print("👋 WebSocket ThingsBoard déconnecté")
            except:
                pass
            self.ws_client = None

        # Déconnexion REST
        if self.client:
            try:
                self.client.logout()
                print("👋 ThingsBoard REST déconnecté")
            except:
                pass
        self.connected = False

    def refresh_asset_cache(self):
        """Rafraîchir le cache des assets et devices"""
        if not self.connected:
            return

        # Récupérer les assets
        if self.customer_id:
            try:
                print("🔄 ThingsBoard: Récupération des assets...")
                assets_page = self.client.get_customer_assets(
                    customer_id=self.customer_id,
                    page_size=100,
                    page=0
                )

                self.asset_cache = {}
                self.asset_id_to_name = {}  # Réinitialiser le mapping inverse
                for asset in assets_page.data:
                    self.asset_cache[asset.name] = asset.id.id
                    self.asset_id_to_name[asset.id.id] = asset.name  # Mapping inverse

                print(f"📦 ThingsBoard: {len(self.asset_cache)} assets en cache")

            except Exception as e:
                print(f"❌ ThingsBoard: Erreur récupération assets: {e}")

        # Récupérer les devices
        try:
            print("🔄 ThingsBoard: Récupération des devices...")

            # Essayer d'abord comme tenant
            try:
                devices_page = self.client.get_tenant_devices(
                    page_size=100,
                    page=0
                )
            except:
                # Sinon essayer comme customer
                if self.customer_id:
                    devices_page = self.client.get_customer_devices(
                        customer_id=self.customer_id,
                        page_size=100,
                        page=0
                    )
                else:
                    print("⚠️ Impossible de récupérer les devices")
                    return

            self.device_cache = {}
            self.device_id_to_name = {}
            for device in devices_page.data:
                self.device_cache[device.name] = device.id.id
                self.device_id_to_name[device.id.id] = device.name
                # Initialiser loc_code à None
                self.device_loc_code[device.name] = {
                    'value': None,
                    'timestamp': None,
                    'device_id': device.id.id
                }

            print(f"📱 ThingsBoard: {len(self.device_cache)} devices en cache")

            # Afficher les devices DALKIA
            dalkia_devices = [name for name in self.device_cache.keys() if 'DALKIA' in name.upper()]
            if dalkia_devices:
                print(f"   Badges DALKIA: {', '.join(dalkia_devices)}")

        except Exception as e:
            print(f"❌ ThingsBoard: Erreur récupération devices: {e}")

    def send_battery_telemetry(self, node_name: str, voltage: float, percentage: int) -> bool:
        """Envoyer la télémétrie batterie pour un node"""
        if not self.connected:
            return False

        # Vérifier si le token est proche de l'expiration (renouveler 1 minute avant)
        if time.time() - self.token_timestamp > (self.token_lifetime - 60):
            print("🔄 ThingsBoard: Token proche de l'expiration, renouvellement...")
            if not self.reconnect():
                return False

        try:
            # Chercher l'asset correspondant au node
            asset_id = self.asset_cache.get(node_name)
            if not asset_id:
                print(f"⚠️ ThingsBoard: Asset '{node_name}' non trouvé")
                return False

            # Préparer les données de télémétrie
            telemetry_data = {
                "ts": int(time.time() * 1000),
                "values": {
                    "battery_level": percentage,  # Pourcentage
                    "battery_value": voltage      # Voltage
                }
            }

            # Obtenir le token JWT
            token = self.client.configuration.api_key['X-Authorization'].replace('Bearer ', '')

            # Headers
            headers = {
                'Content-Type': 'application/json',
                'X-Authorization': f'Bearer {token}'
            }

            # URL de l'endpoint télémétrie
            telemetry_url = f"{self.tb_config['url']}/api/plugins/telemetry/ASSET/{asset_id}/timeseries/SERVER_SCOPE"

            # Envoyer la requête
            response = requests.post(telemetry_url, json=telemetry_data, headers=headers)

            if response.status_code == 200:
                print(f"☁️ ThingsBoard: Télémétrie envoyée pour {node_name}")
                return True
            elif response.status_code == 401:
                # Token expiré, tenter de se reconnecter
                print("🔄 ThingsBoard: Token expiré, reconnexion...")
                if self.reconnect():
                    # Réessayer l'envoi après reconnexion
                    return self.send_battery_telemetry(node_name, voltage, percentage)
                else:
                    return False
            else:
                print(f"❌ ThingsBoard: Erreur envoi télémétrie: HTTP {response.status_code}")
                return False

        except Exception as e:
            print(f"❌ ThingsBoard: Erreur envoi télémétrie: {e}")
            return False

    def reconnect(self) -> bool:
        """Reconnexion à ThingsBoard (renouvellement du token)"""
        try:
            # Se déconnecter proprement
            self.disconnect()

            # Se reconnecter
            return self.connect()

        except Exception as e:
            print(f"❌ ThingsBoard: Erreur reconnexion: {e}")
            return False
