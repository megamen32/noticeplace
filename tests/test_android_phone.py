from __future__ import annotations

import subprocess
import unittest

from notification_center.android_phone import AndroidPhoneAdapter, AndroidPhoneConfig, voice_service_registered


class AndroidPhoneAdapterTests(unittest.TestCase):
    def test_voice_service_requires_an_explicit_in_service_state(self) -> None:
        self.assertTrue(voice_service_registered("mVoiceRegState=0(IN_SERVICE)"))
        self.assertFalse(voice_service_registered("mVoiceRegState=1(OUT_OF_SERVICE)"))

    def test_telegram_call_taps_only_a_visible_voice_call_control(self) -> None:
        commands: list[list[str]] = []
        xml = '<hierarchy><node content-desc="Voice call" bounds="[10,20][30,60]" /></hierarchy>'

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            stdout = "device\n" if command[-1] == "get-state" else xml if command[-2:] == ["cat", "/sdcard/notify-center-window.xml"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        adapter = AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "@bezrabotnyi"), runner=runner, sleeper=lambda _seconds: None)
        adapter.telegram_call({})
        self.assertIn(["adb", "-s", "serial", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "tg://resolve?domain=bezrabotnyi"], commands)
        self.assertIn(["adb", "-s", "serial", "shell", "input", "tap", "20", "40"], commands)

    def test_phone_call_refuses_when_voice_service_is_out(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            stdout = "device\n" if command[-1] == "get-state" else "mVoiceRegState=1(OUT_OF_SERVICE)"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        adapter = AndroidPhoneAdapter(AndroidPhoneConfig("adb", "serial", "bezrabotnyi", "+70000000000"), runner=runner)
        with self.assertRaisesRegex(RuntimeError, "voice service"):
            adapter.phone_call({})
