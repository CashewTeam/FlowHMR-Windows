"""Validate AMD's Windows ROCm slot before it can be enabled in FlowHMR."""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flowhmr import runtime_rocm as rocm


def main():
    site = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(site))
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")

    import torch
    import torchvision
    import torch.nn.functional as fn

    for module, release in ((torch, rocm.TORCH_VERSION), (torchvision, rocm.VISION_VERSION)):
        if not Path(module.__file__).resolve().is_relative_to(site) or module.__version__ != release:
            raise RuntimeError(f"Unexpected module origin or version: {module.__file__} {module.__version__}")
    if torch.version.rocm != rocm.VERSION or not torch.version.hip:
        raise RuntimeError(f"Expected AMD ROCm {rocm.VERSION}")

    # The CPU smoke works even on a test host without an AMD device. It never
    # marks a runtime as installed; the portable manager always tests a GPU.
    if sys.argv[2] == "--imports-only":
        assert (torch.ones(4) + 1).tolist() == [2.] * 4
        assert torchvision.ops.nms(torch.tensor([[0., 0., 10., 10.]]), torch.tensor([.9]), .5).tolist() == [0]
        print(torch.__version__, torchvision.__version__, torch.version.hip, torch.cuda.is_available())
        return

    profile = rocm.PROFILES[sys.argv[2]]
    index = rocm.gpu_ordinal(torch, profile.arch)
    device = torch.device("cuda", index)
    torch.cuda.set_device(device)
    rocm.configure_windows_hip(torch, device=device, attention=True)
    dtype = torch.bfloat16 if rocm.supports_bf16(torch, device) else torch.float16
    x = torch.ones((64, 64), dtype=dtype, device=device)
    assert (x @ x)[0, 0].item() == 64
    image = torch.ones((2, 3, 32, 32), device=device)
    weight = torch.ones((8, 3, 3, 3), device=device)
    filtered = fn.conv2d(image, weight)
    assert filtered[0, 0, 0, 0].item() == 27
    assert torch.isfinite(fn.batch_norm(filtered, None, None, training=True)).all()
    q = torch.ones((1, 4, 32, 64), dtype=dtype, device=device)
    assert fn.scaled_dot_product_attention(q, q, q).float().mean().item() == 1.0
    boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]], device=device)
    scores = torch.tensor([0.9, 0.8], device=device)
    assert torchvision.ops.nms(boxes, scores, 0.5).tolist() == [0]
    if profile.arch in rocm.RDNA1_ARCHES:
        indices = torch.tensor([[0, 1], [0, 1]], device=device)
        values = torch.tensor([2., 3.], device=device)
        dense = torch.sparse_coo_tensor(indices, values, (2, 2)).to_dense()
        assert (dense @ torch.ones(2, device=device)).tolist() == [2., 3.]
    torch.cuda.synchronize(device)
    print(torch.cuda.get_device_name(index), rocm.gpu_arch(torch.cuda.get_device_properties(device)),
          torch.__version__, dtype)


if __name__ == "__main__":
    main()
