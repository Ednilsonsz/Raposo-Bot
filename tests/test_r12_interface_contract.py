import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'


class R12InterfaceContractTests(unittest.TestCase):
    def test_r12_is_visible_and_voice_starts_off(self):
        source = (APP / 'ui_overlay_massa_v386_r11.py').read_text(encoding='utf-8')
        launcher = (APP / 'launcher_ui.py').read_text(encoding='utf-8')
        self.assertIn("BUILD_LABEL = 'V3.86 · R12'", source)
        self.assertIn('VOZ: OFF', source)
        self.assertIn("'loss_shifts'", source)
        self.assertIn('STOP LOSS POR TURNO', source)
        self.assertIn('build_revision', launcher)
        for name in ('config.json', 'config_v386_release.json'):
            config = json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))
            self.assertEqual(config['revision'], 'R12')
            self.assertFalse(config['voice_alerts']['enabled'])

    def test_embedded_overlay_javascript_parses(self):
        source = (APP / 'ui_overlay_massa_v386_r11.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        scripts = [node.value.value for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'js' for target in node.targets)
                   and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
        self.assertEqual(len(scripts), 1)
        if shutil.which('node'):
            completed = subprocess.run(['node', '-e', 'new Function(process.argv[1])', scripts[0]],
                                       text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
