from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)


class AudioFeedback:
    _instance: AudioFeedback | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> AudioFeedback:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def play_start(self) -> None:
        self._beep(800, 100)

    def play_pause(self) -> None:
        self._beep(600, 80)

    def play_resume(self) -> None:
        self._beep(800, 80)

    def play_stop(self) -> None:
        self._beep(400, 200)

    def play_error(self) -> None:
        self._beep(200, 400)

    def play_countdown_tick(self) -> None:
        self._beep(1000, 50)

    def play_countdown_finish(self) -> None:
        self._beep(1200, 150)

    def _beep(self, frequency: int, duration_ms: int) -> None:
        if not self._enabled:
            return

        try:
            if sys.platform == "darwin":
                import subprocess

                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Tink.aiff"],
                    capture_output=True,
                    timeout=1,
                )
            elif sys.platform == "win32":
                import winsound

                winsound.Beep(frequency, duration_ms)
            elif sys.platform == "linux":
                import subprocess

                subprocess.run(
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/message.ogg"],
                    capture_output=True,
                    timeout=1,
                )
        except Exception:
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except Exception:
                pass
