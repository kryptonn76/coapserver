#!/usr/bin/env python3
"""
Module de gestion de la bibliothèque audio
Gère le catalogue des 258 messages vocaux + 95 pistes musicales
"""

import json
from pathlib import Path
from typing import List, Dict, Optional

class AudioLibrary:
    """Gestion du catalogue audio ESP32"""

    def __init__(self, catalog_path: str = "data/audio_catalog_esp32.json"):
        """
        Initialise la bibliothèque audio

        Args:
            catalog_path: Chemin vers le fichier catalogue JSON
        """
        self.catalog_path = Path(catalog_path)
        self.catalog = {}
        self.load_catalog()

    def load_catalog(self):
        """Charge le catalogue depuis le fichier JSON"""
        if not self.catalog_path.exists():
            print(f"⚠️  Catalogue non trouvé: {self.catalog_path}")
            print("    Exécutez: python3 generate_audio_catalog.py")
            return

        with open(self.catalog_path, 'r', encoding='utf-8') as f:
            self.catalog = json.load(f)

        print(f"✓ Catalogue audio chargé: {self.catalog.get('total_messages', 0)} messages")

    def get_instant_messages(self, count: int = 20) -> List[Dict]:
        """
        Retourne les messages instantanés prioritaires pour le tertiaire

        Args:
            count: Nombre de messages à retourner (défaut: 20)

        Returns:
            Liste des messages prioritaires
        """
        instant = self.catalog.get('instant_messages', [])
        return instant[:count]

    def get_category(self, category: str) -> Dict:
        """
        Retourne tous les messages d'une catégorie

        Args:
            category: Nom de la catégorie (ex: 'alertes_pti')

        Returns:
            Dictionnaire avec description et messages
        """
        categories = self.catalog.get('categories', {})
        return categories.get(category, {})

    def get_all_categories(self) -> Dict:
        """
        Retourne toutes les catégories avec leurs messages

        Returns:
            Dictionnaire complet des catégories
        """
        return self.catalog.get('categories', {})

    def search(self, keywords: str) -> List[Dict]:
        """
        Recherche full-text dans les descriptions

        Args:
            keywords: Mots-clés à rechercher (insensible à la casse)

        Returns:
            Liste des messages correspondants
        """
        keywords_lower = keywords.lower()
        results = []

        all_messages = self.catalog.get('all_messages', [])

        for msg in all_messages:
            description = msg.get('description', '').lower()

            # Recherche simple (contient les mots-clés)
            if keywords_lower in description:
                results.append(msg)

        return results

    def get_message_by_id(self, msg_id: int) -> Optional[Dict]:
        """
        Récupère un message par son ID

        Args:
            msg_id: ID du message (1-258)

        Returns:
            Dictionnaire du message ou None si non trouvé
        """
        all_messages = self.catalog.get('all_messages', [])

        for msg in all_messages:
            if msg.get('id') == msg_id:
                return msg

        return None

    def get_statistics(self) -> Dict:
        """
        Retourne les statistiques du catalogue

        Returns:
            Dictionnaire avec nombre total, catégories, etc.
        """
        return {
            'total_messages': self.catalog.get('total_messages', 0),
            'categories_count': self.catalog.get('categories_count', 0),
            'instant_messages_count': len(self.catalog.get('instant_messages', [])),
            'categories': {
                name: data.get('count', 0)
                for name, data in self.catalog.get('categories', {}).items()
            }
        }

    def get_category_names(self) -> List[str]:
        """
        Retourne la liste des noms de catégories

        Returns:
            Liste des noms de catégories
        """
        return list(self.catalog.get('categories', {}).keys())

    def format_for_web(self, messages: List[Dict]) -> List[Dict]:
        """
        Formate les messages pour l'affichage web

        Args:
            messages: Liste de messages

        Returns:
            Liste formatée avec informations simplifiées
        """
        formatted = []

        for msg in messages:
            formatted.append({
                'id': msg.get('id'),
                'description': msg.get('description'),
                'category': msg.get('category'),
                'path': msg.get('path_relative'),  # Chemin relatif pour affichage
                'path_full': msg.get('path'),      # Chemin complet pour ESP32
                'filename': msg.get('filename')
            })

        return formatted

# Instance globale (pour import direct)
audio_lib = AudioLibrary()

if __name__ == "__main__":
    # Tests
    lib = AudioLibrary()

    print("\n=== Tests AudioLibrary ===\n")

    # Statistiques
    stats = lib.get_statistics()
    print(f"📊 Statistiques:")
    print(f"   Total messages: {stats['total_messages']}")
    print(f"   Catégories: {stats['categories_count']}")
    print(f"   Messages instantanés: {stats['instant_messages_count']}")
    print()

    # Messages instantanés
    instant = lib.get_instant_messages(5)
    print(f"📢 Top 5 messages instantanés:")
    for msg in instant:
        print(f"   [{msg['id']}] {msg['description'][:60]}...")
    print()

    # Recherche
    results = lib.search("évacuation")
    print(f"🔍 Recherche 'évacuation': {len(results)} résultats")
    for msg in results[:3]:
        print(f"   [{msg['id']}] {msg['description'][:60]}...")
    print()

    # Catégorie
    alertes = lib.get_category('alertes_pti')
    print(f"📁 Catégorie 'alertes_pti': {alertes.get('count', 0)} messages")
    for msg in alertes.get('messages', [])[:3]:
        print(f"   [{msg['id']}] {msg['description'][:60]}...")
