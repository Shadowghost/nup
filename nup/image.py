"""Icon images for the icon TLVs (SPEC §3, §3.1).

The console ships icons as square JPEGs (256x256 on Switch 1), and the
TLVs carry them the same way.  A JPEG that fits what is left of the
package's 64 KiB TLV block is stored byte for byte; anything else (a PNG,
an oversized JPEG) is converted to a 256x256 JPEG, which needs Pillow
(``pip install nup[images]``).
"""

import io

from .errors import NupError

ICON_SIDE = 256
JPEG_MAGIC = b"\xff\xd8\xff"
QUALITIES = (92, 85, 75, 65, 50, 35)


def is_jpeg(data: bytes) -> bool:
    return data.startswith(JPEG_MAGIC)


def prepare_icon(data: bytes, budget: int, source: str = "icon") -> bytes:
    """``data`` as a JPEG of at most ``budget`` bytes."""
    if budget <= 0:
        raise NupError(f"{source}: no room left in the 64 KiB TLV block for an icon (SPEC §10.3)")
    if is_jpeg(data) and len(data) <= budget:
        return data
    try:
        from PIL import Image
    except ImportError:
        reason = (f"is {len(data):,} bytes, over the {budget:,} left in the 64 KiB TLV block"
                  if is_jpeg(data) else "is not a JPEG")
        raise NupError(
            f"{source} {reason}; install Pillow (pip install nup[images]) to convert it, "
            f"or pass a {ICON_SIDE}x{ICON_SIDE} JPEG"
        ) from None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as error:  # Pillow raises many types for unreadable input
        raise NupError(f"{source} is not an image Pillow can read: {error}") from None
    try:
        square = to_square(image, Image)
        for quality in QUALITIES:
            out = io.BytesIO()
            square.save(out, format="JPEG", quality=quality, optimize=True)
            if out.tell() <= budget:
                return out.getvalue()
    except Exception as error:  # an image Pillow reads but cannot make a JPEG of (a mode it cannot convert)
        raise NupError(f"{source} cannot be converted to a JPEG: {error}") from None
    raise NupError(
        f"{source}: even at JPEG quality {QUALITIES[-1]} the icon exceeds the "
        f"{budget:,} bytes left in the 64 KiB TLV block (SPEC §10.3)"
    )


def to_square(image, Image):
    """Center-crop to a square, scale to the console's icon size, drop alpha."""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    image = image.convert("RGB")
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    return image.resize((ICON_SIDE, ICON_SIDE), Image.LANCZOS)
