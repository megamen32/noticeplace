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

    @property
    def can_phone_call(self) -> bool:
        return bool(self._config.phone_number)

    def _run(self, *arguments: str) -> str:
        completed = self._runner(
            [self._config.adb_path, "-s", self._config.serial, *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._config.command_timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Android command failed: {' '.join(arguments[:3])}")
        return completed.stdout

    def _ensure_ready(self) -> None:
        if self._run("get-state").strip() != "device":
            raise RuntimeError("Android device is not ready")

    def _window_xml(self) -> str:
        self._run("shell", "uiautomator", "dump", "/sdcard/notify-center-window.xml")
        return self._run("exec-out", "cat", "/sdcard/notify-center-window.xml")

    def telegram_call(self, _payload: dict[str, Any]) -> None:
        """Open the configured Telegram chat and tap only an explicit voice-call control."""
        self._ensure_ready()
        if not self._config.telegram_target:
            raise RuntimeError("Android Telegram target is not configured")
        target = self._config.telegram_target.removeprefix("@").strip()
        self._run("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"tg://resolve?domain={target}")
        self._sleeper(1.5)
        point = _tap_bounds(self._window_xml(), self._config.call_labels)
        if point is None:
            raise RuntimeError("Telegram voice-call control is not visible")
        self._run("shell", "input", "tap", str(point[0]), str(point[1]))

    def phone_call(self, _payload: dict[str, Any]) -> None:
        """Place a cellular call only after Android reports voice service in operation."""
        self._ensure_ready()
        if not self._config.phone_number:
            raise RuntimeError("Android phone number is not configured")
        if not voice_service_registered(self._run("shell", "dumpsys", "telephony.registry")):
            raise RuntimeError("Android voice service is unavailable")
        self._run("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{self._config.phone_number}")
