from src.recorder.clipboard_recorder import ClipboardRecorder
from src.recorder.filesystem_recorder import FilesystemRecorder, _WatchdogHandler
from src.recorder.session_manager import SessionManager
from src.recorder.web_recorder import WebRecorder
from src.recorder.window_recorder import WindowRecorder


def test_create_recorders_uses_configurable_poll_intervals(monkeypatch):
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_MOUSE", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_KEYBOARD", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_WINDOW", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_CLIPBOARD", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_FILESYSTEM", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_WINDOW_CHECK_INTERVAL_MS", 125)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_CLIPBOARD_INTERVAL_MS", 275)

    manager = SessionManager()

    recorders = manager._create_recorders(mode="desktop")

    window_recorder = next(recorder for recorder in recorders if isinstance(recorder, WindowRecorder))
    clipboard_recorder = next(recorder for recorder in recorders if isinstance(recorder, ClipboardRecorder))

    assert window_recorder._poll_interval == 0.125
    assert clipboard_recorder._poll_interval == 0.275


def test_create_recorders_supports_auto_and_browser_alias(monkeypatch):
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_MOUSE", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_KEYBOARD", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_WINDOW", True)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_CLIPBOARD", False)
    monkeypatch.setattr("src.recorder.session_manager.settings.RECORD_ENABLE_FILESYSTEM", False)

    manager = SessionManager()

    auto_recorders = manager._create_recorders(mode="auto")
    browser_recorders = manager._create_recorders(mode="browser")

    assert any(isinstance(recorder, WindowRecorder) for recorder in auto_recorders)
    assert len(browser_recorders) == 1
    assert isinstance(browser_recorders[0], WebRecorder)


def test_filesystem_recorder_ignores_data_dir_and_sqlite_journal(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    screenshots_dir = data_dir / "screenshots"
    scripts_dir = data_dir / "scripts"
    database_path = data_dir / "auto_agent.db"

    monkeypatch.setattr("src.recorder.filesystem_recorder.settings.DATA_DIR", str(data_dir))
    monkeypatch.setattr("src.recorder.filesystem_recorder.settings.SCREENSHOT_DIR", str(screenshots_dir))
    monkeypatch.setattr("src.recorder.filesystem_recorder.settings.SCRIPT_DIR", str(scripts_dir))
    monkeypatch.setattr(
        "src.recorder.filesystem_recorder.settings.DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )

    recorder = FilesystemRecorder(watch_paths=[str(tmp_path)])
    handler = _WatchdogHandler(None, None, ignored_paths=recorder._ignored_paths)

    assert handler._should_filter(str(database_path.parent / "auto_agent.db-journal")) is True
    assert handler._should_filter(str(screenshots_dir / "capture.png")) is True
    assert handler._should_filter(str(tmp_path / "useful.txt")) is False
