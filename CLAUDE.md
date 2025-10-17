# CLAUDE.md - Contexte d'exécution du serveur CoAP

Ce fichier fournit le contexte complet pour Claude Code lors du travail sur ce projet.

---

## ⚠️ RÈGLES CRITIQUES - SERVEUR PYTHON

**INTERDICTION ABSOLUE:**
- ❌ **NE JAMAIS tuer le serveur Python** (`server.py`)
- ❌ **NE JAMAIS redémarrer le serveur Python**
- ❌ **NE JAMAIS exécuter** `python3 server.py` ou `lsof -ti:5001 | xargs kill`

**L'utilisateur gère le serveur manuellement. Ne jamais toucher aux processus Python.**

---

## Vue d'ensemble du projet

**LuxNavix CoAP Server** est un serveur Python Flask qui contrôle un réseau de nodes ESP32-C6 via le protocole CoAP sur OpenThread (réseau mesh IPv6). Le système gère la lecture de messages audio et de musique sur les nodes, le monitoring du réseau Thread, et le tracking de localisation via beacons BLE.

---