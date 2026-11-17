"""Static guard: undefined names (e.g. a module used without import) fail the suite instead of a 500 in prod."""
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "shorts"


def test_no_undefined_names():
    out = subprocess.run([sys.executable, "-m", "pyflakes", str(SRC)], capture_output=True, text=True)
    bad = [l for l in (out.stdout + out.stderr).splitlines() if "undefined name" in l]
    assert not bad, "\n".join(bad)
