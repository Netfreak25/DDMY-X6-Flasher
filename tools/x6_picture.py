"""Bounded static image import and reproducible crop geometry for native editing."""

from __future__ import annotations

import io
import math
import warnings
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from x6_artwork import catalog


def import_picture(path: Path) -> Image.Image:
    """Decode upright sRGB, reject animation, and composite alpha over black."""
    config = catalog()
    if path.stat().st_size > config["max_input_bytes"]:
        raise ValueError("Das Bild darf höchstens 25 MiB groß sein.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in ("PNG", "JPEG", "WEBP"):
                    raise ValueError("Bitte ein statisches PNG-, JPEG- oder WebP-Bild wählen.")
                if source.width * source.height > config["max_input_pixels"]:
                    raise ValueError("Das Bild darf höchstens 40 Megapixel enthalten.")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Animierte Bilder werden nicht unterstützt.")
                source.load()
                upright = ImageOps.exif_transpose(source)
                alpha = upright.convert("RGBA").getchannel("A")
                icc = source.info.get("icc_profile")
                if icc:
                    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    color = upright if upright.mode in ("RGB", "CMYK", "LAB") else upright.convert("RGB")
                    rgb = ImageCms.profileToProfile(color, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
                else:
                    rgb = upright.convert("RGB")
                return Image.composite(rgb, Image.new("RGB", rgb.size, "black"), alpha)
    except (UnidentifiedImageError, OSError, ImageCms.PyCMSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError(f"Das Bild konnte nicht sicher gelesen werden: {error}") from error


def geometry(width: int, height: int, *, mode: str = "cover", zoom: float = 1,
             pan_x: float = 0, pan_y: float = 0) -> tuple[float, float, float, float]:
    """Shared geometry: normalized pan moves between the two available edges."""
    config = catalog()
    if mode not in ("cover", "contain") or not all(math.isfinite(value) for value in (zoom, pan_x, pan_y)):
        raise ValueError("Ungültiger Bildausschnitt.")
    if not 1 <= zoom <= config["max_zoom"] or not -1 <= pan_x <= 1 or not -1 <= pan_y <= 1 or min(width, height) < 1:
        raise ValueError("Bildausschnitt außerhalb des erlaubten Bereichs.")
    target_w, target_h = config["width"], config["height"]
    scale = (max if mode == "cover" else min)(target_w / width, target_h / height) * zoom
    drawn_w, drawn_h = width * scale, height * scale
    return ((target_w - drawn_w) / 2 + pan_x * abs(target_w - drawn_w) / 2,
            (target_h - drawn_h) / 2 + pan_y * abs(target_h - drawn_h) / 2, drawn_w, drawn_h)


def render(source: Image.Image, *, rotation: int = 0, mode: str = "cover", zoom: float = 1,
           pan_x: float = 0, pan_y: float = 0) -> Image.Image:
    """Rotate clockwise and resample the selected view into the exact device size."""
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Nur Drehungen in 90°-Schritten werden unterstützt.")
    rotated = source.rotate(-rotation, expand=True)
    x, y, width, height = geometry(*rotated.size, mode=mode, zoom=zoom, pan_x=pan_x, pan_y=pan_y)
    config = catalog()
    scale_x, scale_y = rotated.width / width, rotated.height / height
    return rotated.transform((config["width"], config["height"]), Image.Transform.AFFINE,
                             (scale_x, 0, -x * scale_x, 0, scale_y, -y * scale_y),
                             resample=Image.Resampling.BICUBIC, fillcolor="black").convert("RGB")
