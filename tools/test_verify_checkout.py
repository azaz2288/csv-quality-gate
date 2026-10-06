"""Verify the exporter path assertion with real canonical and Windows aliases."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_checkout import IMPORT_CHECK


class ExportPathTests(unittest.TestCase):
    def check_copy(self, short=False):
        with tempfile.TemporaryDirectory(prefix="csvgate-long-export-name-") as directory:
            root = Path(directory)
            shutil.copytree(Path(__file__).resolve().parents[1] / "csvgate", root / "csvgate",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            cwd = str(root)
            if short:
                function = ctypes.windll.kernel32.GetShortPathNameW
                function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
                function.restype = ctypes.c_uint32
                required = function(cwd, None, 0)
                if not required:
                    raise ctypes.WinError()
                buffer = ctypes.create_unicode_buffer(required)
                if not function(cwd, buffer, required):
                    raise ctypes.WinError()
                cwd = buffer.value
                if cwd.lower() == str(root).lower():
                    self.skipTest("Filesystem does not expose a distinct short-path alias")
                # Establish the original assertion really is wrong for this alias.
                old = IMPORT_CHECK.replace("root = Path.cwd().resolve()", "root = Path.cwd()")
                result = subprocess.run([sys.executable, "-E", "-s", "-c", old],
                                        cwd=cwd, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("AssertionError", result.stderr)
            result = subprocess.run([sys.executable, "-E", "-s", "-c", IMPORT_CHECK],
                                    cwd=cwd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("export version: 0.2.0", result.stdout)

    def test_real_source_export_import_location(self):
        self.check_copy()

    @unittest.skipUnless(os.name == "nt", "Windows short-path alias")
    def test_real_windows_short_path_does_not_false_fail(self):
        self.check_copy(short=True)


if __name__ == "__main__":
    unittest.main()
