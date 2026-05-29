# =============================================================================
#  CATALOGUE LOCAL (HORS LIGNE) — local_bulk_catalog.py
# =============================================================================
# Ce module permet de rechercher des cartes Magic SANS connexion internet.
#
# COMMENT ÇA FONCTIONNE ?
# Scryfall propose un fichier JSON téléchargeable contenant TOUTES les cartes
# existantes ("all-cards-YYYYMMDD.json", plusieurs centaines de Mo). Si ce fichier
# est présent dans le dossier du programme, on peut l'utiliser à la place de l'API.
#
# ÉTAPE 1 — Indexation (une seule fois)
# La première utilisation du fichier all-cards peut prendre quelques minutes car
# on le parcourt entièrement pour construire un INDEX SQLite3 (base de données
# locale légère). Cet index est ensuite sauvegardé dans :
#   C:\Users\NomUtilisateur\.scryfall_art_downloader\local_bulk_index\
#
# ÉTAPE 2 — Recherche rapide
# Les fois suivantes (et tant que le fichier all-cards n'a pas changé),
# on utilise directement l'index SQLite3, ce qui est TRÈS rapide (millisecondes).
#
# POURQUOI SQLITE3 ?
# SQLite3 est une base de données légère intégrée à Python. Elle permet de faire
# des recherches efficaces avec des index, sans serveur, directement sur le disque.
# Parfait pour un usage local et offline.
#
# VALIDATION DE L'INDEX
# L'index est invalidé automatiquement si le fichier all-cards est modifié
# (la taille et la date de modification sont comparées). Cela garantit
# que l'index est toujours à jour par rapport au fichier source.
# =============================================================================

from __future__ import annotations

import json         # Pour lire le JSON du fichier bulk et sérialiser les URLs
import sqlite3      # Base de données locale légère (intégrée à Python)
from contextlib import closing   # Pour fermer automatiquement les connexions SQLite
from pathlib import Path         # Manipulation de chemins de fichiers
from typing import Callable, Iterable   # Types pour les fonctions de rappel

from .models import CardPrint, DecklistEntry   # Nos structures de données


# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

LOCAL_BULK_INDEX_DIR = Path.home() / ".scryfall_art_downloader" / "local_bulk_index"
# Dossier où sera stocké l'index SQLite3 généré depuis le fichier all-cards.
# Path.home() = C:\Users\NomUtilisateur sur Windows.

INSERT_BATCH_SIZE = 2000
# Nombre de lignes insérées par batch dans SQLite3.
# Insérer par lots est BEAUCOUP plus rapide qu'insérer une par une
# (SQLite3 est optimisé pour les transactions groupées).


# ---------------------------------------------------------------------------
#  Classe principale LocalBulkCatalog
# ---------------------------------------------------------------------------

class LocalBulkCatalog:
    """
    Interface de recherche dans le fichier bulk local Scryfall (mode hors ligne).

    Cette classe gère toute la chaîne : vérification de l'index, indexation
    du fichier JSON, et recherche de cartes dans la base de données SQLite3.

    Attributs :
        bulk_file    (Path) : Chemin vers le fichier all-cards JSON.
        on_status    (Callable|None) : Callback de messages (affiché dans le log).
        on_progress  (Callable|None) : Callback de progression (barre de progression).
        should_cancel (Callable|None) : Callback pour annuler l'opération.
        index_file   (Path) : Chemin du fichier SQLite3 de l'index.
    """

    def __init__(
        self,
        bulk_file: Path,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        """
        Initialise le catalogue local.

        Arguments :
            bulk_file     (Path)           : Fichier JSON all-cards de Scryfall.
            on_status     (Callable|None)  : Fonction appelée pour les messages de statut.
            on_progress   (Callable|None)  : Fonction appelée pour la progression (traités, total).
            should_cancel (Callable|None)  : Fonction retournant True si on doit annuler.
        """
        self.bulk_file = bulk_file
        self.on_status = on_status
        self.on_progress = on_progress
        self.should_cancel = should_cancel
        # Le fichier d'index porte le même nom que le bulk mais avec l'extension .sqlite3
        self.index_file = self._index_dir() / f"{self.bulk_file.stem}.sqlite3"

    def search_deck_prints(
        self,
        entries: list[DecklistEntry],
        language: str,
        image_size: str = "large",
    ) -> dict[int, list[CardPrint]]:
        """
        Recherche toutes les impressions disponibles pour une liste de cartes.

        C'est la méthode principale utilisée par l'onglet Decklist. Elle :
        1. S'assure que l'index est à jour (le crée si nécessaire)
        2. Pour chaque entrée de la decklist, cherche toutes les éditions
        3. Retourne les résultats indexés par position dans la liste

        Arguments :
            entries    (list[DecklistEntry]) : Cartes à chercher.
            language   (str)                 : Langue préférée pour le tri.
            image_size (str)                 : Taille d'image souhaitée.

        Retourne :
            dict[int, list[CardPrint]] : {index_carte: [toutes les impressions trouvées]}
        """
        # Étape 1 : Vérifier/créer l'index SQLite3
        self._ensure_index()

        if self.on_status:
            self.on_status("Bulk local: recherche dans l'index local...")
        if self.on_progress:
            self.on_progress(0, max(len(entries), 1))

        result: dict[int, list[CardPrint]] = {}

        # On ouvre une seule connexion SQLite3 pour toutes les recherches
        # closing() garantit la fermeture même en cas d'erreur
        with closing(sqlite3.connect(self.index_file)) as connection:
            for index, entry in enumerate(entries):
                self._raise_if_cancelled()
                result[index] = self._lookup_prints(connection, entry.name, language.lower(), image_size)
                if self.on_progress:
                    self.on_progress(index + 1, max(len(entries), 1))

        return result

    # -----------------------------------------------------------------------
    #  Gestion de l'index SQLite3
    # -----------------------------------------------------------------------

    def _ensure_index(self) -> None:
        """
        Vérifie que l'index SQLite3 est à jour ; le recrée si nécessaire.

        L'index est considéré périmé si :
        - Le fichier .sqlite3 n'existe pas encore
        - La taille ou la date de modification du fichier all-cards a changé
        - La version du schéma a changé (structure de la base modifiée)

        Si l'index doit être recréé, on le construit dans un fichier temporaire
        (.sqlite3.tmp) puis on le renomme à la fin, pour ne jamais laisser
        l'index dans un état corrompu/incomplet en cas d'interruption.
        """
        bulk_stat = self.bulk_file.stat()   # Statistiques du fichier (taille, date)
        if self._index_is_current(bulk_stat.st_size, int(bulk_stat.st_mtime)):
            return   # L'index est à jour → rien à faire

        if self.on_status:
            self.on_status(f"Bulk local: indexation unique de {self.bulk_file.name}...")

        # Fichier temporaire (pour éviter un index à moitié construit en cas de crash)
        temp_index = self.index_file.with_suffix(".sqlite3.tmp")
        if temp_index.exists():
            temp_index.unlink()   # Supprime un éventuel fichier temporaire résiduel

        with closing(sqlite3.connect(temp_index)) as connection:
            self._prepare_connection(connection)    # Optimisations SQLite pour la construction
            self._create_schema(connection)         # Création des tables
            self._populate_index(connection, bulk_stat.st_size)  # Remplissage depuis le JSON
            if self.on_status:
                self.on_status("Bulk local: finalisation de l'index...")
            self._create_indexes(connection)        # Création des index de recherche
            # Sauvegarde des métadonnées pour valider l'index la prochaine fois
            connection.execute("INSERT INTO meta(key, value) VALUES('bulk_size', ?)", (str(bulk_stat.st_size),))
            connection.execute("INSERT INTO meta(key, value) VALUES('bulk_mtime', ?)", (str(int(bulk_stat.st_mtime)),))
            connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
            connection.commit()

        # Remplacement atomique : supprime l'ancien index et renomme le nouveau
        if self.index_file.exists():
            try:
                self.index_file.unlink()
            except Exception:
                pass   # Échec de suppression → on essaie quand même le renommage
        temp_index.replace(self.index_file)

        if self.on_status:
            self.on_status("Bulk local: index prêt.")

    def _index_is_current(self, bulk_size: int, bulk_mtime: int) -> bool:
        """
        Vérifie si l'index SQLite3 existant est encore valide.

        Lit les métadonnées stockées dans l'index (taille, date, version)
        et les compare aux données actuelles du fichier bulk.

        Arguments :
            bulk_size  (int) : Taille actuelle du fichier bulk en octets.
            bulk_mtime (int) : Date de modification actuelle du fichier bulk (timestamp Unix).

        Retourne :
            bool : True si l'index est à jour et utilisable. False sinon.
        """
        if not self.index_file.exists():
            return False   # Pas d'index → forcément pas à jour

        try:
            with closing(sqlite3.connect(self.index_file)) as connection:
                # Lecture de toutes les métadonnées en un seul appel
                rows = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            return (
                rows.get("bulk_size") == str(bulk_size)
                and rows.get("bulk_mtime") == str(bulk_mtime)
                and rows.get("schema_version") == "2"
            )
        except sqlite3.Error:
            # Fichier d'index corrompu ou illisible → on le considère périmé
            return False

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        """
        Crée les tables de la base de données SQLite3.

        Structure de la base :
            meta     : Paires clé/valeur pour les métadonnées (taille, date, version)
            aliases  : Noms alternatifs des cartes (nom oracle + nom traduit) → oracle_id
            prints   : Toutes les impressions (une ligne par version de carte)

        La table aliases permet de chercher une carte sous son nom traduit
        (ex: "Éclair" trouvera "Lightning Bolt" en français).
        """
        connection.executescript(
            """
            CREATE TABLE meta (
                key   TEXT PRIMARY KEY,   -- Nom de la métadonnée
                value TEXT NOT NULL       -- Valeur de la métadonnée
            );

            CREATE TABLE aliases (
                name      TEXT NOT NULL,  -- Nom normalisé (minuscules, espaces normaux)
                oracle_id TEXT NOT NULL   -- ID oracle correspondant
            );

            CREATE TABLE prints (
                oracle_id        TEXT NOT NULL,   -- ID oracle (relie les impressions d'une même carte)
                language         TEXT NOT NULL,   -- Langue de cette impression (ex: "fr")
                card_id          TEXT PRIMARY KEY, -- ID unique de cette impression
                set_code         TEXT NOT NULL,   -- Code du set (ex: "fin")
                set_name         TEXT NOT NULL,   -- Nom du set (ex: "Foundations")
                collector_number TEXT NOT NULL,   -- Numéro de collecteur
                card_name        TEXT NOT NULL,   -- Nom de la carte (dans cette langue)
                released_at      TEXT NOT NULL,   -- Date de sortie
                image_urls       TEXT NOT NULL,   -- JSON des URLs d'image (toutes tailles)
                preview_url      TEXT NOT NULL,   -- URL de la vignette (petite image)
                highres_image    INTEGER NOT NULL  -- 1 si haute résolution, 0 sinon
            );
            """
        )

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        """
        Crée les index de recherche SQLite3 sur les colonnes les plus utilisées.

        Les index accélèrent énormément les recherches (comme un index de livre),
        mais ralentissent l'insertion. On les crée donc APRÈS avoir tout inséré.
        """
        connection.executescript(
            """
            -- Index sur les noms (pour retrouver l'oracle_id d'une carte rapidement)
            CREATE INDEX idx_aliases_name ON aliases(name);

            -- Index sur oracle_id + langue + date (pour trouver les impressions d'une carte)
            CREATE INDEX idx_prints_oracle_lang ON prints(oracle_id, language, released_at DESC);
            """
        )

    @staticmethod
    def _prepare_connection(connection: sqlite3.Connection) -> None:
        """
        Configure SQLite3 pour la construction rapide de l'index.

        Ces paramètres désactivent les garanties de durabilité habituelles
        (journalisation, synchronisation disque), ce qui est acceptable ici
        car en cas de crash on va simplement recréer l'index depuis le JSON.

        PRAGMA journal_mode = OFF  : Pas de journal de transactions (dangereux en prod, ok ici)
        PRAGMA synchronous = OFF   : Pas d'attente de confirmation d'écriture disque
        PRAGMA temp_store = MEMORY : Tables temporaires en mémoire (plus rapide)
        """
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = MEMORY;
            """
        )

    def _populate_index(self, connection: sqlite3.Connection, total_size: int) -> None:
        """
        Parcourt le fichier JSON bulk et remplit la base de données SQLite3.

        On traite les cartes par lots (batches) pour des raisons de performance.
        SQLite3 est beaucoup plus rapide quand on insère 2000 lignes en une
        transaction que 2000 transactions d'une ligne chacune.

        La progression est signalée en mégaoctets lus (les sauts de 20 Mo évitent
        de mettre à jour la barre de progression trop fréquemment).

        Arguments :
            connection (sqlite3.Connection) : Connexion à la base à remplir.
            total_size (int)                : Taille totale du fichier bulk en octets.
        """
        alias_rows: list[tuple[str, str]] = []
        print_rows: list[tuple[str, str, str, str, str, str, str, str, str, str, int]] = []
        last_progress = 0   # Dernier nombre d'octets lus au moment du dernier signal de progression

        for card, bytes_read in self._iter_bulk_cards():
            self._raise_if_cancelled()

            oracle_id = str(card.get("oracle_id") or "")
            if not oracle_id:
                continue   # Carte sans oracle_id → on l'ignore (données incomplètes)

            # Ajout des noms alternatifs (oracle + traduit) pour la recherche par nom
            # card.get("name")         = nom anglais/oracle de la carte
            # card.get("printed_name") = nom imprimé dans la langue de cette version
            for value in (card.get("name"), card.get("printed_name")):
                if value:
                    alias_rows.append((_normalize_name(str(value)), oracle_id))

            # Conversion de la carte en ligne de base de données
            print_row = _print_row_from_card(card)
            if print_row is not None:
                print_rows.append(print_row)

            # Insertion par batch quand on a assez de lignes accumulées
            if len(alias_rows) >= INSERT_BATCH_SIZE or len(print_rows) >= INSERT_BATCH_SIZE:
                self._flush_rows(connection, alias_rows, print_rows)
                alias_rows.clear()
                print_rows.clear()

            # Mise à jour de la progression toutes les 20 Mo lus
            if self.on_progress and bytes_read - last_progress >= 20 * 1024 * 1024:
                last_progress = bytes_read
                self.on_progress(min(bytes_read, total_size), max(total_size, 1))

        # Insertion du reste (dernier batch incomplet)
        self._flush_rows(connection, alias_rows, print_rows)

        # Signal de fin : 100% de progression
        if self.on_progress:
            self.on_progress(max(total_size, 1), max(total_size, 1))

    @staticmethod
    def _flush_rows(
        connection: sqlite3.Connection,
        alias_rows: list[tuple[str, str]],
        print_rows: list[tuple[str, str, str, str, str, str, str, str, str, str, int]],
    ) -> None:
        """
        Insère un batch de lignes dans la base de données.

        executemany() est une méthode SQLite3 qui insère une liste de lignes
        en une seule opération — beaucoup plus efficace qu'une boucle de INSERT.

        Arguments :
            connection  : Connexion SQLite3 active.
            alias_rows  : Lignes à insérer dans la table aliases.
            print_rows  : Lignes à insérer dans la table prints.
        """
        if alias_rows:
            connection.executemany("INSERT INTO aliases(name, oracle_id) VALUES(?, ?)", alias_rows)
        if print_rows:
            connection.executemany(
                """
                INSERT OR REPLACE INTO prints(
                    oracle_id, language, card_id, set_code, set_name,
                    collector_number, card_name, released_at,
                    image_urls, preview_url, highres_image
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                print_rows,
            )

    def _lookup_prints(
        self,
        connection: sqlite3.Connection,
        name: str,
        language: str,
        image_size: str,
    ) -> list[CardPrint]:
        """
        Recherche toutes les impressions d'une carte par son nom.

        Stratégie :
        1. Normaliser le nom (minuscules, espaces uniques)
        2. Chercher les oracle_id correspondants dans la table aliases
        3. Pour chaque oracle_id, récupérer toutes les impressions
        4. Trier : langue cible en premier, puis par date décroissante

        Arguments :
            connection (sqlite3.Connection) : Connexion à la base.
            name       (str)                : Nom de la carte (dans n'importe quelle langue).
            language   (str)                : Langue préférée pour le tri.
            image_size (str)                : Taille d'image souhaitée.

        Retourne :
            list[CardPrint] : Toutes les impressions trouvées, triées.
        """
        # Étape 1 : Chercher les oracle_id correspondant au nom
        oracle_rows = connection.execute(
            "SELECT DISTINCT oracle_id FROM aliases WHERE name = ?",
            (_normalize_name(name),),
        ).fetchall()

        if not oracle_rows:
            return []   # Carte introuvable dans l'index

        seen: set[str] = set()     # Pour dédupliquer (une même carte peut avoir plusieurs alias)
        prints: list[CardPrint] = []

        # Étape 2 : Pour chaque oracle_id trouvé, récupérer toutes les impressions
        for (oracle_id,) in oracle_rows:
            rows = connection.execute(
                """
                SELECT card_id, set_code, set_name, collector_number, card_name,
                       released_at, image_urls, preview_url, language, highres_image
                FROM prints
                WHERE oracle_id = ?
                ORDER BY released_at DESC
                """,
                (oracle_id,),
            ).fetchall()

            for row in rows:
                card_id, set_code, set_name, collector_number, card_name, \
                    released_at, image_urls_json, preview_url, card_lang, highres_image = row

                if card_id in seen:
                    continue   # Déjà vu (peut arriver si la carte a plusieurs alias)
                seen.add(card_id)

                # Décodage du JSON des URLs d'image
                image_urls = json.loads(image_urls_json)

                # Sélection de l'URL pour la taille demandée (avec repli sur "large")
                image_url = (
                    image_urls.get(image_size)
                    or image_urls.get("large")
                    or next(iter(image_urls.values()), "")
                )
                if not image_url:
                    continue   # Aucune URL d'image → impression inutilisable

                prints.append(
                    CardPrint(
                        id=card_id,
                        set_code=set_code,
                        set_name=set_name,
                        collector_number=collector_number,
                        language=card_lang,
                        name=card_name,
                        image_url=image_url,
                        released_at=released_at,
                        # URL de prévisualisation : petite taille si disponible
                        preview_url=preview_url or image_urls.get("small", "") or image_urls.get("normal", "") or image_url,
                        image_urls=image_urls,
                        highres_image=bool(highres_image),
                    )
                )

        # Tri final : langue cible d'abord, puis plus récent d'abord
        prints.sort(key=lambda p: (p.language == language, p.released_at), reverse=True)
        return prints

    def _index_dir(self) -> Path:
        """
        Retourne le dossier où stocker l'index SQLite3.

        Essaie d'abord le dossier principal dans le profil utilisateur.
        Si la création échoue (ex: droits insuffisants), utilise un dossier
        local à côté du fichier bulk.

        Retourne :
            Path : Chemin du dossier d'index (créé s'il n'existe pas).
        """
        try:
            LOCAL_BULK_INDEX_DIR.mkdir(parents=True, exist_ok=True)
            return LOCAL_BULK_INDEX_DIR
        except OSError:
            # Repli : dossier caché à côté du fichier bulk source
            fallback = self.bulk_file.parent / ".scryfall_local_bulk_index"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _iter_bulk_cards(self) -> Iterable[tuple[dict, int]]:
        """
        Itère (lit une par une) les cartes du fichier JSON bulk.

        PROBLÈME : Le fichier all-cards.json fait plusieurs centaines de Mo.
        On ne peut PAS le charger entièrement en mémoire avec json.load()
        (risque de dépasser la RAM disponible).

        SOLUTION : On lit le fichier par MORCEAUX (chunks de 1 Mo) et on extrait
        les objets JSON au fur et à mesure avec un décodeur incrémental.

        Le fichier est un tableau JSON de la forme :
            [ {carte1}, {carte2}, {carte3}, ... ]

        On cherche le début du tableau "[", puis on extrait chaque objet {}
        séparé par des virgules.

        Génère (yield) pour chaque carte :
            tuple (dict, int) : (données de la carte, octets lus jusqu'ici)
        """
        decoder = json.JSONDecoder()
        buffer = ""          # Tampon de texte lu mais pas encore analysé
        bytes_read = 0       # Compteur de progression en octets
        array_started = False  # A-t-on trouvé le "[" d'ouverture du tableau ?
        eof = False          # Sommes-nous à la fin du fichier ?

        with self.bulk_file.open("r", encoding="utf-8") as source:
            while True:
                # Lecture du prochain morceau (1 Mo) si on n'est pas à la fin
                if not eof:
                    chunk = source.read(1024 * 1024)   # 1 Mo à la fois
                    if chunk:
                        buffer += chunk
                        # Comptage en octets (UTF-8 peut avoir plusieurs octets par caractère)
                        bytes_read += len(chunk.encode("utf-8", errors="ignore"))
                    else:
                        eof = True   # Plus rien à lire

                # Analyse du buffer accumulé
                while True:
                    buffer = buffer.lstrip()   # Supprime les espaces/retours en début
                    if not buffer:
                        break   # Buffer vide → attendre le prochain chunk

                    if not array_started:
                        # Cherche le "[" d'ouverture du tableau JSON
                        if buffer[0] != "[":
                            raise RuntimeError("Le fichier all-cards local ne contient pas un tableau JSON.")
                        buffer = buffer[1:]    # Avance après le "["
                        array_started = True
                        continue

                    if buffer[0] == ",":
                        buffer = buffer[1:]   # Séparateur entre éléments → on l'ignore
                        continue
                    if buffer[0] == "]":
                        return   # Fin du tableau → on a tout lu

                    try:
                        # Tentative de décodage du prochain objet JSON dans le buffer
                        # raw_decode() retourne (objet_décodé, position_fin_dans_buffer)
                        card, end_index = decoder.raw_decode(buffer)
                    except json.JSONDecodeError:
                        if eof:
                            raise   # En fin de fichier : l'erreur est réelle
                        break       # Objet incomplet → on attend plus de données

                    if isinstance(card, dict):
                        yield card, bytes_read   # On retourne la carte et la position

                    # Avance le buffer après l'objet qu'on vient de décoder
                    buffer = buffer[end_index:]

                if eof:
                    # Fin de fichier : le buffer devrait être vide ou contenir juste "]"
                    if buffer.strip() in {"", "]"}:
                        return
                    raise RuntimeError("Lecture du fichier all-cards local incomplète.")

    def _raise_if_cancelled(self) -> None:
        """
        Lève une exception si l'utilisateur a demandé l'annulation.

        Appelé régulièrement pendant l'indexation pour permettre l'interruption.

        Lève :
            RuntimeError : Si should_cancel() retourne True.
        """
        if self.should_cancel and self.should_cancel():
            raise RuntimeError("Indexation du bulk local annulée.")


# ---------------------------------------------------------------------------
#  Fonctions utilitaires (hors classe)
# ---------------------------------------------------------------------------

def find_local_bulk_file(root: Path, include_oracle_cards: bool = False) -> Path | None:
    """
    Cherche le fichier all-cards JSON le plus récent dans un dossier.

    On cherche les fichiers correspondant aux patterns :
    - "all-cards-YYYYMMDD.json"    (par défaut)
    - "oracle-cards-*.json"        (si include_oracle_cards=True)

    S'il y a plusieurs fichiers, on retourne le plus récent (par date de modification).

    Arguments :
        root                (Path) : Dossier où chercher le fichier.
        include_oracle_cards (bool): Inclure les fichiers oracle-cards ? (moins complets)

    Retourne :
        Path : Chemin du fichier le plus récent trouvé.
        None : Si aucun fichier correspondant n'existe.
    """
    candidates = []
    patterns = ("all-cards-*.json", "oracle-cards-*.json") if include_oracle_cards else ("all-cards-*.json",)
    for pattern in patterns:
        candidates.extend(root.glob(pattern))   # glob() cherche par motif avec wildcards
    # Tri par date de modification décroissante (le plus récent en premier)
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _print_row_from_card(card: dict) -> tuple[str, str, str, str, str, str, str, str, str, str, int] | None:
    """
    Convertit un objet carte JSON brut en tuple prêt pour l'insertion SQLite3.

    Extrait toutes les URLs d'image disponibles et les sérialise en JSON.
    Retourne None si la carte n'a aucune image utilisable.

    Arguments :
        card (dict) : Objet carte brut du fichier JSON bulk.

    Retourne :
        tuple : Ligne prête pour INSERT, ou None si la carte est inutilisable.
    """
    # Extraction des URLs d'image pour toutes les tailles disponibles
    image_urls = {
        image_size: image_url
        for image_size in ("small", "normal", "large", "png", "art_crop", "border_crop")
        if (image_url := _extract_image_url(card, image_size))
    }
    if not image_urls:
        return None   # Carte sans aucune image → on l'ignore

    oracle_id = str(card.get("oracle_id") or "")
    if not oracle_id:
        return None   # Carte sans oracle_id → données incomplètes

    # URL de prévisualisation : la plus petite disponible (chargement rapide)
    preview_url = (
        image_urls.get("small")
        or image_urls.get("normal")
        or image_urls.get("large")
        or next(iter(image_urls.values()))
    )

    highres = 1 if card.get("highres_image") else 0   # SQLite3 stocke les booléens en entier (0/1)

    return (
        oracle_id,
        str(card.get("lang", "")),
        str(card.get("id", "")),
        str(card.get("set", "")),
        str(card.get("set_name", "")),
        str(card.get("collector_number", "")),
        str(card.get("name", "")),
        str(card.get("released_at", "")),
        json.dumps(image_urls, ensure_ascii=False),   # Sérialisation du dict d'URLs en JSON
        preview_url,
        highres,
    )


def _extract_image_url(card: dict, image_size: str) -> str | None:
    """
    Extrait l'URL d'image d'une taille donnée depuis un objet carte JSON.

    Gère les cartes simples (image_uris direct) et les cartes double-face
    (image_uris dans chaque élément de card_faces).

    Arguments :
        card       (dict) : Objet carte brut du JSON bulk.
        image_size (str)  : Taille souhaitée ("small", "normal", "large"...).

    Retourne :
        str  : URL de l'image si disponible.
        None : Si aucune image de cette taille n'existe.
    """
    # Cas 1 : Carte simple — les URLs sont directement dans image_uris
    image_uris = card.get("image_uris")
    if isinstance(image_uris, dict) and image_uris.get(image_size):
        return str(image_uris[image_size])

    # Cas 2 : Carte double-face — les URLs sont dans card_faces[0].image_uris
    card_faces = card.get("card_faces")
    if isinstance(card_faces, list):
        for face in card_faces:
            face_uris = face.get("image_uris") if isinstance(face, dict) else None
            if isinstance(face_uris, dict) and face_uris.get(image_size):
                return str(face_uris[image_size])

    return None


def _normalize_name(value: str) -> str:
    """
    Normalise un nom de carte pour la comparaison textuelle insensible à la casse.

    La normalisation consiste à :
    - Mettre en minuscules avec casefold() (plus robuste que lower() pour les accents)
    - Remplacer les espaces multiples par un seul espace (split() + join())

    Exemples :
        "Lightning   Bolt"  → "lightning bolt"
        "FOREST"            → "forest"
        "Éclair"            → "éclair"  (casefold gère les accents)

    Arguments :
        value (str) : Nom à normaliser.

    Retourne :
        str : Nom normalisé.
    """
    return " ".join(value.casefold().split())
