"""Start the desktop with CPython's GUI executable and FlowHMR's private packages."""

import ctypes
import os
from pathlib import Path
import runpy
import site
import sys
import traceback


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_messages import MESSAGES, read_language


def report_failure(message):
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "desktop.log").open("a", encoding="utf-8") as log:
        traceback.print_exc(file=log)
    if os.environ.get("FLOWHMR_UPDATE_TRIAL") != "1":
        heading, detail = MESSAGES[read_language()][message]
        ctypes.windll.user32.MessageBoxW(None, detail, heading, 0x10)
    raise SystemExit(1)


if not (ROOT / "runtime/pythonw.exe").is_file():
    site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))

try:
    from pythonnet import load

    dotnet = ROOT / "runtime/dotnet"
    if dotnet.is_dir():
        load("coreclr", dotnet_root=dotnet, runtime_config=Path(__file__).with_name("desktop.runtimeconfig.json"))
    else:
        load()
except Exception:
    report_failure("desktop_runtime_failed")

try:
    runpy.run_path(str(ROOT / "tools/windows/desktop.py"), run_name="__main__")
except Exception:
    report_failure("failed")
