"""Run optional HaMeR finger refinement with FlowHMR's own environment and models."""

from collections import deque
from importlib.util import find_spec
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Thread
import json
import os
import subprocess
import sys
import time

from flowhmr.hamer_setup import runtime_installed
from flowhmr.model_files import HAMER_FILES, MANO_FILES, file_present, resolve_model


MISSING_HAMER = "HaMeR 手指精修缺少运行环境或模型，请在右上角「模型」查看。"
PROGRESS_PREFIX = "@@HAMER@@"
HAMER_STRIDES = (1, 2, 3, 4)
HAMER_ISSUES = {
    "hamer_gpu_required": "手指精修需要 GPU 推理环境。",
    "hamer_runtime_missing": "请先在「运行环境」中下载并启用 GPU 推理环境。",
    "hamer_code_missing": "缺少 HaMeR 推理组件。请在「模型」中下载。",
    "hamer_mano_missing": "缺少 MANO v1.2。请从 MANO 官网下载「Models & Code（mano_v1_2.zip）」并导入。",
    "hamer_mano_invalid": "MANO_RIGHT.pkl 不完整或不是原版 MANO v1.2。请用 MANO 官网「Models & Code（mano_v1_2.zip）」中的原文件替换。",
}


def _mano_v12_issue(path):
    """Check the official hand file's container without unpickling it on UI polls."""
    try:
        if not path.is_file():
            return "hamer_mano_missing"
        with path.open("rb") as stream:
            # Reject truncated downloads, renamed ZIPs and the much larger SMPL-H
            # body files. The worker checks the actual MANO array shapes.
            if not (1 << 20) <= os.fstat(stream.fileno()).st_size <= (16 << 20):
                return "hamer_mano_invalid"
            prefix = stream.read(64)
            stream.seek(-1, os.SEEK_END)
            if not prefix.startswith(b"\x80\x02}") or b"numpy" not in prefix or stream.read(1) != b".":
                return "hamer_mano_invalid"
    except FileNotFoundError:
        return "hamer_mano_missing"
    except OSError:
        return "hamer_mano_invalid"
    return None


def hamer_model_details(device):
    paths = tuple(map(resolve_model, HAMER_FILES))
    if str(device).split(":", 1)[0] != "cuda":
        issue = "hamer_gpu_required"
    elif find_spec("torch") is None:
        issue = "hamer_runtime_missing"
    elif not runtime_installed() or any(find_spec(name) is None for name in ("timm", "einops", "yacs", "scipy", "cv2")):
        issue = "hamer_code_missing"
    else:
        issue = _mano_v12_issue(resolve_model(MANO_FILES[1]))
    return paths, issue, HAMER_ISSUES.get(issue)


def hamer_model_status(device):
    paths, _, message = hamer_model_details(device)
    return paths, message


def hamer_available(device):
    paths, issue = hamer_model_status(device)
    return issue is None and all(map(file_present, paths))


def load_hand_boxes(path, frames):
    import numpy as np

    try:
        boxes = np.load(path, allow_pickle=False)
        if isinstance(boxes, np.lib.npyio.NpzFile):
            boxes.close()
        elif boxes.shape == (frames, 2, 4) and boxes.dtype == np.float32:
            return boxes
    except (OSError, ValueError, EOFError):
        pass
    return None


def merge_fingers(output, rotations, valid):
    """Overlay only the 15 local finger joints of each hand; keep both wrists."""
    import numpy as np
    import torch

    pose = output["rot6d"]
    frames = pose.shape[1]
    if pose.shape != (1, frames, 52, 6):
        raise ValueError("HaMeR expects one SMPL-H motion with 52 joints")
    if (rotations.shape != (frames, 2, 15, 3, 3) or rotations.dtype != np.float32
            or valid.shape != (frames, 2) or valid.dtype != np.bool_
            or not np.isfinite(rotations).all()
            or not np.allclose(rotations.swapaxes(-1, -2) @ rotations, np.eye(3), atol=2e-4)
            or np.any(np.linalg.det(rotations) < .999)):
        raise ValueError("HaMeR returned invalid finger rotations")
    if not valid.any():
        return output

    result = dict(output)
    result["rot6d"] = pose.clone()
    for side, offset in enumerate((22, 37)):
        mask = torch.from_numpy(valid[:, side]).to(device=pose.device)
        # FlowHMR's 6D representation stores the first two matrix columns.
        hand = rotations[valid[:, side], side, :, :, :2].reshape(-1, 15, 6)
        result["rot6d"][0, mask, offset:offset + 15] = torch.from_numpy(hand).to(pose)
    result["body_rot6d"] = result["rot6d"][:, :, 1:]
    result.pop("local_joints_positions", None)  # Derived from the old finger rotations.
    return result


def refine_fingers(video_path, hand_boxes_path, start_frame, output, *, device,
                   stride=1, cancel_check=None, on_progress=None):
    import numpy as np
    import torch

    from flowhmr.runtime_environments import RUNTIME
    from flowhmr.runtime_rocm import uses_shared_memory
    from flowhmr.utils.sam_batch import MIB, require_shared_ram

    if type(stride) is not int or stride not in HAMER_STRIDES:
        raise ValueError("HaMeR 采样间隔须为 1、2、3 或 4 帧。")
    model_paths, issue = hamer_model_status(device)
    if issue:
        raise RuntimeError(issue)
    if not all(map(file_present, model_paths)):
        raise RuntimeError(MISSING_HAMER)
    device = torch.device(device)
    with torch.cuda.device(device):
        gpu = torch.cuda.current_device()
        torch.cuda.empty_cache()
    if uses_shared_memory(torch, device):
        require_shared_ram(2 * model_paths[0].stat().st_size + 512 * MIB)
    video = Path(video_path).resolve()
    worker = Path(__file__).with_name("runtime_hamer_worker.py")
    frames = output["rot6d"].shape[1]
    if load_hand_boxes(hand_boxes_path, frames) is None:
        raise ValueError("HaMeR 手框与待精修的动作帧数不一致。")

    started = time.monotonic()
    print(f"  [hamer] sample every {stride} frame(s), {frames} frames from {start_frame}; keep FlowHMR body/wrists/trajectory")
    with TemporaryDirectory(prefix="hamer-", dir=video.parent) as temp:
        target = Path(temp) / "fingers.npz"
        command = [sys.executable, "-I", "-B", "-X", "utf8", "-u", str(worker), str(video),
                   str(Path(hand_boxes_path).resolve()), str(start_frame), str(gpu), str(target), str(stride)]
        if RUNTIME.active is not None:
            command.append(RUNTIME.active)  # Keep the parent's GPU runtime in the isolated interpreter.
        events = Queue()
        tail = deque(maxlen=24)
        with subprocess.Popen(command, cwd=temp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
            def read_output():
                try:
                    for line in child.stdout:
                        if line.startswith(PROGRESS_PREFIX):
                            events.put(json.loads(line[len(PROGRESS_PREFIX):]))
                        else:
                            tail.append(line.rstrip())
                finally:
                    events.put(None)

            reader = Thread(target=read_output, daemon=True)
            reader.start()
            try:
                while True:
                    if cancel_check is not None:
                        cancel_check()
                    try:
                        event = events.get(timeout=.2)
                    except Empty:
                        continue
                    if event is None:
                        break
                    if on_progress is not None:
                        on_progress(event["frame"], frames, event["message"])
                if child.wait() != 0:
                    detail = "\n".join(tail)[-4000:]
                    print(f"  [hamer] worker error:\n{detail}", flush=True)
                    raise RuntimeError(f"HaMeR 手指精修失败：{next((line for line in reversed(tail) if line), '请查看后台日志。')}")
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                reader.join()

        with np.load(target, allow_pickle=False) as payload:
            rotations, valid, sampled = payload["rotations"], payload["valid"], payload["sampled"]
        if sampled.shape != (frames, 2) or sampled.dtype != np.bool_:
            raise ValueError("HaMeR returned invalid sampling data")
        result = merge_fingers(output, rotations, valid)
    report = {"frames": frames, "left": int(valid[:, 0].sum()), "right": int(valid[:, 1].sum()),
              "stride": stride, "sampled_left": int(sampled[:, 0].sum()), "sampled_right": int(sampled[:, 1].sum()),
              "seconds": round(time.monotonic() - started, 2)}
    print(f"  [hamer] sampled L={report['sampled_left']}, R={report['sampled_right']}; "
          f"covered L={report['left']}/{frames}, R={report['right']}/{frames}; {report['seconds']:.2f}s")
    return result, report
