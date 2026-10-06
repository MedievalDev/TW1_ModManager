"""The self-update starts the new exe from a batch. A one-file exe must not pass its
_PYI_* variables on, or the new exe reuses the old one's (deleted) temp folder and fails
with "Failed to load Python DLL" (reproduced 2026-10-06 with a one-file test exe)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import updater  # noqa: E402


class UpdaterEnv(unittest.TestCase):
    def test_clean_env_drops_pyinstaller_state(self):
        env = updater.clean_env({'PATH': 'x', '_PYI_APPLICATION_HOME_DIR': 'C:/old/_MEI1', '_PYI_ARCHIVE_FILE': 'a.exe',
                                 '_PYI_PARENT_PROCESS_LEVEL': '1', '_MEIPASS2': 'C:/old/_MEI1', 'KEEP': '1'})
        self.assertEqual(env['PATH'], 'x')
        self.assertEqual(env['KEEP'], '1')
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertFalse([k for k in env if k.startswith('_PYI_') or k.upper() == '_MEIPASS2'])

    def test_swap_script_resets_before_start(self):
        text = updater.swap_script(r'C:\t\mm.exe', r'C:\t\mm.exe.new', 4242)
        self.assertLess(text.index('set PYINSTALLER_RESET_ENVIRONMENT=1'), text.index('start ""'))


if __name__ == '__main__':
    unittest.main()
