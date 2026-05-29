# =============================================================================
#  CLIENT API SCRYFALL — scryfall_client.py
# =============================================================================
# Ce module gère toute la communication avec le site Scryfall.
#
# Scryfall met à disposition une API (interface de programmation) gratuite
# permettant d'obtenir des informations sur les cartes Magic : noms, images,
# éditions, langues, etc.
#
# Ce module s'occupe de :
#   1. Envoyer des requêtes HTTP à l'API Scryfall
#   2. Respecter les limites de débit (ne pas envoyer trop de requêtes trop vite)
#   3. Mettre en cache les résultats pour éviter de redemander les mêmes données
#   4. Convertir les réponses JSON en objets Python (CardImage, CardPrint...)
#
# LIMITE DE DÉBIT (Rate Limiting) :
# Scryfall impose un délai minimum entre les requêtes pour éviter la surcharge
# de leurs serveurs. Ce module respecte scrupuleusement ces limites.
# Voir : https://scryfall.com/docs/api
# =============================================================================

from __future__ import annotations

import hashlib    # Pour générer des empreintes SHA1 (noms de fichiers cache uniques)
import json       # Pour lire/écrire du JSON (format de données de Scryfall)
import re         # Pour les expressions régulières (extraction de délais dans les erreurs)
import threading  # Pour la gestion thread-safe du limiteur de débit
import time       # Pour mesurer le temps et faire des pauses
from dataclasses import asdict   # Pour convertir un dataclass en dictionnaire
from pathlib import Path         # Pour manipuler les chemins de fichiers
from typing import Callable, Iterable   # Types pour les fonctions de rappel (callbacks)
from urllib.error import HTTPError, URLError   # Exceptions pour les erreurs HTTP
from urllib.parse import quote, urlencode, urlparse   # Outils d'URL
from urllib.request import Request, urlopen            # Pour envoyer des requêtes HTTP

from .models import CardImage, CardPrint, CardRequest, SetRequest   # Nos structures de données


# ---------------------------------------------------------------------------
#  Constantes de configuration
# ---------------------------------------------------------------------------

SCRYFALL_API = "https://api.scryfall.com"
# URL de base de l'API Scryfall. Toutes les requêtes commencent par cette adresse.

USER_AGENT = "ScryfallArtDownloader/1.0"
# Identifiant envoyé à Scryfall dans chaque requête pour qu'ils sachent qui parle.
# Scryfall le demande dans leurs conditions d'utilisation.

DEFAULT_REQUEST_INTERVAL_SECONDS = 0.11
# Délai minimum entre deux requêtes sur les endpoints normaux (~9 requêtes/seconde).
# Scryfall recommande max 10 req/s.

SLOW_ENDPOINT_INTERVAL_SECONDS = 0.55
# Délai plus long pour les endpoints "lourds" qui sollicitent davantage les serveurs.

MAX_RATE_LIMIT_RETRIES = 3
# Nombre maximum de fois qu'on réessaie si Scryfall répond "trop de requêtes" (HTTP 429).

MAX_RATE_LIMIT_DELAY_SECONDS = 30
# Pause maximum en secondes lors d'un HTTP 429.

PRINT_SEARCH_CACHE_SECONDS = 24 * 60 * 60
# Durée de vie du cache local : 24 heures.
# Passé ce délai, les données sont considérées périmées et re-téléchargées.

PRINT_SEARCH_CACHE_DIR = Path.home() / ".scryfall_art_downloader" / "api_cache" / "prints"
# Dossier où sont stockés les fichiers de cache.
# Path.home() = C:\Users\NomUtilisateur sur Windows.

SLOW_ENDPOINTS = (
    "/cards/search",    # Recherche générale — retourne potentiellement des milliers de résultats
    "/cards/named",     # Recherche par nom exact
    "/cards/random",    # Carte aléatoire
    "/cards/collection",# Requête de collection
)
# Ces endpoints sont plus lents à répondre → on leur applique un délai plus long.


# ---------------------------------------------------------------------------
#  Classe principale ScryfallClient
# ---------------------------------------------------------------------------

class ScryfallClient:
    """
    Client HTTP pour l'API Scryfall.

    Cette classe centralise toutes les communications réseau avec Scryfall.
    Elle gère :
    - Le respect des limites de débit (trop de requêtes trop vite → ban temporaire)
    - Le cache local des résultats de recherche (évite de re-télécharger)
    - La conversion des données JSON brutes en objets Python structurés

    Note sur le limiteur de débit :
        _request_lock et _last_request_at_by_bucket sont des attributs de CLASSE
        (partagés entre toutes les instances) pour éviter que plusieurs threads
        simultanés ne dépassent la limite.

    Usage :
        client = ScryfallClient()
        for card_image in client.iter_card_images(set_request):
            ...
    """

    # Verrou partagé entre toutes les instances pour sérialiser les requêtes
    # (évite que deux threads envoient des requêtes en même temps)
    _request_lock = threading.Lock()

    # Mémorise le dernier instant de requête par "bucket" (groupe de endpoints)
    # Clé = nom du bucket ("default" ou chemin endpoint), Valeur = timestamp
    _last_request_at_by_bucket: dict[str, float] = {}

    def __init__(self, pause_seconds: float = 0.1) -> None:
        """
        Initialise le client Scryfall.

        Arguments :
            pause_seconds (float) : Pause additionnelle entre les pages de résultats
                                    (en plus du limiteur de débit automatique).
                                    Par défaut : 0.1 seconde.
        """
        self.pause_seconds = pause_seconds   # Pause optionnelle entre chaque page de résultats

    # -----------------------------------------------------------------------
    #  Méthodes publiques
    # -----------------------------------------------------------------------

    def iter_card_images(
        self,
        set_request: SetRequest,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
        on_total: Callable[[int], None] | None = None,
    ) -> Iterable[CardImage]:
        """
        Génère (itère) toutes les images de cartes d'un set Scryfall.

        Cette méthode parcourt les résultats paginés de Scryfall et retourne
        une image à la fois (via "yield"), ce qui évite de tout charger en mémoire.

        Arguments :
            set_request (SetRequest)           : Set et langue à télécharger.
            image_size  (str)                  : Taille d'image souhaitée ("large" par défaut).
            on_status   (Callable|None)        : Fonction appelée pour signaler la progression.
            on_total    (Callable|None)        : Fonction appelée une fois avec le nombre total.

        Génère (yield) :
            CardImage : Une image de carte à la fois.
        """
        # Construction de la requête de recherche Scryfall
        # "set:FIN lang:fr" → toutes les cartes du set FIN en français
        query = f"set:{set_request.set_code} lang:{set_request.language}"
        params = urlencode(
            {
                "q": query,
                "unique": "prints",           # Une seule version par numéro de collecteur
                "order": "set",               # Trier par ordre du set
                "include_multilingual": "true",  # Inclure toutes les langues
            }
        )
        # URL de la première page de résultats
        next_url: str | None = f"{SCRYFALL_API}/cards/search?{params}"

        # Boucle sur toutes les pages (Scryfall pagine les résultats à 175 cartes/page)
        while next_url:
            if on_status:
                on_status(f"Lecture Scryfall: {next_url}")

            payload = self._get_json(next_url, on_status)

            # On signale le total une seule fois (à la première page)
            if on_total and payload.get("total_cards"):
                on_total(int(payload["total_cards"]))
                on_total = None   # On met à None pour ne pas rappeler cette fonction

            # Parcours de chaque carte dans la page courante
            for raw_card in payload.get("data", []):
                image_url = self._extract_image_url(raw_card, image_size)
                if not image_url:
                    continue   # Carte sans image dans ce format → on passe
                yield CardImage(
                    set_code=set_request.set_code,
                    language=set_request.language,
                    collector_number=str(raw_card.get("collector_number", "")),
                    name=str(raw_card.get("name", "")),
                    image_url=image_url,
                )

            # Passage à la page suivante (None si c'était la dernière page)
            next_url = payload.get("next_page") if payload.get("has_more") else None
            if next_url:
                time.sleep(self.pause_seconds)   # Petite pause entre les pages

    def get_card_image(
        self,
        card_request: CardRequest,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
    ) -> CardImage:
        """
        Télécharge les informations d'une seule carte spécifique.

        Arguments :
            card_request (CardRequest)    : Set, numéro collecteur et langue.
            image_size   (str)            : Taille d'image souhaitée ("large" par défaut).
            on_status    (Callable|None)  : Fonction de progression.

        Retourne :
            CardImage : L'image de la carte demandée.

        Lève :
            RuntimeError : Si aucune image n'est disponible dans la taille demandée.
        """
        url = self._card_url(card_request)
        if on_status:
            on_status(f"Lecture Scryfall: {url}")

        raw_card = self._get_json(url, on_status)
        image_url = self._extract_image_url(raw_card, image_size)
        if not image_url:
            raise RuntimeError(f"Aucune image '{image_size}' trouvée pour cette carte.")

        return CardImage(
            set_code=str(raw_card.get("set", card_request.set_code)),
            language=str(raw_card.get("lang", card_request.language or "")),
            collector_number=str(raw_card.get("collector_number", card_request.collector_number)),
            name=str(raw_card.get("name", "")),
            image_url=image_url,
        )

    def search_card_prints(
        self,
        name: str,
        language: str,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
    ) -> list[CardPrint]:
        """
        Recherche toutes les impressions (éditions) disponibles d'une carte.

        Exemples : "Lightning Bolt" a été imprimé dans des dizaines de sets.
        Cette méthode retourne toutes les versions disponibles, triées avec
        la langue cible en premier, puis par date de sortie décroissante.

        Stratégie de recherche :
        1. On vérifie d'abord le cache local (évite une requête réseau si < 24h)
        2. On cherche l'oracle_id de la carte (identifiant interne Scryfall)
        3. On cherche toutes les impressions par oracle_id (plus fiable que par nom)
        4. Si l'oracle_id est introuvable, on cherche par nom exact

        Arguments :
            name       (str)           : Nom exact de la carte.
            language   (str)           : Langue préférée (ex: "fr").
            image_size (str)           : Taille d'image souhaitée.
            on_status  (Callable|None) : Fonction de progression.

        Retourne :
            list[CardPrint] : Toutes les impressions disponibles, triées.
        """
        # Étape 1 : Vérifier le cache
        cached_prints = self._read_print_search_cache(name, language, image_size)
        if cached_prints is not None:
            if on_status:
                on_status(f"Cache Scryfall: {name} ({language.upper()})")
            return cached_prints

        # Étape 2 : Résoudre l'oracle_id (ID unique de la "règle" de la carte)
        # Toutes les impressions d'une même carte partagent le même oracle_id
        oracle_id = self._oracle_id_for_name(name, on_status)
        if oracle_id:
            # Recherche par oracle_id → trouve toutes les impressions sans erreur de langue
            query = f"oracleid:{oracle_id} lang:any"
        else:
            # Repli : recherche par nom exact avec guillemets → "Lightning Bolt"
            query = f'!"{self._escape_search_text(name)}" lang:any'

        params = urlencode(
            {
                "q": query,
                "unique": "prints",     # Une entrée par impression unique
                "order": "released",    # Trier par date de sortie
                "dir": "desc",          # Plus récent en premier
                "include_multilingual": "true",
            }
        )
        next_url: str | None = f"{SCRYFALL_API}/cards/search?{params}"
        prints: list[CardPrint] = []

        # Parcours de toutes les pages de résultats
        while next_url:
            if on_status:
                on_status(f"Recherche editions: {name} (ANY)")
            try:
                payload = self._get_json(next_url, on_status)
            except RuntimeError as error:
                if "HTTP 404" in str(error):
                    # La carte n'existe pas sur Scryfall → on s'arrête proprement
                    break
                raise   # Autre erreur → on propage

            for raw_card in payload.get("data", []):
                card_print = self._raw_card_to_print(raw_card, image_size)
                if card_print is not None:
                    prints.append(card_print)

            next_url = payload.get("next_page") if payload.get("has_more") else None
            if next_url:
                time.sleep(self.pause_seconds)

        # Tri final : langue cible en premier, puis par date décroissante
        # La clé de tri est un tuple : (True=langue cible, date) → inversé → langue cible + récent d'abord
        prints.sort(key=lambda p: (p.language == language, p.released_at), reverse=True)

        # Sauvegarde dans le cache pour éviter une prochaine requête réseau
        self._write_print_search_cache(name, language, image_size, prints)
        return prints

    # -----------------------------------------------------------------------
    #  Méthodes privées (internes à la classe)
    # -----------------------------------------------------------------------

    def _oracle_id_for_name(self, name: str, on_status: Callable[[str], None] | None = None) -> str | None:
        """
        Résout le nom d'une carte en son oracle_id Scryfall.

        L'oracle_id est un identifiant unique qui regroupe toutes les impressions
        d'une même carte, quelle que soit la langue ou le set.

        Arguments :
            name       (str)           : Nom exact de la carte.
            on_status  (Callable|None) : Fonction de progression.

        Retourne :
            str | None : L'oracle_id si trouvé, None si la carte est introuvable.
        """
        params = urlencode({"exact": name})
        url = f"{SCRYFALL_API}/cards/named?{params}"
        if on_status:
            on_status(f"Resolution du nom: {name}")
        try:
            raw_card = self._get_json(url, on_status)
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return None   # Carte introuvable → retourne None sans erreur
            raise
        oracle_id = raw_card.get("oracle_id")
        return str(oracle_id) if oracle_id else None

    @staticmethod
    def _card_url(card_request: CardRequest) -> str:
        """
        Construit l'URL de l'API Scryfall pour une carte spécifique.

        Arguments :
            card_request (CardRequest) : Set, numéro, et langue optionnelle.

        Retourne :
            str : URL de l'API, ex: "https://api.scryfall.com/cards/fin/101/fr"
        """
        # quote() encode les caractères spéciaux dans l'URL (espace → %20, etc.)
        set_code = quote(card_request.set_code, safe="")
        collector_number = quote(card_request.collector_number, safe="")
        if card_request.language:
            language = quote(card_request.language, safe="")
            return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}/{language}"
        return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"

    def _get_json(self, url: str, on_status: Callable[[str], None] | None = None) -> dict:
        """
        Effectue une requête HTTP GET et retourne la réponse JSON parsée.

        Gère automatiquement :
        - Le respect du limiteur de débit (pause avant la requête si nécessaire)
        - Les erreurs HTTP 429 (trop de requêtes) avec retry automatique
        - Les erreurs réseau

        Arguments :
            url       (str)           : URL à requêter.
            on_status (Callable|None) : Fonction de progression.

        Retourne :
            dict : La réponse JSON de Scryfall.

        Lève :
            RuntimeError : En cas d'erreur HTTP ou réseau après tous les essais.
        """
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

        # On essaie jusqu'à MAX_RATE_LIMIT_RETRIES fois en cas de HTTP 429
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._wait_for_rate_limit(url)   # Pause si on va trop vite
            try:
                with urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))

            except HTTPError as error:
                # Lecture du corps de la réponse d'erreur pour plus de détails
                detail = error.read().decode("utf-8", errors="replace")

                if error.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                    # HTTP 429 = "Too Many Requests" → on attend et on réessaie
                    delay = self._rate_limit_delay(detail)
                    if on_status:
                        on_status(f"Limite Scryfall atteinte, pause {delay} secondes avant reprise.")
                    time.sleep(delay)
                    continue   # Réessaie depuis le début de la boucle

                raise RuntimeError(f"Erreur Scryfall HTTP {error.code}: {detail}") from error

            except URLError as error:
                # Problème réseau (pas de connexion, DNS, timeout...)
                raise RuntimeError(f"Impossible de joindre Scryfall: {error.reason}") from error

        # On arrive ici seulement si tous les essais ont échoué avec HTTP 429
        raise RuntimeError("Erreur Scryfall: nombre maximum de reprises atteint.")

    @classmethod
    def _wait_for_rate_limit(cls, url: str = "") -> None:
        """
        Attend si nécessaire pour respecter le limiteur de débit.

        Cette méthode est thread-safe grâce au verrou _request_lock.
        Elle calcule combien de temps s'est écoulé depuis la dernière requête
        sur le même "bucket" et dort la durée manquante.

        Arguments :
            url (str) : URL de la requête (pour déterminer le bucket).
        """
        bucket, interval = cls._rate_limit_bucket(url)
        with cls._request_lock:
            now = time.monotonic()
            # Temps écoulé depuis la dernière requête sur ce bucket
            elapsed = now - cls._last_request_at_by_bucket.get(bucket, 0.0)
            if elapsed < interval:
                # Pas encore assez de temps écoulé → on dort la différence
                time.sleep(interval - elapsed)
            # Enregistre le timestamp de cette requête
            cls._last_request_at_by_bucket[bucket] = time.monotonic()

    @staticmethod
    def _rate_limit_bucket(url: str) -> tuple[str, float]:
        """
        Détermine le "bucket" (groupe) et l'intervalle pour une URL donnée.

        Les endpoints lourds (recherche, etc.) ont un intervalle plus long.

        Arguments :
            url (str) : URL de la requête.

        Retourne :
            tuple (str, float) : (nom_du_bucket, intervalle_en_secondes)
        """
        path = urlparse(url).path if url else ""
        for endpoint in SLOW_ENDPOINTS:
            if path.startswith(endpoint):
                return endpoint, SLOW_ENDPOINT_INTERVAL_SECONDS
        return "default", DEFAULT_REQUEST_INTERVAL_SECONDS

    @staticmethod
    def _rate_limit_delay(detail: str) -> int:
        """
        Extrait la durée d'attente depuis le message d'erreur d'un HTTP 429.

        Scryfall peut indiquer "retry after X seconds" dans son message d'erreur.
        On tente d'extraire ce délai ; sinon on utilise la valeur maximale par défaut.

        Arguments :
            detail (str) : Corps de la réponse d'erreur HTTP 429.

        Retourne :
            int : Nombre de secondes à attendre (entre 1 et MAX_RATE_LIMIT_DELAY_SECONDS).
        """
        match = re.search(r"after\s+(\d+)\s+seconds?", detail, flags=re.IGNORECASE)
        if match:
            return max(1, min(int(match.group(1)), MAX_RATE_LIMIT_DELAY_SECONDS))
        return MAX_RATE_LIMIT_DELAY_SECONDS

    # -----------------------------------------------------------------------
    #  Gestion du cache local
    # -----------------------------------------------------------------------

    def _read_print_search_cache(self, name: str, language: str, image_size: str) -> list[CardPrint] | None:
        """
        Lit les résultats en cache pour une recherche donnée.

        Le cache permet d'éviter de refaire la même requête Scryfall si les
        données ont été téléchargées il y a moins de 24 heures.

        Arguments :
            name       (str) : Nom de la carte.
            language   (str) : Langue de recherche.
            image_size (str) : Taille d'image.

        Retourne :
            list[CardPrint] : Les impressions en cache si valides et récentes.
            None            : Si le cache est absent, corrompu, ou périmé (> 24h).
        """
        path = self._print_search_cache_path(name, language, image_size)
        if not path.exists():
            return None   # Pas de fichier cache → rien à retourner
        try:
            with path.open("r", encoding="utf-8") as source:
                payload = json.load(source)
            # Vérification de la fraîcheur du cache
            if time.time() - float(payload.get("saved_at", 0)) > PRINT_SEARCH_CACHE_SECONDS:
                return None   # Cache trop vieux (> 24h) → on le considère périmé
            # Reconstruction des objets CardPrint depuis les dictionnaires JSON
            return [CardPrint(**raw_print) for raw_print in payload.get("prints", [])]
        except Exception:
            # Cache corrompu ou illisible → on l'ignore silencieusement
            return None

    def _write_print_search_cache(self, name: str, language: str, image_size: str, prints: list[CardPrint]) -> None:
        """
        Sauvegarde les résultats d'une recherche dans le cache local.

        Arguments :
            name       (str)            : Nom de la carte.
            language   (str)            : Langue de recherche.
            image_size (str)            : Taille d'image.
            prints     (list[CardPrint]): Impressions à sauvegarder.
        """
        try:
            PRINT_SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),    # Timestamp de sauvegarde
                "name": name,
                "language": language,
                "image_size": image_size,
                "prints": [asdict(card_print) for card_print in prints],   # Conversion en dict
            }
            with self._print_search_cache_path(name, language, image_size).open("w", encoding="utf-8") as target:
                json.dump(payload, target, ensure_ascii=False)
        except Exception:
            # Échec de l'écriture du cache → on l'ignore silencieusement
            # (le cache est optionnel, le programme fonctionne sans)
            return

    @staticmethod
    def _print_search_cache_path(name: str, language: str, image_size: str) -> Path:
        """
        Génère le chemin du fichier cache pour une combinaison nom+langue+taille.

        On utilise un hash SHA1 de la clé pour obtenir un nom de fichier unique
        et sûr (évite les problèmes avec les caractères spéciaux dans les noms).

        Arguments :
            name       (str) : Nom de la carte.
            language   (str) : Langue.
            image_size (str) : Taille d'image.

        Retourne :
            Path : Chemin complet du fichier cache.
        """
        # Création d'une clé de cache reproductible et normalisée
        cache_key = json.dumps(
            {
                "name": name.casefold().strip(),
                "language": language.lower().strip(),
                "image_size": image_size,
                "version": 2,   # Numéro de version pour invalider l'ancien cache si le format change
            },
            sort_keys=True,   # Ordre des clés fixe → même hash quel que soit l'ordre
        )
        # sha1() génère une empreinte hexadécimale unique de 40 caractères
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        return PRINT_SEARCH_CACHE_DIR / f"{digest}.json"

    # -----------------------------------------------------------------------
    #  Extraction des données d'image depuis les réponses JSON
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_image_url(card: dict, image_size: str) -> str | None:
        """
        Extrait l'URL d'image d'une certaine taille depuis un objet carte JSON.

        Gère les deux structures possibles de Scryfall :
        - Carte simple : {"image_uris": {"large": "https://..."}}
        - Carte double face : {"card_faces": [{"image_uris": {...}}, {"image_uris": {...}}]}
          Pour les cartes double face (transformers, flip cards), on prend la première face.

        Arguments :
            card       (dict) : Objet carte brut de l'API Scryfall.
            image_size (str)  : Taille souhaitée ("small", "normal", "large", "png"...).

        Retourne :
            str  : URL de l'image si disponible.
            None : Si aucune image de cette taille n'existe.
        """
        # Cas 1 : Carte simple → image_uris directement sur la carte
        image_uris = card.get("image_uris")
        if isinstance(image_uris, dict) and image_uris.get(image_size):
            return str(image_uris[image_size])

        # Cas 2 : Carte double face → les images sont sur chaque "face"
        card_faces = card.get("card_faces")
        if isinstance(card_faces, list):
            for face in card_faces:
                face_uris = face.get("image_uris") if isinstance(face, dict) else None
                if isinstance(face_uris, dict) and face_uris.get(image_size):
                    return str(face_uris[image_size])   # On retourne la première face trouvée

        return None   # Aucune image trouvée pour cette taille

    def _raw_card_to_print(self, card: dict, image_size: str) -> CardPrint | None:
        """
        Convertit un objet carte JSON brut en CardPrint.

        Arguments :
            card       (dict) : Objet carte brut de l'API Scryfall.
            image_size (str)  : Taille d'image principale souhaitée.

        Retourne :
            CardPrint : Objet structuré prêt à l'emploi.
            None      : Si aucune URL d'image utilisable n'est disponible.
        """
        # Extraction de toutes les URLs disponibles (toutes tailles)
        image_urls = self._extract_image_urls(card)

        # URL principale : taille voulue → large → rien
        image_url = image_urls.get(image_size) or image_urls.get("large") or ""
        if not image_url:
            return None   # Carte sans image → on l'ignore

        return CardPrint(
            id=str(card.get("id", "")),
            set_code=str(card.get("set", "")),
            set_name=str(card.get("set_name", "")),
            collector_number=str(card.get("collector_number", "")),
            language=str(card.get("lang", "")),
            name=str(card.get("name", "")),
            image_url=image_url,
            released_at=str(card.get("released_at", "")),
            # URL de prévisualisation : on préfère la petite taille (chargement rapide)
            preview_url=image_urls.get("small") or image_urls.get("normal") or image_url,
            image_urls=image_urls,
            highres_image=bool(card.get("highres_image", True)),
        )

    @staticmethod
    def _extract_image_urls(card: dict) -> dict[str, str]:
        """
        Extrait toutes les URLs d'image disponibles pour une carte.

        Retourne un dictionnaire avec toutes les tailles disponibles.

        Arguments :
            card (dict) : Objet carte brut de l'API Scryfall.

        Retourne :
            dict[str, str] : {taille: url}, ex: {"small": "...", "large": "...", "png": "..."}
        """
        # Syntaxe walrus operator (:=) : assigne et teste en même temps
        # Pour chaque taille, on l'inclut seulement si l'URL existe (non-None)
        return {
            image_size: image_url
            for image_size in ("small", "normal", "large", "png", "art_crop", "border_crop")
            if (image_url := ScryfallClient._extract_image_url(card, image_size))
        }

    @staticmethod
    def _escape_search_text(value: str) -> str:
        """
        Échappe les caractères spéciaux pour les requêtes de recherche Scryfall.

        Dans les requêtes Scryfall, les guillemets " doivent être échappés \\"
        pour être traités comme des caractères littéraux.

        Arguments :
            value (str) : Texte à échapper.

        Retourne :
            str : Texte avec les backslashes et guillemets échappés.
        """
        return value.replace("\\", "\\\\").replace('"', '\\"')
