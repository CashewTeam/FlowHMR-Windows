"""Check the Windows environment and required local inference assets."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface"))
os.environ.setdefault("TORCH_HOME", str(ROOT / ".cache/torch"))

from flowhmr.model_files import MODEL_ROOT, VGGT, core_model_paths, file_present, missing_model_paths, resolve_model
from flowhmr.runtime_environments import RUNTIME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-only", action="store_true")
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    result = {"python": sys.version.split()[0], "errors": [], "optional": {}}
    RUNTIME.configure_process()
    device_type = RUNTIME.default_device()
    result["runtime"] = RUNTIME.summary()
    if not args.models_only and not result["runtime"]["ready"]:
        result["errors"].append("请在 FlowHMR 右上角「运行环境」下载并启用推理环境。")
    if not args.models_only and result["runtime"]["ready"]:
        for name in ["torch", "torchvision", "numpy", "cv2", "flask", "yolox",
                     "supervision", "flowhmr.pipeline.v2m_inference",
                     "flowhmr.utils.runtime_sam_features", "flowhmr.utils.runtime_vggt_camera"]:
            try:
                importlib.import_module(name)
            except Exception as exc:
                result["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
        try:
            import torch
            import torchvision
            result["torch"] = torch.__version__
            result["cuda_runtime"] = torch.version.cuda
            result["hip_runtime"] = torch.version.hip
            from flowhmr.runtime_environments import cuda_ordinal
            from flowhmr import runtime_rocm, runtime_xpu

            device = torch.device(device_type)
            if device_type == "cpu":
                if torch.version.cuda or torch.version.hip or torch.cuda.is_available():
                    raise RuntimeError("Expected a CPU-only build of PyTorch")
            elif device_type == "xpu":
                from probe_xpu import probe_device

                index = runtime_xpu.xpu_ordinal(torch)
                probe_device(torch, index)
                result["gpu"] = torch.xpu.get_device_name(index)
                result["xpu_encoder_probe"] = "passed"
                device = torch.device("cpu")  # The detector and NMS run on the host.
            else:
                if not torch.cuda.is_available():
                    raise RuntimeError("GPU acceleration is unavailable")
                if torch.version.hip:
                    profile = runtime_rocm.PROFILES.get(RUNTIME.active)
                    index = runtime_rocm.gpu_ordinal(torch, profile.arch if profile else None)
                else:
                    variant = RUNTIME.cuda_variant()
                    index = cuda_ordinal(torch, variant) if variant else 0
                device = torch.device("cuda", index)
                runtime_rocm.configure_windows_hip(torch, device=device, attention=True)
                result["gpu"] = torch.cuda.get_device_name(device)
                result["gpu_capability"] = list(torch.cuda.get_device_capability(device))
            x = torch.randn(256, 256, device=device)
            assert torch.isfinite(x @ x).all().item()
            boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]], device=device)
            scores = torch.tensor([0.9, 0.8], device=device)
            assert torchvision.ops.nms(boxes, scores, 0.5).tolist() == [0]
            if device_type == "cuda":
                torch.cuda.synchronize(device)
            result[f"{device.type}_matmul_and_nms"] = "passed"
        except Exception as exc:
            result["errors"].append(f"{device_type}: {type(exc).__name__}: {exc}")
        result["ffmpeg"] = shutil.which("ffmpeg")
        if result["ffmpeg"] is None:
            try:
                import imageio_ffmpeg
                result["ffmpeg"] = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception as exc:
                result["errors"].append(f"FFmpeg: {exc}")
        for name in ["fbx", "motion_correction", "rgbmocap_correction"]:
            try:
                importlib.import_module(name)
                result["optional"][name] = "available"
            except Exception as exc:
                result["optional"][name] = f"unavailable: {type(exc).__name__}: {exc}"
        if result["optional"]["rgbmocap_correction"] == "available":
            from flowhmr.utils.runtime_capabilities import native_correction_available
            result["optional"]["contact_correction"] = "available" if native_correction_available() else "native solver unavailable"
    if not args.environment_only:
        from flowhmr.model_inventory import model_inventory

        result["model_directory"] = str(MODEL_ROOT)
        result["missing_models"] = missing_model_paths(core_model_paths())
        result["moving_camera_model"] = "available" if file_present(resolve_model(VGGT)) else "missing (required)"
        result["model_files"] = model_inventory(device_type)["items"]
    result["ready"] = not result["errors"] and not result.get("missing_models")
    report = json.dumps(result, ensure_ascii=False, indent=2)
    print(report)
    if not args.models_only:
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs/install-check.json").write_text(report + "\n", encoding="utf-8")
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
