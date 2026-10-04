"""Avisos de voz resumidos do Raposo R11.

A voz é informativa: nunca autoriza, retarda ou cancela uma ordem.
O worker é assíncrono e aplica supressão curta para não repetir bloqueios.
"""
from __future__ import annotations

import os
import platform
import queue
import shutil
import subprocess
import threading
import time
from typing import Optional


class VoiceAnnouncer:
    SUMMARY_EVENTS = {
        "entry": "Entrada autorizada",
        "sent": "Ordem enviada",
        "blocked": "Ordem bloqueada",
        "win": "Resultado win",
        "loss": "Resultado loss",
        "draw": "Resultado empate",
        "paused": "Motor pausado",
        "resumed": "Motor retomado",
        "setup_waiting": "Aguardando setup",
        "setup_analyzing": "Setup analisando entrada",
        "setup_favorable": "Setup favorável",
    }

    def __init__(self, config: Optional[dict] = None, runner=None):
        config = config or {}
        opts = config.get("voice_alerts", {}) if isinstance(config, dict) else {}
        self.enabled = bool(opts.get("enabled", False))
        self.mode = str(opts.get("mode", "summary")).lower()
        self.cooldown = float(opts.get("cooldown_seconds", 3.0))
        self._platform_runner = runner is None
        self._runner = runner or self._run_platform
        self._queue = queue.Queue(maxsize=32)
        self._last = {}
        self._last_setup_state = None
        self._closed = False
        self._worker = threading.Thread(target=self._consume, name="raposo-voice", daemon=True)
        self._worker.start()

    def announce(self, event: str, detail: str = "") -> bool:
        """Queue a short summary; returns false when disabled/suppressed."""
        if not self.enabled or self.mode != "summary" or self._closed:
            return False
        event = str(event).lower().strip()
        base = self.SUMMARY_EVENTS.get(event)
        if not base:
            return False
        now = time.monotonic()
        if event.startswith("setup_"):
            setup_state = (event, str(detail))
            if setup_state == self._last_setup_state:
                return False
            self._last_setup_state = setup_state
        key = (event, str(detail)) if event in ("blocked", "setup_favorable") else event
        if now - self._last.get(key, -1e9) < self.cooldown:
            return False
        self._last[key] = now
        text = f"{base}. {detail}".strip() if detail else f"{base}."
        try:
            self._queue.put_nowait((text, event))
        except queue.Full:
            return False
        return True

    def close(self):
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _consume(self):
        while not self._closed:
            item = self._queue.get()
            if item is None:
                return
            try:
                text, event = item
                if self._platform_runner:
                    self._runner(text, event)
                else:
                    self._runner(text)
            except Exception:
                # Voz nunca pode afetar o loop de captura/execução.
                pass

    @staticmethod
    def _run_platform(text: str, event: str = ""):
        system = platform.system().lower()
        if system == "windows":
            # SAPI local, sem rede e sem bloquear o processo principal.
            escaped = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Rate=" + ("-2" if event == "loss" else "1" if event == "win" else "0") + "; "
                "$s.Volume=" + ("72" if event == "loss" else "100") + "; "
                "$s.Speak('" + escaped + "')"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        binary = shutil.which("espeak") or shutil.which("espeak-ng")
        if binary:
            subprocess.Popen([binary, "-v", "pt", "-s", "145" if event == "win" else "115" if event == "loss" else "130", text], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)


_default: Optional[VoiceAnnouncer] = None


def configure(config: Optional[dict] = None) -> VoiceAnnouncer:
    global _default
    if _default is not None:
        _default.close()
    _default = VoiceAnnouncer(config)
    return _default


def announce(event: str, detail: str = "") -> bool:
    return _default.announce(event, detail) if _default is not None else False
