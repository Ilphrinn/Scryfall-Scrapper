from __future__ import annotations

from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[str], None]
ProgressCountCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
DEFAULT_SOURCE_DPI = 280.0
TARGET_DPI = 1200
TARGET_CARD_SIZE = (3193, 4457)
MAX_OUTPUT_PIXELS = 120_000_000


def iter_image_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
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
        temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
        if temp_path.exists():
            temp_path.unlink()

        with Image.open(image_path) as image:
            current_dpi = _current_dpi(image)
            if on_status:
                on_status(f"DPI détecté: {current_dpi:.0f} - {image_path.name}")
            result = image.copy()
            target_dpi = float(minimum_dpi)
            new_size = _target_card_size(minimum_dpi)
            _validate_output_size(new_size)
            if result.size != new_size:
                if on_status:
                    on_status(
                        f"Normalisation: {result.width}x{result.height} -> "
                        f"{new_size[0]}x{new_size[1]} ({target_dpi:.0f} DPI)"
                    )
                result = result.resize(new_size, Image.Resampling.LANCZOS)
            elif on_status:
                on_status(f"Taille déjà normalisée: {result.width}x{result.height}")

            extension = image_path.suffix.lower()
            result = _prepare_image_for_format(result, extension)
            save_kwargs = _save_kwargs(extension, target_dpi)
            if on_status:
                on_status(
                    f"Ecriture: {result.width}x{result.height} - "
                    f"{target_dpi:.0f} DPI"
                )
            result.save(temp_path, **save_kwargs)

        _verify_image(temp_path)
        written_dpi = _read_file_dpi(temp_path)
        temp_path.replace(target_path)
        if on_status:
            on_status(f"DPI écrit: {written_dpi:.0f} - {target_path.name}")

        count += 1
        if on_progress:
            on_progress(count, total)

    return count, target_folder


def _target_path(source_path: Path, target_folder: Path, dpi: int) -> Path:
    return target_folder / f"{source_path.stem}_{dpi}DPI{source_path.suffix}"


def _target_card_size(dpi: int) -> tuple[int, int]:
    scale = float(dpi) / TARGET_DPI
    return (
        max(1, round(TARGET_CARD_SIZE[0] * scale)),
        max(1, round(TARGET_CARD_SIZE[1] * scale)),
    )


def _save_kwargs(extension: str, target_dpi: float) -> dict:
    dpi = int(round(target_dpi))
    kwargs: dict = {"dpi": (dpi, dpi)}

    if extension in {".jpg", ".jpeg"}:
        kwargs.update({"quality": 95, "subsampling": 0})
    elif extension == ".webp":
        kwargs.update({"quality": 95})

    return kwargs


def _prepare_image_for_format(image, extension: str):
    from PIL import Image

    if extension in {".jpg", ".jpeg", ".bmp"}:
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (0, 0, 0))
            alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel(1)
            background.paste(image.convert("RGB"), mask=alpha)
            return background
        if image.mode not in {"RGB", "L"}:
            return image.convert("RGB")
    return image


def _validate_output_size(size: tuple[int, int]) -> None:
    pixels = size[0] * size[1]
    if pixels > MAX_OUTPUT_PIXELS:
        raise RuntimeError(
            f"Image trop grande après upscale: {size[0]}x{size[1]} "
            f"({pixels:,} pixels)."
        )


def _verify_image(path: Path) -> None:
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as error:
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError(f"Le fichier généré est invalide: {path.name}") from error


def _read_file_dpi(path: Path) -> float:
    from PIL import Image

    with Image.open(path) as image:
        return _current_dpi(image)


def _current_dpi(image) -> float:
    dpi = image.info.get("dpi")
    detected = _normalize_dpi(dpi)
    if detected:
        return detected

    jfif_dpi = _jfif_dpi(image)
    if jfif_dpi:
        return jfif_dpi

    exif_dpi = _exif_dpi(image)
    if exif_dpi:
        return exif_dpi

    return DEFAULT_SOURCE_DPI


def _normalize_dpi(value) -> float | None:
    if isinstance(value, tuple) and value:
        values = [float(item) for item in value[:2] if item]
        if values:
            return max(1.0, min(values))
    if isinstance(value, (int, float)) and value:
        return max(1.0, float(value))
    return None


def _jfif_dpi(image) -> float | None:
    unit = image.info.get("jfif_unit")
    density = image.info.get("jfif_density")
    if not density:
        return None

    detected = _normalize_dpi(density)
    if not detected:
        return None

    # JFIF unit: 1 = dots/inch, 2 = dots/cm.
    if unit == 1:
        return detected
    if unit == 2:
        return detected * 2.54
    return None


def _exif_dpi(image) -> float | None:
    try:
        exif = image.getexif()
    except Exception:
        return None
    if not exif:
        return None

    resolution_unit = exif.get(296)
    x_resolution = _rational_to_float(exif.get(282))
    y_resolution = _rational_to_float(exif.get(283))
    values = [value for value in (x_resolution, y_resolution) if value]
    if not values:
        return None

    detected = max(1.0, min(values))
    # EXIF ResolutionUnit: 2 = inch, 3 = centimeter.
    if resolution_unit == 2:
        return detected
    if resolution_unit == 3:
        return detected * 2.54
    return detected


def _rational_to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if numerator is not None and denominator:
        return float(numerator) / float(denominator)

    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return float(value[0]) / float(value[1])

    return None
