"""Save Windows motion exports in the directory last used successfully."""

import logging
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
import winreg


_EXPORT_TYPES = {"fbx": {".fbx"}, "vmd": {".vmd", ".zip"}, "vrma": {".vrma", ".zip"}}


def _export_extension(uri, filename, port):
    extension = Path(filename).suffix.lower()
    blob = uri.startswith("blob:")
    url = urlsplit(uri.removeprefix("blob:"))
    if (url.scheme, url.netloc) != ("http", f"127.0.0.1:{port}"):
        return None
    if blob:
        if extension in (".fbx", ".vmd") or filename.lower().endswith("_pmx_vmd.zip"):
            return extension
        return None
    match = re.fullmatch(r"/api/jobs/[^/]+/(fbx|vmd|vrma)", url.path)
    return extension if match and extension in _EXPORT_TYPES[match[1]] else None


def _initial_directory(preference):
    try:
        saved = Path(preference.read_text(encoding="utf-8"))
        if saved.is_absolute() and saved.is_dir():
            return saved
    except (OSError, ValueError):
        pass
    try:
        key_name = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
            return Path(winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
    except OSError:
        return Path.home() / "Downloads"


def _save_directory(preference, directory):
    preference.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=preference.parent, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(str(directory).encode("utf-8"))
            temporary.close()
            path.replace(preference)
        finally:
            temporary.close()
            path.unlink(missing_ok=True)


def _choose_destination(owner, directory, filename, extension):
    import System.Windows.Forms as WinForms

    dialog = WinForms.SaveFileDialog()
    try:
        dialog.InitialDirectory = str(directory)
        dialog.FileName = filename
        dialog.DefaultExt = extension[1:]
        dialog.Filter = f"{extension[1:].upper()} (*{extension})|*{extension}"
        dialog.RestoreDirectory = True
        if dialog.ShowDialog(owner) == WinForms.DialogResult.OK:
            return dialog.FileName
    finally:
        dialog.Dispose()


def _remember_completed(operation, preference):
    from Microsoft.Web.WebView2.Core import CoreWebView2DownloadState

    def on_state_changed(sender, _args):
        state = sender.State
        if state == CoreWebView2DownloadState.InProgress:
            return
        sender.StateChanged -= on_state_changed
        if state == CoreWebView2DownloadState.Completed:
            try:
                _save_directory(preference, Path(sender.ResultFilePath).parent)
            except OSError:
                logging.exception("Could not remember the motion export directory")

    operation.StateChanged += on_state_changed


def configure_downloads(window, port, preference):
    """Use the custom dialog for motion exports and pywebview for other files."""
    browser = getattr(window.native, "browser", None)
    control = getattr(browser, "webview", None)
    original = getattr(browser, "on_download_starting", None)
    if original is None or not hasattr(control, "CoreWebView2"):
        return

    def on_download_starting(sender, args):
        args.Handled = True  # Hide the WebView2 download flyout.
        operation = args.DownloadOperation
        filename = Path(args.ResultFilePath).name
        extension = _export_extension(operation.Uri, filename, port)
        if not extension:
            original(sender, args)
            return
        selected = _choose_destination(window.native, _initial_directory(preference), filename, extension)
        if selected is None:
            args.Cancel = True
            return
        args.ResultFilePath = selected
        _remember_completed(operation, preference)

    def replace_handler(core):
        core.DownloadStarting -= original
        core.DownloadStarting += on_download_starting

    def on_ready(sender, args):
        sender.CoreWebView2InitializationCompleted -= on_ready
        if args.IsSuccess:
            replace_handler(sender.CoreWebView2)

    core = control.CoreWebView2
    if core is None:
        control.CoreWebView2InitializationCompleted += on_ready
    else:
        replace_handler(core)
