from types import SimpleNamespace

import pytest

from src.models.desktop import Rect, WindowInfo
from src.platform.macos.window import get_active_window


def _window(app_name: str, title: str, *, layer: int, width: int, height: int) -> WindowInfo:
    return WindowInfo(
        window_id=f"{app_name}-{title}-{layer}",
        title=title,
        app_name=app_name,
        pid=1,
        bounds=Rect(x=0, y=0, width=width, height=height),
        is_visible=True,
        layer=layer,
    )


@pytest.mark.asyncio
async def test_get_active_window_prefers_regular_window_for_frontmost_app(monkeypatch):
    windows = [
        _window("Code - Insiders", "Code - Insiders", layer=26, width=2560, height=89),
        _window("Code - Insiders", "Auto Agent Workflow", layer=0, width=2560, height=1607),
    ]

    async def _fake_get_windows():
        return windows

    monkeypatch.setattr("src.platform.macos.window.get_windows", _fake_get_windows)
    monkeypatch.setattr(
        "src.platform.macos.window.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Code - Insiders\n"),
    )

    active_window = await get_active_window()

    assert active_window is not None
    assert active_window.title == "Auto Agent Workflow"
    assert active_window.layer == 0


@pytest.mark.asyncio
async def test_get_active_window_ignores_system_overlay_and_recording_hud(monkeypatch):
    windows = [
        _window("控制中心", "Item-0", layer=25, width=56, height=57),
        _window("python", "Recording HUD", layer=3, width=420, height=250),
        _window("夸克", "无标题", layer=0, width=1926, height=1123),
    ]

    async def _fake_get_windows():
        return windows

    monkeypatch.setattr("src.platform.macos.window.get_windows", _fake_get_windows)
    monkeypatch.setattr(
        "src.platform.macos.window.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="控制中心\n"),
    )

    active_window = await get_active_window()

    assert active_window is not None
    assert active_window.app_name == "夸克"
    assert active_window.title == "无标题"


@pytest.mark.asyncio
async def test_get_active_window_ignores_wallpaper_windows(monkeypatch):
    windows = [
        _window("墙纸", "Offscreen Wallpaper Window", layer=0, width=2560, height=1600),
        _window("墙纸", "Wallpaper-CE0C0E34", layer=-2147483624, width=2560, height=1600),
        _window("Google Chrome", "GitHub", layer=0, width=1800, height=1200),
    ]

    async def _fake_get_windows():
        return windows

    monkeypatch.setattr("src.platform.macos.window.get_windows", _fake_get_windows)
    monkeypatch.setattr(
        "src.platform.macos.window.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="墙纸\n"),
    )
    monkeypatch.setattr("src.platform.macos.window.enrich_browser_window", lambda window: window)

    active_window = await get_active_window()

    assert active_window is not None
    assert active_window.app_name == "Google Chrome"
    assert active_window.title == "GitHub"
