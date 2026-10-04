import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from voice_announcer_r11 import VoiceAnnouncer


class VoiceAnnouncerR11Tests(unittest.TestCase):
    def test_summary_is_queued_without_blocking(self):
        spoken = []
        voice = VoiceAnnouncer(
            {"voice_alerts": {"enabled": True, "mode": "summary", "cooldown_seconds": 0}},
            runner=spoken.append,
        )
        self.assertTrue(voice.announce("entry", "acima"))
        deadline = time.time() + 1
        while not spoken and time.time() < deadline:
            time.sleep(0.01)
        voice.close()
        self.assertEqual(spoken, ["Entrada autorizada. acima"])

    def test_disabled_voice_has_no_side_effect(self):
        spoken = []
        voice = VoiceAnnouncer({"voice_alerts": {"enabled": False}}, runner=spoken.append)
        self.assertFalse(voice.announce("sent", "acima"))
        time.sleep(0.03)
        voice.close()
        self.assertEqual(spoken, [])

    def test_repeated_blocked_alert_is_suppressed(self):
        voice = VoiceAnnouncer({"voice_alerts": {"enabled": True, "mode": "summary", "cooldown_seconds": 60}}, runner=lambda _: None)
        self.assertTrue(voice.announce("blocked", "janela perdida"))
        self.assertFalse(voice.announce("blocked", "janela perdida"))
        voice.close()

    def test_config_keeps_summary_voice_disabled_at_start(self):
        import json
        config = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["voice_alerts"]["mode"], "summary")
        self.assertFalse(config["voice_alerts"]["enabled"])


if __name__ == "__main__":
    unittest.main()


