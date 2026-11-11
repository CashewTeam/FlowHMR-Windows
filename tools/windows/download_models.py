"""Download FlowHMR resources into models/ from verified public sources."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flowhmr import model_files as files
from flowhmr import model_sources as sources


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_url(url, relative, expected_size=None, sha256=None):
    destination = files.resolve_model(relative)
    if (files.file_present(destination)
            and (expected_size is None or destination.stat().st_size == expected_size)
            and (sha256 is None or checksum(destination) == sha256)):
        print(f"Present: {destination}", flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"Downloading: {relative}", flush=True)
    offset = temporary.stat().st_size if temporary.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        append = offset > 0 and response.status == 206
        if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise RuntimeError("Invalid download resume response")
        written = offset if append else 0
        next_progress = written + 128 * 1024 * 1024
        with temporary.open("ab" if append else "wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                written += len(chunk)
                if written >= next_progress:
                    print(f"  {relative}: {written / (1024**2):.0f} MiB", flush=True)
                    next_progress = written + 128 * 1024 * 1024
    if temporary.stat().st_size < 1:
        raise RuntimeError(f"Unexpectedly small download: {relative}")
    if expected_size is not None and temporary.stat().st_size != expected_size:
        raise RuntimeError(f"Size mismatch: {relative}")
    if sha256 is not None and checksum(temporary) != sha256:
        raise RuntimeError(f"SHA-256 mismatch: {relative}; remove the .part file before retrying")
    temporary.replace(destination)
    print(f"Saved: {relative} ({destination.stat().st_size:,} bytes)", flush=True)


def download_flowhmr():
    repo = sources.FLOWHMR_REPO
    for host in (sources.HF_MIRROR, sources.HF_OFFICIAL):
        try:
            with urllib.request.urlopen(f"{host}/api/models/{repo}?blobs=true", timeout=30) as response:
                manifest = json.load(response)
            revision = manifest["sha"]
            print(f"Downloading {repo} @ {revision}", flush=True)
            for item in manifest["siblings"]:
                filename = item["rfilename"]
                if not (filename.startswith("flowhmr_latest/") or filename in ("config.json", "README.md", "LICENSE")):
                    continue
                target = filename if filename.startswith("flowhmr_latest/") else f"flowhmr_latest/{filename}"
                download_url(f"{host}/{repo}/resolve/{revision}/{filename}",
                             target, item["size"], item.get("lfs", {}).get("sha256"))
            (files.MODEL_ROOT / "flowhmr_latest/download-revision.txt").write_text(f"{repo}\n{revision}\n", encoding="utf-8")
            return
        except Exception as exc:
            if host == sources.HF_OFFICIAL:
                raise
            print(f"Mirror unavailable: {type(exc).__name__}: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--public-only", action="store_true", help="Legacy option: skip the perception downloads")
    mode.add_argument("--direct-only", action="store_true", help="Only YOLOX and the joint regressor")
    mode.add_argument("--perception-only", action="store_true", help="Only SAM 3D Body and VGGT-Omega from ModelScope")
    args = parser.parse_args()
    files.ensure_model_folders()
    failures = []

    def fetch(url, target, *checks):
        try:
            download_url(url, target, *checks)
        except Exception as exc:
            failures.append(target)
            print(f"Failed {target}: {type(exc).__name__}: {exc}", flush=True)

    if not args.perception_only:
        fetch(sources.YOLOX_URL, files.YOLOX)
        fetch(sources.REGRESSOR_URL, files.JOINT_REGRESSOR)
        if not args.direct_only:
            try:
                download_flowhmr()
            except Exception as exc:
                failures.append(sources.FLOWHMR_REPO)
                print(f"Failed FlowHMR: {type(exc).__name__}: {exc}", flush=True)
    if not args.direct_only and not args.public_only:
        for repo, name, target, size, sha in sources.PERCEPTION_DOWNLOADS:
            fetch(sources.modelscope_file(repo, name), target, size, sha)
    if files.smplh_reference() is None:
        entry, archive = sources.MANO_DOWNLOAD
        print(f"SMPL-H: sign in at {sources.MANO_URL} and download '{entry}' ({archive}).\n"
              f"Extract into {files.MODEL_ROOT} or import the extracted folder in the desktop app.\n"
              "An installed male or female body is sufficient. Extended SMPL+H neutral is optional.", flush=True)
    if failures:
        print("Unfinished downloads: " + ", ".join(failures), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
