# =============================================================================
#  CRÉATEUR DE MARGES — margin_creator.py
# =============================================================================
# Ce module ajoute des marges colorées autour des cartes Magic pour l'impression.
#
# POURQUOI DES MARGES ?
# Lors de l'impression de cartes proxy, les imprimantes et découpeuses physiques
# ont besoin d'une zone de "saignant" autour de l'image pour compenser les
# imprécisions de découpe. Sans marge, on risque d'avoir des bords blancs disgracieux.
#
# COMMENT FONCTIONNE LA CRÉATION DE MARGE ?
# 1. On détecte la couleur dominante des bords de la carte (coin haut-gauche,
#    haut-droit, bas-gauche, bas-droit)
# 2. On crée un fond légèrement plus grand que la carte
# 3. On étend les pixels de bord de la carte dans la marge pour une continuité visuelle
# 4. On applique un léger flou sur les bords de la marge pour un rendu propre
#
# RÉSULTAT :
# Une image légèrement plus grande que l'originale, avec des bords qui se fondent
# naturellement dans la couleur dominante de la carte.
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .dpi_upscaler import TARGET_CARD_SIZE, _current_dpi, _verify_image, iter_image_files


# ---------------------------------------------------------------------------
#  Types de callbacks
# ---------------------------------------------------------------------------
ProgressCallback = Callable[[str], None]
ProgressCountCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

VISIBLE_ALPHA_THRESHOLD = 220
# Seuil de transparence : un pixel avec alpha ≥ 220 est considéré "visible".
# Les cartes Magic ont souvent des coins arrondis (zones transparentes).
# Ce seuil permet de distinguer les vrais bords des zones transparentes des coins.

TARGET_DPI = 1200.0
# DPI de sortie pour toutes les images avec marges.
# Même résolution que le module dpi_upscaler pour la cohérence.

TARGET_MARGIN_PIXELS = 144
# Taille de la marge en pixels à 1200 DPI.
# Correspond à environ 3 mm de marge physique (144 px / 1200 DPI * 25.4 mm ≈ 3.05 mm).


# ---------------------------------------------------------------------------
#  Fonction principale
# ---------------------------------------------------------------------------

def create_black_margins(
    source_folder: Path | str,
    output_folder: Path | str | None = None,
    base_margin_pixels: int = 36,
    base_dpi: int = 300,
    on_status: ProgressCallback | None = None,
    on_progress: ProgressCountCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> tuple[int, Path]:
    """
    Ajoute des marges colorées à toutes les images d'un dossier.

    Pour chaque image :
    1. Normalise la taille à TARGET_CARD_SIZE (3193 × 4457 pixels)
    2. Détecte la couleur des bords de la carte
    3. Crée une image légèrement plus grande avec des bords étendus
    4. Applique un léger flou sur les bords pour un rendu propre
    5. Sauvegarde à 1200 DPI

    Arguments :
        source_folder     (Path|str)      : Dossier contenant les images à traiter.
        output_folder     (Path|str|None) : Dossier de sortie. None = "Margin_Creator/" dans la source.
        base_margin_pixels (int)          : Taille de marge à base_dpi. Défaut: 36 px @ 300 DPI.
        base_dpi          (int)           : DPI de référence pour base_margin_pixels. Défaut: 300.
        on_status         (Callable|None) : Callback de messages.
        on_progress       (Callable|None) : Callback de progression (traités, total).
        should_cancel     (Callable|None) : Callback d'annulation.

    Retourne :
        tuple (int, Path) : (nombre d'images traitées, chemin du dossier de sortie).

    Lève :
        RuntimeError : Si Pillow n'est pas installé ou si une image est invalide.
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

    target_folder = Path(output_folder) if output_folder else source / "Margin_Creator"
    target_folder.mkdir(parents=True, exist_ok=True)

    image_files = iter_image_files(source)
    total = len(image_files)
    if on_status:
        on_status(f"Dossier cible: {target_folder}")
        on_status(f"Images détectées: {total}")
    if on_progress:
        on_progress(0, total)

    count = 0
    for image_path in image_files:
        if should_cancel and should_cancel():
            if on_status:
                on_status("Annulé.")
            break

        target_path = target_folder / f"{image_path.stem}_Marged{image_path.suffix}"
        temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
        if temp_path.exists():
            temp_path.unlink()
        if on_status:
            on_status(f"Marge harmonisée: {image_path.name}")

        with Image.open(image_path) as image:
            detected_dpi = _current_dpi(image)
            # Calcul de la taille de marge adaptée au DPI de sortie
            margin = _target_margin_pixels(TARGET_DPI, base_margin_pixels, base_dpi)
            edge_color = _edge_color(image)
            if on_status:
                on_status(
                    f"DPI détecté: {detected_dpi:.0f} - DPI de sortie: {TARGET_DPI:.0f} - "
                    f"marge: {margin}px par côté - "
                    f"couleur de repli: {_hex_color(edge_color)}"
                )

            # Étape 1 : Conversion en RGBA et normalisation de taille
            base = _normalize_card_size(image.convert("RGBA"))

            # Étape 2 : Création du cadre avec bords étendus
            framed = _create_edge_extended_frame(base, margin, edge_color)

            # Étape 3 : Conversion du mode couleur pour le format de sortie
            framed = _prepare_for_format(framed, image_path.suffix.lower())

            # Étape 4 : Sauvegarde avec paramètres de qualité optimaux
            save_kwargs = _save_kwargs(image_path.suffix.lower(), TARGET_DPI)
            framed.save(temp_path, **save_kwargs)

        # Vérification et remplacement atomique
        _verify_image(temp_path)
        temp_path.replace(target_path)

        count += 1
        if on_progress:
            on_progress(count, total)

    return count, target_folder


# ---------------------------------------------------------------------------
#  Algorithme de création des bords étendus
# ---------------------------------------------------------------------------

def _create_edge_extended_frame(base, margin: int, fallback_color: tuple[int, int, int]):
    """
    Crée une image élargie avec des bords étendus depuis la carte originale.

    Algorithme :
    1. On crée une nouvelle image plus grande (width + 2*margin, height + 2*margin)
    2. On colle la carte au centre
    3. On étend les pixels de bord (une ligne/colonne de pixels) dans la marge
    4. On crée des coins avec un mélange diagonal des deux bords adjacents
    5. On applique un lissage sur les marges extérieures

    Le résultat : une marge qui se fond naturellement dans la couleur du bord de la carte.

    Arguments :
        base          : Image Pillow de la carte (RGBA, à TARGET_CARD_SIZE).
        margin        (int) : Largeur de la marge en pixels.
        fallback_color (tuple[int,int,int]) : Couleur RGB de repli pour le fond.

    Retourne :
        Image Pillow : Carte avec marges, en mode RGB.
    """
    from PIL import Image

    width, height = base.size

    # Conversion de la carte aux coins arrondis en rectangle complet
    # (les zones transparentes des coins sont remplies avec la couleur de bord)
    rectangle = _rounded_card_to_rectangle(base, fallback_color, margin)

    # Création du fond de l'image finale (fallback_color rempli les coins extrêmes)
    framed = Image.new("RGB", (width + margin * 2, height + margin * 2), fallback_color)

    # Collage de la carte au centre
    framed.paste(rectangle, (margin, margin))

    # Extension des 4 bords (étirement d'une ligne/colonne sur toute la marge)
    # NEAREST = pixel le plus proche (pas d'interpolation, on veut étendre exactement)
    top_flap    = rectangle.crop((0, 0, width, 1)).resize((width, margin), Image.Resampling.NEAREST)
    bottom_flap = rectangle.crop((0, height - 1, width, height)).resize((width, margin), Image.Resampling.NEAREST)
    left_flap   = rectangle.crop((0, 0, 1, height)).resize((margin, height), Image.Resampling.NEAREST)
    right_flap  = rectangle.crop((width - 1, 0, width, height)).resize((margin, height), Image.Resampling.NEAREST)

    framed.paste(top_flap,    (margin, 0))                   # Marge du haut
    framed.paste(bottom_flap, (margin, margin + height))     # Marge du bas
    framed.paste(left_flap,   (0, margin))                   # Marge gauche
    framed.paste(right_flap,  (margin + width, margin))      # Marge droite

    # Création des 4 coins (zones diagonales de mélange entre bord horizontal et vertical)
    _paste_corner_triangles(
        framed,
        horizontal=_corner_from_horizontal(rectangle, margin, top=True, left=True),
        vertical=_corner_from_vertical(rectangle, margin, top=True, left=True),
        vertical_mask=_side_triangle_mask(margin, "top_left"),
        origin=(0, 0),
    )
    _paste_corner_triangles(
        framed,
        horizontal=_corner_from_horizontal(rectangle, margin, top=True, left=False),
        vertical=_corner_from_vertical(rectangle, margin, top=True, left=False),
        vertical_mask=_side_triangle_mask(margin, "top_right"),
        origin=(margin + width, 0),
    )
    _paste_corner_triangles(
        framed,
        horizontal=_corner_from_horizontal(rectangle, margin, top=False, left=True),
        vertical=_corner_from_vertical(rectangle, margin, top=False, left=True),
        vertical_mask=_side_triangle_mask(margin, "bottom_left"),
        origin=(0, margin + height),
    )
    _paste_corner_triangles(
        framed,
        horizontal=_corner_from_horizontal(rectangle, margin, top=False, left=False),
        vertical=_corner_from_vertical(rectangle, margin, top=False, left=False),
        vertical_mask=_side_triangle_mask(margin, "bottom_right"),
        origin=(margin + width, margin + height),
    )

    # Lissage final des marges extérieures pour réduire les artefacts visuels
    _polish_outer_margins(framed, margin)
    return framed


def _rounded_card_to_rectangle(base, fallback_color: tuple[int, int, int], margin: int):
    """
    Convertit une carte aux coins arrondis en rectangle plein.

    Les cartes Magic ont des coins arrondis, ce qui crée des zones transparentes
    dans les images PNG. Cette fonction remplace ces zones transparentes par
    une couleur de bord cohérente avec la carte.

    Pour chaque ligne de l'image :
    1. Détecte les pixels visibles (alpha ≥ seuil)
    2. Remplit la zone gauche (coins arrondis) avec la couleur du bord gauche
    3. Copie les pixels visibles
    4. Remplit la zone droite avec la couleur du bord droit
    Les lignes vides (haut et bas) sont remplies par répétition de la première/dernière ligne visible.

    Arguments :
        base          : Image Pillow de la carte en mode RGBA.
        fallback_color (tuple) : Couleur RGB de repli.
        margin        (int)    : Largeur de marge (pour calculer l'échantillonnage de couleur).

    Retourne :
        Image Pillow en mode RGB (rectangle plein, sans transparence).
    """
    from PIL import Image

    width, height = base.size
    rgb = base.convert("RGB")

    # Masque binaire de visibilité : pixel visible (255) ou transparent (0)
    alpha_mask = base.getchannel("A").point(
        lambda value: 255 if value >= VISIBLE_ALPHA_THRESHOLD else 0
    )
    rectangle = Image.new("RGB", (width, height), fallback_color)
    visible_rows: list[int] = []   # Lignes ayant au moins un pixel visible

    for y in range(height):
        # Bounding box de la ligne : où commencent et finissent les pixels visibles
        row_box = alpha_mask.crop((0, y, width, y + 1)).getbbox()
        if not row_box:
            continue   # Ligne entièrement transparente → on la traite après

        left, _, right, _ = row_box
        visible_rows.append(y)

        # Remplissage de la zone gauche (coins arrondis)
        if left > 0:
            left_color = _stable_row_edge_color(rgb, alpha_mask, y, left, right, margin, from_left=True)
            rectangle.paste(Image.new("RGB", (left, 1), left_color), (0, y))

        # Copie des pixels visibles de la carte
        rectangle.paste(rgb.crop((left, y, right, y + 1)), (left, y))

        # Remplissage de la zone droite
        if right < width:
            right_color = _stable_row_edge_color(rgb, alpha_mask, y, left, right, margin, from_left=False)
            rectangle.paste(Image.new("RGB", (width - right, 1), right_color), (right, y))

    # Remplissage des lignes vides (haut et bas de la carte)
    _fill_empty_rows(rectangle, visible_rows)
    return rectangle


def _stable_row_edge_color(rgb, alpha_mask, y: int, left: int, right: int, margin: int, from_left: bool):
    """
    Calcule la couleur de bord stable d'une ligne, en moyennant plusieurs pixels.

    Plutôt que de prendre un seul pixel de bord (qui pourrait être atypique),
    on moyenne plusieurs pixels proches du bord pour une couleur plus stable.

    Arguments :
        rgb        : Image RGB source.
        alpha_mask : Masque de visibilité.
        y          (int)  : Ligne à traiter.
        left       (int)  : Position du premier pixel visible.
        right      (int)  : Position après le dernier pixel visible.
        margin     (int)  : Marge (pour déterminer le rayon d'échantillonnage).
        from_left  (bool) : True = on cherche la couleur du bord gauche.

    Retourne :
        tuple (R, G, B) : Couleur moyenne de bord.
    """
    center = _inset_edge_x(left, right, margin, from_left)
    samples = [
        rgb.getpixel((x, y))
        for x in _sample_x_positions(left, right, center, margin, from_left)
        if alpha_mask.getpixel((x, y)) == 255   # Seulement les pixels visibles
    ]
    if not samples:
        return rgb.getpixel((center, y))   # Repli sur le pixel central si aucun échantillon visible

    # Moyenne des composantes RGB de tous les échantillons
    return tuple(round(sum(pixel[channel] for pixel in samples) / len(samples)) for channel in range(3))


def _sample_x_positions(left: int, right: int, center: int, margin: int, from_left: bool) -> list[int]:
    """
    Génère une liste de positions X à échantillonner autour du centre.

    Le rayon d'échantillonnage est calculé proportionnellement à la marge,
    avec un maximum de 3 pixels pour rester proche du bord réel.

    Arguments :
        left, right (int)  : Limites de la zone visible.
        center      (int)  : Position centrale d'échantillonnage.
        margin      (int)  : Taille de la marge.
        from_left   (bool) : Ordre de tri (gauche=croissant, droite=décroissant).

    Retourne :
        list[int] : Positions X dans les limites [left, right[.
    """
    radius = min(3, max(1, margin // 24), max(0, right - left - 1))
    positions = []
    for offset in range(-radius, radius + 1):
        x = center + offset
        if left <= x < right:
            positions.append(x)

    if from_left:
        return sorted(set(positions))
    return sorted(set(positions), reverse=True)


def _inset_edge_x(left: int, right: int, margin: int, from_left: bool) -> int:
    """
    Calcule la position X légèrement à l'intérieur du bord visible.

    On s'éloigne légèrement du bord strict pour éviter les pixels de bord
    qui peuvent être atypiques (dégradés, antialiasing des coins arrondis).

    Arguments :
        left, right (int)  : Limites de la zone visible.
        margin      (int)  : Taille de la marge.
        from_left   (bool) : True = on cherche depuis le bord gauche.

    Retourne :
        int : Position X à l'intérieur du bord visible.
    """
    inset = min(max(2, margin + max(1, margin // 16)), max(1, right - left) - 1)
    if from_left:
        return min(right - 1, left + inset)
    return max(left, right - 1 - inset)


def _fill_empty_rows(rectangle, visible_rows: list[int]) -> None:
    """
    Remplit les lignes transparentes en haut et bas de la carte.

    Les cartes aux coins arrondis ont des lignes entièrement transparentes
    en haut et en bas (les lignes où les coins sont coupés).
    On les remplit par répétition de la première/dernière ligne visible.

    Arguments :
        rectangle    : Image PIL (modifiée en place).
        visible_rows (list[int]) : Indices des lignes ayant des pixels visibles.
    """
    width, height = rectangle.size
    if not visible_rows:
        return

    first_y = visible_rows[0]
    last_y = visible_rows[-1]

    # Copie de la première et dernière ligne visible
    top_row = rectangle.crop((0, first_y, width, first_y + 1))
    bottom_row = rectangle.crop((0, last_y, width, last_y + 1))

    # Remplissage des lignes vides du haut par répétition de la première ligne
    for y in range(0, first_y):
        rectangle.paste(top_row, (0, y))

    # Remplissage des lignes vides du bas par répétition de la dernière ligne
    for y in range(last_y + 1, height):
        rectangle.paste(bottom_row, (0, y))


# ---------------------------------------------------------------------------
#  Gestion des coins (zones de mélange diagonal)
# ---------------------------------------------------------------------------

def _corner_from_horizontal(rectangle, margin: int, top: bool, left: bool):
    """
    Extrait et redimensionne la source horizontale d'un coin de marge.

    Pour chaque coin, on mélange deux sources :
    - Source horizontale : bande de la ligne de bord (haut ou bas)
    - Source verticale   : colonne du bord (gauche ou droite)
    Un masque triangulaire définit quelle source domine où.

    Arguments :
        rectangle : Image PIL source (rectangle plein).
        margin    (int)  : Taille de la marge.
        top       (bool) : True = coin du haut. False = coin du bas.
        left      (bool) : True = coin gauche. False = coin droit.

    Retourne :
        Image PIL : Carré de taille (margin × margin) depuis la ligne de bord.
    """
    from PIL import Image

    source_y = 0 if top else rectangle.height - 1
    if left:
        start_x, end_x = _stable_corner_range(rectangle.width, margin, from_start=True)
    else:
        start_x, end_x = _stable_corner_range(rectangle.width, margin, from_start=False)
    source_box = (start_x, source_y, end_x, source_y + 1)
    return rectangle.crop(source_box).resize((margin, margin), Image.Resampling.NEAREST)


def _corner_from_vertical(rectangle, margin: int, top: bool, left: bool):
    """
    Extrait et redimensionne la source verticale d'un coin de marge.

    Similaire à _corner_from_horizontal mais pour la colonne de bord.

    Arguments :
        rectangle : Image PIL source.
        margin    (int)  : Taille de la marge.
        top       (bool) : True = coin du haut.
        left      (bool) : True = coin gauche.

    Retourne :
        Image PIL : Carré de taille (margin × margin) depuis la colonne de bord.
    """
    from PIL import Image

    source_x = 0 if left else rectangle.width - 1
    if top:
        start_y, end_y = _stable_corner_range(rectangle.height, margin, from_start=True)
    else:
        start_y, end_y = _stable_corner_range(rectangle.height, margin, from_start=False)
    source_box = (source_x, start_y, source_x + 1, end_y)
    return rectangle.crop(source_box).resize((margin, margin), Image.Resampling.NEAREST)


def _stable_corner_range(length: int, margin: int, from_start: bool) -> tuple[int, int]:
    """
    Calcule une plage stable de pixels pour l'échantillonnage d'un coin.

    On évite de prendre les pixels trop proches du coin réel de la carte
    qui peuvent être atypiques (coins arrondis, dégradés).

    Arguments :
        length     (int)  : Dimension de l'image dans la direction concernée.
        margin     (int)  : Taille de la marge.
        from_start (bool) : True = depuis le début. False = depuis la fin.

    Retourne :
        tuple (int, int) : (début, fin) de la plage d'échantillonnage.
    """
    if length <= margin:
        return (0, length)

    if from_start:
        start = min(margin, max(0, length - margin))
        end = min(length, start + margin)
    else:
        end = max(margin, length - margin)
        start = max(0, end - margin)
    return (start, end)


def _paste_corner_triangles(framed, horizontal, vertical, vertical_mask, origin: tuple[int, int]) -> None:
    """
    Compose un coin de marge en mélangeant les sources horizontale et verticale.

    La source horizontale remplit tout le coin par défaut.
    La source verticale est collée par-dessus en utilisant un masque triangulaire
    pour définir la zone de transition diagonale.

    Arguments :
        framed        : Image PIL finale (modifiée en place).
        horizontal    : Image PIL source horizontale (margin × margin).
        vertical      : Image PIL source verticale (margin × margin).
        vertical_mask : Masque PIL (L) définissant où la source verticale domine.
        origin        (tuple) : Position (x, y) du coin dans l'image finale.
    """
    corner = horizontal.copy()
    corner.paste(vertical, (0, 0), vertical_mask)   # Collage de la source verticale avec masque
    framed.paste(corner, origin)


def _side_triangle_mask(size: int, corner: str):
    """
    Crée un masque triangulaire pour la transition diagonale d'un coin.

    Pour chaque coin, le triangle détermine où la source verticale (bord latéral)
    domine sur la source horizontale (bord haut/bas).

    Le triangle divise le carré en diagonale :
    - top_left    : triangle inférieur-gauche (y > x)
    - top_right   : triangle inférieur-droit (y > size-1-x)
    - bottom_left : triangle supérieur-gauche (y < size-1-x)
    - bottom_right: triangle supérieur-droit (y < x)

    Arguments :
        size   (int) : Taille du carré (margin × margin).
        corner (str) : "top_left", "top_right", "bottom_left", ou "bottom_right".

    Retourne :
        Image PIL en mode "L" (niveaux de gris) : 255 là où vertical domine, 0 ailleurs.
    """
    from PIL import Image

    mask = Image.new("L", (size, size), 0)
    last = size - 1
    pixels = mask.load()

    for y in range(size):
        for x in range(size):
            if _is_side_triangle_pixel(x, y, last, corner):
                pixels[x, y] = 255

    return mask


def _is_side_triangle_pixel(x: int, y: int, last: int, corner: str) -> bool:
    """
    Détermine si un pixel (x, y) appartient au triangle du coin donné.

    Arguments :
        x, y  (int) : Coordonnées du pixel.
        last  (int) : Indice du dernier pixel (size - 1).
        corner (str) : Nom du coin.

    Retourne :
        bool : True si le pixel est dans le triangle vertical.
    """
    if corner == "top_left":
        return y > x                # Partie basse-gauche du carré
    if corner == "top_right":
        return y > last - x         # Partie basse-droite
    if corner == "bottom_left":
        return y < last - x         # Partie haute-gauche
    if corner == "bottom_right":
        return y < x                # Partie haute-droite
    raise ValueError(f"Coin inconnu: {corner}")


# ---------------------------------------------------------------------------
#  Post-traitement et utilitaires
# ---------------------------------------------------------------------------

def _polish_outer_margins(image, margin: int) -> None:
    """
    Applique un léger lissage sur les bandes de marge extérieures.

    L'extension pixel par pixel des bords peut créer des artefacts visuels
    (lignes droites trop nettes, pixels isolés de couleur différente).
    Un filtre médian sur les bandes extérieures atténue ces artefacts.

    Arguments :
        image  : Image PIL (modifiée en place).
        margin (int) : Taille de la marge en pixels.
    """
    if margin < 8:
        return   # Marges trop petites → lissage pas utile

    # Lissage des 4 bandes de marge (haut, bas, gauche, droite)
    _polish_band(image, (0, 0, image.width, margin), horizontal=True)
    _polish_band(image, (0, image.height - margin, image.width, image.height), horizontal=True)
    _polish_band(image, (0, margin, margin, image.height - margin), horizontal=False)
    _polish_band(image, (image.width - margin, margin, image.width, image.height - margin), horizontal=False)


def _polish_band(image, box: tuple[int, int, int, int], horizontal: bool) -> None:
    """
    Applique un filtre médian sélectif sur une bande de l'image.

    Le filtre n'est appliqué que sur les pixels qui diffèrent suffisamment
    de la version floutée (détection d'artefacts). Les zones lisses restent intactes.

    Le filtre médian remplace chaque pixel par la médiane de ses voisins,
    ce qui élimine les pics de couleur (pixels isolés aberrants).

    Arguments :
        image      : Image PIL (modifiée en place).
        box        (tuple) : Coordonnées (left, top, right, bottom) de la bande.
        horizontal (bool)  : True si la bande est horizontale (haut/bas).
    """
    from PIL import ImageFilter

    band = image.crop(box)
    blurred = band.filter(ImageFilter.MedianFilter(size=3))  # Médiane sur fenêtre 3×3
    mask = _subtle_artifact_mask(band, blurred, horizontal)
    band.paste(blurred, (0, 0), mask)    # Colle le flou seulement sur les artefacts
    image.paste(band, box[:2])           # Recolle la bande traitée dans l'image


def _subtle_artifact_mask(band, blurred, horizontal: bool):
    """
    Crée un masque des pixels "artefacts" à lisser.

    Un artefact est un pixel qui diffère significativement de ses voisins.
    On calcule la différence entre l'image originale et la version floutée.
    Les pixels avec une grande différence sont considérés comme des artefacts.

    Les seuils sont légèrement différents pour les bandes horizontales et verticales
    car la direction des transitions de couleur est différente.

    Arguments :
        band       : Bande originale.
        blurred    : Bande après filtre médian.
        horizontal (bool) : True si bande horizontale.

    Retourne :
        Image PIL en mode "L" : 255 là où il faut lisser, 0 ailleurs.
    """
    from PIL import Image, ImageChops

    diff = ImageChops.difference(band, blurred).convert("L")   # Différence pixel à pixel
    threshold = 18 if horizontal else 14   # Seuil de tolérance
    return diff.point(lambda value: 255 if value >= threshold else 0)


def _prepare_for_format(image, extension: str):
    """
    Convertit une image RGB/RGBA au mode compatible avec le format de sortie.

    JPEG et BMP ne supportent pas les modes avec canal alpha ou palette.
    On convertit en RGB si nécessaire.

    Arguments :
        image     : Image Pillow.
        extension (str) : Extension du format (".jpg", ".png"...).

    Retourne :
        Image Pillow (convertie ou originale).
    """
    if extension in {".jpg", ".jpeg", ".bmp"} and image.mode not in {"RGB", "L"}:
        return image.convert("RGB")
    return image


def _edge_color(image) -> tuple[int, int, int]:
    """
    Détecte la couleur dominante des bords d'une carte en échantillonnant les 4 coins.

    On sample les 4 pixels de coin de la bounding box de la carte visible
    et on fait la moyenne pour obtenir une couleur de fond cohérente.

    Utile pour les cartes à fond de couleur (ex: cartes Planéchase dorées,
    cartes Commander à fond marron...).

    Arguments :
        image : Image Pillow (n'importe quel mode).

    Retourne :
        tuple (R, G, B) : Couleur moyenne des 4 coins visibles, ou (0,0,0) si invisible.
    """
    sample = image.convert("RGBA")
    # Bounding box des pixels visibles (alpha ≥ seuil)
    bbox = sample.getchannel("A").point(
        lambda value: 255 if value >= VISIBLE_ALPHA_THRESHOLD else 0
    ).getbbox()
    if not bbox:
        return (0, 0, 0)   # Image entièrement transparente → fond noir

    left, top, right, bottom = bbox
    # Échantillonnage des 4 coins de la zone visible
    candidates = [
        sample.getpixel((left, top))[:3],          # Coin haut-gauche
        sample.getpixel((right - 1, top))[:3],     # Coin haut-droit
        sample.getpixel((left, bottom - 1))[:3],   # Coin bas-gauche
        sample.getpixel((right - 1, bottom - 1))[:3], # Coin bas-droit
    ]
    # Moyenne des composantes RGB de tous les coins
    return tuple(round(sum(color[channel] for color in candidates) / len(candidates)) for channel in range(3))


def _hex_color(color: tuple[int, int, int]) -> str:
    """
    Convertit une couleur RGB en notation hexadécimale CSS.

    Exemple : (255, 128, 0) → "#FF8000"

    Arguments :
        color (tuple[int,int,int]) : Couleur (R, G, B) avec valeurs 0-255.

    Retourne :
        str : Notation hexadécimale de la couleur.
    """
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def _normalize_card_size(image):
    """
    Redimensionne une carte à la taille standard si nécessaire.

    On utilise TARGET_CARD_SIZE importé de dpi_upscaler (3193 × 4457 pixels).
    Si l'image est déjà à cette taille, on la retourne sans modification.

    Arguments :
        image : Image Pillow.

    Retourne :
        Image Pillow : À la taille cible (potentiellement redimensionnée).
    """
    from PIL import Image
    if image.size == TARGET_CARD_SIZE:
        return image
    return image.resize(TARGET_CARD_SIZE, Image.Resampling.LANCZOS)


def _target_margin_pixels(dpi: float, base_margin_pixels: int, base_dpi: int) -> int:
    """
    Calcule la taille de marge en pixels pour le DPI de sortie.

    Utilise TARGET_MARGIN_PIXELS directement pour les paramètres standard
    (1200 DPI, 36px @ 300 DPI) pour éviter les erreurs d'arrondi.

    Arguments :
        dpi               (float) : DPI de sortie.
        base_margin_pixels (int)  : Taille de marge à base_dpi.
        base_dpi          (int)   : DPI de référence.

    Retourne :
        int : Taille de marge en pixels.
    """
    if int(round(dpi)) == int(TARGET_DPI) and base_margin_pixels == 36 and base_dpi == 300:
        return TARGET_MARGIN_PIXELS   # Cas standard → valeur précalculée
    return _margin_pixels(dpi, base_margin_pixels, base_dpi)


def _margin_pixels(dpi: float, base_margin_pixels: int, base_dpi: int) -> int:
    """
    Calcule la taille de marge en pixels par proportionnalité au DPI.

    Formule : marge = base_margin_pixels × (dpi / base_dpi)
    Exemple : 36px @ 300 DPI → 144px @ 1200 DPI (× 4)

    Arguments :
        dpi               (float) : DPI de sortie.
        base_margin_pixels (int)  : Taille de référence à base_dpi.
        base_dpi          (int)   : DPI de référence.

    Retourne :
        int : Taille de marge (au moins 1 pixel).
    """
    return max(1, round(base_margin_pixels * dpi / base_dpi))


def _save_kwargs(extension: str, dpi: float) -> dict:
    """
    Génère les paramètres de sauvegarde Pillow pour le format et le DPI donnés.

    Arguments :
        extension (str)   : Extension du format (".jpg", ".png", ".webp"...).
        dpi       (float) : DPI à inscrire dans les métadonnées.

    Retourne :
        dict : Paramètres pour Image.save().
    """
    rounded_dpi = int(round(dpi))
    kwargs: dict = {"dpi": (rounded_dpi, rounded_dpi)}

    if extension in {".jpg", ".jpeg"}:
        kwargs.update({"quality": 95, "subsampling": 0})
    elif extension == ".webp":
        kwargs.update({"quality": 95})

    return kwargs
