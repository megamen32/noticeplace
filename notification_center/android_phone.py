"""In-process Android call adapter for the Notify Center delivery worker."""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Sequence


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def voice_service_registered(telephony_dump: str) -> bool:
    """Return true only when Android reports an in-service voice registration."""
    return bool(re.search(r"mVoiceRegState=0\(IN_SERVICE\)", telephony_dump))


def _tap_bounds(xml_text: str, labels: Sequence[str]) -> tuple[int, int] | None:
    """Find a visible call control by accessible label without retaining screen text."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    wanted = {label.casefold() for label in labels}
    for node in root.iter("node"):
        label = (node.attrib.get("content-desc") or node.attrib.get("text") or "").strip().casefold()
        if label not in wanted:
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        if match:
            left, top, right, bottom = (int(value) for value in match.groups())
            return ((left + right) // 2, (top + bottom) // 2)
    return None


def _telegram_header_call_point(xml_text: str) -> tuple[int, int] | None:
    """Return Telegram's unlabelled header call control only for its known layout.

    Current Telegram Android draws the call and overflow actions as two adjacent
    unlabelled ``ImageView`` nodes.  Requiring the complete header pair avoids
    guessing from a fixed screen coordinate or tapping a lone unrelated icon.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    candidates: list[tuple[int, int, int, int]] = []
    for node in root.iter("node"):
        if node.attrib.get("class") != "android.widget.ImageView":
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if left >= 500 and top <= 180 and 50 <= right - left <= 120 and 50 <= bottom - top <= 120:
            candidates.append((left, top, right, bottom))
    candidates.sort()
    if len(candidates) != 2:
        return None
    call, overflow = candidates
    if not (call[0] < overflow[0] and call[2] <= overflow[0] + 24 and abs(call[1] - overflow[1]) <= 12):
        return None
    return ((call[0] + call[2]) // 2, (call[1] + call[3]) // 2)


def _telegram_header_overflow_point(xml_text: str) -> tuple[int, int] | None:
    """Find a sole top-right Telegram overflow icon without assuming coordinates."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    all_bounds = [re.findall(r"\d+", node.attrib.get("bounds", "")) for node in root.iter("node")]
    widths = [int(values[2]) for values in all_bounds if len(values) == 4]
    if not widths:
        return None
    screen_width = max(widths)
    candidates: list[tuple[int, int, int, int]] = []
    for node in root.iter("node"):
        if node.attrib.get("class") != "android.widget.ImageView":
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if left >= screen_width * 3 // 4 and top <= 180 and 20 <= right - left <= 120 and 20 <= bottom - top <= 120:
            candidates.append((left, top, right, bottom))
    if len(candidates) != 1:
        return None
    left, top, right, bottom = candidates[0]
    return ((left + right) // 2, (top + bottom) // 2)


def _telegram_qr_login_visible(xml_text: str) -> bool:
    """Identify Telegram's device-linking screen without retaining UI contents."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    labels = " ".join((node.attrib.get("content-desc") or node.attrib.get("text") or "").casefold() for node in root.iter("node"))
    return any(marker in labels for marker in ("scan qr", "qr-код", "привязк"))


def android_input_scale(wm_size_text: str) -> tuple[float, float]:
    """Map UIAutomator's override coordinates to ``adb input`` physical pixels."""
    physical = re.search(r"Physical size:\s*(\d+)x(\d+)", wm_size_text)
    override = re.search(r"Override size:\s*(\d+)x(\d+)", wm_size_text)
    if physical is None or override is None:
        return (1.0, 1.0)
    physical_width, physical_height = (int(value) for value in physical.groups())
    override_width, override_height = (int(value) for value in override.groups())
    if not override_width or not override_height:
        return (1.0, 1.0)
    return (physical_width / override_width, physical_height / override_height)


@dataclass(frozen=True)
class AndroidPhoneConfig:
    adb_path: str
    serial: str
    telegram_target: str
    phone_number: str = ""
    call_labels: tuple[str, ...] = ("Voice call", "Call", "Позвонить", "Голосовой звонок")
    command_timeout_seconds: float = 12.0


class AndroidPhoneAdapter:
    """Use the already USB-connected S21 directly from the Notify Center process."""

    def __init__(self, config: AndroidPhoneConfig, runner: CommandRunner = subprocess.run, sleeper: Callable[[float], None] = time.sleep) -> None:
        self._config = config
        self._runner = runner
        self._sleeper = sleeper
        self._input_scale: tuple[float, float] | None = None
        self._transport_serial: str | None = None

    @property
    def can_phone_call(self) -> bool:
        return bool(self._config.phone_number)

    def _completed(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._config.command_timeout_seconds,
        )

    def _run_on(self, serial: str, *arguments: str) -> str:
        completed = self._completed([self._config.adb_path, "-s", serial, *arguments])
        if completed.returncode != 0:
            raise RuntimeError(f"Android command failed: {' '.join(arguments[:3])}")
        return completed.stdout

    def _connected_transport_serial(self) -> str:
        """Find the configured physical handset on either USB or Wi-Fi ADB.

        ``adb devices`` assigns Wi-Fi debugging a transport-specific service name,
        whereas the Notify Center setting intentionally holds the stable hardware
        serial.  A failed direct lookup therefore falls back only to connected
        transports whose on-device ``ro.serialno`` matches that configured serial.
        """
        if self._transport_serial is not None:
            return self._transport_serial
        configured = self._config.serial
        direct = self._completed([self._config.adb_path, "-s", configured, "get-state"])
        if direct.returncode == 0 and direct.stdout.strip() == "device":
            self._transport_serial = configured
            return configured
        devices = self._completed([self._config.adb_path, "devices"])
        if devices.returncode != 0:
            raise RuntimeError("Android device discovery failed")
        for line in devices.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2 or fields[1] != "device":
                continue
            candidate = fields[0]
            reported = self._completed([self._config.adb_path, "-s", candidate, "shell", "getprop", "ro.serialno"])
            if reported.returncode != 0 or reported.stdout.strip() != configured:
                continue
            ready = self._completed([self._config.adb_path, "-s", candidate, "get-state"])
            if ready.returncode == 0 and ready.stdout.strip() == "device":
                self._transport_serial = candidate
                return candidate
        raise RuntimeError("Configured Android device is not connected")

    def _run(self, *arguments: str) -> str:
        return self._run_on(self._connected_transport_serial(), *arguments)

    def _ensure_ready(self) -> None:
        if self._run("get-state").strip() != "device":
            raise RuntimeError("Android device is not ready")

    def _window_xml(self) -> str:
        self._run("shell", "uiautomator", "dump", "/sdcard/notify-center-window.xml")
        return self._run("exec-out", "cat", "/sdcard/notify-center-window.xml")

    def _telegram_foreground(self) -> bool:
        return "org.telegram.messenger" in self._run("shell", "dumpsys", "window")

    def _tap(self, point: tuple[int, int]) -> None:
        if self._input_scale is None:
            self._input_scale = android_input_scale(self._run("shell", "wm", "size"))
        scale_x, scale_y = self._input_scale
        self._run("shell", "input", "tap", str(round(point[0] * scale_x)), str(round(point[1] * scale_y)))

    def _wake_for_interaction(self) -> None:
        """Wake only an always-on screen with ordinary Android input events."""
        if "mWakefulness=Dozing" not in self._run("shell", "dumpsys", "power"):
            return
        self._run("shell", "input", "keyevent", "KEYCODE_POWER")
        self._sleeper(0.2)
        self._run("shell", "input", "keyevent", "KEYCODE_MENU")
        self._run("shell", "input", "swipe", "540", "1800", "540", "700", "180")

    def telegram_call(self, _payload: dict[str, Any]) -> None:
        """Open the configured Telegram chat and tap a verified voice-call control."""
        self._ensure_ready()
        if not self._config.telegram_target:
            raise RuntimeError("Android Telegram target is not configured")
        target = self._config.telegram_target.removeprefix("@").strip()
        self._wake_for_interaction()
        self._run("shell", "cmd", "statusbar", "collapse")
        self._run("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"tg://resolve?domain={target}")
        self._sleeper(1.5)
        if not self._telegram_foreground():
            raise RuntimeError("Telegram did not reach the foreground")
        xml_text = self._window_xml()
        if _telegram_qr_login_visible(xml_text):
            raise RuntimeError("Telegram device login is required")
        point = _tap_bounds(xml_text, self._config.call_labels) or _telegram_header_call_point(xml_text)
        if point is None:
            overflow = _telegram_header_overflow_point(xml_text)
            if overflow is None:
                raise RuntimeError("Telegram voice-call control is not visible")
            self._tap(overflow)
            self._sleeper(0.3)
            point = _tap_bounds(self._window_xml(), self._config.call_labels)
        if point is None:
            raise RuntimeError("Telegram voice-call control is not visible")
        self._tap(point)

    def phone_call(self, _payload: dict[str, Any]) -> None:
        """Place a cellular call only after Android reports voice service in operation."""
        self._ensure_ready()
        if not self._config.phone_number:
            raise RuntimeError("Android phone number is not configured")
        if not voice_service_registered(self._run("shell", "dumpsys", "telephony.registry")):
            raise RuntimeError("Android voice service is unavailable")
        self._run("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{self._config.phone_number}")
        voice = _payload.get("voice") if isinstance(_payload, dict) else None
        if not isinstance(voice, dict) or not str(voice.get("text") or "").strip():
            return
        text = str(voice["text"]).strip()
        repeat = max(1, min(3, int(voice.get("repeat") or 2)))
        connect_wait_seconds = max(5, min(30, float(voice.get("connect_wait_seconds") or 12)))
        hangup_after = bool(voice.get("hangup_after", True))
        try:
            self._sleeper(connect_wait_seconds)
            speaker = _tap_bounds(self._window_xml(), ("Speaker", "Динамик", "Громкая связь"))
            if speaker is None:
                raise RuntimeError("Phone speaker control is not visible")
            self._tap(speaker)
            self._sleeper(0.5)
            for index in range(repeat):
                self._run("shell", "am", "broadcast", "-a", "com.termux.api.tts.SPEAK", "--es", "com.termux.api.extra.TEXT", text)
                if index + 1 < repeat:
                    self._sleeper(1.5)
        finally:
            if hangup_after:
                self._run("shell", "input", "keyevent", "KEYCODE_ENDCALL")
