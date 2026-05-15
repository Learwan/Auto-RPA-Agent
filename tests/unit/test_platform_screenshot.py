from io import BytesIO

from PIL import Image

from src.platform.screenshot import mss_screenshot_to_png_bytes, pil_image_to_png_bytes


class _FakeMSSShot:
    size = (1, 1)
    rgb = b"\xff\x00\x00"


def test_pil_image_to_png_bytes_returns_decodable_png():
    image = Image.new("RGB", (2, 1), color=(0, 255, 0))

    png_bytes = pil_image_to_png_bytes(image)

    decoded = Image.open(BytesIO(png_bytes))
    assert decoded.size == (2, 1)
    assert decoded.getpixel((0, 0)) == (0, 255, 0)


def test_mss_screenshot_to_png_bytes_returns_decodable_png():
    png_bytes = mss_screenshot_to_png_bytes(_FakeMSSShot())

    decoded = Image.open(BytesIO(png_bytes))
    assert decoded.size == (1, 1)
    assert decoded.getpixel((0, 0)) == (255, 0, 0)