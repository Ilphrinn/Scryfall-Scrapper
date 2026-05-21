from __future__ import annotations

from pathlib import Path

from .dpi_upscaler import _current_dpi, _verify_image


TARGET_ASPECT_RATIO = 0.714


def centered_crop_rect(width: int, height: int, aspect_ratio: float = TARGET_ASPECT_RATIO) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Dimensions d'image invalides.")

    image_ratio = width / height
    if image_ratio > aspect_ratio:
        crop_height = height
        crop_width = round(height * aspect_ratio)
    else:
        crop_width = width
        crop_height = round(width / aspect_ratio)

    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    return (left, top, left + crop_width, top + crop_height)


def crop_image_to_ratio(
    source_file: Path | str,
    output_file: Path | str,
    crop_rect: tuple[int, int, int, int] | None = None,
    aspect_ratio: float = TARGET_ASPECT_RATIO,
) -> Path:
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
        target = target.with_suffix(source.suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.stem}.tmp{target.suffix}")
    if temp_path.exists():
        temp_path.unlink()

    with Image.open(source) as image:
        rect = crop_rect or centered_crop_rect(image.width, image.height, aspect_ratio)
        left, top, right, bottom = _normalize_aspect_rect(rect, image.width, image.height, aspect_ratio)
        cropped = image.crop((left, top, right, bottom))
        cropped = _prepare_for_format(cropped, target.suffix.lower())
        cropped.save(temp_path, **_save_kwargs(target.suffix.lower(), _current_dpi(image)))

    _verify_image(temp_path)
    temp_path.replace(target)
    return target


def _normalize_aspect_rect(
    rect: tuple[int, int, int, int],
    width: int,
    height: int,
    aspect_ratio: float,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = _clamp_rect(rect, width, height)
    crop_width = right - left
    crop_height = bottom - top
    current_ratio = crop_width / crop_height

    if current_ratio > aspect_ratio:
        crop_width = max(1, round(crop_height * aspect_ratio))
    else:
        crop_height = max(1, round(crop_width / aspect_ratio))

    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    left = round(center_x - crop_width / 2)
    top = round(center_y - crop_height / 2)
    left = max(0, min(width - crop_width, left))
    top = max(0, min(height - crop_height, top))
    return (left, top, left + crop_width, top + crop_height)


def _clamp_rect(rect: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    left = max(0, min(width - 1, int(round(left))))
    top = max(0, min(height - 1, int(round(top))))
    right = max(left + 1, min(width, int(round(right))))
    bottom = max(top + 1, min(height, int(round(bottom))))
    return (left, top, right, bottom)


def _prepare_for_format(image, extension: str):
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


def _save_kwargs(extension: str, dpi: float) -> dict:
    rounded_dpi = int(round(dpi))
    kwargs: dict = {"dpi": (rounded_dpi, rounded_dpi)}

    if extension in {".jpg", ".jpeg"}:
        kwargs.update({"quality": 95, "subsampling": 0})
    elif extension == ".webp":
        kwargs.update({"quality": 95})

    return kwargs
