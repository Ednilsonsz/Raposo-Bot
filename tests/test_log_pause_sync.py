import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from mobile_server import MobileControlServer
from pause_control_r11 import synchronize_pause


class Driver:
    def __init__(self, paused):
        self.paused = paused

    def execute_script(self, script, *args):
        if args:
            self.paused = args[0]
        return self.paused


class LogPauseSyncTests(unittest.TestCase):
    def test_accepted_controls_survive_stale_browser_flag(self):
        for command, previous, expected in [('PAUSE', False, True), ('RESUME', True, False)]:
            with self.subTest(command=command):
                analyzer = SimpleNamespace(paused=previous)
                executor = SimpleNamespace(armed=True)
                executor.disarm = lambda: setattr(executor, 'armed', False)
                server = MobileControlServer({})
                def fast_handler(cmd, command_id):
                    analyzer.paused = cmd == 'PAUSE'
                    executor.disarm()
                    return {'paused': analyzer.paused, 'error': ''}
                server.set_control_handler(fast_handler)
                result = server.apply_control(command, 'log-test')
                self.assertTrue(result['ok'])
                driver = Driver(previous)
                paused = synchronize_pause(driver, analyzer.paused, server.pop_command(), analyzer, executor)
                self.assertEqual(paused, expected)
                self.assertEqual(driver.paused, expected)
                self.assertFalse(executor.armed)
                self.assertIsNone(server.pop_command())

    def test_rejected_command_is_not_published(self):
        server = MobileControlServer({})
        server.set_control_handler(lambda *_: {'paused': True, 'error': 'MOTOR_INDISPONIVEL'})
        self.assertFalse(server.apply_control('RESUME')['ok'])
        self.assertIsNone(server.pop_command())


if __name__ == '__main__':
    unittest.main()
