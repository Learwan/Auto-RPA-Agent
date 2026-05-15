import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class ModelUsageMetrics:
    model: str
    tokens_in: int
    tokens_out: int
    images_count: int
    latency_ms: float
    timestamp: float = field(default_factory=time.time)


class TokenCounter:
    _instance: "TokenCounter | None" = None

    _PATCH_SIZE = 16
    _MERGE_SIZE = 2
    _EFFECTIVE_PATCH = _PATCH_SIZE * _MERGE_SIZE

    def __init__(self) -> None:
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        self._total_images: int = 0
        self._session_stats: OrderedDict[str, dict] = OrderedDict()

    @classmethod
    def get_instance(cls) -> "TokenCounter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def estimate_image_tokens(width: int, height: int) -> int:
        patches_w = max(1, width // TokenCounter._EFFECTIVE_PATCH)
        patches_h = max(1, height // TokenCounter._EFFECTIVE_PATCH)
        return 512 + patches_w * patches_h

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    def record_call(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        images_count: int,
        latency_ms: float,
    ) -> ModelUsageMetrics:
        self._total_tokens_in += tokens_in
        self._total_tokens_out += tokens_out
        self._total_images += images_count
        return ModelUsageMetrics(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            images_count=images_count,
            latency_ms=latency_ms,
        )

    def get_global_stats(self) -> dict:
        return {
            "total_tokens_in": self._total_tokens_in,
            "total_tokens_out": self._total_tokens_out,
            "total_images": self._total_images,
            "total_tokens": self._total_tokens_in + self._total_tokens_out,
        }

    def reset(self) -> None:
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._total_images = 0
        self._session_stats.clear()
