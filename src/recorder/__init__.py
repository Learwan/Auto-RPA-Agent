from importlib import import_module


_EXPORTS = {
    "AudioFeedback": "src.recorder.audio_feedback",
    "BaseRecorder": "src.recorder.base",
    "ClipboardRecorder": "src.recorder.clipboard_recorder",
    "EventBus": "src.recorder.event_bus",
    "FilesystemRecorder": "src.recorder.filesystem_recorder",
    "HotkeyManager": "src.recorder.hotkey_manager",
    "RecordingHUDManager": "src.recorder.hud_manager",
    "KeyboardRecorder": "src.recorder.keyboard_recorder",
    "MouseRecorder": "src.recorder.mouse_recorder",
    "SystemNotifier": "src.recorder.notification",
    "RecordingService": "src.recorder.service",
    "SessionManager": "src.recorder.session_manager",
    "WebRecorder": "src.recorder.web_recorder",
    "WindowRecorder": "src.recorder.window_recorder",
}


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'src.recorder' has no attribute {name!r}")
    module = import_module(module_name)
    return getattr(module, name)


def __dir__():
    return sorted(list(globals().keys()) + list(__all__))

__all__ = [
    "BaseRecorder",
    "MouseRecorder",
    "KeyboardRecorder",
    "WindowRecorder",
    "FilesystemRecorder",
    "ClipboardRecorder",
    "WebRecorder",
    "SessionManager",
    "RecordingService",
    "EventBus",
    "HotkeyManager",
    "RecordingHUDManager",
    "AudioFeedback",
    "SystemNotifier",
]
