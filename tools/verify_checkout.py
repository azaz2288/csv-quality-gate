"""Verify a source export in an isolated temp directory, without Git or installs."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile


IMPORT_CHECK = (
    "from pathlib import Path; import csvgate; "
    "imported = Path(csvgate.__file__).resolve(); root = Path.cwd().resolve(); "
    "assert imported.is_relative_to(root), (imported, root); "
    "assert not Path('.git').exists(); print('export version:', csvgate.__version__)"
)


def main():
    source = Path(__file__).resolve().parents[1]
    # Tests launch real CLI children without inheriting interpreter -E/-s.
    # Sanitize their environment too, keeping a hostile PYTHONPATH out.
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    environment["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="csvgate-export-") as directory:
        export = Path(directory)
        for folder in ("csvgate", "tests"):
            shutil.copytree(source / folder, export / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        commands = [
            [sys.executable, "-E", "-s", "-m", "unittest", "discover", "-s", "tests", "-v"],
            [sys.executable, "-E", "-s", "-m", "csvgate", "compare", "--help"],
            [sys.executable, "-E", "-s", "-c", IMPORT_CHECK],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=export, env=environment, check=False, timeout=120)
            if result.returncode:
                return result.returncode
    print("Synthetic export verification passed; temporary copy removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
