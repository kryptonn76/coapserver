"""
Client CoAP pour envoyer des commandes aux nodes ESP32
"""
import socket
import struct
import time


COAP_PORT = 5683


class CoAPClient:
    """Client CoAP pour envoyer des commandes POST aux nodes"""

    def send_post(self, address, uri_path, payload, verbose=True):
        """Envoie un POST CoAP à une adresse IPv6

        Args:
            address: Adresse IPv6 du node cible
            uri_path: Chemin URI de la ressource (ex: "led", "audio")
            payload: Payload du message (str)
            verbose: Afficher les logs (défaut: True)

        Returns:
            bool: True si l'envoi a réussi, False sinon
        """
        try:
            # Créer un nouveau socket pour l'envoi
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

            # Header CoAP POST
            message_id = int(time.time()) % 0xFFFF
            header = struct.pack('!BBH',
                                0x50,  # Ver=1, Type=NON, TKL=0
                                0x02,  # Code=POST (0.02)
                                message_id)

            # Option Uri-Path
            uri_bytes = uri_path.encode('utf-8')
            option_header = bytes([0xB0 + len(uri_bytes)])  # Delta=11

            # Construire le paquet
            packet = header + option_header + uri_bytes + b'\xff' + payload.encode('utf-8')

            # Envoyer
            sock.sendto(packet, (address, COAP_PORT))

            if verbose:
                print(f"✅ Envoyé '{payload}' à {address}/{uri_path}")

            sock.close()
            return True

        except Exception as e:
            if verbose:
                print(f"❌ Erreur envoi CoAP: {e}")
            return False
