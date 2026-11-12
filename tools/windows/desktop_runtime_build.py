"""Stage the tested private Windows Desktop runtime and pywebview adapter."""

from hashlib import sha512
from pathlib import Path
from textwrap import indent
from zipfile import ZipFile


DOTNET_ARCHIVES = {
    "dotnet-runtime-win-x64.zip":
        "06f0bedd59ae4ac722ada805a6d6bbf605bcdc790c71f59b7003878072902b5cde5dec8c40c0baa81192660d4748e9d79afdbdcb0c0ea9d6b821d0a044c4c6dd",
    "windowsdesktop-runtime-win-x64.zip":
        "d82ee2e70f0c1ee7af3a908a944541993782aa80a3fd2b2e02a3fdf27edc81c0ad946f14a3fe34bae295f1b7a23f5d35a466239726e4457bb200de0344e077c4",
}


def stage_dotnet_runtime(destination, prerequisites):
    for name, expected in DOTNET_ARCHIVES.items():
        archive = prerequisites / name
        digest = sha512()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-512 for {archive.name}")
    destination.mkdir(parents=True, exist_ok=False)
    for name in DOTNET_ARCHIVES:
        with ZipFile(prerequisites / name) as archive:
            archive.extractall(destination)


def adapt_winforms(site):
    # pywebview 6.2.1 predates the CoreCLR changes in upstream PR #1832.
    # Modern WinForms exposes folder selection without the private Vista APIs.
    file = Path(site) / "webview/platforms/winforms.py"
    original = file.read_text(encoding="utf-8")
    reference = "clr.AddReference('System.Reflection')"
    start = "class OpenFolderDialog:\n"
    end = "\n\n_main_window_created = Event()"
    if original.count(reference) != 1 or original.count(start) != 1 or original.count(end) != 1:
        raise ValueError("Expected the pywebview 6.2.1 WinForms backend")
    src = original.replace(reference, reference + """

from pythonnet import get_runtime_info
_flowhmr_coreclr = get_runtime_info().kind == 'CoreCLR'
if _flowhmr_coreclr:
    clr.AddReference('Microsoft.Win32.SystemEvents')
    clr.AddReference('System.Drawing.Common')
""")
    before, _, dialog = src.partition(start)
    dialog, _, after = dialog.partition(end)
    modern = """if _flowhmr_coreclr:
    class OpenFolderDialog:
        @staticmethod
        def show(parent=None, initialDirectory=None, allow_multiple=False, title=None):
            picker = WinForms.FolderBrowserDialog()
            try:
                picker.InitialDirectory = initialDirectory or ''
                picker.Multiselect = allow_multiple
                if title:
                    picker.Description = title
                    picker.UseDescriptionForTitle = True
                if picker.ShowDialog(parent) == WinForms.DialogResult.OK:
                    return tuple(picker.SelectedPaths)
                return None
            finally:
                picker.Dispose()
else:
"""
    file.write_text(before + modern + indent(start + dialog, "    ") + end + after, encoding="utf-8")
