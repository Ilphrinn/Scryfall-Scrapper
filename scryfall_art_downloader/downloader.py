# =============================================================================
#  TÉLÉCHARGEUR D'IMAGES — downloader.py
# =============================================================================
# Ce module s'occupe de télécharger et sauvegarder les images de cartes sur
# le disque dur de l'utilisateur.
#
# Il gère trois cas d'usage :
#
#   1. Télécharger un SET complet
#      → Toutes les cartes d'un set dans un dossier "SET_LANGUE/"
#
#   2. Télécharger une CARTE individuelle
#      → Une seule carte dans un dossier "SET_LANGUE/"
#
#   3. Télécharger une DECKLIST
#      → Une liste de cartes sélectionnées dans un dossier "DECKLIST_LANGUE/"
#      → NOMMAGE DES FICHIERS :
#          - Carte unique dans la liste  → "Lightning Bolt.jpg"
#          - Carte présente N fois       → "Swamp_1.jpg", "Swamp_2.jpg", ... "Swamp_N.jpg"
#        Ce suffixe _N est appliqué dès qu'un même nom de carte apparaît plus
#        d'une fois, que ce soit via la quantité ("4 Swamp") ou via plusieurs
#        lignes distinctes avec des éditions différentes ("1 Swamp" × 2 éditions).
#      → OPTIMISATION : la première copie d'un groupe d'exemplaires identiques
#        est téléchargée, les suivantes sont copiées depuis le disque.
#        Ex : 4 Lightning Bolt = 1 téléchargement + 3 copies locales.
#
# CALLBACKS DE PROGRESSION :
# Les fonctions de téléchargement acceptent des "callbacks" (fonctions de rappel)
# qui permettent à l'interface graphique d'afficher la progression en temps réel
# sans bloquer. Ces callbacks sont appelés depuis le thread de travail.
# =============================================================================

from __future__ import annotations

from pathlib import Path              # Pour manipuler les chemins de fichiers
from shutil import copyfile, copyfileobj   # Pour copier des fichiers et flux
from typing import Callable           # Pour typer les fonctions de rappel
from urllib.error import HTTPError, URLError   # Exceptions réseau
from urllib.parse import urlparse     # Pour extraire l'extension d'une URL
from urllib.request import Request, urlopen    # Pour envoyer des requêtes HTTP

from .models import CardImage, CardPrint, CardRequest, DecklistEntry, SetRequest
from .scryfall_client import ScryfallClient, USER_AGENT


# ---------------------------------------------------------------------------
#  Types de callbacks (fonctions de rappel pour signaler la progression)
# ---------------------------------------------------------------------------

# Callback texte : appelé avec un message de statut (ex: "Téléchargement: Forest.jpg")
ProgressCallback = Callable[[str], None]

# Callback numérique : appelé avec (nombre_traités, total) pour la barre de progression
ProgressCountCallback = Callable[[int, int], None]

# Callback d'annulation : appelé pour savoir si l'utilisateur a cliqué "Annuler"
# Retourne True si l'opération doit être stoppée
CancelCallback = Callable[[], bool]


# ---------------------------------------------------------------------------
#  Classe principale ArtDownloader
# ---------------------------------------------------------------------------

class ArtDownloader:
    """
    Gère le téléchargement des images de cartes Magic depuis Scryfall.

    Cette classe est le "chef d'orchestre" : elle utilise ScryfallClient
    pour récupérer les données, puis sauvegarde les fichiers sur le disque.

    Attributs :
        output_root (Path)        : Dossier racine où seront créés les sous-dossiers.
        client      (ScryfallClient) : Client API pour communiquer avec Scryfall.

    Usage :
        downloader = ArtDownloader(output_root="C:/Images/Magic")
        count, folder = downloader.download(set_request, image_size="large")
    """

    def __init__(self, output_root: Path | str = "ART") -> None:
        """
        Initialise le téléchargeur.

        Arguments :
            output_root (Path|str) : Dossier racine de sortie. Par défaut "ART"
                                     dans le répertoire courant.
        """
        self.output_root = Path(output_root)    # On s'assure que c'est un objet Path
        self.client = ScryfallClient()          # Client pour communiquer avec Scryfall

    def download(
        self,
        request: SetRequest | CardRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        """
        Point d'entrée unifié pour télécharger un set ou une carte.

        Détermine automatiquement s'il faut appeler download_set() ou download_card()
        en fonction du type de la requête.

        Arguments :
            request      (SetRequest|CardRequest) : Quoi télécharger.
            image_size   (str)                    : Taille d'image (défaut: "large").
            overwrite    (bool)                   : Remplacer les fichiers existants ?
            on_status    (Callable|None)          : Callback message de progression.
            on_progress  (Callable|None)          : Callback compteur (fait, total).
            should_cancel (Callable|None)         : Callback pour annuler.

        Retourne :
            tuple (int, Path) : (nombre de fichiers traités, chemin du dossier cible).
        """
        if isinstance(request, CardRequest):
            return self.download_card(request, image_size, overwrite, on_status, on_progress, should_cancel)
        return self.download_set(request, image_size, overwrite, on_status, on_progress, should_cancel)

    def download_set(
        self,
        set_request: SetRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        """
        Télécharge toutes les cartes d'un set Scryfall.

        Crée un dossier "CODE_LANGUE/" (ex: "FIN_FR/") et y télécharge chaque
        carte dans l'ordre. Les cartes déjà présentes sont ignorées sauf si
        overwrite=True.

        Arguments :
            set_request  (SetRequest)     : Set et langue à télécharger.
            image_size   (str)            : Taille d'image souhaitée.
            overwrite    (bool)           : Si True, réécrit les fichiers existants.
            on_status    (Callable|None)  : Callback pour les messages de log.
            on_progress  (Callable|None)  : Callback pour la barre de progression.
            should_cancel (Callable|None) : Callback pour vérifier l'annulation.

        Retourne :
            tuple (int, Path) : (nombre d'images traitées, chemin du dossier créé).
        """
        target_dir = self.output_root / set_request.folder_name
        target_dir.mkdir(parents=True, exist_ok=True)   # Crée le dossier (et les parents si besoin)
        if on_status:
            on_status(f"Dossier cible: {target_dir}")

        count = 0   # Nombre de cartes traitées
        total = 0   # Nombre total de cartes dans le set (connu après la première page)

        def set_total(value: int) -> None:
            """Fonction interne appelée quand on connaît le nombre total de cartes."""
            nonlocal total   # Accède à la variable 'total' du scope parent
            total = value
            if on_status:
                on_status(f"Total cartes: {total}")
            if on_progress:
                on_progress(count, total)

        # Parcours de toutes les images du set (résultats paginés)
        for card in self.client.iter_card_images(set_request, image_size, on_status, set_total):
            if should_cancel and should_cancel():
                if on_status:
                    on_status("Annulé.")
                break

            target = self._target_path(target_dir, card)

            if target.exists() and not overwrite:
                # Fichier déjà présent et on ne veut pas l'écraser → on passe
                count += 1
                if on_status:
                    on_status(f"Déjà présent: {target.name}")
                if on_progress:
                    on_progress(count, total)
                continue

            # Téléchargement du fichier
            if on_status:
                on_status(f"Téléchargement: {target.name}")
            self._download_file(card.image_url, target)
            count += 1
            if on_progress:
                on_progress(count, total)

        return count, target_dir

    def download_card(
        self,
        card_request: CardRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        """
        Télécharge une seule carte spécifique.

        Arguments :
            card_request (CardRequest)    : Set, numéro et langue de la carte.
            image_size   (str)            : Taille d'image souhaitée.
            overwrite    (bool)           : Réécrire si déjà présent ?
            on_status    (Callable|None)  : Callback de messages.
            on_progress  (Callable|None)  : Callback de progression.
            should_cancel (Callable|None) : Callback d'annulation.

        Retourne :
            tuple (int, Path) : (1 si téléchargé ou présent, chemin du dossier).
        """
        target_dir = self.output_root / card_request.folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        if on_status:
            on_status(f"Dossier cible: {target_dir}")
            on_status("Total cartes: 1")
        if on_progress:
            on_progress(0, 1)   # Barre de progression : 0/1

        if should_cancel and should_cancel():
            if on_status:
                on_status("Annulé.")
            return 0, target_dir

        # Récupération des informations de la carte via l'API
        card = self.client.get_card_image(card_request, image_size, on_status)
        target = self._target_path(target_dir, card)

        if target.exists() and not overwrite:
            if on_status:
                on_status(f"Déjà présent: {target.name}")
            if on_progress:
                on_progress(1, 1)
            return 1, target_dir

        if on_status:
            on_status(f"Téléchargement: {target.name}")
        self._download_file(card.image_url, target)
        if on_progress:
            on_progress(1, 1)
        return 1, target_dir

    def download_decklist(
        self,
        selections: list[tuple[DecklistEntry, CardPrint]],
        language: str,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        """
        Télécharge les images d'une liste de cartes (decklist).

        NOMMAGE DES FICHIERS :
            Un nom de carte présent une seule fois dans toute la liste :
                → "Lightning Bolt.jpg"
            Un nom de carte présent plusieurs fois (quantité > 1 ou même nom
            avec deux éditions différentes) :
                → "Swamp_1.jpg", "Swamp_2.jpg", "Swamp_3.jpg", "Swamp_4.jpg"
            Le suffixe _N est global : si "Swamp" apparaît en ligne 3 (×2)
            et en ligne 7 (×1 autre édition), les trois fichiers reçoivent _1, _2, _3.

        OPTIMISATION TÉLÉCHARGEMENT :
            Pour un groupe d'exemplaires ayant la MÊME image (même CardPrint),
            seul le premier exemplaire est téléchargé depuis Scryfall.
            Les exemplaires suivants sont obtenus par une simple copie locale,
            ce qui évite des requêtes réseau inutiles.
            Ex : 4 Swamp identiques = 1 téléchargement + 3 copies disque.

        Arguments :
            selections    (list)          : Liste de (DecklistEntry, CardPrint) sélectionnés.
                                            Chaque élément représente une copie individuelle
                                            (quantity=1 dans la pratique courante).
            language      (str)           : Langue pour nommer le dossier (ex: "fr").
            image_size    (str)           : Taille d'image souhaitée (ex: "large").
            overwrite     (bool)          : Si True, réécrit les fichiers déjà présents.
            on_status     (Callable|None) : Callback appelé avec un message texte à chaque étape.
            on_progress   (Callable|None) : Callback appelé avec (traités, total) pour la barre.
            should_cancel (Callable|None) : Callback retournant True si l'utilisateur annule.

        Retourne :
            tuple (int, Path) : (nombre d'images traitées, chemin du dossier créé).
        """
        target_dir = self.output_root / f"DECKLIST_{language.upper()}"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Calcul du total : somme des quantités de toutes les entrées
        total = sum(entry.quantity for entry, _card_print in selections)
        count = 0

        if on_status:
            on_status(f"Dossier cible: {target_dir}")
            on_status(f"Total images: {total}")
            on_status(f"Format image: {image_size}")
        if on_progress:
            on_progress(0, total)

        # Pré-calcul du nombre total d'occurrences par nom de carte.
        # Nécessaire pour savoir si un nom doit recevoir un suffixe numérique.
        # Ex: si "Swamp" apparaît 4 fois → tous les fichiers auront _1, _2, _3, _4.
        # Si "Lightning Bolt" n'apparaît qu'une fois → pas de suffixe.
        name_total: dict[str, int] = {}
        for entry, _ in selections:
            name_total[entry.name] = name_total.get(entry.name, 0) + entry.quantity

        # Compteur courant par nom (incrémenté à chaque copie produite)
        name_seen: dict[str, int] = {}

        # Parcours de chaque entrée de la decklist (une entrée = un nom de carte)
        for entry, card_print in selections:
            # Adaptation de l'URL de l'image à la taille souhaitée
            card_print = card_print.for_image_size(image_size)

            # source_file : chemin vers le premier fichier téléchargé pour cette entrée.
            # Les copies suivantes du même fichier sont copiées depuis source_file
            # plutôt que retéléchargées, ce qui est bien plus rapide.
            source_file: Path | None = None

            # Boucle sur chaque exemplaire de cette entrée (ex: 4 copies de "Swamp")
            for _ in range(entry.quantity):
                if should_cancel and should_cancel():
                    if on_status:
                        on_status("Annulé.")
                    return count, target_dir

                name_seen[entry.name] = name_seen.get(entry.name, 0) + 1
                copy_num = name_seen[entry.name]
                target = self._decklist_target_path(
                    target_dir, entry, card_print, copy_num, name_total[entry.name]
                )

                if target.exists() and not overwrite:
                    count += 1
                    source_file = target
                    if on_status:
                        on_status(f"Déjà présent: {target.name}")
                    if on_progress:
                        on_progress(count, total)
                    continue

                if source_file is not None and source_file.exists():
                    # Même image déjà téléchargée → simple copie de fichier
                    if on_status:
                        on_status(f"Copie: {target.name}")
                    copyfile(source_file, target)
                else:
                    if card_print.custom_file_path:
                        if on_status:
                            on_status(f"Copie custom: {target.name}")
                        copyfile(card_print.custom_file_path, target)
                    else:
                        if on_status:
                            on_status(f"Téléchargement: {target.name}")
                        self._download_file(card_print.image_url, target)
                    source_file = target

                count += 1
                if on_progress:
                    on_progress(count, total)

        return count, target_dir

    # -----------------------------------------------------------------------
    #  Méthodes utilitaires (statiques)
    # -----------------------------------------------------------------------

    @staticmethod
    def _target_path(target_dir: Path, card: CardImage) -> Path:
        """
        Génère le chemin de fichier de destination pour une image de carte (set).

        L'extension est extraite de l'URL de l'image (ex: ".jpg", ".png").

        Arguments :
            target_dir (Path)      : Dossier de destination.
            card       (CardImage) : Carte avec son URL d'image.

        Retourne :
            Path : Chemin complet du fichier, ex: "/ART/FIN_FR/FIN_FR_101.jpg"
        """
        # urlparse().path extrait le chemin de l'URL → on en extrait le suffixe (extension)
        extension = Path(urlparse(card.image_url).path).suffix or ".jpg"
        return target_dir / f"{card.base_filename}{extension}"

    @staticmethod
    def _decklist_target_path(
        target_dir: Path,
        entry: DecklistEntry,
        card_print: CardPrint,
        copy_num: int,
        total_copies: int,
    ) -> Path:
        """
        Génère le chemin de fichier de destination pour une image de decklist.

        Règle de nommage :
        - 1 seul exemplaire au total → "Lightning Bolt.jpg"
        - Plusieurs exemplaires     → "Swamp_1.jpg", "Swamp_2.jpg", "Swamp_3.jpg"

        Arguments :
            target_dir   (Path)         : Dossier de destination.
            entry        (DecklistEntry): Entrée de la decklist.
            card_print   (CardPrint)    : Impression sélectionnée.
            copy_num     (int)          : Numéro de cette copie (1, 2, 3…).
            total_copies (int)          : Nombre total de copies de ce nom dans la decklist.

        Retourne :
            Path : Chemin complet du fichier.
        """
        extension = ArtDownloader._card_print_extension(card_print)
        card_name = ArtDownloader._clean_display_filename(entry.name)
        if total_copies > 1:
            return target_dir / f"{card_name}_{copy_num}{extension}"
        return target_dir / f"{card_name}{extension}"

    @staticmethod
    def _card_print_extension(card_print: CardPrint) -> str:
        """
        Détermine l'extension du fichier image pour une impression.

        Pour les images personnalisées, on utilise l'extension du fichier source.
        Pour les images Scryfall, on extrait l'extension de l'URL.

        Arguments :
            card_print (CardPrint) : Impression de carte.

        Retourne :
            str : Extension du fichier, ex: ".jpg", ".png" (avec le point).
        """
        if card_print.custom_file_path:
            return Path(card_print.custom_file_path).suffix or ".jpg"
        return Path(urlparse(card_print.image_url).path).suffix or ".jpg"

    @staticmethod
    def _clean_filename_part(value: str) -> str:
        """
        Nettoie une chaîne pour qu'elle soit utilisable dans un nom de fichier.
        Version simplifiée utilisée en interne par le downloader.

        Arguments :
            value (str) : Texte à nettoyer.

        Retourne :
            str : Texte nettoyé.
        """
        cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        return cleaned.strip("_") or "card"

    @staticmethod
    def _clean_display_filename(value: str) -> str:
        """
        Nettoie un nom de carte pour l'utiliser comme nom de fichier affiché.

        Contrairement à _clean_filename_part, on conserve les espaces et
        la casse originale (ex: "Lightning Bolt" reste "Lightning Bolt"),
        mais on remplace les caractères INTERDITS par Windows :
            < > : " / \\ | ? *
        et les caractères de contrôle (code ASCII < 32).

        Exemples :
            "Lightning Bolt"   → "Lightning Bolt"
            "Fire/Ice"         → "Fire_Ice"
            "Test..."          → "Test"  (les points en fin de nom sont supprimés)

        Arguments :
            value (str) : Nom de carte brut.

        Retourne :
            str : Nom de fichier propre, ou "card" si le résultat est vide.
        """
        invalid = '<>:"/\\|?*'   # Caractères interdits dans les noms de fichiers Windows
        # Remplace les caractères invalides par "_", puis supprime les espaces en bordure
        cleaned = "".join("_" if (char in invalid or ord(char) < 32) else char for char in value).strip()
        # Windows interdit aussi les points et espaces EN FIN de nom de fichier
        cleaned = cleaned.rstrip(". ")
        return cleaned or "card"

    @staticmethod
    def _download_file(url: str, target: Path) -> None:
        """
        Télécharge un fichier depuis une URL et le sauvegarde sur le disque.

        Utilise un flux (streaming) pour éviter de charger tout le fichier
        en mémoire avant de l'écrire — important pour les grandes images PNG.

        Arguments :
            url    (str)  : URL de l'image à télécharger.
            target (Path) : Chemin complet du fichier de destination.

        Lève :
            RuntimeError : En cas d'erreur HTTP ou réseau.
        """
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=60) as response:
                with target.open("wb") as output:
                    # copyfileobj copie le flux réseau → fichier par blocs de 16 Ko
                    # (évite de mettre tout le fichier en mémoire vive)
                    copyfileobj(response, output)
        except HTTPError as error:
            raise RuntimeError(f"Erreur image HTTP {error.code}: {url}") from error
        except URLError as error:
            raise RuntimeError(f"Impossible de telecharger l'image: {error.reason}") from error
