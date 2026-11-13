"""Assemble a clean Windows release with optional downloadable CUDA runtimes."""

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

if __package__:
    from .adapt_sam import adapt_sam
    from .desktop_runtime_build import adapt_winforms, stage_dotnet_runtime
    from .desktop_update import PACKAGE, UPDATE_INFO, VERSION_FILE, read_version, release_version
    from .install_fbx import WHEEL_NAME as FBX_WHEEL_NAME, binding_from_wheel
else:
    from adapt_sam import adapt_sam
    from desktop_runtime_build import adapt_winforms, stage_dotnet_runtime
    from desktop_update import PACKAGE, UPDATE_INFO, VERSION_FILE, read_version, release_version
    from install_fbx import WHEEL_NAME as FBX_WHEEL_NAME, binding_from_wheel


ROOT = Path(__file__).resolve().parents[2]
SUBMODULES = ("YOLOX", "sam-3d-body", "vggt-omega")
SUBMODULE_DEMOS = {"YOLOX": "assets/", "sam-3d-body": "assets/qualitative_comparisons/", "vggt-omega": "examples/"}
DINOV3_REVISION = "6876159a11b4df116f30f667f8c9888617df0751"
PREREQUISITES = ("MicrosoftEdgeWebView2RuntimeInstallerX64.exe", "vc_redist.x64.exe")
UPSTREAM_EXAMPLE = Path("data/rendering_data/examples/motions/flowhmr_demo.npz")
FFMPEG_SOURCE_NAME = "FFmpeg-9.0.2-Source.tar.gz"
FFMPEG_SOURCE_SHA256 = "86f82e82456005881d1b97e9ce02f8a40bf2d181c8c958f8ae736886fdba2a34"
PAYLOAD = "FlowHMR-Windows-cu128"
VOLUME_SIZE = 1_900_000_000  # GitHub requires each release asset to be under 2 GiB.
CHUNK = 8 * 1024 * 1024


def git(*args, cwd=ROOT, env=None):
    return subprocess.check_output(("git", *args), cwd=cwd, env=env)


def git_files(*args, cwd=ROOT):
    return (Path(name.decode("utf-8")) for name in git("ls-files", "-z", *args, cwd=cwd).split(b"\0") if name)


def source_files():
    files = sorted({name for name in git_files("--cached", "--others", "--exclude-standard")
                    if (ROOT / name).is_file()})
    for name in files:
        path = ROOT / name
        original_example = name == UPSTREAM_EXAMPLE and not git("diff", "--name-only", "HEAD", "--", name.as_posix()).strip()
        if (not path.resolve().is_relative_to(ROOT) or path.stat().st_size >= 100_000_000
                or any(part in {".cache", ".venv", ".runtime", "runtime", "logs", "output", "dist", ".git"} for part in name.parts)
                or (name.parts[0] == "models" and name.as_posix() != "models/README.md")
                or (not original_example and name.suffix.lower() in {".ckpt", ".pth", ".pt", ".pkl", ".npz", ".exe", ".lnk", ".whl"})):
            raise ValueError(f"Unexpected file in the public source tree: {name}")
    return files


def copy_files(origin, destination, names):
    for name in names:
        source = origin / name
        if not source.is_file():
            continue  # A submodule gitlink is not a file.
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_runtime(target, prerequisites, *, bundle_cuda=False):
    try:
        mcp_version = version("mcp")
    except PackageNotFoundError:
        mcp_version = None
    if mcp_version != "1.28.0":
        raise RuntimeError("Install the locked Windows requirements before staging the MCP runtime.")
    if (sys.platform != "win32" or sys.version_info[:2] != (3, 10) or struct.calcsize("P") != 8
            or version("torch") != "2.8.0+cu128" or version("torchvision") != "0.23.0+cu128"
            or version("imageio-ffmpeg") != "0.8.1"):
        raise RuntimeError("Build from the tested Windows Python 3.10 x64 cu128 environment.")
    base = Path(sys.base_prefix).resolve()
    site = Path(sys.prefix) / "Lib/site-packages"
    # Always stage the known x64 runtime from the verified build input.
    fbx_binding = binding_from_wheel((prerequisites / FBX_WHEEL_NAME).read_bytes())

    def base_ignore(directory, names):
        relative = Path(directory).relative_to(base)
        ignored = {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}
        if relative == Path("."):
            ignored.update({"include", "libs", "Scripts"})
        if relative == Path("Lib"):
            ignored.update({"site-packages", "test"})
        return ignored

    def site_ignore(directory, names):
        relative = Path(directory).relative_to(site)
        ignored = {name for name in names if name in {"__pycache__", "direct_url.json", "uv_cache.json"}
                   or name.endswith((".pyc", ".pyo"))}
        if relative == Path("."):
            ignored.update(name for name in names if name.startswith(("__editable__", "_virtualenv", "fbxsdkpy-")))
            ignored.update(name for name in names if name in {"av", "av.libs"} or name.startswith("av-"))
            ignored.update(name for name in names if name.endswith(".whl"))  # Leftover build downloads, not installed packages.
            # Use only the verified release input, never the developer's installed binding or SDK samples.
            ignored.update({"fbx.pyd", "FbxCommon.py"})
            ignored.update(name for name in names if name == "cmake" or name.startswith("cmake-"))
            if not bundle_cuda:
                ignored.update(name for name in names if name in {"torch", "torchgen", "functorch", "torchvision", "torchvision.libs"}
                               or name.startswith(("torch-", "torchvision-")))
        elif relative == Path("torch"):
            ignored.update({"include", "share"})
        elif relative == Path("torch/lib"):
            ignored.update(name for name in names if name.lower().endswith(".lib"))
        return ignored

    print("Copying Python and desktop dependencies…", flush=True)
    shutil.copytree(base, target, ignore=base_ignore)
    target_site = target / "Lib/site-packages"
    shutil.copytree(site, target_site, ignore=site_ignore)
    adapt_winforms(target_site)
    stage_dotnet_runtime(target / "dotnet", prerequisites)
    (target_site / "fbx.pyd").write_bytes(fbx_binding)
    (target_site / "flowhmr_yolox.pth").write_text("../../../third_party/YOLOX\n", encoding="ascii")
    prereq_dir = target / "prerequisites"
    prereq_dir.mkdir()
    for name in PREREQUISITES:
        source = prerequisites / name
        with source.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError(f"Invalid Microsoft installer: {name}")
        shutil.copy2(source, prereq_dir / name)
    gpu_info = (f"PyTorch {version('torch')}\nTorchVision {version('torchvision')}\n" if bundle_cuda
                else "PyTorch and TorchVision are installed on demand from the Runtime Environment page.\n")
    (target / "BUILD-INFO.txt").write_text(f"CPython {sys.version.split()[0]} x64\n" + gpu_info, encoding="ascii")
    subprocess.run((str(target / "python.exe"), "-I", "-c",
                    "from mcp.server.fastmcp import FastMCP; import pydantic, pywintypes"),
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def copy_dinov3(destination, source):
    if (git("rev-parse", "HEAD", cwd=source).decode().strip() != DINOV3_REVISION
            or git("diff", "--name-only", "HEAD", cwd=source).strip()):
        raise RuntimeError("Use a clean checkout of DINOv3 revision " + DINOV3_REVISION)
    copy_files(source, destination / "third_party/dinov3", (
        path for path in git_files("--cached", cwd=source)
        if path.parts[0] in {"dinov3", "hubconf.py", "LICENSE.md", "README.md", "MODEL_CARD.md"}
    ))
    # Adapt only the staged SAM source. The developer checkout stays on its upstream gitlink.
    backbone = destination / "third_party/sam-3d-body/sam_3d_body/models/backbones/dinov3.py"
    code = backbone.read_text(encoding="utf-8")
    original = '        self.encoder = torch.hub.load(\n            "facebookresearch/dinov3",\n            self.name,\n            source="github",'
    if code.count(original) != 1:
        raise RuntimeError("The pinned SAM DINOv3 loader changed; review the offline adapter.")
    replacement = ('        repository = Path(__file__).resolve().parents[4] / "dinov3"\n'
                   '        self.encoder = torch.hub.load(\n'
                   '            str(repository),\n            self.name,\n            source="local",')
    backbone.write_text(code.replace("import torch\n", "from pathlib import Path\n\nimport torch\n", 1)
                        .replace(original, replacement), encoding="utf-8")


def stage_release(destination, prerequisites, dinov3_source, *, bundle_cuda=False, version=None):
    if version is not None:
        release_version(version)
    files = source_files()
    destination.mkdir(parents=True, exist_ok=False)
    copy_files(ROOT, destination, files)
    for name in SUBMODULES:
        source = ROOT / "third_party" / name
        expected = git("rev-parse", f"HEAD:third_party/{name}").strip()
        if git("rev-parse", "HEAD", cwd=source).strip() != expected or git("diff", "--name-only", "HEAD", cwd=source).strip():
            raise RuntimeError(f"The {name} submodule does not match the pinned upstream revision.")
        copy_files(source, destination / "third_party" / name,
                   (path for path in git_files("--cached", cwd=source)
                    if not path.as_posix().startswith(SUBMODULE_DEMOS[name])))
    copy_dinov3(destination, dinov3_source)
    adapt_sam(destination)
    from flowhmr.hamer_setup import runtime_installed

    if not runtime_installed():
        raise RuntimeError("Install the pinned HaMeR code before building the portable bundle.")
    shutil.copytree(ROOT / "third_party/hamer_inference", destination / "third_party/hamer_inference",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy_runtime(destination / "runtime", prerequisites, bundle_cuda=bundle_cuda)
    if version is not None and not bundle_cuda:
        (destination / VERSION_FILE).write_text(version + "\n", encoding="ascii")
    subprocess.run(("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "tools/windows/build-exe.ps1"), "-Destination", str(destination / "FlowHMR.exe")),
                   cwd=ROOT, check=True)
    print(f"Portable staging ready: {destination}", flush=True)


def build_source_assets(output):
    """Build a patch and an archive without changing the user's Git index or refs."""
    with tempfile.TemporaryDirectory(prefix="source-index-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        objects = scratch / "objects"
        objects.mkdir()
        original_objects = Path(git("rev-parse", "--git-path", "objects").decode().strip())
        env = {**os.environ, "GIT_INDEX_FILE": str(scratch / "index"), "GIT_OBJECT_DIRECTORY": str(objects),
               "GIT_ALTERNATE_OBJECT_DIRECTORIES": str((ROOT / original_objects).resolve())}
        git("read-tree", "HEAD", env=env)
        git("-c", "core.autocrlf=false", "add", "-A", env=env)
        tree = git("write-tree", env=env).decode().strip()
        included = set(git("ls-tree", "-r", "--name-only", "-z", tree, env=env).decode().split("\0"))
        paths = {p.as_posix() for p in source_files()}
        gitlinks = {line.split(b"\t", 1)[1].decode() for line in git("ls-files", "--stage").splitlines()
                    if line.startswith(b"160000 ")}
        if included - {""} != paths | gitlinks:
            raise RuntimeError("The staged tree did not match the audited public files.")
        (output / "FlowHMR-Windows.patch").write_bytes(git("diff", "--cached", "--binary", "--no-ext-diff", "HEAD", env=env))
        subprocess.run(("git", "archive", "--format=zip", "--prefix=FlowHMR/",
                        "--output=" + str(output / "FlowHMR-Windows-Source.zip"), tree), cwd=ROOT, env=env, check=True)


def pack_release(stage, output, ffmpeg_source):
    if checksum(ffmpeg_source) != FFMPEG_SOURCE_SHA256:
        raise ValueError("The pinned FFmpeg source archive SHA-256 did not match.")
    output.mkdir(parents=True, exist_ok=False)
    build_source_assets(output)
    print("Compressing the portable environment…", flush=True)
    bundle_cuda = (stage / "runtime/Lib/site-packages/torch/lib/torch_cuda.dll").is_file()
    with tempfile.TemporaryDirectory(prefix="windows-package-", dir=output.parent) as temporary:
        work = Path(temporary)
        archive = work / ((PAYLOAD if bundle_cuda else "FlowHMR-Windows") + ".zip")
        seven_zip = shutil.which("7z") or Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip/7z.exe"
        if bundle_cuda and Path(seven_zip).is_file():
            subprocess.run((str(seven_zip), "a", "-tzip", "-mm=Deflate", "-mx=3", "-mmt=on", "-bso0",
                            str(archive), "."), cwd=stage, check=True)
        else:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zip_file:
                for path in stage.rglob("*"):
                    if path.is_file():
                        relative = path.relative_to(stage)
                        zip_file.write(path, relative if bundle_cuda else Path("FlowHMR") / relative)
        if archive.stat().st_size <= VOLUME_SIZE:
            shutil.move(archive, output / archive.name)
        elif not bundle_cuda:
            raise RuntimeError("The lightweight ZIP exceeded the GitHub release asset size limit.")
        else:
            volumes = []
            remaining = archive.stat().st_size
            with archive.open("rb") as source:
                while remaining:
                    destination = output / f"{PAYLOAD}.{len(volumes) + 1:03d}"
                    count = min(VOLUME_SIZE, remaining)
                    remaining -= count
                    with destination.open("wb") as target:
                        while count:
                            data = source.read(min(CHUNK, count))
                            if not data:
                                raise EOFError(archive)
                            target.write(data)
                            count -= len(data)
                    volumes.append(destination)
            manifest = work / "volumes.txt"
            manifest.write_text("\n".join(f"{p.name}|{p.stat().st_size}|{checksum(p)}" for p in volumes) + "\n", encoding="ascii")
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
            subprocess.run((str(compiler), "/nologo", "/codepage:65001", "/target:winexe", "/platform:x64", "/optimize+",
                            "/reference:System.Windows.Forms.dll", "/reference:System.IO.Compression.dll",
                            f"/resource:{manifest},FlowHMR.volumes", f"/win32icon:{ROOT / 'assets/flowhmr.ico'}",
                            f"/out:{output / 'FlowHMR-Windows-Setup.exe'}", str(ROOT / "tools/windows/FlowHMRInstaller.cs")),
                           cwd=ROOT, check=True)
    shutil.copy2(ROOT / "docs/release-windows.md", output / "README-Windows.md")
    shutil.copy2(ffmpeg_source, output / FFMPEG_SOURCE_NAME)
    if not bundle_cuda and (build_version := read_version(stage)) is not None:
        (output / UPDATE_INFO).write_text(json.dumps({"version": build_version, "package": PACKAGE,
                                                      "sha256": checksum(output / PACKAGE)}, indent=2) + "\n",
                                          encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{checksum(p)}  {p.name}\n" for p in sorted(output.iterdir()) if p.is_file()), encoding="utf-8",
    )
    print(f"Release assets ready: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("destination", type=Path)
    stage.add_argument("--prerequisites", type=Path, required=True)
    stage.add_argument("--dinov3-source", type=Path, required=True)
    stage.add_argument("--bundle-cuda", action="store_true", help="include the current cu128 environment for an offline bundle")
    stage.add_argument("--version", help="Windows release version (X.Y.Z) for the online portable updater")
    pack = sub.add_parser("pack")
    pack.add_argument("stage", type=Path)
    pack.add_argument("output", type=Path)
    pack.add_argument("--ffmpeg-source", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    if args.action == "stage":
        stage_release(args.destination.resolve(), args.prerequisites.resolve(), args.dinov3_source.resolve(),
                      bundle_cuda=args.bundle_cuda, version=args.version)
    else:
        pack_release(args.stage.resolve(), args.output.resolve(), args.ffmpeg_source.resolve())


if __name__ == "__main__":
    main()
