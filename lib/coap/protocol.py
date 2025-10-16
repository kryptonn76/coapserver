"""
Module de parsing et création de paquets CoAP (RFC 7252)
"""
import struct
import time


def parse_coap_packet(data):
    """Parse un paquet CoAP

    Args:
        data: bytes du paquet CoAP

    Returns:
        dict contenant:
            - version: Version CoAP
            - type: Type de message (CON, NON, ACK, RST)
            - code: Code CoAP (format "class.detail")
            - message_id: ID du message
            - token_length: Longueur du token
            - uri_path: Chemin URI reconstruit
            - payload: Payload du message
    """
    if len(data) < 4:
        return None

    # Header CoAP
    byte0 = data[0]
    version = (byte0 >> 6) & 0x03
    msg_type = (byte0 >> 4) & 0x03
    token_length = byte0 & 0x0F

    code = data[1]
    message_id = struct.unpack('!H', data[2:4])[0]

    # Code CoAP
    code_class = code >> 5
    code_detail = code & 0x1F

    # Skip token
    offset = 4 + token_length

    # Parser les options pour trouver l'URI path
    uri_path = []
    payload = b''
    option_number = 0

    while offset < len(data):
        if data[offset] == 0xFF:  # Marqueur de fin des options
            offset += 1
            if offset < len(data):
                payload = data[offset:]
            break

        # Parser l'option
        byte = data[offset]
        option_delta = (byte >> 4) & 0x0F
        option_length = byte & 0x0F
        offset += 1

        # Gérer les deltas/longueurs étendus (simplifié)
        if option_delta == 13:
            option_delta = 13 + data[offset]
            offset += 1
        elif option_delta == 14:
            option_delta = 269 + struct.unpack('!H', data[offset:offset+2])[0]
            offset += 2

        if option_length == 13:
            option_length = 13 + data[offset]
            offset += 1
        elif option_length == 14:
            option_length = 269 + struct.unpack('!H', data[offset:offset+2])[0]
            offset += 2

        option_number += option_delta

        # Extraire la valeur de l'option
        if offset + option_length <= len(data):
            option_value = data[offset:offset + option_length]
            offset += option_length

            # Option 11 = Uri-Path
            if option_number == 11:
                uri_path.append(option_value.decode('utf-8', errors='ignore'))
        else:
            break

    return {
        'version': version,
        'type': msg_type,
        'code': f"{code_class}.{code_detail:02d}",
        'message_id': message_id,
        'uri_path': '/'.join(uri_path),
        'payload': payload,
        'token_length': token_length
    }


def create_coap_response(message_id, code=0x45):
    """Crée une réponse CoAP ACK

    Args:
        message_id: ID du message à acquitter
        code: Code de réponse (défaut: 0x45 = 2.05 Content)

    Returns:
        bytes du paquet CoAP de réponse
    """
    header = struct.pack('!BBH',
                        0x60,  # Ver=1, Type=2 (ACK), TKL=0
                        code,  # 2.05 Content par défaut
                        message_id)
    return header + b'\xff' + b'ok'  # Payload marker + contenu


def create_coap_post_packet(uri_path, payload):
    """Crée un paquet CoAP POST

    Args:
        uri_path: Chemin URI de la ressource
        payload: Payload du message (str)

    Returns:
        bytes du paquet CoAP POST
    """
    message_id = int(time.time()) % 0xFFFF
    header = struct.pack('!BBH',
                        0x50,  # Ver=1, Type=NON, TKL=0
                        0x02,  # Code=POST (0.02)
                        message_id)

    # Option Uri-Path
    uri_bytes = uri_path.encode('utf-8')
    option_header = bytes([0xB0 + len(uri_bytes)])  # Delta=11 (Uri-Path)

    # Construire le paquet
    return header + option_header + uri_bytes + b'\xff' + payload.encode('utf-8')
