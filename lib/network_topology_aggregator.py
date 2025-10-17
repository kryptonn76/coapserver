"""
Network Topology Aggregator for Thread Network Discovery

This module aggregates Network Diagnostic events from multiple Border Routers
to build a complete, deduplicated topology with radio metrics.

Based on OpenThread Network Diagnostic API:
- multicast ff03::1 for node discovery (TLV 0/1/5/6/8)
- meshdiag for radio metrics (RSSI/LQI/Margin)
"""

import ipaddress
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict
import time


class NetworkTopologyAggregator:
    """
    Aggregates Thread network topology from asynchronous diagnostic events

    Key features:
    - Deduplicates nodes by (partition_id, ext_addr)
    - Extracts stable ML-EID from IPv6 address lists
    - Aggregates radio metrics per link
    - Supports multiple Border Routers scanning same partition
    """

    def __init__(self, mesh_local_prefix: str = "fd00::/8"):
        """
        Initialize topology aggregator

        Args:
            mesh_local_prefix: Thread Mesh-Local prefix (from dataset)
                              Default fd00::/8 covers most Thread networks
        """
        self.mesh_local_prefix = mesh_local_prefix

        # Nodes indexed by (partition_id, ext_addr) for deduplication
        # Value: {ext_addr, rloc16s: set, mleids: set, roles: set, last_seen, br_ids: set}
        self.nodes: Dict[Tuple[int, str], dict] = {}

        # Router↔Router links indexed by sorted (rloc16_a, rloc16_b)
        # Value: {avg_rssi, last_rssi, lqi, margin_db, frame_err, msg_err, last_seen}
        self.router_links: Dict[Tuple[str, str], dict] = {}

        # Parent↔Child links indexed by (parent_rloc16, child_rloc16)
        # Value: {avg_rssi, last_rssi, lqi, mode, version, last_seen}
        self.child_links: Dict[Tuple[str, str], dict] = {}

        print(f"🔧 NetworkTopologyAggregator initialized with mesh_local_prefix={mesh_local_prefix}")

    def is_rloc(self, addr: str) -> bool:
        """
        Detect if IPv6 address is a RLOC/ALOC (Routing Locator / Anycast Locator)

        RLOC/ALOC have IID pattern: 00:00:00:ff:fe:00:<rloc16>
        ML-EID (Mesh-Local Endpoint Identifier) is topology-independent and doesn't match this pattern.

        Args:
            addr: IPv6 address as string

        Returns:
            True if address is RLOC/ALOC, False if ML-EID or other
        """
        try:
            # Extract Interface Identifier (last 64 bits)
            ipv6_int = int(ipaddress.IPv6Address(addr))
            iid = ipv6_int & ((1 << 64) - 1)  # Mask to get last 64 bits

            # Convert IID to bytes (8 bytes)
            iid_bytes = iid.to_bytes(8, 'big')

            # Check RLOC/ALOC pattern: 00:00:00:ff:fe:00:xx:xx
            return (iid_bytes[0:3] == b'\x00\x00\x00' and
                    iid_bytes[3] == 0xff and
                    iid_bytes[4] == 0xfe and
                    iid_bytes[5] == 0x00)
        except Exception as e:
            print(f"⚠️ Error checking RLOC pattern for {addr}: {e}")
            return False

    def extract_mleids(self, ip_list: List[str]) -> List[str]:
        """
        Extract ML-EID (Mesh-Local Endpoint Identifier) addresses from IPv6 list

        ML-EID is:
        - Within mesh-local prefix (from Thread dataset)
        - NOT a RLOC/ALOC (doesn't match 00:00:00:ff:fe:00:xxxx pattern)
        - Topology-independent and stable across network changes

        Args:
            ip_list: List of IPv6 addresses

        Returns:
            List of ML-EID addresses (normalized, lowercase)
        """
        mleids = []

        try:
            prefix_network = ipaddress.IPv6Network(self.mesh_local_prefix)

            for ip_str in ip_list:
                try:
                    ip = ipaddress.IPv6Address(ip_str)

                    # Check if in mesh-local prefix AND not RLOC/ALOC
                    if ip in prefix_network and not self.is_rloc(str(ip)):
                        mleids.append(str(ip).lower())

                except Exception as e:
                    print(f"⚠️ Invalid IPv6 in list: {ip_str} - {e}")

        except Exception as e:
            print(f"❌ Error extracting ML-EID: {e}")

        return mleids

    def upsert_node(self, event: dict, br_id: str):
        """
        Add or update node from Network Diagnostic event

        Deduplicates by (partition_id, ext_addr) and merges information
        from multiple sources (different BRs, multiple scans).

        Args:
            event: Node event with keys:
                   - partition (int): Thread partition ID
                   - ext_addr (str): Extended Address (EUI-64)
                   - rloc16 (str, optional): Routing Locator
                   - role (str, optional): router/reed/child/leader
                   - ipv6_list (list, optional): All IPv6 addresses
                   - mleids (list, optional): Pre-extracted ML-EID
            br_id: Border Router ID that reported this node
        """
        partition = event.get('partition')
        ext_addr = event.get('ext_addr', '').lower()

        if not partition or not ext_addr:
            print(f"⚠️ Invalid node event: missing partition or ext_addr")
            return

        # Deduplicate by (partition, ext_addr)
        key = (partition, ext_addr)

        # Initialize or retrieve node record
        if key not in self.nodes:
            self.nodes[key] = {
                'ext_addr': ext_addr,
                'partition_id': partition,
                'rloc16s': set(),
                'mleids': set(),
                'roles': set(),
                'br_ids': set(),
                'is_br': False,
                'last_seen': 0
            }

        node = self.nodes[key]

        # Merge RLOC16 (can change over time)
        if 'rloc16' in event and event['rloc16']:
            node['rloc16s'].add(event['rloc16'].lower())

        # Merge role
        if 'role' in event and event['role']:
            node['roles'].add(event['role'].lower())

        # Mark as Border Router if flag is set
        if event.get('is_br', False):
            node['is_br'] = True
            print(f"   🌐 Node marked as Border Router")

        # Extract and merge ML-EID
        if 'mleids' in event and event['mleids']:
            # Pre-extracted ML-EID
            for mleid in event['mleids']:
                node['mleids'].add(mleid.lower())
        elif 'ipv6_list' in event and event['ipv6_list']:
            # Extract from IPv6 list
            mleids = self.extract_mleids(event['ipv6_list'])
            for mleid in mleids:
                node['mleids'].add(mleid)

        # Track which BRs have seen this node
        node['br_ids'].add(br_id)

        # Update last seen timestamp
        node['last_seen'] = time.time()

        print(f"   ✅ Node upserted: {ext_addr[:8]}... partition={partition} rloc16s={node['rloc16s']} mleids={len(node['mleids'])}")

    def upsert_router_link(self, event: dict):
        """
        Add or update router↔router link from meshdiag routerneighbortable

        Args:
            event: Link event with keys:
                   - a_rloc16 (str): First router RLOC16
                   - b_rloc16 (str): Second router RLOC16
                   - avg_rssi (int, optional): Average RSSI
                   - last_rssi (int, optional): Last RSSI
                   - lqi (int, optional): Link Quality Indicator
                   - margin_db (int, optional): Link margin in dB
                   - frame_err (float, optional): Frame error rate
                   - msg_err (float, optional): Message error rate
        """
        a = event.get('a_rloc16', '').lower()
        b = event.get('b_rloc16', '').lower()

        if not a or not b:
            print(f"⚠️ Invalid router link event: missing rloc16")
            return

        # Normalize link key (sorted for undirected graph)
        key = tuple(sorted([a, b]))

        self.router_links[key] = {
            'avg_rssi': event.get('avg_rssi'),
            'last_rssi': event.get('last_rssi'),
            'lqi': event.get('lqi'),
            'margin_db': event.get('margin_db'),
            'frame_err': event.get('frame_err'),
            'msg_err': event.get('msg_err'),
            'last_seen': time.time()
        }

        print(f"   ✅ Router link upserted: {a} ↔ {b} RSSI={event.get('avg_rssi')}")

    def upsert_child_link(self, event: dict, br_id: str):
        """
        Add or update parent↔child link from meshdiag childtable

        Also creates/updates child node entry if not already present.

        Args:
            event: Child event with keys:
                   - parent_rloc16 (str): Parent router RLOC16
                   - child_rloc16 (str): Child RLOC16
                   - child_mleids (list, optional): Child ML-EID addresses
                   - child_ext_addr (str, optional): Child Extended Address
                   - partition (int, optional): Partition ID
                   - avg_rssi (int, optional): Average RSSI
                   - last_rssi (int, optional): Last RSSI
                   - lqi (int, optional): Link Quality
                   - mode (str, optional): Child mode (rx-on/mtd/sed)
                   - version (int, optional): Thread version
            br_id: Border Router ID
        """
        parent = event.get('parent_rloc16', '').lower()
        child = event.get('child_rloc16', '').lower()

        if not parent or not child:
            print(f"⚠️ Invalid child link event: missing parent or child rloc16")
            return

        # Store child link
        key = (parent, child)
        self.child_links[key] = {
            'avg_rssi': event.get('avg_rssi'),
            'last_rssi': event.get('last_rssi'),
            'lqi': event.get('lqi'),
            'mode': event.get('mode'),
            'version': event.get('version'),
            'last_seen': time.time()
        }

        # If child has ext_addr and partition, also upsert as node
        if 'child_ext_addr' in event and 'partition' in event:
            child_node_event = {
                'partition': event['partition'],
                'ext_addr': event['child_ext_addr'],
                'rloc16': child,
                'role': 'child',
                'mleids': event.get('child_mleids', [])
            }
            self.upsert_node(child_node_event, br_id)

        print(f"   ✅ Child link upserted: {parent} → {child} RSSI={event.get('avg_rssi')}")

    def get_topology(self) -> dict:
        """
        Export current topology as JSON-serializable dict

        Returns:
            Dictionary with:
            - nodes: List of node records
            - router_links: List of router↔router links
            - child_links: List of parent↔child links
            - stats: Summary statistics
        """
        # Convert sets to lists for JSON serialization
        nodes_list = []
        for (partition, ext_addr), node in self.nodes.items():
            nodes_list.append({
                'partition_id': partition,
                'ext_addr': ext_addr,
                'rloc16s': list(node['rloc16s']),
                'mleids': list(node['mleids']),
                'roles': list(node['roles']),
                'br_ids': list(node['br_ids']),
                'is_br': node.get('is_br', False),
                'last_seen': node['last_seen']
            })

        # Router links
        router_links_list = []
        for (a, b), link in self.router_links.items():
            router_links_list.append({
                'a_rloc16': a,
                'b_rloc16': b,
                **link
            })

        # Child links
        child_links_list = []
        for (parent, child), link in self.child_links.items():
            child_links_list.append({
                'parent_rloc16': parent,
                'child_rloc16': child,
                **link
            })

        return {
            'nodes': nodes_list,
            'router_links': router_links_list,
            'child_links': child_links_list,
            'stats': {
                'total_nodes': len(self.nodes),
                'total_router_links': len(self.router_links),
                'total_child_links': len(self.child_links),
                'timestamp': time.time()
            }
        }

    def clear(self):
        """Clear all topology data (useful for full refresh)"""
        self.nodes.clear()
        self.router_links.clear()
        self.child_links.clear()
        print("🗑️ Topology data cleared")
