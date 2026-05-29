# =============================================================================
#  INITIALISATION DU PAQUET (PACKAGE)
# =============================================================================
# Ce fichier marque le dossier "scryfall_art_downloader" comme un paquet Python.
# Un paquet Python, c'est simplement un dossier qui contient plusieurs fichiers
# .py liés entre eux.
#
# Ce fichier peut rester vide (ou presque) — son existence suffit à indiquer
# à Python que ce dossier contient du code importable.
# =============================================================================

"""
Scryfall Artwork Downloader
===========================
Application de téléchargement d'illustrations de cartes Magic: The Gathering
depuis Scryfall (https://scryfall.com).

Modules inclus :
    - app.py             : Interface graphique (fenêtre principale)
    - models.py          : Structures de données (cartes, impressions, requêtes)
    - scryfall_client.py : Communication avec l'API Scryfall
    - downloader.py      : Téléchargement des images sur le disque
    - local_bulk_catalog : Index local des données Scryfall (sans internet)
    - decklist_parser.py : Analyse d'une liste de cartes en texte brut
    - url_parser.py      : Analyse des liens Scryfall
    - aspect_cropper.py  : Recadrage au ratio des cartes Magic (0.714:1)
    - dpi_upscaler.py    : Normalisation DPI pour l'impression (1200 DPI)
    - margin_creator.py  : Ajout de marges colorées autour des cartes
"""
