from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class SystemNotifier:
    _instance: SystemNotifier | None = None

    @classmethod
    def get_instance(cls) -> SystemNotifier:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def notify_recording_started(self, session_name: str) -> None:
        self._notify("录制已开始", f"正在录制: {session_name}")

    def notify_recording_paused(self, session_name: str) -> None:
        self._notify("录制已暂停", f"已暂停: {session_name}")

    def notify_recording_resumed(self, session_name: str) -> None:
        self._notify("录制已恢复", f"继续录制: {session_name}")

    def notify_recording_stopped(self, session_name: str, duration: str) -> None:
        self._notify("录制已停止", f"{session_name} 已完成，时长 {duration}")

    def notify_error(self, message: str) -> None:
        self._notify("录制错误", message)

    def _notify(self, title: str, message: str) -> None:
        if not self._enabled:
            return

        try:
            if sys.platform == "darwin":
                import subprocess

                script = f'display notification "{message}" with title "{title}" sound name "Tink"'
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=3,
                )
            elif sys.platform == "win32":
                try:
                    from win10toast import ToastNotifier

                    toaster = ToastNotifier()
                    toaster.show_toast(title, message, duration=3, threaded=True)
                except ImportError:
                    logger.debug("win10toast not available for Windows notifications")
            elif sys.platform == "linux":
                try:
                    import subprocess

                    subprocess.run(
                        ["notify-send", title, message],
                        capture_output=True,
                        timeout=3,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Failed to send system notification: %s", e)
