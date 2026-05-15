import threading
import time
from collections import deque
from dataclasses import dataclass, field

_MAX_HISTORY = 10000


@dataclass
class _LLMCallRecord:
    model: str
    tokens_in: int
    tokens_out: int
    images_count: int
    latency_ms: float
    success: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class _ErrorRecord:
    endpoint: str
    error_type: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class MetricsSummary:
    total_requests: int
    total_errors: int
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    total_tokens: int
    total_images: int


class MetricsCollector:
    _instance: "MetricsCollector | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._llm_calls: deque[_LLMCallRecord] = deque(maxlen=_MAX_HISTORY)
        self._errors: deque[_ErrorRecord] = deque(maxlen=_MAX_HISTORY)

    @classmethod
    def get_instance(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def track_llm_call(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        images_count: int = 0,
        success: bool = True,
    ) -> None:
        record = _LLMCallRecord(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            images_count=images_count,
            latency_ms=latency_ms,
            success=success,
        )
        self._llm_calls.append(record)

    def track_error(self, endpoint: str, error_type: str) -> None:
        self._errors.append(_ErrorRecord(endpoint=endpoint, error_type=error_type))

    def get_summary(self) -> MetricsSummary:
        calls = list(self._llm_calls)
        errors = list(self._errors)

        if not calls:
            return MetricsSummary(
                total_requests=0,
                total_errors=len(errors),
                error_rate=0.0,
                avg_latency_ms=0.0,
                p95_latency_ms=0.0,
                total_tokens=0,
                total_images=0,
            )

        latencies = sorted(c.latency_ms for c in calls)
        success_calls = [c for c in calls if c.success]
        total_tokens = sum(c.tokens_in + c.tokens_out for c in success_calls)
        total_images = sum(c.images_count for c in success_calls)
        avg_latency = sum(latencies) / len(latencies)

        p95_index = int(len(latencies) * 0.95)
        p95_latency = latencies[min(p95_index, len(latencies) - 1)]

        error_count = sum(1 for c in calls if not c.success) + len(errors)
        error_rate = error_count / (len(calls) + len(errors)) if (len(calls) + len(errors)) > 0 else 0.0

        return MetricsSummary(
            total_requests=len(calls),
            total_errors=error_count,
            error_rate=error_rate,
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
            total_tokens=total_tokens,
            total_images=total_images,
        )
