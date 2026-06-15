# =============================================================================
#  DÉTECTEUR DE DOUBLONS — duplicate_finder.py
# =============================================================================
# Ce module analyse un dossier d'images et regroupe celles qui sont
# visuellement identiques ou très proches (doublons).
#
# POURQUOI UNE ANALYSE "VISUELLE" ET PAS UNE SIMPLE COMPARAISON DE FICHIERS ?
# Une même illustration de carte peut exister en plusieurs exemplaires qui ne
# sont PAS des copies bit-à-bit : tailles différentes (large vs png), niveaux de
# compression JPEG différents, recadrages légers, conversions de format...
# Comparer les octets ne détecterait aucun de ces cas. On utilise donc une
# "empreinte perceptuelle" (perceptual hash) qui capture l'apparence de l'image
# indépendamment de sa résolution ou de son format.
#
# COMMENT FONCTIONNE L'EMPREINTE (dHash — difference hash) ?
# 1. On réduit l'image en niveaux de gris à une toute petite taille (9×8 pixels).
# 2. Pour chaque ligne, on compare chaque pixel à son voisin de droite :
#    plus clair → bit 1, plus sombre → bit 0.
# 3. On obtient ainsi 8×8 = 64 bits : une "empreinte" d'une zone de l'image.
# Deux zones proches produisent des empreintes proches. On mesure leur
# différence avec la "distance de Hamming" (nombre de bits qui diffèrent).
#
# POURQUOI UNE GRILLE DE RÉGIONS ET PAS UNE SEULE EMPREINTE ?
# Les cartes partagent un cadre commun. Avec UNE empreinte de l'image entière,
# deux cartes différentes paraissent quasi identiques (le cadre domine). Mais
# l'illustration n'est pas toujours au même endroit selon le type de carte :
#   - carte classique : illustration en HAUT, bloc de texte en bas ;
#   - carte "Saga"     : illustration en bande verticale à DROITE, chapitres
#                        numérotés (I, II, III...) à GAUCHE.
# Aucun recadrage fixe unique ne convient donc à tous les types. On découpe
# plutôt l'image en grille (quadrants) et on calcule une empreinte PAR cellule.
# La "signature" d'une image est le tuple de ces empreintes.
#
# DISTANCE ENTRE DEUX IMAGES = le MAXIMUM des distances de Hamming cellule par
# cellule. Autrement dit, deux images ne sont des doublons que si TOUTES leurs
# cellules se ressemblent. Ainsi, quel que soit l'endroit où se trouve l'art
# (haut pour une carte classique, droite pour une Saga), la/les cellule(s) qui
# le contiennent suffisent à distinguer deux cartes différentes, tandis qu'une
# vraie copie (même carte redimensionnée/recompressée) concorde partout.
#
# REGROUPEMENT :
# On compare toutes les images deux à deux. Si leur distance est sous le seuil
# choisi, elles appartiennent au même groupe (via une structure union-find).
# Seuls les groupes contenant au moins 2 images sont retournés (= doublons).
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .dpi_upscaler import SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
#  Types de callbacks (mêmes conventions que les autres modules)
# ---------------------------------------------------------------------------
ProgressCallback = Callable[[str], None]
ProgressCountCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

HASH_SIZE = 8
# Taille de l'empreinte d'UNE cellule : 8 → 8×8 = 64 bits par cellule.
# La distance de Hamming maximale possible par cellule est donc 64.

GRID_COLS = 2
GRID_ROWS = 2
# Découpage de l'image en grille (2×2 = 4 quadrants) pour la signature.
# 2×2 suffit à séparer l'illustration du cadre/texte quelle que soit la
# disposition (art en haut pour une carte classique, à droite pour une Saga) :
# au moins une cellule contient l'art et diffère entre deux cartes différentes.


# ---------------------------------------------------------------------------
#  Structures de données
# ---------------------------------------------------------------------------

@dataclass
class DuplicateImage:
    """Une image analysée, avec ses métadonnées et sa signature perceptuelle."""
    path: Path
    width: int
    height: int
    file_size: int            # Taille du fichier en octets
    signature: tuple[int, ...]  # Empreinte perceptuelle par cellule de la grille

    @property
    def pixels(self) -> int:
        """Nombre total de pixels (sert à choisir la meilleure image d'un groupe)."""
        return self.width * self.height


@dataclass
class DuplicateGroup:
    """Un ensemble d'images considérées comme des doublons les unes des autres."""
    images: list[DuplicateImage]

    def best_index(self) -> int:
        """
        Index de l'image à conserver par défaut dans le groupe.

        Critère : la plus grande résolution (nombre de pixels), et en cas
        d'égalité, le plus gros fichier (souvent la meilleure qualité).
        """
        best = 0
        for index in range(1, len(self.images)):
            current = self.images[index]
            reference = self.images[best]
            if (current.pixels, current.file_size) > (reference.pixels, reference.file_size):
                best = index
        return best


# ---------------------------------------------------------------------------
#  Empreinte perceptuelle (dHash)
# ---------------------------------------------------------------------------

def difference_hash(image, hash_size: int = HASH_SIZE) -> int:
    """
    Calcule l'empreinte perceptuelle (dHash) d'UNE image/cellule Pillow.

    Réduit l'image en niveaux de gris à (hash_size+1 × hash_size) pixels, puis
    compare chaque pixel à son voisin de droite pour produire hash_size² bits.

    Arguments :
        image     : Image Pillow (n'importe quel mode).
        hash_size (int) : Côté de l'empreinte (8 → 64 bits).

    Retourne :
        int : Empreinte sur hash_size² bits.
    """
    from PIL import Image

    # Niveaux de gris + réduction à (hash_size+1) × hash_size.
    # La colonne supplémentaire permet hash_size comparaisons par ligne.
    small = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    row_width = hash_size + 1

    bits = 0
    bit_index = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * row_width + col]
            right = pixels[row * row_width + col + 1]
            if left > right:
                bits |= 1 << bit_index
            bit_index += 1
    return bits


def image_signature(
    image,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
    hash_size: int = HASH_SIZE,
) -> tuple[int, ...]:
    """
    Calcule la signature d'une image : une empreinte dHash par cellule de grille.

    L'image est découpée en (cols × rows) cellules de taille égale, et chacune
    reçoit sa propre empreinte. Le tuple résultant capture l'apparence de chaque
    zone séparément, ce qui permet de localiser où deux images diffèrent
    (l'illustration) indépendamment du cadre commun.

    Arguments :
        image     : Image Pillow.
        cols      (int) : Nombre de colonnes de la grille.
        rows      (int) : Nombre de lignes de la grille.
        hash_size (int) : Côté de l'empreinte de chaque cellule.

    Retourne :
        tuple[int, ...] : Une empreinte par cellule (lecture ligne par ligne).
    """
    width, height = image.size
    signature: list[int] = []
    for row in range(rows):
        for col in range(cols):
            box = (
                width * col // cols,
                height * row // rows,
                width * (col + 1) // cols,
                height * (row + 1) // rows,
            )
            signature.append(difference_hash(image.crop(box), hash_size))
    return tuple(signature)


def hamming_distance(first: int, second: int) -> int:
    """
    Compte le nombre de bits qui diffèrent entre deux empreintes de cellule.

    C'est la "distance de Hamming" : 0 = identiques, plus c'est grand, plus les
    cellules sont différentes.
    """
    return bin(first ^ second).count("1")


def signature_distance(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    """
    Distance entre deux signatures = MAXIMUM des distances de Hamming par cellule.

    Prendre le maximum impose que TOUTES les cellules se ressemblent pour que
    deux images soient considérées comme des doublons : il suffit qu'une seule
    cellule (typiquement celle de l'illustration) diffère franchement pour
    séparer deux cartes différentes, où que se trouve cette illustration.

    Arguments :
        first, second (tuple[int, ...]) : Signatures à comparer (même longueur).

    Retourne :
        int : La plus grande distance de Hamming observée entre cellules.
    """
    return max(hamming_distance(left, right) for left, right in zip(first, second))


# ---------------------------------------------------------------------------
#  Parcours des fichiers
# ---------------------------------------------------------------------------

def iter_image_files(folder: Path, recursive: bool = False) -> list[Path]:
    """
    Liste les fichiers images d'un dossier.

    Arguments :
        folder    (Path) : Dossier à parcourir.
        recursive (bool) : True = inclure aussi les sous-dossiers.

    Retourne :
        list[Path] : Fichiers images trouvés, triés alphabétiquement.
    """
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


# ---------------------------------------------------------------------------
#  Fonction principale
# ---------------------------------------------------------------------------

def find_duplicate_groups(
    source_folder: Path | str,
    recursive: bool = False,
    max_distance: int = 8,
    on_status: ProgressCallback | None = None,
    on_progress: ProgressCountCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[DuplicateGroup]:
    """
    Analyse un dossier et regroupe les images visuellement similaires.

    Étapes :
    1. Liste toutes les images du dossier (et sous-dossiers si recursive).
    2. Calcule l'empreinte perceptuelle de chacune.
    3. Compare toutes les paires : si leur distance de Hamming ≤ max_distance,
       elles sont fusionnées dans le même groupe (union-find).
    4. Retourne uniquement les groupes d'au moins 2 images.

    Arguments :
        source_folder (Path|str)     : Dossier à analyser.
        recursive     (bool)         : Inclure les sous-dossiers.
        max_distance  (int)          : Distance de Hamming maximale pour considérer
                                       deux images comme doublons (0 = identique).
        on_status     (Callable|None): Callback de messages texte.
        on_progress   (Callable|None): Callback de progression (traités, total).
        should_cancel (Callable|None): Callback d'annulation.

    Retourne :
        list[DuplicateGroup] : Groupes de doublons, triés par taille décroissante.

    Lève :
        RuntimeError : Si Pillow n'est pas installé.
        ValueError   : Si le dossier source est invalide.
    """
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow n'est pas installé. Lance la compilation pour installer les dépendances, "
            "ou installe Pillow avec: py -3 -m pip install Pillow"
        ) from error

    source = Path(source_folder)
    if not source.exists() or not source.is_dir():
        raise ValueError("Le dossier source est invalide.")

    files = iter_image_files(source, recursive)
    total = len(files)
    if on_status:
        on_status(f"Images détectées: {total}")
    if on_progress:
        on_progress(0, total)

    # --- Étape 1 : calcul des signatures ---
    images: list[DuplicateImage] = []
    for index, path in enumerate(files, start=1):
        if should_cancel and should_cancel():
            if on_status:
                on_status("Annulé.")
            return []
        try:
            with Image.open(path) as image:
                width, height = image.size
                signature = image_signature(image)
            images.append(
                DuplicateImage(
                    path=path,
                    width=width,
                    height=height,
                    file_size=path.stat().st_size,
                    signature=signature,
                )
            )
        except Exception as error:
            # Une image illisible ne doit pas arrêter toute l'analyse.
            if on_status:
                on_status(f"Ignorée (illisible): {path.name} — {error}")
        if on_progress:
            on_progress(index, total)

    if on_status:
        on_status("Comparaison des empreintes...")

    # --- Étape 2 : regroupement par union-find ---
    groups = _group_similar(images, max_distance, should_cancel)

    # Tri : les plus gros groupes d'abord, puis par nom de la première image.
    groups.sort(key=lambda group: (-len(group.images), group.images[0].path.name.lower()))

    if on_status:
        doublons = sum(len(group.images) for group in groups)
        on_status(
            f"{len(groups)} groupe(s) de doublons trouvé(s) "
            f"({doublons} fichiers concernés)."
        )
    return groups


def _group_similar(
    images: list[DuplicateImage],
    max_distance: int,
    should_cancel: CancelCallback | None,
) -> list[DuplicateGroup]:
    """
    Regroupe les images dont la distance de signature est ≤ max_distance.

    Utilise une structure union-find : chaque image commence dans son propre
    groupe, et on fusionne les groupes des paires suffisamment proches.

    Arguments :
        images        (list)      : Images analysées (avec signatures).
        max_distance  (int)       : Seuil de distance (max des cellules).
        should_cancel (Callable)  : Callback d'annulation.

    Retourne :
        list[DuplicateGroup] : Groupes contenant au moins 2 images.
    """
    count = len(images)
    parent = list(range(count))

    def find(node: int) -> int:
        # Recherche de la racine avec compression de chemin (itératif).
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    # Comparaison de toutes les paires (i < j).
    for i in range(count):
        if should_cancel and should_cancel():
            return []
        for j in range(i + 1, count):
            if find(i) == find(j):
                continue   # Déjà dans le même groupe → comparaison inutile
            if signature_distance(images[i].signature, images[j].signature) <= max_distance:
                union(i, j)

    # Reconstruction des groupes à partir des racines union-find.
    clusters: dict[int, list[DuplicateImage]] = {}
    for index in range(count):
        clusters.setdefault(find(index), []).append(images[index])

    return [DuplicateGroup(images=members) for members in clusters.values() if len(members) >= 2]
