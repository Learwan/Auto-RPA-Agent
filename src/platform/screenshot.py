from __future__ import annotations

from io import BytesIO

from PIL import Image


def pil_image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mss_screenshot_to_png_bytes(screenshot) -> bytes:
    image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    return pil_image_to_png_bytes(image)