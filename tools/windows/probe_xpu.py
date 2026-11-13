"""Validate the Intel XPU slot in a fresh interpreter before enabling it."""

from pathlib import Path
import sys


def probe_device(torch, index):
    """Exercise the operations used by the SAM image encoder on the selected Intel GPU."""
    from torch.nn import functional as fn
    from torch.nn.attention import SDPBackend, sdpa_kernel

    device = torch.device("xpu", index)
    prop = torch.xpu.get_device_properties(index)
    dtype = (torch.bfloat16 if getattr(prop, "has_bfloat16_conversions", False) else
             torch.float16 if getattr(prop, "has_fp16", False) else torch.float32)
    with torch.inference_mode(), torch.xpu.device(device):
        x = torch.ones((64, 64), dtype=dtype, device=device)
        assert (x @ x)[0, 0].item() == 64
        image = torch.ones((1, 3, 32, 32), dtype=dtype, device=device)
        weight = torch.ones((8, 3, 4, 4), dtype=dtype, device=device)
        patches = fn.conv2d(image, weight, stride=4).flatten(2).transpose(1, 2).contiguous()
        assert patches[0, 0, 0].item() == 48
        patches = patches + torch.arange(8, device=device, dtype=torch.float32).to(dtype)
        normalized = fn.layer_norm(patches, (8,), torch.ones(8, dtype=dtype, device=device),
                                   torch.zeros(8, dtype=dtype, device=device))
        assert torch.isfinite(normalized).all().item() and normalized.abs().max().item() > 1
        q = torch.ones((1, 4, 32, 64), dtype=dtype, device=device)
        assert fn.scaled_dot_product_attention(q, q, q).float().mean().item() == 1.0
        with sdpa_kernel(SDPBackend.MATH):
            attention = fn.scaled_dot_product_attention(q, q, q)
        assert attention.device == device and attention.float().mean().item() == 1.0
        torch.xpu.synchronize(device)
    return dtype


def main():
    root = Path(__file__).resolve().parents[2]
    site = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(site), str(root)]
    from flowhmr import runtime_xpu as xpu

    mode = sys.argv[2]
    if mode not in (xpu.PROFILE, "--imports-only"):
        raise ValueError(f"Unknown XPU probe mode: {mode}")
    xpu.configure_process(site)

    import elftools
    import torch
    import torchvision
    import triton

    for module, release in ((torch, xpu.TORCH_VERSION), (torchvision, xpu.VISION_VERSION),
                            (triton, "3.7.1"), (elftools, "0.33")):
        if not Path(module.__file__).resolve().is_relative_to(site) or module.__version__ != release:
            raise RuntimeError(f"Unexpected module origin or version: {module.__file__} {module.__version__}")
    if not torch.version.xpu or torch.version.cuda or torch.version.hip:
        raise RuntimeError("Expected an XPU build of PyTorch")
    # Person detection and NMS run on the CPU in this backend.
    boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]])
    scores = torch.tensor([.9, .8])
    assert torchvision.ops.nms(boxes, scores, .5).tolist() == [0]
    assert (torch.ones((4, 4)) @ torch.ones((4, 4)))[0, 0].item() == 4
    if mode == "--imports-only":
        # Developer-only smoke; the installer never uses this mode or marks this slot ready.
        print("Isolated imports and CPU operators passed:", torch.__version__, torchvision.__version__)
        return

    index = xpu.xpu_ordinal(torch)
    dtype = probe_device(torch, index)
    print(torch.xpu.get_device_name(index), f"xpu:{index}", torch.__version__, dtype)


if __name__ == "__main__":
    main()
