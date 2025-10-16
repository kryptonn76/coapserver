# TODO - Network Scan System Roadmap

## Contexte

Suite aux recommandations de ChatGPT et à l'implémentation des Phases 1-3, ce document liste les améliorations futures pour optimiser le système de scan réseau orchestré.

---

## Phase 4 (OPTIONNEL) : Migration ASGI/AsyncIO 🚀

**Objectif** : Passer d'un modèle multi-threads à un modèle async pour meilleures performances et scalabilité.

### 4.1 Migration vers FastAPI/Starlette
- [ ] Migrer les routes HTTP Flask → FastAPI
- [ ] WebSocket natif via Starlette (remplace Flask-Sock)
- [ ] Garder Socket.IO via `python-socketio[asgi]` pour le web
- [ ] Pattern 2-tasks par BR : `async def reader()` + `async def writer()`
- [ ] Remplacer `queue.Queue` par `asyncio.Queue`

**Alternative** : Quart (Flask-like mais async)

### 4.2 Orchestrateur Async
- [ ] Utiliser `asyncio.Semaphore(16)` pour limiter concurrence
- [ ] `asyncio.wait_for(scan_future, timeout=5)` pour timeout par node
- [ ] `asyncio.as_completed()` pour streamer résultats dès réception
- [ ] Remplacer watchdog thread par `asyncio.create_task()`

**Bénéfices** :
- Meilleure gestion des timeouts (natif asyncio)
- Moins de contentions (pas de locks threading)
- Scalabilité accrue (1000+ connexions simultanées)

**Coût** : 0.5-1 jour de migration + tests

---

## Phase 5 : Scalabilité (50+ nodes, multi-sites) 📊

### 5.1 Message Broker (Redis)
- [ ] Redis Streams pour commandes Python → BR
  - Key pattern : `redis:stream:br:commands:{br_id}`
  - Consumer groups pour workers multiples
- [ ] Redis Pub/Sub pour résultats BR → Python
  - Channel : `scan-results`
  - Channel : `topology-updates`
- [ ] Worker dédié par BR (ou par groupe de BRs)
- [ ] Persistance des commandes pour relecture/retry

**Bénéfices** :
- Multi-process / multi-host (HA)
- Découplage fort (Python API ↔ Worker ↔ BR)
- Observabilité (sniff streams Redis)

**Prérequis** : Phase 4 (asyncio recommandé) + Redis installé

### 5.2 Optimisations ESP32 (Border Router)

#### Pool de Contextes Statiques
- [ ] Remplacer `malloc()/free()` par pool statique
  ```c
  #define MAX_SCAN_CONTEXTS 16
  scan_node_context_t ctx_pool[MAX_SCAN_CONTEXTS];
  uint16_t ctx_bitmap; // bit=1 si libre
  ```
- [ ] Fonction `ctx_pool_alloc()` / `ctx_pool_free()`
- [ ] Zéro fragmentation heap

#### Limiter CoAP Simultané
- [ ] Limiter à 4-8 requêtes CoAP max simultanées
  - Éviter épuisement buffers OpenThread (`otMessage` pool)
- [ ] File d'attente locale si `otCoapSendRequest()` retourne `OT_ERROR_NO_BUFS`
- [ ] Backoff exponentiel : 200ms, 500ms, 1s

#### CBOR Encoding (Optionnel)
- [ ] Encoder payloads CoAP en CBOR au lieu de JSON
  - Librairie : `tinycbor` ou `qcbor`
  - Gain : ~40-60% taille payload
- [ ] Décoder côté Python : `cbor2` library
- [ ] Content-Format CoAP : `application/cbor` (60)

**Bénéfices** :
- Moins de RAM consommée
- Moins de trafic Thread
- Plus rapide (parsing CBOR < JSON)

### 5.3 Rate Limiting & Batching

#### Côté Python
- [ ] Sémaphore pour limiter scans actifs : `asyncio.Semaphore(16)`
- [ ] Batching : scanner par groupes de 16 nodes à la fois
- [ ] Backpressure : si queue BR pleine, attendre avant d'envoyer plus

#### Côté BR
- [ ] Token bucket pour limiter taux de commandes acceptées
  - Ex : max 10 scans/seconde par BR
- [ ] Rejeter commandes si backlog > seuil (ex. 32)

---

## Phase 6 : Observabilité & Monitoring 📈

### 6.1 Métriques par BR
- [ ] Latence moyenne CoAP (par BR, par node)
- [ ] Taux de succès/échec scans (%)
- [ ] Taille moyenne queue WebSocket
- [ ] Nombre de retries CoAP

**Stack recommandée** :
- Prometheus + Grafana
- Export métriques via `/metrics` endpoint (library : `prometheus-client`)

### 6.2 Dashboard Web
- [ ] Afficher état BRs en temps réel
- [ ] Timeline des scans (début, fin, échecs)
- [ ] Graphe topologie Thread interactif (D3.js ou Cytoscape.js)
- [ ] Logs filtrables par BR/node

### 6.3 Alerting
- [ ] Alerte si BR offline > 1 min
- [ ] Alerte si taux échec scan > 50%
- [ ] Alerte si latence CoAP > 5s (99th percentile)

---

## Phase 7 : Tests & Robustesse 🧪

### 7.1 Tests Unitaires
- [ ] `test_border_router_manager.py` : toutes les méthodes
- [ ] `test_native_websocket_handler.py` : enqueue/dequeue, timeouts
- [ ] `test_scan_orchestrator.py` : agrégation, timeouts globaux

### 7.2 Tests d'Intégration
- [ ] Simulateur BR WebSocket (mock ESP32)
  - Génère heartbeats, répond aux scans
- [ ] Test charge : 100 nodes, 10 BRs simultanés
- [ ] Test panne BR : déconnexion pendant scan

### 7.3 Tests Performance
- [ ] Benchmark : temps scan 10/50/100 nodes
- [ ] Profiling Python : identifier hot paths (cProfile)
- [ ] Profiling ESP32 : heap usage, stack overflow checks

---

## Priorités Recommandées

### Court Terme (1-2 semaines)
✅ Phase 1 : Fix bug `get_active_border_routers()` (FAIT)
✅ Phase 2 : Thread TX dédié + optimisation queue (FAIT)
✅ Phase 3 : Timeouts & agrégation robustes (FAIT)

### Moyen Terme (1-2 mois)
- Phase 4 : Migration ASGI/AsyncIO **si besoin de scaler à 50+ nodes**
- Phase 5.2 : Optimisations ESP32 (pool contextes, rate limit)
- Phase 6.1 : Métriques de base (latence, taux succès)

### Long Terme (3-6 mois)
- Phase 5.1 : Redis Streams pour HA multi-host
- Phase 5.2 : CBOR encoding pour réduire bande passante
- Phase 6 : Dashboard complet + alerting
- Phase 7 : Suite de tests automatisés

---

## Références

- **ChatGPT Recommendations** : Voir `QUESTION_CHATGPT_SCAN_BUG.md` (réponses complètes)
- **Architecture actuelle** : `ARCHITECTURE_NETWORK_SCAN.md`
- **RFC WebSocket** : RFC 6455
- **OpenThread CoAP** : https://openthread.io/guides/border-router/coap

---

**Dernière mise à jour** : 2025-10-16
**Auteur** : Claude Code + ChatGPT recommendations
