# Protocole WebSocket Border Router

Documentation du protocole de communication WebSocket entre le serveur cloud Python et les Border Routers ESP32-C6.

## Architecture

```
┌─────────────────────────────────────────┐
│    SERVEUR CLOUD (Python Flask)         │
│    wss://server.com:443                 │
│    Namespace: /ws/br                    │
└──────────────┬──────────────────────────┘
               │ WebSocket Secure (WSS)
               │ Port 443 (HTTPS)
    ┌──────────┴────────┬────────────┐
    │                   │            │
┌───▼──────────┐  ┌────▼──────┐  ┌──▼────┐
│ BR-001       │  │ BR-002    │  │ BR-N  │
│ ESP32-C6     │  │ ESP32-C6  │  │ ...   │
│ + CoAP Proxy │  │           │  │       │
└──────────────┘  └───────────┘  └───────┘
```

## Connexion

### URL de connexion

```
wss://your-server.com:443/ws/br?br_id=BR-001&auth_token=SECRET&network_prefix=fd78:8e78:3bfe:1::/64
```

### Paramètres Query String

| Paramètre | Requis | Description | Exemple |
|-----------|--------|-------------|---------|
| `br_id` | ✅ | Identifiant unique du BR | `BR-001` |
| `auth_token` | ✅ | Token d'authentification | `secret-token-...` |
| `network_prefix` | ⚪ | Préfixe réseau Thread IPv6 | `fd78:8e78:3bfe:1::/64` |

### Message de Connexion

**Envoyé par le serveur après authentification réussie :**

```json
{
  "event": "connected",
  "data": {
    "status": "ok",
    "br_id": "BR-001",
    "server_time": 1234567890.123,
    "use_websocket_mode": true
  }
}
```

## Messages - Border Router → Serveur

### 1. Heartbeat (toutes les 10s)

**Event:** `heartbeat`

```json
{
  "br_id": "BR-001",
  "timestamp": 1234567890,
  "nodes_count": 5,
  "status": "online"
}
```

**Réponse du serveur :**

```json
{
  "event": "heartbeat_ack",
  "data": {
    "timestamp": 1234567890.123,
    "server_status": "ok"
  }
}
```

### 2. Événement Node

**Event:** `node_event`

#### Bouton pressé

```json
{
  "type": "node_event",
  "br_id": "BR-001",
  "node": "n01",
  "event_type": "button",
  "payload": {
    "state": "pressed",
    "duration_ms": 150
  },
  "timestamp": 1234567890
}
```

#### Batterie

```json
{
  "type": "node_event",
  "br_id": "BR-001",
  "node": "n01",
  "event_type": "battery",
  "payload": {
    "voltage": 3.7,
    "percentage": 85
  },
  "timestamp": 1234567890
}
```

#### Beacon BLE

```json
{
  "type": "node_event",
  "br_id": "BR-001",
  "node": "n01",
  "event_type": "ble-beacon",
  "payload": {
    "ble_addr": "AA:BB:CC:DD:EE:FF",
    "rssi": -45,
    "code": "BADGE-123"
  },
  "timestamp": 1234567890
}
```

### 3. Réponse à une commande

**Event:** `command_response`

```json
{
  "type": "command_response",
  "br_id": "BR-001",
  "request_id": "uuid-1234-5678-90ab",
  "node": "n01",
  "status": "success",
  "result": {
    "message": "Audio playing"
  },
  "error": null
}
```

**En cas d'erreur :**

```json
{
  "type": "command_response",
  "br_id": "BR-001",
  "request_id": "uuid-1234-5678-90ab",
  "node": "n01",
  "status": "error",
  "result": null,
  "error": "Node not reachable"
}
```

### 4. Mise à jour Topologie

**Event:** `topology_update`

```json
{
  "type": "topology_update",
  "br_id": "BR-001",
  "nodes": [
    {
      "name": "n01",
      "rloc16": "0x1800",
      "role": "router",
      "ext_addr": "aa:bb:cc:dd:ee:ff:00:11"
    },
    {
      "name": "n02",
      "rloc16": "0x1801",
      "role": "child",
      "ext_addr": "11:22:33:44:55:66:77:88"
    }
  ],
  "timestamp": 1234567890
}
```

## Messages - Serveur → Border Router

### 1. Commande Audio

**Event:** `command`

#### Lecture audio

```json
{
  "type": "audio_play",
  "target_node": "n01",
  "request_id": "uuid-1234-5678-90ab",
  "payload": {
    "message_id": 341
  }
}
```

#### Stop audio

```json
{
  "type": "audio_stop",
  "target_node": "n01",
  "request_id": "uuid-1234-5678-90ab",
  "payload": {}
}
```

#### Volume

```json
{
  "type": "audio_volume",
  "target_node": "n01",
  "request_id": "uuid-1234-5678-90ab",
  "payload": {
    "volume": 75
  }
}
```

### 2. Commande LED

**Event:** `command`

```json
{
  "type": "led_control",
  "target_node": "n01",
  "request_id": "uuid-1234-5678-90ab",
  "payload": {
    "led": "red",
    "state": "on"
  }
}
```

**LEDs disponibles:** `red`, `light`, `all`
**States:** `on`, `off`

### 3. Commande Clignotement

**Event:** `command`

```json
{
  "type": "led_blink",
  "target_node": "n01",
  "request_id": "uuid-1234-5678-90ab",
  "payload": {
    "led": "red",
    "period_ms": 1000,
    "duty_percent": 50,
    "duration_s": 5
  }
}
```

## Implémentation ESP32-C6 (Border Router)

### Bibliothèques

```c
#include "esp_websocket_client.h"
#include "cJSON.h"
```

### Configuration

```c
esp_websocket_client_config_t ws_cfg = {
    .uri = "wss://your-server.com/ws/br?br_id=BR-001&auth_token=secret&network_prefix=fd78:../64",
    .reconnect_timeout_ms = 5000,
    .network_timeout_ms = 10000,
};
```

### Handler Événements

```c
static void websocket_event_handler(void *handler_args,
                                   esp_event_base_t base,
                                   int32_t event_id,
                                   void *event_data) {
    esp_websocket_event_data_t *data = event_data;

    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "Connected to cloud server");
            xTaskCreate(heartbeat_task, "heartbeat", 4096, NULL, 5, NULL);
            break;

        case WEBSOCKET_EVENT_DATA:
            handle_server_message(data->data_ptr, data->data_len);
            break;

        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "WebSocket error");
            break;

        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "Disconnected, reconnecting...");
            break;
    }
}
```

### Heartbeat Task

```c
void heartbeat_task(void *pvParameters) {
    char msg[256];

    while(1) {
        int nodes_count = count_thread_nodes();

        snprintf(msg, sizeof(msg),
            "{\"br_id\":\"BR-001\","
            "\"timestamp\":%lld,"
            "\"nodes_count\":%d,"
            "\"status\":\"online\"}",
            esp_timer_get_time() / 1000000,
            nodes_count);

        esp_websocket_client_send_text(ws_client, msg, strlen(msg),
                                      portMAX_DELAY);

        vTaskDelay(pdMS_TO_TICKS(10000));  // 10 secondes
    }
}
```

### Proxy CoAP → WebSocket

```c
void send_node_event_to_cloud(const char *event_type,
                               const char *node_name,
                               cJSON *payload_json) {
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "node_event");
    cJSON_AddStringToObject(root, "br_id", "BR-001");
    cJSON_AddStringToObject(root, "node", node_name);
    cJSON_AddStringToObject(root, "event_type", event_type);
    cJSON_AddItemToObject(root, "payload", payload_json);
    cJSON_AddNumberToObject(root, "timestamp", time(NULL));

    char *json_str = cJSON_PrintUnformatted(root);
    esp_websocket_client_send_text(ws_client, json_str, strlen(json_str),
                                  portMAX_DELAY);

    free(json_str);
    cJSON_Delete(root);
}
```

### Handler Commandes Serveur

```c
void handle_server_message(const char *data, int len) {
    cJSON *json = cJSON_ParseWithLength(data, len);
    if (!json) return;

    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(json, "type"));

    if (strcmp(type, "audio_play") == 0) {
        const char *target = cJSON_GetStringValue(
            cJSON_GetObjectItem(json, "target_node"));
        cJSON *payload = cJSON_GetObjectItem(json, "payload");
        int msg_id = cJSON_GetNumberValue(
            cJSON_GetObjectItem(payload, "message_id"));

        // Envoyer CoAP vers le node Thread
        send_coap_to_thread_node(target, "audio", "play:%d", msg_id);

        // Répondre au serveur
        send_command_response(
            cJSON_GetStringValue(cJSON_GetObjectItem(json, "request_id")),
            target,
            "success",
            NULL
        );
    }

    cJSON_Delete(json);
}
```

## Sécurité

### TLS/SSL

- **Obligatoire en production** : utiliser `wss://` (WebSocket Secure)
- Port 443 (HTTPS)
- Certificat SSL valide requis

### Authentification

1. **Token dans query string** (development)
   ```
   ?br_id=BR-001&auth_token=secret-token
   ```

2. **Certificat client TLS** (production recommandé)
   ```c
   ws_cfg.cert_pem = client_cert_pem_start;
   ws_cfg.client_key = client_key_pem_start;
   ```

### Génération de Tokens

```bash
# Générer un token sécurisé
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Monitoring

### Dashboard BR Status

**Endpoint:** `GET /api/br/status`

```json
{
  "statistics": {
    "total_border_routers": 3,
    "online_border_routers": 2,
    "offline_border_routers": 1,
    "total_nodes": 15,
    "total_commands_sent": 1234,
    "total_events_received": 5678
  },
  "border_routers": {
    "BR-001": {
      "status": "online",
      "nodes_count": 5,
      "last_heartbeat": "2025-10-14T16:30:00",
      "time_since_heartbeat": 5.2
    }
  }
}
```

## Troubleshooting

### BR ne se connecte pas

1. Vérifier token d'authentification dans `config/border_routers.json`
2. Vérifier certificat SSL si TLS
3. Vérifier firewall (port 443 ouvert)
4. Logs ESP32 : `esp_websocket_client` debug level

### Heartbeat timeout

1. Augmenter `BR_HEARTBEAT_TIMEOUT` dans `.env`
2. Vérifier connexion réseau du BR
3. Vérifier latence réseau

### Commandes ne passent pas

1. Vérifier que le BR est `online` dans `/api/br/status`
2. Vérifier que le node est dans la liste des nodes du BR
3. Vérifier logs côté serveur et BR

## Performance

### Latence

- **Heartbeat :** 10 secondes (configurable)
- **Commande → Réponse :** < 500ms (typique)
- **Événement → Cloud :** < 100ms (typique)

### Scalabilité

- **Nombre de BR :** Illimité (limité par serveur)
- **Nodes par BR :** Recommandé < 50
- **WebSocket connections :** 1 par BR

## Ressources

- ESP-IDF WebSocket Client: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_websocket_client.html
- Flask-SocketIO: https://flask-socketio.readthedocs.io/
- WebSocket RFC: https://datatracker.ietf.org/doc/html/rfc6455
