# =============================================================================
#  ANALYSEUR D'URL SCRYFALL — url_parser.py
# =============================================================================
# Ce module décode les liens (URLs) Scryfall pour en extraire les informations
# nécessaires au téléchargement.
#
# Deux formats d'URLs sont supportés :
#
#   1. URL de SET (set entier) :
#      https://scryfall.com/sets/fin/fr
#      → télécharge toutes les cartes du set "fin" en français
#
#   2. URL de CARTE (une seule carte) :
#      https://scryfall.com/card/fin/101/fr/nom-de-la-carte
#      → télécharge la carte numéro 101 du set "fin" en français
#
# Si l'URL est invalide, une exception ValueError est levée avec un message
# explicatif pour l'utilisateur.
# =============================================================================

from __future__ import annotations

from typing import TypeAlias        # Pour créer des alias de types plus lisibles
from urllib.parse import urlparse   # Pour décomposer une URL en ses parties

from .models import CardRequest, SetRequest   # Nos structures de données


# Alias de type : ScryfallRequest peut être soit un SetRequest soit un CardRequest
# C'est juste un raccourci pour la lisibilité du code
ScryfallRequest: TypeAlias = SetRequest | CardRequest


def parse_set_url(value: str) -> SetRequest:
    """
    Analyse une URL de set Scryfall et retourne un SetRequest.

    Cette fonction est un raccourci strict : elle appelle parse_scryfall_url()
    mais rejette toute URL qui ne pointe pas vers un set.

    Arguments :
        value (str) : URL à analyser, ex: "https://scryfall.com/sets/fin/fr"

    Retourne :
        SetRequest : Objet contenant le code set et la langue.

    Lève :
        ValueError : Si l'URL est invalide ou pointe vers une carte plutôt qu'un set.

    Exemple :
        req = parse_set_url("https://scryfall.com/sets/fin/fr")
        # req.set_code == "fin", req.language == "fr"
    """
    request = parse_scryfall_url(value)
    if not isinstance(request, SetRequest):
        # parse_scryfall_url a réussi mais a retourné une CardRequest → format incorrect
        raise ValueError("Format attendu: https://scryfall.com/sets/fin/fr")
    return request


def parse_scryfall_url(value: str) -> ScryfallRequest:
    """
    Analyse une URL Scryfall (set ou carte) et retourne la requête correspondante.

    Cette fonction est le point d'entrée principal. Elle :
    1. Nettoie l'URL
    2. Vérifie que le domaine est bien scryfall.com
    3. Détecte si c'est un set (/sets/...) ou une carte (/card/...)
    4. Délègue l'analyse détaillée aux fonctions internes

    Arguments :
        value (str) : URL à analyser.

    Retourne :
        SetRequest  : Si l'URL pointe vers un set complet.
        CardRequest : Si l'URL pointe vers une carte individuelle.

    Lève :
        ValueError : Si l'URL est vide, hors scryfall.com, ou au mauvais format.

    Exemples :
        parse_scryfall_url("https://scryfall.com/sets/fin/fr")
        → SetRequest(set_code="fin", language="fr")

        parse_scryfall_url("https://scryfall.com/card/fin/101/fr/forest")
        → CardRequest(set_code="fin", collector_number="101", language="fr")
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("Veuillez saisir un lien Scryfall.")

    # urlparse décompose l'URL en parties : schéma, domaine, chemin, paramètres...
    # Ex: "https://scryfall.com/sets/fin/fr"
    #   parsed.netloc = "scryfall.com"
    #   parsed.path   = "/sets/fin/fr"
    parsed = urlparse(stripped)

    # Vérification que l'URL pointe bien vers scryfall.com
    if parsed.netloc.lower() not in {"scryfall.com", "www.scryfall.com"}:
        raise ValueError("Le lien doit pointer vers scryfall.com.")

    # Découpe le chemin en segments non-vides
    # "/sets/fin/fr".split("/") → ["", "sets", "fin", "fr"]
    # On filtre les segments vides avec [part for part in ... if part]
    parts = [part for part in parsed.path.split("/") if part]

    # Détection du type d'URL selon le premier segment du chemin
    if len(parts) >= 3 and parts[0].lower() == "sets":
        return _parse_set_parts(parts)

    if len(parts) >= 3 and parts[0].lower() == "card":
        return _parse_card_parts(parts)

    raise ValueError(
        "Format attendu: https://scryfall.com/sets/fin/fr "
        "ou https://scryfall.com/card/fic/7/yshtola-nights-blessed"
    )


def _parse_set_parts(parts: list[str]) -> SetRequest:
    """
    Construit un SetRequest depuis les segments du chemin d'une URL de set.

    Le chemin d'une URL de set a exactement 3 segments :
        ["sets", CODE_SET, LANGUE]
    Exemple : ["sets", "fin", "fr"]

    Arguments :
        parts (list[str]) : Segments du chemin de l'URL (sans le premier "/").

    Retourne :
        SetRequest : Objet avec set_code et language normalisés en minuscules.

    Lève :
        ValueError : Si le nombre de segments est incorrect ou si un champ est vide.
    """
    if len(parts) != 3:
        raise ValueError("Format attendu: https://scryfall.com/sets/fin/fr")

    set_code = parts[1].strip().lower()    # Ex: "fin"
    language = parts[2].strip().lower()   # Ex: "fr"

    if not set_code or not language:
        raise ValueError("Le code du set et la langue sont obligatoires.")

    return SetRequest(set_code=set_code, language=language)


def _parse_card_parts(parts: list[str]) -> CardRequest:
    """
    Construit un CardRequest depuis les segments du chemin d'une URL de carte.

    Le chemin d'une URL de carte peut avoir 3 à 5 segments :
        ["card", CODE_SET, NUMERO]            → sans langue
        ["card", CODE_SET, NUMERO, LANGUE, NOM]  → avec langue
    Exemple : ["card", "fin", "101", "fr", "lightning-bolt"]

    La langue est optionnelle. On la détecte à la 4ème position si elle fait
    2 ou 3 caractères (codes de langue ISO : "fr", "en", "zhs", etc.).

    Arguments :
        parts (list[str]) : Segments du chemin de l'URL.

    Retourne :
        CardRequest : Objet avec set_code, collector_number, et language optionnel.

    Lève :
        ValueError : Si le code set ou le numéro collecteur est manquant.
    """
    set_code = parts[1].strip().lower()          # Code du set, ex: "fin"
    collector_number = parts[2].strip().lower()  # Numéro dans le set, ex: "101"
    language = None                              # Langue optionnelle, None par défaut

    # Si l'URL contient un 4ème segment de 2-3 caractères, c'est la langue
    # (codes de langue ISO : "fr", "en", "ja", "zhs", "zht", "ko", etc.)
    if len(parts) >= 5 and len(parts[3]) in {2, 3}:
        language = parts[3].strip().lower()

    if not set_code or not collector_number:
        raise ValueError("Le code du set et le numéro collector sont obligatoires.")

    return CardRequest(set_code=set_code, collector_number=collector_number, language=language)
