from __future__ import annotations

import json
import queue
import sys
import threading


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _run_macos_hud() -> None:
    import objc
    from AppKit import (
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSButton,
        NSColor,
        NSFloatingWindowLevel,
        NSFont,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSScrollView,
        NSTextField,
        NSTextView,
        NSViewWidthSizable,
        NSViewHeightSizable,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorStationary,
        NSWindowStyleMaskNonactivatingPanel,
        NSWindowStyleMaskTitled,
        NSWindowStyleMaskUtilityWindow,
    )
    from Foundation import NSObject, NSTimer
    from PyObjCTools import AppHelper

    class HUDPanel(NSPanel):
        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return True

    class HUDController(NSObject):
        def init(self):
            self = objc.super(HUDController, self).init()
            if self is None:
                return None
            self._command_queue: queue.Queue = queue.Queue()
            self._interactive = False
            self._thinking = False
            self._state = {
                "session_name": "未开始录制",
                "status": "idle",
                "operation_count": 0,
                "latest_window": "等待捕获窗口",
                "latest_focused": "等待捕获元素",
                "recent_events": [],
                "hud_ai_assist": False,
                "hotkeys": {
                    "start_recording": "Ctrl+Shift+R",
                    "pause_resume_recording": "Ctrl+Shift+P",
                    "stop_recording": "Ctrl+Shift+S",
                    "toggle_hud_interaction": "Ctrl+Shift+U",
                    "capture_focus_context": "Ctrl+Shift+I",
                },
            }
            self._build_window()
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.1,
                self,
                "pollCommands:",
                None,
                True,
            )
            threading.Thread(target=self._stdin_loop, daemon=True).start()
            return self

        def _build_window(self):
            frame = NSScreen.mainScreen().visibleFrame()
            width = 480
            height = 312
            x = frame.origin.x + frame.size.width - width - 24
            y = frame.origin.y + frame.size.height - height - 42
            mask = NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow | NSWindowStyleMaskNonactivatingPanel
            self.window = HUDPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, width, height),
                mask,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_("Recording HUD")
            self.window.setLevel_(NSFloatingWindowLevel)
            self.window.setOpaque_(False)
            self.window.setReleasedWhenClosed_(False)
            self.window.setMovableByWindowBackground_(True)
            self.window.setAlphaValue_(0.97)
            self.window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.97, 0.98, 0.99, 0.76))
            self.window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            self.window.setIgnoresMouseEvents_(True)

            content = self.window.contentView()
            content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            self.title_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 266, 260, 24))
            self.title_label.setBezeled_(False)
            self.title_label.setDrawsBackground_(False)
            self.title_label.setEditable_(False)
            self.title_label.setSelectable_(False)
            self.title_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.26, 0.38, 1.0))
            self.title_label.setFont_(NSFont.boldSystemFontOfSize_(15))
            content.addSubview_(self.title_label)

            self.status_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 242, 420, 18))
            self.status_label.setBezeled_(False)
            self.status_label.setDrawsBackground_(False)
            self.status_label.setEditable_(False)
            self.status_label.setSelectable_(False)
            self.status_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.43, 0.53, 1.0))
            self.status_label.setFont_(NSFont.systemFontOfSize_(11))
            content.addSubview_(self.status_label)

            self.info_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 444, 144))
            self.info_view.setEditable_(False)
            self.info_view.setSelectable_(True)
            self.info_view.setRichText_(False)
            self.info_view.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.52))
            self.info_view.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.26, 0.33, 1.0))
            self.info_view.setFont_(NSFont.systemFontOfSize_(12))

            self.info_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 90, 444, 140))
            self.info_scroll.setHasVerticalScroller_(True)
            self.info_scroll.setBorderType_(0)
            self.info_scroll.setDocumentView_(self.info_view)
            content.addSubview_(self.info_scroll)

            self.chat_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 444, 148))
            self.chat_view.setEditable_(False)
            self.chat_view.setSelectable_(True)
            self.chat_view.setRichText_(False)
            self.chat_view.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.99, 0.97, 0.72))
            self.chat_view.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.26, 0.33, 1.0))
            self.chat_view.setFont_(NSFont.systemFontOfSize_(12))

            self.chat_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 112, 444, 144))
            self.chat_scroll.setHasVerticalScroller_(True)
            self.chat_scroll.setBorderType_(0)
            self.chat_scroll.setDocumentView_(self.chat_view)
            self.chat_scroll.setHidden_(True)
            content.addSubview_(self.chat_scroll)

            self.input_field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 60, 356, 30))
            self.input_field.setPlaceholderString_("向 LLM 描述你当前的录制目标...")
            self.input_field.setTarget_(self)
            self.input_field.setAction_("submitPrompt:")
            self.input_field.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.88))
            self.input_field.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.24, 0.32, 1.0))
            self.input_field.setHidden_(True)
            content.addSubview_(self.input_field)

            self.send_button = NSButton.alloc().initWithFrame_(NSMakeRect(388, 60, 76, 30))
            self.send_button.setTitle_("发送")
            self.send_button.setBezelStyle_(1)
            self.send_button.setTarget_(self)
            self.send_button.setAction_("submitPrompt:")
            self.send_button.setHidden_(True)
            content.addSubview_(self.send_button)

            self.capture_button = NSButton.alloc().initWithFrame_(NSMakeRect(278, 264, 88, 26))
            self.capture_button.setTitle_("抓取焦点")
            self.capture_button.setBezelStyle_(1)
            self.capture_button.setTarget_(self)
            self.capture_button.setAction_("requestFocusContext:")
            self.capture_button.setHidden_(True)
            content.addSubview_(self.capture_button)

            self.exit_button = NSButton.alloc().initWithFrame_(NSMakeRect(372, 264, 92, 26))
            self.exit_button.setTitle_("返回录制")
            self.exit_button.setBezelStyle_(1)
            self.exit_button.setTarget_(self)
            self.exit_button.setAction_("requestPassiveMode:")
            self.exit_button.setHidden_(True)
            content.addSubview_(self.exit_button)

            self.hotkey_hint_primary = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 54, 444, 16))
            self.hotkey_hint_primary.setBezeled_(False)
            self.hotkey_hint_primary.setDrawsBackground_(False)
            self.hotkey_hint_primary.setEditable_(False)
            self.hotkey_hint_primary.setSelectable_(False)
            self.hotkey_hint_primary.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.33, 0.43, 0.57, 1.0))
            self.hotkey_hint_primary.setFont_(NSFont.systemFontOfSize_(10))
            content.addSubview_(self.hotkey_hint_primary)

            self.hotkey_hint_secondary = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 36, 444, 16))
            self.hotkey_hint_secondary.setBezeled_(False)
            self.hotkey_hint_secondary.setDrawsBackground_(False)
            self.hotkey_hint_secondary.setEditable_(False)
            self.hotkey_hint_secondary.setSelectable_(False)
            self.hotkey_hint_secondary.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.33, 0.43, 0.57, 1.0))
            self.hotkey_hint_secondary.setFont_(NSFont.systemFontOfSize_(10))
            content.addSubview_(self.hotkey_hint_secondary)

            self.mode_hint = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 16, 444, 16))
            self.mode_hint.setBezeled_(False)
            self.mode_hint.setDrawsBackground_(False)
            self.mode_hint.setEditable_(False)
            self.mode_hint.setSelectable_(False)
            self.mode_hint.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.52, 0.62, 1.0))
            self.mode_hint.setFont_(NSFont.systemFontOfSize_(10))
            content.addSubview_(self.mode_hint)

            self._layout_views(False)
            self._render_state()
            self._apply_mode(False)
            self.window.orderFrontRegardless()

        def _layout_views(self, interactive: bool):
            content_width = self.window.frame().size.width - 40
            if interactive:
                self.title_label.setFrame_(NSMakeRect(20, 454, 240, 24))
                self.status_label.setFrame_(NSMakeRect(20, 430, content_width, 18))
                self.capture_button.setFrame_(NSMakeRect(278, 452, 88, 26))
                self.exit_button.setFrame_(NSMakeRect(372, 452, 92, 26))
                self.info_scroll.setFrame_(NSMakeRect(20, 284, content_width, 132))
                self.chat_scroll.setFrame_(NSMakeRect(20, 112, content_width, 156))
                self.input_field.setFrame_(NSMakeRect(20, 62, 356, 32))
                self.send_button.setFrame_(NSMakeRect(388, 62, 76, 32))
                self.hotkey_hint_primary.setFrame_(NSMakeRect(20, 40, content_width, 14))
                self.hotkey_hint_secondary.setFrame_(NSMakeRect(20, 24, content_width, 14))
                self.mode_hint.setFrame_(NSMakeRect(20, 8, content_width, 14))
            else:
                self.title_label.setFrame_(NSMakeRect(20, 266, 320, 24))
                self.status_label.setFrame_(NSMakeRect(20, 242, content_width, 18))
                self.info_scroll.setFrame_(NSMakeRect(20, 88, content_width, 142))
                self.hotkey_hint_primary.setFrame_(NSMakeRect(20, 52, content_width, 14))
                self.hotkey_hint_secondary.setFrame_(NSMakeRect(20, 34, content_width, 14))
                self.mode_hint.setFrame_(NSMakeRect(20, 14, content_width, 14))

        def _stdin_loop(self):
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._command_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    continue

        def pollCommands_(self, _timer):
            while True:
                try:
                    command = self._command_queue.get_nowait()
                except queue.Empty:
                    break
                self._handle_command(command)

        def _handle_command(self, command: dict):
            command_type = command.get("type")
            if command_type == "state":
                self._state = dict(command.get("payload") or {})
                self._render_state()
            elif command_type == "mode":
                self._apply_mode(bool(command.get("interactive")))
            elif command_type == "append_message":
                self._append_message(str(command.get("role") or "assistant"), str(command.get("content") or ""))
            elif command_type == "clear_messages":
                self.chat_view.setString_("")
            elif command_type == "thinking":
                self._thinking = bool(command.get("visible"))
                self._render_state()
            elif command_type == "close":
                NSApp.terminate_(None)

        def _render_state(self):
            session_name = self._state.get("session_name") or "未开始录制"
            status = self._state.get("status") or "idle"
            operation_count = self._state.get("operation_count") or 0
            latest_window = self._state.get("latest_window") or "等待捕获窗口"
            latest_focused = self._state.get("latest_focused") or "等待捕获元素"
            recent_events = self._state.get("recent_events") or []
            hotkeys = self._state.get("hotkeys") or {}
            ai_label = "AI assist on" if self._state.get("hud_ai_assist") else "AI assist off"
            thinking_label = " · LLM 思考中" if self._thinking else ""

            self.title_label.setStringValue_(session_name)
            self.status_label.setStringValue_(f"状态: {status} · 已捕获 {operation_count} 个操作 · {ai_label}{thinking_label}")

            lines = [
                f"当前窗口: {latest_window}",
                f"当前焦点: {latest_focused}",
                "",
                "最近事件:",
            ]
            if recent_events:
                for item in recent_events:
                    lines.append(f"- {item.get('action', '事件')}: {item.get('detail', '')}")
                    lines.append(f"  窗口: {item.get('window', '未知')}")
                    lines.append(f"  焦点: {item.get('focused', '未知')}")
            else:
                lines.append("- 暂无事件")
            self.info_view.setString_("\n".join(lines))
            self.hotkey_hint_primary.setStringValue_(
                "抓焦点 {capture}  ·  开始 {start}  ·  暂停/恢复 {pause}".format(
                    capture=hotkeys.get("capture_focus_context", "未配置"),
                    start=hotkeys.get("start_recording", "未配置"),
                    pause=hotkeys.get("pause_resume_recording", "未配置"),
                )
            )
            self.hotkey_hint_secondary.setStringValue_(
                "停止 {stop}  ·  交互 {toggle}".format(
                    stop=hotkeys.get("stop_recording", "未配置"),
                    toggle=hotkeys.get("toggle_hud_interaction", "未配置"),
                )
            )
            self.mode_hint.setStringValue_(
                f"穿透显示中，不抢占前台焦点。按 {hotkeys.get('toggle_hud_interaction', 'Ctrl+Shift+U')} 进入交互态。"
                if not self._interactive
                else (
                    "交互态已开启，可直接输入问题；点“返回录制”或再次按 "
                    f"{hotkeys.get('toggle_hud_interaction', 'Ctrl+Shift+U')} 返回穿透显示。"
                )
            )

        def _apply_mode(self, interactive: bool):
            self._interactive = interactive
            frame = self.window.frame()
            target_height = 500 if interactive else 312
            delta_height = target_height - frame.size.height
            self.window.setFrame_display_(
                NSMakeRect(
                    frame.origin.x,
                    frame.origin.y - delta_height,
                    frame.size.width,
                    target_height,
                ),
                True,
            )
            self._layout_views(interactive)
            self.window.setIgnoresMouseEvents_(not interactive)
            self.info_scroll.setHidden_(False)
            self.chat_scroll.setHidden_(not interactive)
            self.input_field.setHidden_(not interactive)
            self.send_button.setHidden_(not interactive)
            self.capture_button.setHidden_(not interactive)
            self.exit_button.setHidden_(not interactive)
            if interactive:
                NSApp.activateIgnoringOtherApps_(True)
                self.window.makeKeyAndOrderFront_(None)
                self.input_field.selectText_(None)
            else:
                self.window.orderFrontRegardless()
            self._render_state()

        def _append_message(self, role: str, content: str):
            prefix = "你" if role == "user" else "LLM"
            existing = self.chat_view.string() or ""
            chunk = f"{prefix}: {content}\n\n"
            self.chat_view.setString_(existing + chunk)
            self.chat_view.scrollRangeToVisible_((len(self.chat_view.string()), 0))

        def submitPrompt_(self, _sender):
            prompt = (self.input_field.stringValue() or "").strip()
            if not prompt:
                return
            self.input_field.setStringValue_("")
            _emit({"type": "prompt", "prompt": prompt})

        def requestPassiveMode_(self, _sender):
            self._apply_mode(False)
            _emit({"type": "set_interaction", "interactive": False})

        def requestFocusContext_(self, _sender):
            _emit({"type": "capture_focus_context"})

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = HUDController.alloc().init()
    controller.window.orderFrontRegardless()
    AppHelper.runEventLoop()


def main() -> None:
    if sys.platform != "darwin":
        _emit({"type": "error", "message": "HUD overlay currently supports macOS only."})
        return
    _run_macos_hud()


if __name__ == "__main__":
    main()