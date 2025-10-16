"""
Gestion du registre des nodes ESP32
"""
import json
import threading
from pathlib import Path


class NodeRegistry:
    """Gère le registre des nodes et leurs adresses IPv6"""

    def __init__(self, filename="config/adresses.json"):
        self.filename = filename
        self.nodes = {}
        self.lock = threading.Lock()
        self.load()

    def load(self):
        """Charge les adresses depuis le fichier JSON"""
        try:
            if Path(self.filename).exists():
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    with self.lock:
                        self.nodes = data.get('nodes', {})
                    print(f"📂 Chargé {len(self.nodes)} nodes depuis {self.filename}")
            else:
                print(f"📝 Fichier {self.filename} non trouvé, création d'un nouveau")
                self.save()
        except Exception as e:
            print(f"❌ Erreur lecture fichier: {e}")
            self.nodes = {}

    def save(self):
        """Sauvegarde les adresses dans le fichier JSON"""
        try:
            with self.lock:
                nodes_copy = self.nodes.copy()
            with open(self.filename, 'w') as f:
                json.dump({'nodes': nodes_copy}, f, indent=2)
            print(f"💾 Sauvegardé {len(nodes_copy)} nodes")
        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")

    def get_all_addresses(self):
        """Retourne toutes les adresses IPv6"""
        with self.lock:
            # Gestion du nouveau format avec address et ordre
            addresses = []
            for name, node_data in self.nodes.items():
                if isinstance(node_data, dict):
                    addresses.append(node_data.get('address', ''))
                else:
                    # Ancien format (compatibilité)
                    addresses.append(node_data)
            return addresses

    def get_node_by_address(self, address):
        """Trouve le nom du node par son adresse

        Args:
            address: Adresse IPv6 du node

        Returns:
            str: Nom du node ou None si non trouvé
        """
        # Nettoyer l'adresse
        if address.startswith('['):
            address = address[1:address.find(']')]

        with self.lock:
            for name, node_data in self.nodes.items():
                if isinstance(node_data, dict):
                    if node_data.get('address') == address:
                        return name
                else:
                    # Ancien format (compatibilité)
                    if node_data == address:
                        return name
        return None

    def get_nodes_sorted_by_order(self):
        """Retourne les nodes triés par ordre (excluant ceux avec ordre=0)

        Returns:
            list: Liste de dicts avec name, address, ordre
        """
        with self.lock:
            sorted_nodes = []
            for name, node_data in self.nodes.items():
                if isinstance(node_data, dict):
                    ordre = node_data.get('ordre', 0)
                    if ordre > 0:
                        sorted_nodes.append({
                            'name': name,
                            'address': node_data.get('address'),
                            'ordre': ordre
                        })
            # Trier par ordre
            sorted_nodes.sort(key=lambda x: x['ordre'])
            return sorted_nodes

    def get_connected_nodes(self, node_name):
        """Retourne la liste des nodes connexes pour un node donné

        Args:
            node_name: Nom du node

        Returns:
            list: Liste des noms de nodes connexes
        """
        with self.lock:
            if node_name in self.nodes:
                node_data = self.nodes[node_name]
                if isinstance(node_data, dict):
                    return node_data.get('connexes', [])
        return []

    def get_all_node_names(self):
        """Retourne tous les noms de nodes

        Returns:
            list: Liste des noms de nodes
        """
        with self.lock:
            return list(self.nodes.keys())
