#!/usr/bin/env python3
"""
Scanner et cartographe de réseau OpenThread
Découvre automatiquement tous les nœuds et construit la topologie complète
"""

import asyncio
import socket
import struct
import json
import time
from datetime import datetime
from typing import List, Set
from .network_topology import NetworkTopology

# Configuration
COAP_PORT = 5683
NETWORK_INFO_URI = "network-info"
SCAN_TIMEOUT = 5  # secondes
RETRY_COUNT = 2

class OpenThreadScanner:
    """Scanner pour découvrir et cartographier un réseau OpenThread"""

    def __init__(self, known_addresses: List[str] = None, address_names: dict = None):
        self.topology = NetworkTopology()
        self.discovered_ips: Set[str] = set()
        self.known_addresses = known_addresses or []
        self.address_names = address_names or {}  # Mapping IPv6 -> nom
        self.sock = None

    def create_coap_get(self, uri_path: str) -> bytes:
        """Crée un paquet CoAP GET"""
        # Header CoAP GET NON-confirmable
        message_id = int(time.time()) % 0xFFFF
        header = struct.pack('!BBH',
                           0x50,  # Ver=1, Type=NON (1), TKL=0
                           0x01,  # Code=GET (0.01)
                           message_id)

        # Option Uri-Path
        uri_bytes = uri_path.encode('utf-8')
        option_header = bytes([0xB0 + len(uri_bytes)])  # Delta=11 (Uri-Path)

        return header + option_header + uri_bytes

    def parse_coap_response(self, data: bytes) -> tuple:
        """Parse une réponse CoAP et retourne (code, payload)"""
        if len(data) < 4:
            return None, None

        code = data[1]
        code_class = code >> 5
        code_detail = code & 0x1F

        # Trouver le payload marker (0xFF)
        offset = 4  # Skip header
        token_length = data[0] & 0x0F
        offset += token_length

        # Parser les options pour trouver le payload
        payload = b''
        while offset < len(data):
            if data[offset] == 0xFF:  # Payload marker
                offset += 1
                payload = data[offset:]
                break

            # Skip option
            byte = data[offset]
            option_delta = (byte >> 4) & 0x0F
            option_length = byte & 0x0F
            offset += 1

            # Gérer les deltas/longueurs étendus
            if option_delta == 13:
                offset += 1
            elif option_delta == 14:
                offset += 2

            if option_length == 13:
                option_length = 13 + data[offset]
                offset += 1
            elif option_length == 14:
                option_length = 269 + struct.unpack('!H', data[offset:offset+2])[0]
                offset += 2

            offset += option_length

        return f"{code_class}.{code_detail:02d}", payload

    async def query_node(self, ipv6: str) -> dict:
        """Interroge un nœud spécifique sur /network-info (async)"""
        def _query_sync():
            """Requête synchrone dans un thread séparé"""
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                sock.settimeout(2)

                request = self.create_coap_get(NETWORK_INFO_URI)
                sock.sendto(request, (ipv6, COAP_PORT))

                data, addr = sock.recvfrom(4096)
                sock.close()

                code, payload = self.parse_coap_response(data)

                if code and code.startswith("2.") and payload:
                    json_str = payload.decode('utf-8', errors='ignore')
                    return json.loads(json_str)

            except socket.timeout:
                pass
            except Exception:
                pass

            return None

        # Exécuter dans un thread pour ne pas bloquer l'event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query_sync)

    async def scan_multicast(self) -> Set[str]:
        """Scan via multicast CoAP pour découvrir les nœuds"""
        # Multicast désactivé sur macOS (problème de routage IPv6)
        # Utiliser uniquement le scan par adresses connues
        return set()

        print("\n🔍 Scanning réseau via multicast CoAP...")

        discovered = set()

        # Adresses multicast à tester
        multicast_addrs = [
            "ff03::1",  # Realm-local all nodes
            "ff02::1",  # Link-local all nodes
        ]

        for mcast_addr in multicast_addrs:
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                sock.settimeout(1)

                # Envoyer une requête multicast
                request = self.create_coap_get("network-info")
                sock.sendto(request, (mcast_addr, COAP_PORT))

                # Collecter les réponses pendant SCAN_TIMEOUT secondes
                start_time = time.time()
                while time.time() - start_time < SCAN_TIMEOUT:
                    try:
                        data, addr = sock.recvfrom(4096)
                        source_ip = addr[0]

                        # Nettoyer l'adresse (enlever le scope ID)
                        if '%' in source_ip:
                            source_ip = source_ip.split('%')[0]

                        if source_ip not in discovered:
                            discovered.add(source_ip)
                            print(f"  ✓ Nœud découvert: {source_ip}")

                    except socket.timeout:
                        break

                sock.close()

            except Exception as e:
                print(f"  ⚠️  Erreur scan multicast {mcast_addr}: {e}")

        return discovered

    async def scan_known_addresses(self) -> Set[str]:
        """Scan les adresses connues depuis adresses.json (en parallèle)"""
        print("\n📋 Scanning adresses connues...")

        discovered = set()

        # Créer toutes les tâches de requête en parallèle
        tasks = [self.query_node(ipv6) for ipv6 in self.known_addresses]

        # Exécuter toutes les requêtes en parallèle
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Traiter les résultats
        for ipv6, result in zip(self.known_addresses, results):
            if isinstance(result, Exception):
                print(f"  ⚠️  Node inactif: {ipv6} (erreur: {result})")
            elif result:
                discovered.add(ipv6)
                print(f"  ✓ Node actif: {ipv6}")
            else:
                print(f"  ⚠️  Node inactif: {ipv6}")

        return discovered

    async def build_topology(self):
        """Construit la topologie complète du réseau"""
        print("\n🗺️  Construction de la topologie...")

        # Découvrir les nœuds
        discovered_nodes = set()

        # Méthode 1: Multicast
        multicast_nodes = await self.scan_multicast()
        discovered_nodes.update(multicast_nodes)

        # Méthode 2: Adresses connues
        if self.known_addresses:
            known_nodes = await self.scan_known_addresses()
            discovered_nodes.update(known_nodes)

        if not discovered_nodes:
            print("❌ Aucun nœud découvert!")
            return

        print(f"\n📊 Total: {len(discovered_nodes)} nœuds découverts")

        # Interroger tous les nœuds en parallèle pour obtenir les détails
        print("\n🔎 Interrogation des nœuds...")
        tasks = [self.query_node(ipv6) for ipv6 in discovered_nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Traiter les résultats
        for ipv6, result in zip(discovered_nodes, results):
            if isinstance(result, Exception):
                print(f"  ❌ {ipv6}: Erreur ({result})")
            elif result:
                node = self.topology.add_node_from_network_info(ipv6, result)
                if node:
                    # Ajouter le nom si disponible dans le mapping
                    if ipv6 in self.address_names:
                        node.name = self.address_names[ipv6]

                    node_label = f"{node.name} ({node.rloc16})" if node.name else node.rloc16
                    print(f"  ✓ {node_label} - {node.role}")
                    print(f"    ExtAddr: {node.ext_addr}")
                    print(f"    Children: {len(node.children)}")
                    print(f"    Neighbors: {len(node.neighbors)}")
            else:
                print(f"  ❌ {ipv6}: Impossible d'obtenir les infos réseau")

    def display_results(self):
        """Affiche les résultats du scan"""
        print("\n" + "="*60)
        print("🌐 TOPOLOGIE DU RÉSEAU OPENTHREAD")
        print("="*60)

        # Statistiques
        stats = self.topology.get_statistics()
        print(f"\n📊 Statistiques:")
        print(f"  • Nom du réseau: {stats['network_name']}")
        print(f"  • Partition ID: {stats['partition_id']}")
        print(f"  • Total nœuds: {stats['total_nodes']}")
        print(f"  • Leaders: {stats['leaders']}")
        print(f"  • Routers: {stats['routers']}")
        print(f"  • End Devices: {stats['children']}")
        print(f"  • Profondeur max: {stats['max_depth']}")
        print(f"  • Dernière mise à jour: {stats['last_update']}")

        # Arbre hiérarchique
        print(f"\n🌳 Hiérarchie du réseau:")
        print("-"*60)
        self.topology.print_tree()
        print()

    def export_results(self, base_filename: str = "openthread_topology"):
        """Exporte les résultats dans différents formats"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON
        json_file = f"{base_filename}_{timestamp}.json"
        self.topology.save_to_file(json_file)

        # Graphviz
        dot_file = f"{base_filename}_{timestamp}.dot"
        self.topology.export_graphviz(dot_file)

        print(f"\n💾 Résultats exportés:")
        print(f"  • JSON: {json_file}")
        print(f"  • Graphviz: {dot_file}")


async def main():
    """Fonction principale"""
    print("""
╔═══════════════════════════════════════════════════════════╗
║         OPENTHREAD NETWORK MAPPER                         ║
║         Découverte et cartographie automatique            ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Charger les adresses connues depuis adresses.json
    known_addresses = []
    address_names = {}  # Mapping IPv6 -> nom du nœud
    try:
        with open('config/adresses.json', 'r') as f:
            data = json.load(f)
            nodes = data.get('nodes', {})
            for node_name, node_data in nodes.items():
                if isinstance(node_data, dict):
                    addr = node_data.get('address')
                    if addr:
                        known_addresses.append(addr)
                        address_names[addr] = node_name
                else:
                    known_addresses.append(node_data)

        print(f"📋 {len(known_addresses)} adresses chargées depuis adresses.json")

    except FileNotFoundError:
        print("⚠️  Fichier adresses.json non trouvé - scan multicast uniquement")
    except Exception as e:
        print(f"⚠️  Erreur lecture adresses.json: {e}")

    # Créer le scanner avec le mapping des noms
    scanner = OpenThreadScanner(known_addresses=known_addresses, address_names=address_names)

    # Scanner le réseau
    await scanner.build_topology()

    # Afficher les résultats
    scanner.display_results()

    # Exporter
    scanner.export_results()

    print("\n✅ Scan terminé!\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Scan interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
