# =============================================================================
#  NORMALISEUR DE DPI — dpi_upscaler.py
# =============================================================================
# Ce module normalise les images à 1200 DPI pour l'impression de cartes proxy.
#
# QU'EST-CE QUE LE DPI ?
# DPI = Dots Per Inch (points par pouce). C'est la résolution d'impression.
# Plus le DPI est élevé, plus l'image est nette à l'impression.
# Pour une carte Magic standard à 63×88 mm :
#   - 300 DPI  : 745 × 1040 pixels  (qualité web, acceptable)
#   - 800 DPI  : 1985 × 2771 pixels (bonne qualité)
#   - 1200 DPI : 2978 × 4157 pixels (qualité impression professionnelle)  ← notre cible
#
# CE QUE FAIT CE MODULE :
# 1. Détecte le DPI actuel de chaque image (lecture des métadonnées)
# 2. Redimensionne l'image à la taille cible en pixels (3193 × 4457 @ 1200 DPI)
# 3. Inscrit "1200 DPI" dans les métadonnées du fichier de sortie
#
# NOTE : On utilise 3193 × 4457 pixels (légèrement plus grand que le strict 1200 DPI)
# pour avoir une petite marge lors de la découpe physique des cartes.
# =============================================================================

from __future__ import annotations

from pathlib import Path         # Pour manipuler les chemins de fichiers
from typing import Callable      # Pour typer les fonctions de rappel


# ---------------------------------------------------------------------------
#  Types de callbacks
# ---------------------------------------------------------------------------
ProgressCallback = Callable[[str], None]          # Fonction appelée avec un message texte
ProgressCountCallback = Callable[[int, int], None] # Fonction appelée avec (traités, total)
CancelCallback = Callable[[], bool]               # Fonction retournant True si annulé


# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
# Formats d'image supportés. On ignore les autres fichiers (ex: .txt, .xml).

DEFAULT_SOURCE_DPI = 280.0
# DPI supposé si l'image n'a pas de métadonnées DPI.
# 280 DPI est une valeur typique des images Scryfall "large".

TARGET_DPI = 1200
# DPI cible pour toutes les images de sortie.
# 1200 DPI est la résolution standard pour l'impression professionnelle de cartes.

TARGET_CARD_SIZE = (3193, 4457)
# Taille en pixels d'une carte Magic à 1200 DPI avec légère marge de découpe.
# Calculé pour 63 mm × 88 mm + marges : (63/25.4) × 1200 = 2976 ≈ 3193 avec marge.

MAX_OUTPUT_PIXELS = 120_000_000
# Limite de sécurité : 120 millions de pixels maximum.
# Une image 1200 DPI = ~14 M pixels (ok). Cette limite protège contre les crash
# mémoire si quelqu'un fournit une image anormalement grande à très haute résolution.


# ---------------------------------------------------------------------------
#  Fonctions principales
# ---------------------------------------------------------------------------

def iter_image_files(folder: Path) -> list[Path]:
    """
    Liste tous les fichiers images dans un dossier (non récursif).

    Filtre uniquement les formats supportés et retourne la liste triée.

    Arguments :
        folder (Path) : Dossier à parcourir.

    Retourne :
        list[Path] : Liste des fichiers images trouvés, triée alphabétiquement.
    """
    return sorted(
        path
        for path in folder.iterdir()   # Parcours du contenu du dossier (non récursif)
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def upscale_folder_dpi(
    source_folder: Path | str,
    output_folder: Path | str | None = None,
    minimum_dpi: int = 1200,
    on_status: ProgressCallback | None = None,
    on_progress: ProgressCountCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> tuple[int, Path]:
    """
    Normalise toutes les images d'un dossier à un DPI minimum.

    Pour chaque image du dossier source :
    1. Ouvre l'image et détecte son DPI actuel
    2. Redimensionne l'image à la taille cible (3193 × 4457 pixels)
    3. Inscrit le DPI cible dans les métadonnées
    4. Sauvegarde dans le dossier de sortie

    La sauvegarde est toujours faite (même si l'image est déjà à bonne taille)
    pour s'assurer que les métadonnées DPI sont correctement inscrites.

    Arguments :
        source_folder (Path|str)      : Dossier contenant les images à traiter.
        output_folder (Path|str|None) : Dossier de sortie. Si None, crée "DPI_Upscale/" dans la source.
        minimum_dpi   (int)           : DPI cible (défaut: 1200).
        on_status     (Callable|None) : Callback pour les messages de log.
        on_progress   (Callable|None) : Callback de progression (traités, total).
        should_cancel (Callable|None) : Callback d'annulation.

    Retourne :
        tuple (int, Path) : (nombre d'images traitées, chemin du dossier de sortie).

    Lève :
        RuntimeError : Si Pillow n'est pas installé ou si une image traitée est invalide.
        ValueError   : Si le dossier source est invalide.
    """
    # Vérification de la disponibilité de Pillow (bibliothèque de traitement d'images)
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

    # Dossier de sortie : spécifié ou automatiquement "DPI_Upscale/" dans la source
    target_folder = Path(output_folder) if output_folder else source / "DPI_Upscale"
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

        target_path = _target_path(image_path, target_folder, minimum_dpi)

        # Fichier temporaire pour la sauvegarde (sécurité anti-corruption)
        temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
        if temp_path.exists():
            temp_path.unlink()

        with Image.open(image_path) as image:
            current_dpi = _current_dpi(image)
            if on_status:
                on_status(f"DPI détecté: {current_dpi:.0f} - {image_path.name}")

            result = image.copy()    # Copie pour ne pas modifier l'original en mémoire
            target_dpi = float(minimum_dpi)
            new_size = _target_card_size(minimum_dpi)
            _validate_output_size(new_size)   # Vérification anti-overflow mémoire

            if result.size != new_size:
                # Redimensionnement nécessaire → on utilise LANCZOS (meilleure qualité)
                if on_status:
                    on_status(
                        f"Normalisation: {result.width}x{result.height} -> "
                        f"{new_size[0]}x{new_size[1]} ({target_dpi:.0f} DPI)"
                    )
                result = result.resize(new_size, Image.Resampling.LANCZOS)
                # LANCZOS = algorithme de redimensionnement de haute qualité
                # (préserve les détails fins, évite les aliasings/marches d'escalier)
            elif on_status:
                on_status(f"Taille déjà normalisée: {result.width}x{result.height}")

            # Conversion du mode couleur si nécessaire (ex: RGBA → RGB pour JPEG)
            extension = image_path.suffix.lower()
            result = _prepare_image_for_format(result, extension)

            # Sauvegarde avec les paramètres de qualité et le DPI cible
            save_kwargs = _save_kwargs(extension, target_dpi)
            if on_status:
                on_status(f"Ecriture: {result.width}x{result.height} - {target_dpi:.0f} DPI")
            result.save(temp_path, **save_kwargs)

        # Vérification que l'image sauvegardée est valide
        _verify_image(temp_path)

        # Vérification du DPI effectivement inscrit dans le fichier
        written_dpi = _read_file_dpi(temp_path)

        # Remplacement atomique : le temporaire devient le fichier final
        temp_path.replace(target_path)

        if on_status:
            on_status(f"DPI écrit: {written_dpi:.0f} - {target_path.name}")

        count += 1
        if on_progress:
            on_progress(count, total)

    return count, target_folder


# ---------------------------------------------------------------------------
#  Fonctions utilitaires (internes)
# ---------------------------------------------------------------------------

def _target_path(source_path: Path, target_folder: Path, dpi: int) -> Path:
    """
    Génère le chemin du fichier de sortie en ajoutant le DPI au nom.

    Exemple : "Forest.jpg" → "Forest_1200DPI.jpg"

    Arguments :
        source_path   (Path) : Chemin du fichier source.
        target_folder (Path) : Dossier de destination.
        dpi           (int)  : DPI à inclure dans le nom.

    Retourne :
        Path : Chemin du fichier de sortie.
    """
    return target_folder / f"{source_path.stem}_{dpi}DPI{source_path.suffix}"


def _target_card_size(dpi: int) -> tuple[int, int]:
    """
    Calcule la taille en pixels d'une carte Magic pour un DPI donné.

    Utilise TARGET_CARD_SIZE comme référence à TARGET_DPI (1200) et
    scale proportionnellement pour les autres DPI.

    Exemple pour 1200 DPI : (3193, 4457) (valeur de référence)
    Exemple pour 600 DPI  : (3193/2, 4457/2) = (1597, 2229)

    Arguments :
        dpi (int) : DPI souhaité.

    Retourne :
        tuple (int, int) : (largeur, hauteur) en pixels.
    """
    scale = float(dpi) / TARGET_DPI   # Facteur d'échelle par rapport à la référence
    return (
        max(1, round(TARGET_CARD_SIZE[0] * scale)),
        max(1, round(TARGET_CARD_SIZE[1] * scale)),
    )


def _save_kwargs(extension: str, target_dpi: float) -> dict:
    """
    Génère les paramètres de sauvegarde Pillow pour le format et le DPI donnés.

    Arguments :
        extension  (str)   : Extension du format (".jpg", ".png", ".webp"...).
        target_dpi (float) : DPI à inscrire dans les métadonnées.

    Retourne :
        dict : Paramètres pour Image.save().
    """
    dpi = int(round(target_dpi))
    kwargs: dict = {"dpi": (dpi, dpi)}   # DPI horizontal et vertical

    if extension in {".jpg", ".jpeg"}:
        kwargs.update({"quality": 95, "subsampling": 0})
        # quality=95 : 95% de la qualité maximale (très bon compromis taille/qualité)
        # subsampling=0 : chroma 4:4:4 (meilleure fidélité des couleurs)
    elif extension == ".webp":
        kwargs.update({"quality": 95})

    return kwargs


def _prepare_image_for_format(image, extension: str):
    """
    Convertit le mode couleur d'une image si le format de sortie ne le supporte pas.

    JPEG ne supporte pas la transparence (canal alpha).
    On fusionne donc les images avec transparence sur fond noir.

    Arguments :
        image     : Objet image Pillow.
        extension (str) : Extension du format de sortie.

    Retourne :
        Image Pillow (convertie ou originale).
    """
    from PIL import Image

    if extension in {".jpg", ".jpeg", ".bmp"}:
        if image.mode in {"RGBA", "LA"}:
            # Image avec transparence → fusion sur fond noir
            background = Image.new("RGB", image.size, (0, 0, 0))
            alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel(1)
            background.paste(image.convert("RGB"), mask=alpha)
            return background
        if image.mode not in {"RGB", "L"}:
            return image.convert("RGB")
    return image


def _validate_output_size(size: tuple[int, int]) -> None:
    """
    Vérifie que la taille de sortie ne dépasse pas la limite de sécurité.

    Une image trop grande consommerait toute la RAM et ferait crasher le programme.
    Cette limite (120 millions de pixels) est largement supérieure aux images
    normales à 1200 DPI et protège contre des entrées anormales.

    Arguments :
        size (tuple[int, int]) : (largeur, hauteur) en pixels.

    Lève :
        RuntimeError : Si le nombre de pixels dépasse MAX_OUTPUT_PIXELS.
    """
    pixels = size[0] * size[1]
    if pixels > MAX_OUTPUT_PIXELS:
        raise RuntimeError(
            f"Image trop grande après upscale: {size[0]}x{size[1]} "
            f"({pixels:,} pixels)."
        )


def _verify_image(path: Path) -> None:
    """
    Vérifie qu'un fichier image est valide et non corrompu.

    Pillow peut parfois écrire un fichier partiellement valide.
    verify() lit le fichier sans le décoder complètement et lève une exception
    si la structure est invalide.

    Si le fichier est invalide, on le supprime pour éviter de laisser un
    fichier corrompu sur le disque.

    Arguments :
        path (Path) : Chemin du fichier à vérifier.

    Lève :
        RuntimeError : Si le fichier est invalide ou corrompu.
    """
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.verify()   # Vérification de la structure du fichier
    except Exception as error:
        try:
            path.unlink()   # Suppression du fichier corrompu
        except OSError:
            pass
        raise RuntimeError(f"Le fichier généré est invalide: {path.name}") from error


def _read_file_dpi(path: Path) -> float:
    """
    Lit le DPI effectivement inscrit dans un fichier image sauvegardé.

    Utilisé après la sauvegarde pour vérifier que le DPI a bien été inscrit.

    Arguments :
        path (Path) : Chemin du fichier image.

    Retourne :
        float : DPI lu dans les métadonnées, ou DEFAULT_SOURCE_DPI si absent.
    """
    from PIL import Image
    with Image.open(path) as image:
        return _current_dpi(image)


def _current_dpi(image) -> float:
    """
    Détecte le DPI d'une image Pillow ouverte.

    Essaie plusieurs méthodes dans l'ordre :
    1. Métadonnées "dpi" de Pillow (info["dpi"])
    2. Métadonnées JFIF (spécifiques aux JPEG)
    3. Métadonnées EXIF (standard photo)
    4. Valeur par défaut (DEFAULT_SOURCE_DPI = 280 DPI)

    Arguments :
        image : Objet image Pillow.

    Retourne :
        float : DPI détecté ou valeur par défaut si non détectable.
    """
    # Méthode 1 : info["dpi"] de Pillow (le plus direct)
    dpi = image.info.get("dpi")
    detected = _normalize_dpi(dpi)
    if detected:
        return detected

    # Méthode 2 : Métadonnées JFIF (format JPEG ancien)
    jfif_dpi = _jfif_dpi(image)
    if jfif_dpi:
        return jfif_dpi

    # Méthode 3 : Métadonnées EXIF (standard pour les photos numériques)
    exif_dpi = _exif_dpi(image)
    if exif_dpi:
        return exif_dpi

    # Méthode 4 : Valeur par défaut si aucune métadonnée DPI trouvée
    return DEFAULT_SOURCE_DPI


def _normalize_dpi(value) -> float | None:
    """
    Normalise une valeur DPI potentiellement en tuple, entier, ou float.

    Pillow peut retourner le DPI sous différentes formes :
    - tuple (x_dpi, y_dpi) : on prend le minimum
    - entier ou flottant : on l'utilise directement

    Arguments :
        value : Valeur DPI brute (peut être None, int, float, ou tuple).

    Retourne :
        float : DPI normalisé (≥ 1.0), ou None si la valeur est invalide.
    """
    if isinstance(value, tuple) and value:
        # Tuple (x, y) → on prend le minimum des valeurs non-nulles
        values = [float(item) for item in value[:2] if item]
        if values:
            return max(1.0, min(values))
    if isinstance(value, (int, float)) and value:
        return max(1.0, float(value))
    return None


def _jfif_dpi(image) -> float | None:
    """
    Extrait le DPI depuis les métadonnées JFIF d'une image JPEG.

    JFIF (JPEG File Interchange Format) stocke la densité de pixels dans deux
    champs : jfif_unit et jfif_density.

    Unités JFIF :
        0 = pas d'unité (densité relative, pas de DPI réel)
        1 = dots per inch (DPI) → on utilise directement
        2 = dots per centimeter → on convertit en DPI (× 2.54)

    Arguments :
        image : Objet image Pillow.

    Retourne :
        float : DPI si disponible et valide, None sinon.
    """
    unit = image.info.get("jfif_unit")
    density = image.info.get("jfif_density")
    if not density:
        return None

    detected = _normalize_dpi(density)
    if not detected:
        return None

    if unit == 1:
        return detected            # Dots/inch → déjà en DPI
    if unit == 2:
        return detected * 2.54    # Dots/cm → conversion en DPI (1 pouce = 2.54 cm)
    return None   # Unit 0 = densité relative, pas utilisable


def _exif_dpi(image) -> float | None:
    """
    Extrait le DPI depuis les métadonnées EXIF d'une image.

    EXIF (Exchangeable Image File Format) est le standard utilisé par les appareils
    photo numériques pour stocker les métadonnées.

    Tags EXIF utilisés :
        282 = XResolution (résolution horizontale)
        283 = YResolution (résolution verticale)
        296 = ResolutionUnit (unité : 2=inch, 3=cm)

    Arguments :
        image : Objet image Pillow.

    Retourne :
        float : DPI si disponible et valide, None sinon.
    """
    try:
        exif = image.getexif()
    except Exception:
        return None
    if not exif:
        return None

    resolution_unit = exif.get(296)   # Tag EXIF pour l'unité de résolution
    x_resolution = _rational_to_float(exif.get(282))   # Tag EXIF XResolution
    y_resolution = _rational_to_float(exif.get(283))   # Tag EXIF YResolution

    values = [value for value in (x_resolution, y_resolution) if value]
    if not values:
        return None

    detected = max(1.0, min(values))   # On prend le minimum des résolutions X et Y

    if resolution_unit == 2:
        return detected            # Inch → déjà en DPI
    if resolution_unit == 3:
        return detected * 2.54    # Centimètre → conversion en DPI
    return detected   # Unité inconnue → on suppose DPI


def _rational_to_float(value) -> float | None:
    """
    Convertit une valeur "rationnelle" EXIF en flottant.

    Les valeurs EXIF peuvent être stockées de plusieurs façons :
    - float ou int : Conversion directe
    - Fraction (objet avec numerator/denominator) : Division
    - Tuple (numerateur, denominateur) : Division

    Arguments :
        value : Valeur EXIF à convertir.

    Retourne :
        float : Valeur convertie, ou None si la conversion échoue.
    """
    if value is None:
        return None

    # Tentative de conversion directe (entier ou flottant)
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    # Tentative via les attributs numerator/denominator (objet Fraction Pillow)
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if numerator is not None and denominator:
        return float(numerator) / float(denominator)

    # Tentative via tuple (numerateur, denominateur)
    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return float(value[0]) / float(value[1])

    return None
