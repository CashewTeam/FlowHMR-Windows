"""Start the desktop with CPython's GUI executable and FlowHMR's private packages."""

import ctypes
from pathlib import Path
import runpy
import site
import sys
import traceback


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_messages import MESSAGES, read_language

try:
    if not (ROOT / "runtime/pythonw.exe").is_file():
        site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))
    runpy.run_path(str(ROOT / "tools/windows/desktop.py"), run_name="__main__")
except Exception:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "desktop.log").open("a", encoding="utf-8") as log:
        traceback.print_exc(file=log)
    heading, detail = MESSAGES[read_language()]["failed"]
    ctypes.windll.user32.MessageBoxW(None, detail, heading, 0x10)
