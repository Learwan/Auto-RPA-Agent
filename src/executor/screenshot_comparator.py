import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ComparisonResult:
    similarity: float
    changed_pixels: int
    total_pixels: int
    change_ratio: float
    changed_regions: list[dict] = field(default_factory=list)
    is_significant: bool = False

    def to_dict(self) -> dict:
        return {
            "similarity": round(self.similarity, 4),
            "changed_pixels": self.changed_pixels,
            "total_pixels": self.total_pixels,
            "change_ratio": round(self.change_ratio, 4),
            "changed_regions": self.changed_regions[:5],
            "is_significant": self.is_significant,
        }


class ScreenshotComparator:
    def __init__(
        self,
        similarity_threshold: float = 0.95,
        change_ratio_threshold: float = 0.01,
        region_grid_size: int = 16,
    ):
        self._similarity_threshold = similarity_threshold
        self._change_ratio_threshold = change_ratio_threshold
        self._region_grid_size = region_grid_size

    def compare(self, before: bytes, after: bytes, width: int, height: int) -> ComparisonResult:
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            logger.debug("PIL/numpy not available for screenshot comparison")
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        try:
            img_before = Image.frombytes("RGB", (width, height), before)
            img_after = Image.frombytes("RGB", (width, height), after)
        except Exception as e:
            logger.debug(f"Failed to decode screenshots: {e}")
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        arr_before = np.array(img_before, dtype=np.float32)
        arr_after = np.array(img_after, dtype=np.float32)

        if arr_before.shape != arr_after.shape:
            min_h = min(arr_before.shape[0], arr_after.shape[0])
            min_w = min(arr_before.shape[1], arr_after.shape[1])
            arr_before = arr_before[:min_h, :min_w]
            arr_after = arr_after[:min_h, :min_w]

        diff = np.abs(arr_before - arr_after)
        pixel_diff = np.max(diff, axis=2)
        changed_mask = pixel_diff > 30
        changed_pixels = int(np.sum(changed_mask))
        total_pixels = int(changed_mask.size)
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        mse = float(np.mean(diff**2))
        max_mse = 255.0**2
        similarity = 1.0 - (mse / max_mse) if max_mse > 0 else 1.0

        changed_regions = self._detect_regions(changed_mask, width, height)

        is_significant = similarity < self._similarity_threshold or change_ratio > self._change_ratio_threshold

        return ComparisonResult(
            similarity=similarity,
            changed_pixels=changed_pixels,
            total_pixels=total_pixels,
            change_ratio=change_ratio,
            changed_regions=changed_regions,
            is_significant=is_significant,
        )

    def _detect_regions(self, changed_mask: "numpy.ndarray", width: int, height: int) -> list[dict]:  # noqa: F821
        import numpy as np

        regions: list[dict] = []
        grid_h = max(1, height // self._region_grid_size)
        grid_w = max(1, width // self._region_grid_size)

        for gy in range(self._region_grid_size):
            for gx in range(self._region_grid_size):
                y_start = gy * grid_h
                y_end = min((gy + 1) * grid_h, height)
                x_start = gx * grid_w
                x_end = min((gx + 1) * grid_w, width)

                if y_end <= y_start or x_end <= x_start:
                    continue

                region = changed_mask[y_start:y_end, x_start:x_end]
                region_changed = int(np.sum(region))
                region_total = int(region.size)

                if region_total > 0 and region_changed / region_total > 0.1:
                    regions.append(
                        {
                            "x": x_start,
                            "y": y_start,
                            "width": x_end - x_start,
                            "height": y_end - y_start,
                            "change_ratio": round(region_changed / region_total, 3),
                        }
                    )

        regions.sort(key=lambda r: r["change_ratio"], reverse=True)
        return regions
