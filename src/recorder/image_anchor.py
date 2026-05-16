from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ImageAnchorCapture:
    """Captures small screenshots around click points during recording."""

    def __init__(self, adapter=None, save_dir: str = "data/anchors"):
        self._adapter = adapter
        self._save_dir = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)

    async def capture_anchor(
        self, x: int, y: int, radius: int = 40
    ) -> dict | None:
        """Capture a small region around (x, y) and save to disk.

        Returns metadata dict with path, position, and timestamp,
        or None on failure.
        """
        if self._adapter is None:
            return None

        try:
            screenshot_bytes = await self._adapter.capture_screen()
            if not screenshot_bytes:
                return None

            from io import BytesIO

            from PIL import Image

            image = Image.open(BytesIO(screenshot_bytes))
            img_w, img_h = image.size

            left = max(0, x - radius)
            top = max(0, y - radius)
            right = min(img_w, x + radius)
            bottom = min(img_h, y + radius)

            if right <= left or bottom <= top:
                return None

            crop = image.crop((left, top, right, bottom))

            timestamp = int(time.time() * 1000)
            content_hash = hashlib.md5(crop.tobytes()[:1024]).hexdigest()[:8]
            filename = f"anchor_{timestamp}_{content_hash}.png"
            filepath = self._save_dir / filename

            crop.save(str(filepath), "PNG")

            return {
                "path": str(filepath),
                "x": x,
                "y": y,
                "radius": radius,
                "crop_left": left,
                "crop_top": top,
                "crop_width": right - left,
                "crop_height": bottom - top,
                "timestamp": timestamp,
            }

        except ImportError:
            logger.debug("PIL not available for image anchor capture")
            return None
        except Exception as e:
            logger.debug("Image anchor capture failed: %s", e)
            return None

    @staticmethod
    async def match_anchor(
        screenshot_bytes: bytes,
        anchor_path: str,
        threshold: float = 0.8,
    ) -> tuple[int, int] | None:
        """Find anchor image position in a screenshot using template matching.

        Returns (center_x, center_y) or None if not found.
        """
        try:
            from io import BytesIO

            import numpy as np
            from PIL import Image

            screenshot = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
            anchor = Image.open(anchor_path).convert("RGB")

            screen_arr = np.array(screenshot)
            anchor_arr = np.array(anchor)

            if (
                anchor_arr.shape[0] > screen_arr.shape[0]
                or anchor_arr.shape[1] > screen_arr.shape[1]
            ):
                return None

            try:
                import cv2

                result = cv2.matchTemplate(
                    screen_arr, anchor_arr, cv2.TM_CCOEFF_NORMED
                )
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if max_val >= threshold:
                    h, w = anchor_arr.shape[:2]
                    cx = max_loc[0] + w // 2
                    cy = max_loc[1] + h // 2
                    return (cx, cy)
            except ImportError:
                return _numpy_template_match(screen_arr, anchor_arr, threshold)

        except Exception as e:
            logger.debug("Image anchor matching failed: %s", e)

        return None


def _numpy_template_match(
    source, template, threshold: float
) -> tuple[int, int] | None:
    """Pure-numpy fallback for template matching."""
    import numpy as np

    s_h, s_w = source.shape[:2]
    t_h, t_w = template.shape[:2]
    if t_h > s_h or t_w > s_w:
        return None

    step = max(1, min(t_h, t_w) // 8)
    best_score = -1.0
    best_x, best_y = 0, 0

    src_gray = np.mean(source.astype(np.float32), axis=2)
    tpl_gray = np.mean(template.astype(np.float32), axis=2)
    tpl_std = np.std(tpl_gray)
    if tpl_std < 1e-6:
        return None
    tpl_norm = (tpl_gray - np.mean(tpl_gray)) / tpl_std

    for y in range(0, s_h - t_h + 1, step):
        for x in range(0, s_w - t_w + 1, step):
            patch = src_gray[y : y + t_h, x : x + t_w]
            patch_std = np.std(patch)
            if patch_std < 1e-6:
                continue
            patch_norm = (patch - np.mean(patch)) / patch_std
            score = float(np.sum(patch_norm * tpl_norm) / (t_h * t_w))
            if score > best_score:
                best_score = score
                best_x, best_y = x, y

    if best_score >= threshold:
        return (best_x + t_w // 2, best_y + t_h // 2)
    return None
