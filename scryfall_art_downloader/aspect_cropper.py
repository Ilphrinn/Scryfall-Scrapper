# =============================================================================
#  RECADRAGE AU RATIO — aspect_cropper.py
# =============================================================================
# Ce module recadre (coupe) des images au ratio standard des cartes Magic: The Gathering.
#
# QU'EST-CE QUE LE RATIO D'ASPECT ?
# Le ratio d'aspect est la proportion largeur/hauteur d'une image.
# Une carte Magic standard mesure 63 mm × 88 mm → ratio = 63/88 ≈ 0.7159 ≈ 0.714
# On s'assure que toutes les images ont exactement ce ratio pour l'impression.
#
# POURQUOI RECADRER ?
# Les images téléchargées depuis Scryfall ne sont pas toujours exactement au
# bon ratio. En recadrant (sans redimensionner), on conserve la résolution
# et la qualité originales.
#
# ALGORITHME DE RECADRAGE CENTRÉ :
# 1. Si l'image est "trop large" → on garde toute la hauteur, on coupe les côtés
# 2. Si l'image est "trop haute" → on garde toute la largeur, on coupe le haut/bas
# 3. On centre le recadrage
# =============================================================================

from __future__ import annotations

from pathlib import Path

# Import d'une fonction et d'une constante du module dpi_upscaler
# pour réutiliser la détection DPI et la vérification d'image
from .dpi_upscaler import _current_dpi, _verify_image


# Ratio cible : largeur / hauteur d'une carte Magic standard
# 63 mm / 88 mm ≈ 0.7159, arrondi à 0.714 pour les calculs
TARGET_ASPECT_RATIO = 0.714


def centered_crop_rect(
    width: int,
    height: int,
    aspect_ratio: float = TARGET_ASPECT_RATIO,
) -> tuple[int, int, int, int]:
    """
    Calcule le rectangle de recadrage centré pour obtenir le ratio voulu.

    Le rectangle est calculé pour être aussi grand que possible tout en
    restant dans les dimensions de l'image source.

    Logique :
    - Si l'image est plus "large" que le ratio cible → on réduit la largeur
    - Si l'image est plus "haute" que le ratio cible → on réduit la hauteur

    Arguments :
        width        (int)   : Largeur de l'image source en pixels.
        height       (int)   : Hauteur de l'image source en pixels.
        aspect_ratio (float) : Ratio cible largeur/hauteur (défaut: 0.714).

    Retourne :
        tuple (left, top, right, bottom) : Coordonnées du rectangle de recadrage
            en pixels (format utilisé par Pillow : Image.crop()).

    Lève :
        ValueError : Si les dimensions sont nulles ou négatives.

    Exemple :
        rect = centered_crop_rect(1000, 1200)
        # L'image 1000×1200 a un ratio 0.833 (trop haute)
        # → on garde la largeur 1000, hauteur = 1000/0.714 ≈ 1401 → trop grand
        # → on garde la hauteur 1200, largeur = 1200*0.714 ≈ 857
        # → rect = ((1000-857)//2, 0, (1000-857)//2 + 857, 1200) = (71, 0, 928, 1200)
    """
    if width <= 0 or height <= 0:
        raise ValueError("Dimensions d'image invalides.")

    image_ratio = width / height   # Ratio actuel de l'image source

    if image_ratio > aspect_ratio:
        # L'image est plus large que voulu → on conserve la hauteur entière
        # et on calcule la largeur correspondant au ratio cible
        crop_height = height
        crop_width = round(height * aspect_ratio)
    else:
        # L'image est plus haute que voulu (ou au bon ratio) → on conserve la largeur
        # et on calcule la hauteur correspondant au ratio cible
        crop_width = width
        crop_height = round(width / aspect_ratio)

    # Centrage : on calcule l'offset pour centrer le recadrage dans l'image
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    return (left, top, left + crop_width, top + crop_height)


def crop_image_to_ratio(
    source_file: Path | str,
    output_file: Path | str,
    crop_rect: tuple[int, int, int, int] | None = None,
    aspect_ratio: float = TARGET_ASPECT_RATIO,
) -> Path:
    """
    Recadre une image au ratio standard des cartes Magic et la sauvegarde.

    Le recadrage est effectué SANS redimensionnement pour conserver la qualité.
    Le DPI original de l'image est préservé dans le fichier de sortie.

    Si crop_rect est fourni, on l'utilise directement (recadrage manuel).
    Sinon, on calcule automatiquement le recadrage centré.

    La sauvegarde est faite via un fichier temporaire pour éviter de corrompre
    le fichier de sortie en cas d'erreur.

    Arguments :
        source_file  (Path|str)              : Chemin de l'image source.
        output_file  (Path|str)              : Chemin du fichier de sortie.
        crop_rect    (tuple|None)            : Rectangle (left,top,right,bottom) ou None.
        aspect_ratio (float)                 : Ratio cible (défaut: 0.714).

    Retourne :
        Path : Chemin du fichier de sortie créé.

    Lève :
        RuntimeError : Si Pillow n'est pas installé ou si le fichier résultant est invalide.
        ValueError   : Si le fichier source est invalide.
    """
    # Pillow est une bibliothèque de traitement d'images. On vérifie qu'elle est disponible.
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow n'est pas installé. Lance la compilation pour installer les dépendances, "
            "ou installe Pillow avec: py -3 -m pip install Pillow"
        ) from error

    source = Path(source_file)
    if not source.exists() or not source.is_file():
        raise ValueError("Le fichier source est invalide.")

    target = Path(output_file)
    if not target.suffix:
        # Si pas d'extension spécifiée pour la sortie, on garde celle de la source
        target = target.with_suffix(source.suffix)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Fichier temporaire : on écrit d'abord ici, puis on renomme à la fin
    # Cela évite un fichier de sortie corrompu si une erreur se produit pendant la sauvegarde
    temp_path = target.with_name(f"{target.stem}.tmp{target.suffix}")
    if temp_path.exists():
        temp_path.unlink()   # Supprime un éventuel résidu de précédente exécution

    with Image.open(source) as image:
        # Calcul ou utilisation du rectangle de recadrage
        rect = crop_rect or centered_crop_rect(image.width, image.height, aspect_ratio)

        # Ajustement fin du rectangle pour être EXACTEMENT au bon ratio
        # (centered_crop_rect peut donner une légère erreur due aux arrondis)
        left, top, right, bottom = _normalize_aspect_rect(rect, image.width, image.height, aspect_ratio)

        cropped = image.crop((left, top, right, bottom))

        # Conversion du mode couleur si nécessaire (JPEG ne supporte pas la transparence)
        cropped = _prepare_for_format(cropped, target.suffix.lower())

        # Sauvegarde avec les paramètres adaptés au format et le DPI original
        cropped.save(temp_path, **_save_kwargs(target.suffix.lower(), _current_dpi(image)))

    # Vérification que le fichier sauvegardé est valide (non corrompu)
    _verify_image(temp_path)

    # Remplacement atomique : le fichier temporaire devient le fichier final
    temp_path.replace(target)
    return target


def _normalize_aspect_rect(
    rect: tuple[int, int, int, int],
    width: int,
    height: int,
    aspect_ratio: float,
) -> tuple[int, int, int, int]:
    """
    Ajuste un rectangle de recadrage pour être EXACTEMENT au ratio voulu.

    Les calculs avec des arrondis d'entiers peuvent introduire une légère
    déviation du ratio. Cette fonction recentre et ajuste le rectangle.

    Arguments :
        rect         (tuple) : Rectangle (left, top, right, bottom) initial.
        width        (int)   : Largeur de l'image.
        height       (int)   : Hauteur de l'image.
        aspect_ratio (float) : Ratio cible.

    Retourne :
        tuple : Rectangle ajusté, garanti dans les limites de l'image.
    """
    # D'abord on s'assure que le rectangle est dans les limites de l'image
    left, top, right, bottom = _clamp_rect(rect, width, height)
    crop_width = right - left
    crop_height = bottom - top
    current_ratio = crop_width / crop_height

    # Ajustement de la dimension qui dépasse le ratio cible
    if current_ratio > aspect_ratio:
        crop_width = max(1, round(crop_height * aspect_ratio))
    else:
        crop_height = max(1, round(crop_width / aspect_ratio))

    # Recentrage du rectangle après ajustement
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    left = round(center_x - crop_width / 2)
    top = round(center_y - crop_height / 2)

    # S'assure que le rectangle reste dans les limites de l'image
    left = max(0, min(width - crop_width, left))
    top = max(0, min(height - crop_height, top))
    return (left, top, left + crop_width, top + crop_height)


def _clamp_rect(
    rect: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """
    Borne un rectangle de recadrage dans les limites d'une image.

    "Clamper" signifie forcer une valeur dans un intervalle [min, max].
    On s'assure qu'aucune coordonnée ne dépasse les bords de l'image
    et que le rectangle a une taille minimale de 1×1 pixel.

    Arguments :
        rect   (tuple) : Rectangle (left, top, right, bottom) potentiellement hors limites.
        width  (int)   : Largeur maximale de l'image.
        height (int)   : Hauteur maximale de l'image.

    Retourne :
        tuple : Rectangle borné, garanti dans les limites 0 ≤ coords ≤ dimensions.
    """
    left, top, right, bottom = rect
    left = max(0, min(width - 1, int(round(left))))
    top = max(0, min(height - 1, int(round(top))))
    right = max(left + 1, min(width, int(round(right))))      # Au minimum 1 pixel de large
    bottom = max(top + 1, min(height, int(round(bottom))))    # Au minimum 1 pixel de haut
    return (left, top, right, bottom)


def _prepare_for_format(image, extension: str):
    """
    Convertit le mode couleur d'une image selon le format de sortie.

    PROBLÈME : JPEG ne supporte pas la transparence (canal alpha).
    Si on essaie de sauvegarder une image RGBA en JPEG, Pillow lève une erreur.

    SOLUTION : Pour les formats sans transparence (JPEG, BMP), on fusionne
    l'image sur un fond noir. Le fond noir est utilisé pour les zones transparentes.

    Arguments :
        image     : Objet image Pillow.
        extension (str) : Extension du format de sortie (".jpg", ".png"...).

    Retourne :
        Image Pillow convertie (ou l'originale si aucune conversion nécessaire).
    """
    from PIL import Image

    if extension in {".jpg", ".jpeg", ".bmp"}:
        if image.mode in {"RGBA", "LA"}:
            # L'image a une transparence → fusion sur fond noir
            background = Image.new("RGB", image.size, (0, 0, 0))
            # Extraction du canal alpha selon le mode
            alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel(1)
            # Collage de l'image convertie en RGB en utilisant l'alpha comme masque
            background.paste(image.convert("RGB"), mask=alpha)
            return background
        if image.mode not in {"RGB", "L"}:
            # Autre mode non-compatible (ex: P pour palette) → conversion en RGB
            return image.convert("RGB")
    return image   # Pas de conversion nécessaire


def _save_kwargs(extension: str, dpi: float) -> dict:
    """
    Génère les paramètres de sauvegarde Pillow selon le format et le DPI.

    Chaque format image a ses propres paramètres de qualité/compression.
    On utilise des valeurs de qualité élevées (95) pour minimiser les pertes.

    Arguments :
        extension (str)   : Extension du format (".jpg", ".png", ".webp"...).
        dpi       (float) : DPI à inscrire dans les métadonnées du fichier.

    Retourne :
        dict : Dictionnaire de paramètres pour Image.save().
    """
    rounded_dpi = int(round(dpi))
    kwargs: dict = {"dpi": (rounded_dpi, rounded_dpi)}   # DPI horizontal et vertical

    if extension in {".jpg", ".jpeg"}:
        # JPEG : qualité 95/100, subsampling 0 = 4:4:4 (meilleure qualité couleur)
        kwargs.update({"quality": 95, "subsampling": 0})
    elif extension == ".webp":
        # WebP : qualité 95/100 (compression avec pertes légères)
        kwargs.update({"quality": 95})
    # PNG et autres formats sans pertes : pas de paramètre de qualité nécessaire

    return kwargs
