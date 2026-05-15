from src.models.hotkey import HotkeyBinding
from src.recorder.hotkey_manager import _build_pynput_combo


def test_build_pynput_combo_returns_empty_for_modifier_only_binding():
    binding = HotkeyBinding(action="start_recording", modifiers=["ctrl"], key="ctrl", enabled=True)

    combo = _build_pynput_combo(binding)

    assert combo == ""


def test_build_pynput_combo_builds_valid_combo_for_normal_binding():
    binding = HotkeyBinding(action="start_recording", modifiers=["ctrl", "shift"], key="r", enabled=True)

    combo = _build_pynput_combo(binding)

    assert combo == "<ctrl>+<shift>+r"


def test_build_pynput_combo_builds_valid_combo_for_hud_toggle_binding():
    binding = HotkeyBinding(action="toggle_hud_interaction", modifiers=["ctrl", "shift"], key="u", enabled=True)

    combo = _build_pynput_combo(binding)

    assert combo == "<ctrl>+<shift>+u"


def test_build_pynput_combo_builds_valid_combo_for_capture_focus_binding():
    binding = HotkeyBinding(action="capture_focus_context", modifiers=["ctrl", "shift"], key="i", enabled=True)

    combo = _build_pynput_combo(binding)

    assert combo == "<ctrl>+<shift>+i"
