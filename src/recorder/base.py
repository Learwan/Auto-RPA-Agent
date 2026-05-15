from abc import ABC, abstractmethod


class BaseRecorder(ABC):
    def __init__(self):
        self._running = False
        self._callback = None

    @abstractmethod
    def start(self, session_id: str, callback) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    def is_running(self) -> bool:
        return self._running

    def pause(self) -> None:
        self._running = False

    def resume(self) -> None:
        self._running = True
