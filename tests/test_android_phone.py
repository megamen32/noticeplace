from __future__ import annotations

import subprocess
import unittest

from notification_center.android_phone import (
    AndroidPhoneAdapter,
    AndroidPhoneConfig,
    _telegram_header_call_point,
    _telegram_header_overflow_point,
    _telegram_qr_login_visible,
    android_input_scale,
    voice_service_registered,
)


class AndroidPhoneAdapterTests(unittest.TestCase):
    def test_voice_service_requires_an_explicit_in_service_state(self) -> None:
        self.assertTrue(voice_service_registered("mVoiceRegState=0(IN_SERVICE)"))
        self.assertFalse(voice_service_registered("mVoiceRegState=1(OUT_OF_SERVICE)"))

    def test_input_scale_uses_physical_pixels_when_android_has_a_display_override(self) -> None:
        self.assertEqual((2.0, 2.0), android_input_scale("Physical size: 1440x3200\nOverride size: 720x1600\n"))
        self.assertEqual((1.0, 1.0), android_input_scale("Physical size: 1440x3200\n"))

    def test_telegram_call_taps_only_a_visible_voice_call_control(self) -> None:
        commands: list[list[str]] = []
        xml = '<hierarchy><node content-desc="Voice call" bounds="[10,20][30,60]" /></hierarchy>'

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            stdout = "device\n" if command[-1] == "get-state" else "org.telegram.messenger/.LaunchActivity" if command[-2:] == ["dumpsys", "window"] else xml if command[-2:] == ["cat", "/sdcard/notify-center-window.xml"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        adapter = AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "@bezrabotnyi"), runner=runner, sleeper=lambda _seconds: None)
        adapter.telegram_call({})
        self.assertIn(["adb", "-s", "serial", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "tg://resolve?domain=bezrabotnyi"], commands)
        self.assertIn(["adb", "-s", "serial", "shell", "input", "tap", "20", "40"], commands)

    def test_telegram_call_scales_accessibility_bounds_for_physical_input(self) -> None:
        commands: list[list[str]] = []
        xml = '<hierarchy><node content-desc="Voice call" bounds="[10,20][30,60]" /></hierarchy>'

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            stdout = "device\n" if command[-1] == "get-state" else "Physical size: 1440x3200\nOverride size: 720x1600\n" if command[-2:] == ["wm", "size"] else "org.telegram.messenger/.LaunchActivity" if command[-2:] == ["dumpsys", "window"] else xml if command[-2:] == ["cat", "/sdcard/notify-center-window.xml"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "careviolan"), runner=runner, sleeper=lambda _seconds: None).telegram_call({})
        self.assertIn(["adb", "-s", "serial", "shell", "input", "tap", "40", "80"], commands)

    def test_qr_linking_screen_is_not_treated_as_a_telegram_call_screen(self) -> None:
        self.assertTrue(_telegram_qr_login_visible('<hierarchy><node text="Сканировать QR-код, чтобы продолжить привязку" /></hierarchy>'))
        self.assertFalse(_telegram_qr_login_visible('<hierarchy><node content-desc="Voice call" /></hierarchy>'))

    def test_telegram_header_fallback_requires_the_known_call_then_menu_structure(self) -> None:
        xml = """<hierarchy>
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" />
          <node class="android.widget.ImageView" bounds="[570,51][645,149]" />
          <node class="android.widget.ImageView" bounds="[627,51][702,149]" />
        </hierarchy>"""
        self.assertEqual(_telegram_header_call_point(xml), (607, 100))
        self.assertIsNone(_telegram_header_call_point(xml.replace('[627,51][702,149]', '[400,51][475,149]')))

    def test_telegram_header_overflow_fallback_requires_a_single_right_edge_icon(self) -> None:
        xml = """<hierarchy>
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" />
          <node class="android.widget.ImageView" bounds="[588,23][641,51]" />
        </hierarchy>"""
        self.assertEqual(_telegram_header_overflow_point(xml), (614, 37))
        self.assertIsNone(_telegram_header_overflow_point(xml.replace('[588,23][641,51]', '[388,23][441,51]')))

    def test_telegram_call_uses_a_verified_overflow_menu_when_the_header_has_no_call_icon(self) -> None:
        commands: list[list[str]] = []
        windows = iter((
            '<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" /><node class="android.widget.ImageView" bounds="[588,23][641,51]" /></hierarchy>',
            '<hierarchy><node content-desc="Voice call" bounds="[10,20][30,60]" /></hierarchy>',
        ))

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            stdout = "device\n" if command[-1] == "get-state" else "org.telegram.messenger/.LaunchActivity" if command[-2:] == ["dumpsys", "window"] else next(windows) if command[-2:] == ["cat", "/sdcard/notify-center-window.xml"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "careviolan"), runner=runner, sleeper=lambda _seconds: None).telegram_call({})
        self.assertIn(["adb", "-s", "serial", "shell", "input", "tap", "614", "37"], commands)
        self.assertIn(["adb", "-s", "serial", "shell", "input", "tap", "20", "40"], commands)

    def test_telegram_call_wakes_a_dozing_phone_before_opening_the_target(self) -> None:
        commands: list[list[str]] = []
        xml = """<hierarchy>
          <node class="android.widget.ImageView" bounds="[570,51][645,149]" />
          <node class="android.widget.ImageView" bounds="[627,51][702,149]" />
        </hierarchy>"""

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            stdout = "device\n" if command[-1] == "get-state" else "mWakefulness=Dozing" if command[-2:] == ["dumpsys", "power"] else "org.telegram.messenger/.LaunchActivity" if command[-2:] == ["dumpsys", "window"] else xml if command[-2:] == ["cat", "/sdcard/notify-center-window.xml"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        adapter = AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "bezrabotnyi"), runner=runner, sleeper=lambda _seconds: None)
        adapter.telegram_call({})
        self.assertLess(
            commands.index(["adb", "-s", "serial", "shell", "input", "keyevent", "KEYCODE_POWER"]),
            commands.index(["adb", "-s", "serial", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "tg://resolve?domain=bezrabotnyi"]),
        )

    def test_phone_call_refuses_when_voice_service_is_out(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            stdout = "device\n" if command[-1] == "get-state" else "mVoiceRegState=1(OUT_OF_SERVICE)"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        adapter = AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "bezrabotnyi", "+70000000000"), runner=runner)
        with self.assertRaisesRegex(RuntimeError, "voice service"):
            adapter.phone_call({})
