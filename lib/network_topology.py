#!/usr/bin/env python3
"""
Module de gestion de la topologie du réseau OpenThread
Reconstruit le graphe complet à partir des informations individuelles des nodes
"""

from datetime import datetime
from typing import Dict, List, Set, Optional
import json

class Node:
    """Représente un nœud du réseau OpenThread"""

    def __init__(self, rloc16: str, ext_addr: str, ipv6: str, name: str = None):
        self.rloc16 = rloc16
        self.ext_addr = ext_addr
        self.ipv6 = ipv6
        self.name = name  # Nom depuis adresses.json (ex: "n01", "d2C")
        self.role = "unknown"
        self.network_name = ""
        self.partition_id = None

        # Relations
        self.parent_rloc16 = None
        self.parent_rssi = None  # RSSI du lien vers le parent
        self.children = []  # Liste d'objets {rloc16, rssi}
        self.neighbors = []  # Liste de {rloc16, rssi, lqi, ...}

        # Métadonnées
        self.router_id = None
        self.max_children = 0
        self.last_seen = None
        self.link_quality_in = 0
        self.link_quality_out = 0
        self.hop_distance = None  # Distance en sauts depuis le leader

    def to_dict(self) -> dict:
        """Convertit le nœud en dictionnaire pour JSON"""
        return {
            'rloc16': self.rloc16,
            'ext_addr': self.ext_addr,
            'ipv6': self.ipv6,
            'name': self.name,
            'role': self.role,
            'network_name': self.network_name,
            'partition_id': self.partition_id,
            'parent_rloc16': self.parent_rloc16,
            'parent_rssi': self.parent_rssi,
            'children': self.children,
            'neighbors': self.neighbors,
            'router_id': self.router_id,
            'max_children': self.max_children,
            'last_seen': self.last_seen,
            'link_quality_in': self.link_quality_in,
            'link_quality_out': self.link_quality_out,
            'hop_distance': self.hop_distance
        }

    def __repr__(self):
        return f"Node({self.rloc16}, {self.role}, {self.ipv6})"


class NetworkTopology:
    """Gère la topologie complète du réseau OpenThread"""

    def __init__(self):
        self.nodes: Dict[str, Node] = {}  # rloc16 -> Node
        self.nodes_by_ext_addr: Dict[str, Node] = {}  # ext_addr -> Node
        self.nodes_by_ipv6: Dict[str, Node] = {}  # ipv6 -> Node
        self.last_update = None
        self.network_name = None
        self.partition_id = None

    def add_node_from_network_info(self, ipv6: str, network_info: dict):
        """
        Ajoute ou met à jour un nœud à partir des informations /network-info
        """
        rloc16 = network_info.get('rloc16')
        ext_addr = network_info.get('ext_addr')

        if not rloc16 or not ext_addr:
            return None

        # Créer ou récupérer le nœud
        if rloc16 in self.nodes:
            node = self.nodes[rloc16]
        else:
            node = Node(rloc16, ext_addr, ipv6)
            self.nodes[rloc16] = node
            self.nodes_by_ext_addr[ext_addr] = node
            self.nodes_by_ipv6[ipv6] = node

        # Mettre à jour les informations
        node.role = network_info.get('role', 'unknown')
        node.network_name = network_info.get('network_name', '')
        node.partition_id = network_info.get('partition_id')
        node.last_seen = datetime.now().isoformat()

        # Informations de routeur
        node.router_id = network_info.get('router_id')
        node.max_children = network_info.get('max_children', 0)

        # Parent (si child)
        parent_info = network_info.get('parent')
        if parent_info:
            node.parent_rloc16 = parent_info.get('rloc16')
            node.parent_rssi = parent_info.get('rssi', 0)
            node.link_quality_in = parent_info.get('link_quality_in', 0)
            node.link_quality_out = parent_info.get('link_quality_out', 0)

        # Enfants (si router/leader)
        children = network_info.get('children', [])
        node.children = [{
            'rloc16': child.get('rloc16'),
            'rssi': child.get('rssi', 0)
        } for child in children if child.get('rloc16')]

        # Voisins
        neighbors = network_info.get('neighbors', [])
        node.neighbors = [{
            'rloc16': n.get('rloc16'),
            'ext_addr': n.get('ext_addr'),
            'rssi': n.get('rssi', 0),
            'lqi': n.get('lqi', 0),
            'is_child': n.get('is_child', False),
            'is_ftd': n.get('is_ftd', False)
        } for n in neighbors]

        # Mettre à jour les infos réseau globales
        if not self.network_name and node.network_name:
            self.network_name = node.network_name
        if not self.partition_id and node.partition_id:
            self.partition_id = node.partition_id

        self.last_update = datetime.now().isoformat()

        return node

    def get_leader(self) -> Optional[Node]:
        """Trouve le nœud Leader"""
        for node in self.nodes.values():
            if node.role == 'leader':
                return node
        return None

    def get_routers(self) -> List[Node]:
        """Retourne tous les routeurs (incluant le Leader)"""
        return [n for n in self.nodes.values() if n.role in ['router', 'leader']]

    def get_children(self) -> List[Node]:
        """Retourne tous les end devices"""
        return [n for n in self.nodes.values() if n.role == 'child']

    def calculate_hop_distances(self):
        """
        Calcule la distance en nombre de sauts depuis le leader pour chaque nœud
        Utilise BFS (Breadth-First Search) depuis le leader
        """
        leader = self.get_leader()
        if not leader:
            # Si pas de leader, utiliser un routeur comme racine
            routers = self.get_routers()
            if routers:
                leader = routers[0]
            else:
                return

        # Réinitialiser toutes les distances
        for node in self.nodes.values():
            node.hop_distance = None

        # BFS depuis le leader
        from collections import deque
        queue = deque([(leader.rloc16, 0)])
        visited = set()

        while queue:
            current_rloc16, distance = queue.popleft()

            if current_rloc16 in visited:
                continue

            visited.add(current_rloc16)

            if current_rloc16 not in self.nodes:
                continue

            current_node = self.nodes[current_rloc16]
            current_node.hop_distance = distance

            # Ajouter les enfants à la queue
            for child in current_node.children:
                child_rloc16 = child['rloc16'] if isinstance(child, dict) else child
                if child_rloc16 not in visited:
                    queue.append((child_rloc16, distance + 1))

            # Ajouter les nodes qui ont ce node comme parent
            for node in self.nodes.values():
                if node.parent_rloc16 == current_rloc16 and node.rloc16 not in visited:
                    queue.append((node.rloc16, distance + 1))

    def get_tree_hierarchy(self) -> dict:
        """
        Construit la hiérarchie en arbre du réseau
        Retourne un dict avec le leader/router racine et ses descendants
        """
        leader = self.get_leader()
        if not leader:
            # Si pas de leader, prendre un routeur
            routers = self.get_routers()
            if routers:
                leader = routers[0]
            else:
                return {}

        def build_tree(node: Node, visited: Set[str]) -> dict:
            """Construit récursivement l'arbre"""
            if node.rloc16 in visited:
                return None

            visited.add(node.rloc16)

            tree = {
                'node': node.to_dict(),
                'children': []
            }

            # Ajouter les enfants directs
            for child in node.children:
                child_rloc16 = child['rloc16'] if isinstance(child, dict) else child
                if child_rloc16 in self.nodes:
                    child_node = self.nodes[child_rloc16]
                    child_tree = build_tree(child_node, visited)
                    if child_tree:
                        tree['children'].append(child_tree)

            # Pour les routeurs, ajouter aussi les nodes qui les ont comme parent
            for potential_child in self.nodes.values():
                if potential_child.parent_rloc16 == node.rloc16:
                    if potential_child.rloc16 not in visited:
                        child_tree = build_tree(potential_child, visited)
                        if child_tree:
                            tree['children'].append(child_tree)

            return tree

        return build_tree(leader, set())

    def print_tree(self, node_dict: dict = None, prefix: str = "", is_last: bool = True):
        """Affiche l'arbre de la topologie en ASCII art"""
        if node_dict is None:
            node_dict = self.get_tree_hierarchy()

        if not node_dict:
            print("Aucun nœud dans le réseau")
            return

        node_info = node_dict['node']
        connector = "└── " if is_last else "├── "

        # Icône selon le rôle
        role_icons = {
            'leader': '👑',
            'router': '🔀',
            'child': '📱',
            'disabled': '⚫',
            'detached': '⚠️'
        }
        icon = role_icons.get(node_info['role'], '❓')

        # Afficher le nœud avec le nom si disponible
        node_label = node_info.get('name', node_info['rloc16'])
        if node_info.get('name'):
            node_label = f"{node_info['name']} ({node_info['rloc16']})"
        else:
            node_label = node_info['rloc16']

        print(f"{prefix}{connector}{icon} {node_label} - {node_info['role']}")
        print(f"{prefix}{'    ' if is_last else '│   '}   IPv6: {node_info['ipv6']}")
        print(f"{prefix}{'    ' if is_last else '│   '}   ExtAddr: {node_info['ext_addr']}")

        # Qualité de lien si disponible
        if node_info.get('link_quality_in') or node_info.get('link_quality_out'):
            print(f"{prefix}{'    ' if is_last else '│   '}   LQI: In={node_info['link_quality_in']}, Out={node_info['link_quality_out']}")

        # Afficher les voisins (uniquement le compte)
        if node_info.get('neighbors'):
            print(f"{prefix}{'    ' if is_last else '│   '}   Voisins: {len(node_info['neighbors'])}")

        # Afficher les enfants récursivement
        children = node_dict.get('children', [])
        for i, child in enumerate(children):
            child_prefix = prefix + ("    " if is_last else "│   ")
            self.print_tree(child, child_prefix, i == len(children) - 1)

    def get_statistics(self) -> dict:
        """Retourne des statistiques sur le réseau"""
        total_nodes = len(self.nodes)
        leaders = sum(1 for n in self.nodes.values() if n.role == 'leader')
        routers = sum(1 for n in self.nodes.values() if n.role == 'router')
        children = sum(1 for n in self.nodes.values() if n.role == 'child')

        # Profondeur maximale du réseau
        def get_depth(node: Node, visited: Set[str], depth: int = 0) -> int:
            if node.rloc16 in visited:
                return depth
            visited.add(node.rloc16)

            max_child_depth = depth
            for child in node.children:
                child_rloc16 = child['rloc16'] if isinstance(child, dict) else child
                if child_rloc16 in self.nodes:
                    child_depth = get_depth(self.nodes[child_rloc16], visited, depth + 1)
                    max_child_depth = max(max_child_depth, child_depth)

            return max_child_depth

        max_depth = 0
        leader = self.get_leader()
        if leader:
            max_depth = get_depth(leader, set())

        return {
            'total_nodes': total_nodes,
            'leaders': leaders,
            'routers': routers,
            'children': children,
            'max_depth': max_depth,
            'network_name': self.network_name,
            'partition_id': self.partition_id,
            'last_update': self.last_update
        }

    def to_json(self) -> str:
        """Exporte la topologie en JSON"""
        data = {
            'network_name': self.network_name,
            'partition_id': self.partition_id,
            'last_update': self.last_update,
            'nodes': [node.to_dict() for node in self.nodes.values()],
            'statistics': self.get_statistics(),
            'hierarchy': self.get_tree_hierarchy()
        }
        return json.dumps(data, indent=2)

    def save_to_file(self, filename: str):
        """Sauvegarde la topologie dans un fichier JSON"""
        with open(filename, 'w') as f:
            f.write(self.to_json())
        print(f"Topologie sauvegardée dans {filename}")

    def export_graphviz(self, filename: str):
        """Exporte la topologie au format Graphviz DOT"""
        with open(filename, 'w') as f:
            f.write("digraph OpenThreadNetwork {\n")
            f.write("  rankdir=TB;\n")
            f.write("  node [shape=box, style=filled];\n\n")

            # Définir les nœuds avec couleurs selon le rôle
            role_colors = {
                'leader': 'gold',
                'router': 'lightgreen',
                'child': 'lightblue',
                'disabled': 'gray',
                'detached': 'orange'
            }

            for node in self.nodes.values():
                color = role_colors.get(node.role, 'white')
                label = f"{node.rloc16}\\n{node.role}\\n{node.ipv6}"
                f.write(f'  "{node.rloc16}" [label="{label}", fillcolor={color}];\n')

            f.write("\n")

            # Définir les connexions
            # Parent -> Child
            for node in self.nodes.values():
                if node.parent_rloc16 and node.parent_rloc16 in self.nodes:
                    f.write(f'  "{node.parent_rloc16}" -> "{node.rloc16}" [label="parent", color=blue];\n')

            # Voisins (relations symétriques)
            drawn_edges = set()
            for node in self.nodes.values():
                for neighbor in node.neighbors:
                    neighbor_rloc16 = neighbor.get('rloc16')
                    if neighbor_rloc16 in self.nodes:
                        edge = tuple(sorted([node.rloc16, neighbor_rloc16]))
                        if edge not in drawn_edges:
                            rssi = neighbor.get('rssi', 0)
                            f.write(f'  "{node.rloc16}" -> "{neighbor_rloc16}" '
                                  f'[label="RSSI:{rssi}", color=gray, dir=none, style=dashed];\n')
                            drawn_edges.add(edge)

            f.write("}\n")

        print(f"Graphe Graphviz sauvegardé dans {filename}")
        print(f"Pour générer l'image: dot -Tpng {filename} -o network_graph.png")
