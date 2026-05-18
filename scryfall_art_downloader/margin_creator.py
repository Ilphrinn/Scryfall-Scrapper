from __future__ import annotations

from pathlib import Path
from typing import Callable

from .dpi_upscaler import TARGET_CARD_SIZE, _current_dpi, _verify_image, iter_image_files


ProgressCallback = Callable[[str], None]
ProgressCountCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]
VISIBLE_ALPHA_THRESHOLD = 220
TARGET_DPI = 1200.0
TARGET_MARGIN_PIXELS = 144


def create_black_margins(
    source_folder: Path | str,
    output_folder: Path | str | None = None,
    base_margin_pixels: int = 36,
    base_dpi: int = 300,
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

        target_path = target_folder / image_path.name
        temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
        if temp_path.exists():
            temp_path.unlink()
        if on_status:
            on_status(f"Marge harmonisée: {image_path.name}")

        with Image.open(image_path) as image:
            detected_dpi = _current_dpi(image)
            margin = _target_margin_pixels(TARGET_DPI, base_margin_pixels, base_dpi)
            edge_color = _edge_color(image)
            if on_status:
                on_status(
                    f"DPI détecté: {detected_dpi:.0f} - DPI de sortie: {TARGET_DPI:.0f} - "
                    f"marge: {margin}px par côté - "
                    f"couleur de repli: {_hex_color(edge_color)}"
                )

            base = _normalize_card_size(image.convert("RGBA"))
            framed = _create_edge_extended_frame(base, margin, edge_color)
            framed = _prepare_for_format(framed, image_path.suffix.lower())
            save_kwargs = _save_kwargs(image_path.suffix.lower(), TARGET_DPI)
            framed.save(temp_path, **save_kwargs)

        _verify_image(temp_path)
        temp_path.replace(target_path)

        count += 1
        if on_progress:
            on_progress(count, total)

    return count, target_folder


def _create_edge_extended_frame(base, margin: int, fallback_color: tuple[int, int, int]):
    from PIL import Image

    width, height = base.size
    rectangle = _rounded_card_to_rectangle(base, fallback_color, margin)
    framed = Image.new("RGB", (width + margin * 2, height + margin * 2), fallback_color)

    framed.paste(rectangle, (margin, margin))
    top_flap = rectangle.crop((0, 0, width, 1)).resize((width, margin), Image.Resampling.NEAREST)
    bottom_flap = rectangle.crop((0, height - 1, width, height)).resize((width, margin), Image.Resampling.NEAREST)
    left_flap = rectangle.crop((0, 0, 1, height)).resize((margin, height), Image.Resampling.NEAREST)
    right_flap = rectangle.crop((width - 1, 0, width, height)).resize((margin, height), Image.Resampling.NEAREST)

    framed.paste(top_flap, (margin, 0))
    framed.paste(bottom_flap, (margin, margin + height))
    framed.paste(left_flap, (0, margin))
    framed.paste(right_flap, (margin + width, margin))

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
    _polish_outer_margins(framed, margin)
    return framed


def _rounded_card_to_rectangle(base, fallback_color: tuple[int, int, int], margin: int):
    from PIL import Image

    width, height = base.size
    rgb = base.convert("RGB")
    alpha_mask = base.getchannel("A").point(
        lambda value: 255 if value >= VISIBLE_ALPHA_THRESHOLD else 0
    )
    rectangle = Image.new("RGB", (width, height), fallback_color)
    visible_rows: list[int] = []

    for y in range(height):
        row_box = alpha_mask.crop((0, y, width, y + 1)).getbbox()
        if not row_box:
            continue

        left, _, right, _ = row_box
        visible_rows.append(y)

        if left > 0:
            left_color = _stable_row_edge_color(rgb, alpha_mask, y, left, right, margin, from_left=True)
            rectangle.paste(Image.new("RGB", (left, 1), left_color), (0, y))

        rectangle.paste(rgb.crop((left, y, right, y + 1)), (left, y))

        if right < width:
            right_color = _stable_row_edge_color(rgb, alpha_mask, y, left, right, margin, from_left=False)
            rectangle.paste(Image.new("RGB", (width - right, 1), right_color), (right, y))

    _fill_empty_rows(rectangle, visible_rows)
    return rectangle


def _stable_row_edge_color(rgb, alpha_mask, y: int, left: int, right: int, margin: int, from_left: bool):
    center = _inset_edge_x(left, right, margin, from_left)
    samples = [
        rgb.getpixel((x, y))
        for x in _sample_x_positions(left, right, center, margin, from_left)
        if alpha_mask.getpixel((x, y)) == 255
    ]
    if not samples:
        return rgb.getpixel((center, y))

    return tuple(round(sum(pixel[channel] for pixel in samples) / len(samples)) for channel in range(3))


def _sample_x_positions(left: int, right: int, center: int, margin: int, from_left: bool) -> list[int]:
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
    inset = min(max(2, margin + max(1, margin // 16)), max(1, right - left) - 1)
    if from_left:
        return min(right - 1, left + inset)
    return max(left, right - 1 - inset)


def _fill_empty_rows(rectangle, visible_rows: list[int]) -> None:
    width, height = rectangle.size
    if not visible_rows:
        return

    first_y = visible_rows[0]
    last_y = visible_rows[-1]

    top_row = rectangle.crop((0, first_y, width, first_y + 1))
    bottom_row = rectangle.crop((0, last_y, width, last_y + 1))
    for y in range(0, first_y):
        rectangle.paste(top_row, (0, y))
    for y in range(last_y + 1, height):
        rectangle.paste(bottom_row, (0, y))


def _corner_from_horizontal(rectangle, margin: int, top: bool, left: bool):
    from PIL import Image

    source_y = 0 if top else rectangle.height - 1
    if left:
        start_x, end_x = _stable_corner_range(rectangle.width, margin, from_start=True)
    else:
        start_x, end_x = _stable_corner_range(rectangle.width, margin, from_start=False)
    source_box = (start_x, source_y, end_x, source_y + 1)
    return rectangle.crop(source_box).resize((margin, margin), Image.Resampling.NEAREST)


def _corner_from_vertical(rectangle, margin: int, top: bool, left: bool):
    from PIL import Image

    source_x = 0 if left else rectangle.width - 1
    if top:
        start_y, end_y = _stable_corner_range(rectangle.height, margin, from_start=True)
    else:
        start_y, end_y = _stable_corner_range(rectangle.height, margin, from_start=False)
    source_box = (source_x, start_y, source_x + 1, end_y)
    return rectangle.crop(source_box).resize((margin, margin), Image.Resampling.NEAREST)


def _stable_corner_range(length: int, margin: int, from_start: bool) -> tuple[int, int]:
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
    # Step 3: top/bottom source first. Step 4: side source on its triangle.
    corner = horizontal.copy()
    corner.paste(vertical, (0, 0), vertical_mask)
    framed.paste(corner, origin)


def _side_triangle_mask(size: int, corner: str):
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
    if corner == "top_left":
        return y > x
    if corner == "top_right":
        return y > last - x
    if corner == "bottom_left":
        return y < last - x
    if corner == "bottom_right":
        return y < x
    raise ValueError(f"Coin inconnu: {corner}")


def _polish_outer_margins(image, margin: int) -> None:
    if margin < 8:
        return

    _polish_band(image, (0, 0, image.width, margin), horizontal=True)
    _polish_band(image, (0, image.height - margin, image.width, image.height), horizontal=True)
    _polish_band(image, (0, margin, margin, image.height - margin), horizontal=False)
    _polish_band(image, (image.width - margin, margin, image.width, image.height - margin), horizontal=False)


def _polish_band(image, box: tuple[int, int, int, int], horizontal: bool) -> None:
    from PIL import ImageFilter

    band = image.crop(box)
    blurred = band.filter(ImageFilter.MedianFilter(size=3))
    mask = _subtle_artifact_mask(band, blurred, horizontal)
    band.paste(blurred, (0, 0), mask)
    image.paste(band, box[:2])


def _subtle_artifact_mask(band, blurred, horizontal: bool):
    from PIL import Image, ImageChops

    diff = ImageChops.difference(band, blurred).convert("L")
    threshold = 18 if horizontal else 14
    return diff.point(lambda value: 255 if value >= threshold else 0)


def _prepare_for_format(image, extension: str):
    if extension in {".jpg", ".jpeg", ".bmp"} and image.mode not in {"RGB", "L"}:
        return image.convert("RGB")
    return image


def _edge_color(image) -> tuple[int, int, int]:
    sample = image.convert("RGBA")
    bbox = sample.getchannel("A").point(
        lambda value: 255 if value >= VISIBLE_ALPHA_THRESHOLD else 0
    ).getbbox()
    if not bbox:
        return (0, 0, 0)

    left, top, right, bottom = bbox
    candidates = [
        sample.getpixel((left, top))[:3],
        sample.getpixel((right - 1, top))[:3],
        sample.getpixel((left, bottom - 1))[:3],
        sample.getpixel((right - 1, bottom - 1))[:3],
    ]
    return tuple(round(sum(color[channel] for color in candidates) / len(candidates)) for channel in range(3))


def _hex_color(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def _normalize_card_size(image):
    from PIL import Image

    if image.size == TARGET_CARD_SIZE:
        return image
    return image.resize(TARGET_CARD_SIZE, Image.Resampling.LANCZOS)


def _target_margin_pixels(dpi: float, base_margin_pixels: int, base_dpi: int) -> int:
    if int(round(dpi)) == int(TARGET_DPI) and base_margin_pixels == 36 and base_dpi == 300:
        return TARGET_MARGIN_PIXELS
    return _margin_pixels(dpi, base_margin_pixels, base_dpi)


def _margin_pixels(dpi: float, base_margin_pixels: int, base_dpi: int) -> int:
    return max(1, round(base_margin_pixels * dpi / base_dpi))


def _save_kwargs(extension: str, dpi: float) -> dict:
    rounded_dpi = int(round(dpi))
    kwargs: dict = {"dpi": (rounded_dpi, rounded_dpi)}

    if extension in {".jpg", ".jpeg"}:
        kwargs.update({"quality": 95, "subsampling": 0})
    elif extension == ".webp":
        kwargs.update({"quality": 95})

    return kwargs
