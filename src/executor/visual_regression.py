from __future__ import annotations

import logging
import math

from src.executor.screenshot_comparator import ComparisonResult

logger = logging.getLogger(__name__)


class SSIMComparator:
    def __init__(self, window_size: int = 7):
        self._window_size = window_size

    def compare(
        self, before: bytes, after: bytes, width: int, height: int
    ) -> ComparisonResult:
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        try:
            img_before = np.array(
                Image.frombytes("RGB", (width, height), before), dtype=np.float64
            )
            img_after = np.array(
                Image.frombytes("RGB", (width, height), after), dtype=np.float64
            )
        except Exception:
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        if img_before.shape != img_after.shape:
            min_h = min(img_before.shape[0], img_after.shape[0])
            min_w = min(img_before.shape[1], img_after.shape[1])
            img_before = img_before[:min_h, :min_w]
            img_after = img_after[:min_h, :min_w]

        gray_before = np.mean(img_before, axis=2)
        gray_after = np.mean(img_after, axis=2)

        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2

        mu1 = self._uniform_filter(gray_before, self._window_size)
        mu2 = self._uniform_filter(gray_after, self._window_size)

        mu1_sq = mu1**2
        mu2_sq = mu2**2
        mu1_mu2 = mu1 * mu2

        sigma1_sq = self._uniform_filter(
            gray_before**2, self._window_size
        ) - mu1_sq
        sigma2_sq = self._uniform_filter(
            gray_after**2, self._window_size
        ) - mu2_sq
        sigma12 = self._uniform_filter(
            gray_before * gray_after, self._window_size
        ) - mu1_mu2

        numerator = (2 * mu1_mu2 + c1) * (2 * sigma12 + c2)
        denominator = (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)

        ssim_map = numerator / np.maximum(denominator, 1e-10)
        ssim_value = float(np.mean(ssim_map))

        diff_map = 1.0 - ssim_map
        changed_mask = diff_map > 0.1
        changed_pixels = int(np.sum(changed_mask))
        total_pixels = int(changed_mask.size)
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        return ComparisonResult(
            similarity=ssim_value,
            changed_pixels=changed_pixels,
            total_pixels=total_pixels,
            change_ratio=change_ratio,
            is_significant=ssim_value < 0.95,
        )

    @staticmethod
    def _uniform_filter(arr: numpy.ndarray, size: int) -> numpy.ndarray:  # noqa: F821
        import numpy as np

        kernel = np.ones((size, size), dtype=np.float64) / (size * size)

        h, w = arr.shape
        pad = size // 2
        padded = np.pad(arr, pad, mode="reflect")
        result = np.zeros_like(arr, dtype=np.float64)
        for i in range(h):
            for j in range(w):
                result[i, j] = np.sum(
                    padded[i : i + size, j : j + size] * kernel
                )
        return result


class PerceptualHashComparator:
    def __init__(self, hash_size: int = 16):
        self._hash_size = hash_size

    def dhash(self, image_bytes: bytes, width: int, height: int) -> int:
        try:
            from PIL import Image
        except ImportError:
            return 0
        try:
            img = Image.frombytes("RGB", (width, height), image_bytes)
            img = img.convert("L").resize(
                (self._hash_size + 1, self._hash_size), Image.LANCZOS
            )
            pixels = list(img.getdata())
            hash_value = 0
            for row in range(self._hash_size):
                for col in range(self._hash_size):
                    offset = row * (self._hash_size + 1) + col
                    if pixels[offset] < pixels[offset + 1]:
                        hash_value |= 1 << (row * self._hash_size + col)
            return hash_value
        except Exception:
            return 0

    def phash(self, image_bytes: bytes, width: int, height: int) -> int:
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            return 0
        try:
            img = Image.frombytes("RGB", (width, height), image_bytes)
            img = img.convert("L").resize((32, 32), Image.LANCZOS)
            pixels = np.array(img, dtype=np.float64)
            dct_result = self._dct2d(pixels)
            dct_low = dct_result[: self._hash_size, : self._hash_size]
            median = np.median(dct_low)
            hash_value = 0
            for i in range(self._hash_size):
                for j in range(self._hash_size):
                    if dct_low[i, j] > median:
                        hash_value |= 1 << (i * self._hash_size + j)
            return hash_value
        except Exception:
            return 0

    @staticmethod
    def hamming_distance(hash1: int, hash2: int) -> int:
        return bin(hash1 ^ hash2).count("1")

    def compare(
        self, before: bytes, after: bytes, width: int, height: int
    ) -> ComparisonResult:
        max_bits = self._hash_size * self._hash_size

        dhash1 = self.dhash(before, width, height)
        dhash2 = self.dhash(after, width, height)
        dhash_dist = self.hamming_distance(dhash1, dhash2)

        if dhash_dist / max_bits < 0.1:
            return ComparisonResult(
                similarity=1.0 - dhash_dist / max_bits,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        phash1 = self.phash(before, width, height)
        phash2 = self.phash(after, width, height)
        phash_dist = self.hamming_distance(phash1, phash2)
        similarity = 1.0 - phash_dist / max_bits

        return ComparisonResult(
            similarity=similarity,
            changed_pixels=phash_dist,
            total_pixels=max_bits,
            change_ratio=phash_dist / max_bits,
            is_significant=similarity < 0.90,
        )

    @staticmethod
    def _dct2d(matrix: numpy.ndarray) -> numpy.ndarray:  # noqa: F821
        import numpy as np

        n_rows = matrix.shape[0]
        n_cols = matrix.shape[1]
        result = np.zeros_like(matrix, dtype=np.float64)
        for u in range(n_rows):
            for v in range(n_cols):
                cu = math.sqrt(1.0 / n_rows) if u == 0 else math.sqrt(2.0 / n_rows)
                cv = math.sqrt(1.0 / n_cols) if v == 0 else math.sqrt(2.0 / n_cols)
                s = 0.0
                for i in range(n_rows):
                    for j in range(n_cols):
                        s += matrix[i, j] * math.cos(
                            math.pi * (2 * i + 1) * u / (2 * n_rows)
                        ) * math.cos(
                            math.pi * (2 * j + 1) * v / (2 * n_cols)
                        )
                result[u, v] = cu * cv * s
        return result


class AATolerantComparator:
    def __init__(self, threshold: float = 0.1, aa_tolerance: float = 0.03):
        self._threshold = threshold
        self._aa_tolerance = aa_tolerance

    def compare(
        self, before: bytes, after: bytes, width: int, height: int
    ) -> ComparisonResult:
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        try:
            arr1 = np.array(
                Image.frombytes("RGB", (width, height), before), dtype=np.float32
            )
            arr2 = np.array(
                Image.frombytes("RGB", (width, height), after), dtype=np.float32
            )
        except Exception:
            return ComparisonResult(
                similarity=0.0,
                changed_pixels=0,
                total_pixels=0,
                change_ratio=0.0,
                is_significant=False,
            )

        if arr1.shape != arr2.shape:
            min_h = min(arr1.shape[0], arr2.shape[0])
            min_w = min(arr1.shape[1], arr2.shape[1])
            arr1 = arr1[:min_h, :min_w]
            arr2 = arr2[:min_h, :min_w]
            height, width = min_h, min_w

        diff = np.abs(arr1 - arr2)
        max_diff = np.max(diff, axis=2)
        significant_mask = max_diff > self._threshold * 255

        gray1 = np.mean(arr1, axis=2)
        gray2 = np.mean(arr2, axis=2)

        real_changed = 0
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if significant_mask[y, x] and not self._is_antialiased(
                    gray1, gray2, x, y, width, height
                ):
                    real_changed += 1

        total = (width // 2) * (height // 2)
        change_ratio = real_changed / total if total > 0 else 0.0
        similarity = 1.0 - change_ratio

        return ComparisonResult(
            similarity=similarity,
            changed_pixels=real_changed,
            total_pixels=total,
            change_ratio=change_ratio,
            is_significant=change_ratio > 0.005,
        )

    def _is_antialiased(
        self,
        gray1: numpy.ndarray,  # noqa: F821
        gray2: numpy.ndarray,  # noqa: F821
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if (
                0 <= nx < width
                and 0 <= ny < height
                and abs(float(gray1[ny, nx]) - float(gray2[ny, nx]))
                < self._aa_tolerance * 255
            ):
                return True
        return False


class VisualRegressionDetector:
    STRATEGY_PIXEL = "pixel"
    STRATEGY_SSIM = "ssim"
    STRATEGY_PERCEPTUAL = "perceptual"
    STRATEGY_AA_TOLERANT = "aa_tolerant"
    STRATEGY_AUTO = "auto"

    def __init__(
        self,
        strategy: str = STRATEGY_AUTO,
        similarity_threshold: float = 0.95,
        change_ratio_threshold: float = 0.01,
    ):
        self._strategy = strategy
        self._similarity_threshold = similarity_threshold
        self._change_ratio_threshold = change_ratio_threshold
        self._pixel_comparator = None
        self._ssim_comparator = SSIMComparator()
        self._perceptual_comparator = PerceptualHashComparator()
        self._aa_comparator = AATolerantComparator()

    def compare(
        self, before: bytes, after: bytes, width: int, height: int
    ) -> ComparisonResult:
        if self._strategy == self.STRATEGY_AUTO:
            return self._compare_auto(before, after, width, height)
        return self._compare_with_strategy(
            self._strategy, before, after, width, height
        )

    def _compare_auto(
        self, before: bytes, after: bytes, width: int, height: int
    ) -> ComparisonResult:
        phash_result = self._perceptual_comparator.compare(
            before, after, width, height
        )
        if phash_result.similarity > 0.95:
            return phash_result
        return self._ssim_comparator.compare(before, after, width, height)

    def _compare_with_strategy(
        self,
        strategy: str,
        before: bytes,
        after: bytes,
        width: int,
        height: int,
    ) -> ComparisonResult:
        if strategy == self.STRATEGY_SSIM:
            return self._ssim_comparator.compare(before, after, width, height)
        if strategy == self.STRATEGY_PERCEPTUAL:
            return self._perceptual_comparator.compare(
                before, after, width, height
            )
        if strategy == self.STRATEGY_AA_TOLERANT:
            return self._aa_comparator.compare(before, after, width, height)
        from src.executor.screenshot_comparator import ScreenshotComparator

        if self._pixel_comparator is None:
            self._pixel_comparator = ScreenshotComparator(
                similarity_threshold=self._similarity_threshold,
                change_ratio_threshold=self._change_ratio_threshold,
            )
        return self._pixel_comparator.compare(before, after, width, height)
