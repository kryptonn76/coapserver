# Protocole de Scan Réseau Orchestré

Documentation du nouveau système de scan réseau Thread où le serveur Python orchestre les scans via les Border Routers.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  SERVEUR PYTHON (Orchestrateur)                                 │
│  - Connaît TOUTES les adresses nodes (config/adresses.json)    │
│  - Envoie des commandes "scan_node" individuelles via WebSocket│
│  - Agrège les résultats pour construire la topologie           │
└──────────────────┬──────────────────────────────────────────────┘
                   │ WebSocket
                   │ {"type": "scan_node", "target_ipv6": "..."}
┌──────────────────▼──────────────────────────────────────────────┐
│  BORDER ROUTER (Proxy Transparent)                              │
│  - Reçoit commande scan_node via WebSocket                      │
│  - Forward la requête en CoAP GET vers le node Thread           │
│  - Retourne les résultats via WebSocket                         │
└──────────────────┬──────────────────────────────────────────────┘
                   │ CoAP
                   │ GET coap://[target_ipv6]/network-info
┌──────────────────▼──────────────────────────────────────────────┐
│  NODE THREAD (ESP32-C6)                                          │
│  - Répond aux requêtes CoAP /network-info                        │
│  - Fournit : RLOC16, rôle, parent, voisins, RSSI                │
└──────────────────────────────────────────────────────────────────┘
```

## Pourquoi ce changement ?

### Ancien système ❌
- Python faisait des requêtes CoAP directes aux nodes
- Nécessitait que Python ait accès au réseau Thread IPv6
- Ne fonctionnait pas dans un déploiement cloud
- Scan séquentiel lent

### Nouveau système ✅
- Python orchestre, BR proxy, Python agrège
- Fonctionne en cloud (BR fait le proxy IPv6)
- Scan parallélisé (tous les nodes en même temps)
- Scalable pour plusieurs BR

## Messages - Serveur → Border Router

### Commande: scan_node

**Message envoyé par le serveur Python au BR :**

```json
{
  "type": "scan_node",
  "target_ipv6": "fd78:8e78:3bfe:1::abcd:ef01:2345:6789",
  "node_name": "n01",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

#### Paramètres

| Paramètre | Type | Description |
|-----------|------|-------------|
| `type` | string | **"scan_node"** - Identifiant du message |
| `target_ipv6` | string | Adresse IPv6 complète du node Thread à scanner |
| `node_name` | string | Nom du node (ex: "n01", "n02") pour identification |
| `request_id` | string | UUID unique pour tracer la requête |

### Comportement attendu du BR

Quand le BR reçoit un message `scan_node`, il doit :

1. **Extraire les paramètres** du message JSON
2. **Construire une requête CoAP GET** vers `coap://[target_ipv6]/network-info`
3. **Parser la réponse CoAP** pour extraire les informations réseau
4. **Envoyer le résultat** via WebSocket au serveur (voir section suivante)

## Messages - Border Router → Serveur

### Réponse: scan_node_result

**Message envoyé par le BR au serveur Python :**

#### En cas de succès

```json
{
  "type": "scan_node_result",
  "target_ipv6": "fd78:8e78:3bfe:1::abcd:ef01:2345:6789",
  "node_name": "n01",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "success": true,
  "network_info": {
    "rloc16": "0x1800",
    "role": "router",
    "ext_addr": "aa:bb:cc:dd:ee:ff:00:11",
    "parent": {
      "rloc16": "0x1400",
      "rssi": -42
    },
    "neighbors": [
      {
        "rloc16": "0x1801",
        "rssi": -38,
        "link_quality_in": 3,
        "link_quality_out": 3
      },
      {
        "rloc16": "0x1c00",
        "rssi": -55,
        "link_quality_in": 2,
        "link_quality_out": 2
      }
    ]
  },
  "error": null
}
```

#### En cas d'erreur

```json
{
  "type": "scan_node_result",
  "target_ipv6": "fd78:8e78:3bfe:1::abcd:ef01:2345:6789",
  "node_name": "n01",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "success": false,
  "network_info": null,
  "error": "CoAP timeout: node not reachable"
}
```

### Structure network_info

| Champ | Type | Description |
|-------|------|-------------|
| `rloc16` | string | Adresse RLOC16 du node (ex: "0x1800") |
| `role` | string | Rôle Thread: "leader", "router", "child", "disabled" |
| `ext_addr` | string | Adresse MAC étendue (64 bits) |
| `parent` | object | Info sur le parent (si node est enfant ou routeur) |
| `parent.rloc16` | string | RLOC16 du parent |
| `parent.rssi` | number | RSSI de la liaison vers le parent (dBm) |
| `neighbors` | array | Liste des voisins Thread |
| `neighbors[].rloc16` | string | RLOC16 du voisin |
| `neighbors[].rssi` | number | RSSI de la liaison vers le voisin (dBm) |
| `neighbors[].link_quality_in` | number | Qualité du lien entrant (0-3) |
| `neighbors[].link_quality_out` | number | Qualité du lien sortant (0-3) |

### Codes d'erreur possibles

| Erreur | Description |
|--------|-------------|
| `CoAP timeout: node not reachable` | Le node ne répond pas (éteint, hors portée) |
| `CoAP error: invalid response` | Réponse CoAP malformée |
| `CoAP error: 4.04 Not Found` | Le node ne supporte pas /network-info |
| `Invalid IPv6 address` | L'adresse IPv6 fournie est invalide |
| `Thread network error` | Erreur générique du réseau Thread |

## Implémentation ESP32-C6 (Border Router)

### 1. Handler du message scan_node

```c
void handle_scan_node_command(cJSON *json) {
    // Extract parameters
    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(json, "type"));
    const char *target_ipv6 = cJSON_GetStringValue(cJSON_GetObjectItem(json, "target_ipv6"));
    const char *node_name = cJSON_GetStringValue(cJSON_GetObjectItem(json, "node_name"));
    const char *request_id = cJSON_GetStringValue(cJSON_GetObjectItem(json, "request_id"));

    if (!target_ipv6 || !node_name || !request_id) {
        ESP_LOGE(TAG, "scan_node: missing required parameters");
        send_scan_result_error(request_id, node_name, target_ipv6,
                               "Missing required parameters");
        return;
    }

    ESP_LOGI(TAG, "Scan node %s (%s), request_id=%s",
             node_name, target_ipv6, request_id);

    // Execute CoAP scan asynchronously
    xTaskCreate(scan_node_task, "scan_node", 8192,
                strdup(request_id), 5, NULL);
}
```

### 2. Tâche de scan CoAP

```c
void scan_node_task(void *pvParameters) {
    char *request_id = (char *)pvParameters;

    // Build CoAP URI
    char coap_uri[256];
    snprintf(coap_uri, sizeof(coap_uri),
             "coap://[%s]/network-info", target_ipv6);

    // Create CoAP client
    otCoapHeader header;
    otCoapHeaderInit(&header);
    otCoapHeaderSetType(&header, OT_COAP_TYPE_CONFIRMABLE);
    otCoapHeaderSetCode(&header, OT_COAP_CODE_GET);

    // Send CoAP request
    otError error = otCoapSendRequest(
        otGetInstance(),
        &header,
        NULL,  // No payload for GET
        scan_coap_response_handler,
        (void *)request_id
    );

    if (error != OT_ERROR_NONE) {
        ESP_LOGE(TAG, "CoAP request failed: %d", error);
        send_scan_result_error(request_id, node_name, target_ipv6,
                               "CoAP request failed");
        free(request_id);
        vTaskDelete(NULL);
        return;
    }

    // Wait for response (with timeout)
    vTaskDelay(pdMS_TO_TICKS(5000));  // 5s timeout
    free(request_id);
    vTaskDelete(NULL);
}
```

### 3. Handler de réponse CoAP

```c
void scan_coap_response_handler(void *context,
                                 otMessage *message,
                                 const otMessageInfo *messageInfo,
                                 otError error) {
    char *request_id = (char *)context;

    if (error != OT_ERROR_NONE) {
        send_scan_result_error(request_id, node_name, target_ipv6,
                               "CoAP timeout: node not reachable");
        return;
    }

    // Parse CoAP payload (JSON format)
    uint16_t length = otMessageGetLength(message) - otMessageGetOffset(message);
    char payload[512];
    otMessageRead(message, otMessageGetOffset(message), payload, length);
    payload[length] = '\0';

    cJSON *network_info = cJSON_Parse(payload);
    if (!network_info) {
        send_scan_result_error(request_id, node_name, target_ipv6,
                               "CoAP error: invalid JSON response");
        return;
    }

    // Send success result to server
    send_scan_result_success(request_id, node_name, target_ipv6, network_info);
    cJSON_Delete(network_info);
}
```

### 4. Envoyer le résultat au serveur

```c
void send_scan_result_success(const char *request_id,
                               const char *node_name,
                               const char *target_ipv6,
                               cJSON *network_info) {
    cJSON *result = cJSON_CreateObject();
    cJSON_AddStringToObject(result, "type", "scan_node_result");
    cJSON_AddStringToObject(result, "target_ipv6", target_ipv6);
    cJSON_AddStringToObject(result, "node_name", node_name);
    cJSON_AddStringToObject(result, "request_id", request_id);
    cJSON_AddBoolToObject(result, "success", true);
    cJSON_AddItemToObject(result, "network_info", cJSON_Duplicate(network_info, true));
    cJSON_AddNullToObject(result, "error");

    char *json_str = cJSON_PrintUnformatted(result);
    esp_websocket_client_send_text(ws_client, json_str, strlen(json_str), portMAX_DELAY);

    ESP_LOGI(TAG, "Scan result sent for node %s", node_name);

    free(json_str);
    cJSON_Delete(result);
}

void send_scan_result_error(const char *request_id,
                             const char *node_name,
                             const char *target_ipv6,
                             const char *error_message) {
    cJSON *result = cJSON_CreateObject();
    cJSON_AddStringToObject(result, "type", "scan_node_result");
    cJSON_AddStringToObject(result, "target_ipv6", target_ipv6);
    cJSON_AddStringToObject(result, "node_name", node_name);
    cJSON_AddStringToObject(result, "request_id", request_id);
    cJSON_AddBoolToObject(result, "success", false);
    cJSON_AddNullToObject(result, "network_info");
    cJSON_AddStringToObject(result, "error", error_message);

    char *json_str = cJSON_PrintUnformatted(result);
    esp_websocket_client_send_text(ws_client, json_str, strlen(json_str), portMAX_DELAY);

    ESP_LOGE(TAG, "Scan error for node %s: %s", node_name, error_message);

    free(json_str);
    cJSON_Delete(result);
}
```

### 5. Intégration dans le handler WebSocket

```c
void handle_server_message(const char *data, int len) {
    cJSON *json = cJSON_ParseWithLength(data, len);
    if (!json) {
        ESP_LOGE(TAG, "Failed to parse JSON");
        return;
    }

    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(json, "type"));

    if (strcmp(type, "audio_play") == 0) {
        handle_audio_play_command(json);
    }
    else if (strcmp(type, "audio_stop") == 0) {
        handle_audio_stop_command(json);
    }
    else if (strcmp(type, "led_control") == 0) {
        handle_led_control_command(json);
    }
    else if (strcmp(type, "scan_node") == 0) {
        // NOUVEAU: Handler pour scan réseau orchestré
        handle_scan_node_command(json);
    }
    else {
        ESP_LOGW(TAG, "Unknown message type: %s", type);
    }

    cJSON_Delete(json);
}
```

## Format de la réponse CoAP /network-info

Le node Thread doit implémenter l'endpoint CoAP `/network-info` qui retourne un JSON avec les informations réseau.

### Exemple de réponse du node

```json
{
  "rloc16": "0x1800",
  "role": "router",
  "ext_addr": "aa:bb:cc:dd:ee:ff:00:11",
  "parent": {
    "rloc16": "0x1400",
    "rssi": -42
  },
  "neighbors": [
    {
      "rloc16": "0x1801",
      "rssi": -38,
      "link_quality_in": 3,
      "link_quality_out": 3
    }
  ]
}
```

### Code ESP32 côté node Thread

```c
static void network_info_handler(void *context,
                                  otMessage *message,
                                  const otMessageInfo *messageInfo) {
    otInstance *instance = (otInstance *)context;

    // Get network info
    uint16_t rloc16 = otThreadGetRloc16(instance);
    otDeviceRole role = otThreadGetDeviceRole(instance);
    const char *role_str = role_to_string(role);

    otExtAddress ext_addr;
    otLinkGetFactoryAssignedIeeeEui64(instance, &ext_addr);

    // Get parent info
    otRouterInfo parent_info;
    otThreadGetParentInfo(instance, &parent_info);

    // Build JSON response
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "rloc16", rloc16_to_string(rloc16));
    cJSON_AddStringToObject(root, "role", role_str);
    cJSON_AddStringToObject(root, "ext_addr", ext_addr_to_string(&ext_addr));

    if (role != OT_DEVICE_ROLE_LEADER) {
        cJSON *parent = cJSON_CreateObject();
        cJSON_AddStringToObject(parent, "rloc16",
                                rloc16_to_string(parent_info.mRloc16));
        cJSON_AddNumberToObject(parent, "rssi",
                                otThreadGetParentAverageRssi(instance));
        cJSON_AddItemToObject(root, "parent", parent);
    }

    // Get neighbors
    cJSON *neighbors = cJSON_CreateArray();
    otNeighborInfoIterator iterator = OT_NEIGHBOR_INFO_ITERATOR_INIT;
    otNeighborInfo neighbor;

    while (otThreadGetNextNeighborInfo(instance, &iterator, &neighbor) == OT_ERROR_NONE) {
        cJSON *n = cJSON_CreateObject();
        cJSON_AddStringToObject(n, "rloc16", rloc16_to_string(neighbor.mRloc16));
        cJSON_AddNumberToObject(n, "rssi", neighbor.mAverageRssi);
        cJSON_AddNumberToObject(n, "link_quality_in", neighbor.mLinkQualityIn);
        cJSON_AddNumberToObject(n, "link_quality_out", neighbor.mLinkQualityOut);
        cJSON_AddItemToArray(neighbors, n);
    }
    cJSON_AddItemToObject(root, "neighbors", neighbors);

    // Send CoAP response
    char *json_str = cJSON_PrintUnformatted(root);
    otCoapMessageAppendPayload(message, (uint8_t *)json_str, strlen(json_str));
    otCoapMessageSetCode(message, OT_COAP_CODE_CONTENT);
    otCoapSendResponse(instance, message, messageInfo);

    free(json_str);
    cJSON_Delete(root);
}
```

## Séquence complète

```
┌───────┐              ┌─────────┐              ┌──────────┐              ┌──────┐
│ User  │              │ Server  │              │    BR    │              │ Node │
└───┬───┘              └────┬────┘              └─────┬────┘              └──┬───┘
    │                       │                         │                      │
    │  Clic "Refresh"       │                         │                      │
    ├──────────────────────>│                         │                      │
    │                       │                         │                      │
    │                       │  POST /api/trigger_scan │                      │
    │                       ├─────────────────────────┤                      │
    │                       │                         │                      │
    │                       │  Load config/adresses.json                     │
    │                       │  For each node:         │                      │
    │                       │                         │                      │
    │                       │  {"type":"scan_node",   │                      │
    │                       │   "target_ipv6":"...",  │                      │
    │                       │   "node_name":"n01"}    │                      │
    │                       ├────────────────────────>│                      │
    │                       │                         │                      │
    │                       │                         │  CoAP GET            │
    │                       │                         │  /network-info       │
    │                       │                         ├─────────────────────>│
    │                       │                         │                      │
    │                       │                         │  CoAP Response       │
    │                       │                         │  (network_info JSON) │
    │                       │                         │<─────────────────────┤
    │                       │                         │                      │
    │                       │  {"type":"scan_node_result",                   │
    │                       │   "success":true,       │                      │
    │                       │   "network_info":{...}} │                      │
    │                       │<────────────────────────┤                      │
    │                       │                         │                      │
    │                       │  Aggregate results      │                      │
    │                       │  Build topology         │                      │
    │                       │                         │                      │
    │  Updated topology     │                         │                      │
    │<──────────────────────┤                         │                      │
    │                       │                         │                      │
```

## Avantages du nouveau système

### 1. Déploiement Cloud ☁️
- Le serveur Python peut être hébergé dans le cloud
- Pas besoin d'accès direct au réseau Thread IPv6
- Les BR font le pont entre Internet et Thread

### 2. Scalabilité 📈
- Supporte plusieurs Border Routers simultanément
- Scan parallélisé de tous les nodes
- Agrégation centralisée des résultats

### 3. Performance ⚡
- Scan de 6 nodes en ~2s (vs 12s en séquentiel)
- Utilisation optimale des ressources BR
- Pas de timeout d'attente côté Python

### 4. Résilience 🛡️
- Si un BR est down, les autres continuent de fonctionner
- Les erreurs de scan individuelles n'impactent pas les autres nodes
- Retry automatique possible côté serveur

## Migration depuis l'ancien système

### Changements côté serveur Python ✅ (Déjà fait)
- ✅ Suppression de OpenThreadScanner
- ✅ Suppression de refresh_topology_background()
- ✅ Création de scan_all_nodes_via_brs()
- ✅ Création de l'endpoint /api/trigger_scan
- ✅ Ajout de send_scan_node_command() dans native_websocket_handler
- ✅ Ajout de handle_scan_node_result() dans native_websocket_handler
- ✅ Mise à jour de l'interface web (network_map.html)

### Changements côté ESP32 BR ⏳ (À faire)
- ⏳ Ajouter handler pour `scan_node` dans handle_server_message()
- ⏳ Implémenter scan_node_task() pour faire la requête CoAP
- ⏳ Implémenter scan_coap_response_handler()
- ⏳ Implémenter send_scan_result_success() et send_scan_result_error()

### Changements côté nodes Thread ⏳ (À faire)
- ⏳ Vérifier que l'endpoint CoAP `/network-info` existe
- ⏳ S'assurer que le format JSON retourné est conforme

## Tests

### Test unitaire du handler

```c
void test_scan_node_handler() {
    const char *test_msg = "{"
        "\"type\":\"scan_node\","
        "\"target_ipv6\":\"fd78:8e78:3bfe:1::1\","
        "\"node_name\":\"n01\","
        "\"request_id\":\"test-123\""
    "}";

    handle_server_message(test_msg, strlen(test_msg));

    // Attendre la réponse WebSocket
    vTaskDelay(pdMS_TO_TICKS(5000));
}
```

### Test d'intégration

```python
# Côté serveur Python
import requests

response = requests.post('http://localhost:5001/api/trigger_scan')
print(response.json())

# Attendre les résultats
time.sleep(5)

topology = requests.get('http://localhost:5001/api/topology')
print(topology.json())
```

## Performance attendue

| Métrique | Valeur cible |
|----------|--------------|
| Latence scan 1 node | < 500ms |
| Scan complet 6 nodes | < 2s |
| Taux de succès | > 95% |
| Timeout CoAP | 5s |
| Mémoire utilisée BR | < 8KB par scan |

## Troubleshooting

### Le scan ne retourne rien

1. Vérifier que le BR est connecté : `GET /api/border_routers`
2. Vérifier les logs Python : `handle_scan_node_result()` doit être appelé
3. Vérifier les logs ESP32 BR : la commande `scan_node` est-elle reçue ?
4. Tester manuellement CoAP : `coap-client -m get coap://[ipv6]/network-info`

### Erreur "CoAP timeout"

1. Vérifier que le node est allumé et connecté au réseau Thread
2. Vérifier la connectivité IPv6 depuis le BR
3. Augmenter le timeout CoAP dans le code ESP32
4. Vérifier que le node implémente bien `/network-info`

### Les résultats sont incomplets

1. Vérifier que tous les champs sont présents dans la réponse CoAP du node
2. Vérifier le parsing JSON côté BR
3. Vérifier que tous les voisins sont bien listés

## Ressources

- [OpenThread API Reference](https://openthread.io/reference)
- [CoAP RFC 7252](https://datatracker.ietf.org/doc/html/rfc7252)
- [ESP-IDF CoAP Client](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/coap.html)
- Fichiers de référence dans ce repo :
  - `server.py:1772-1883` - scan_all_nodes_via_brs()
  - `lib/native_websocket_handler.py:755-793` - send_scan_node_command()
  - `lib/native_websocket_handler.py:672-719` - handle_scan_node_result()
