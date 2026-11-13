"""Validate a freshly installed Windows PyTorch environment in a clean process."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from flowhmr.runtime_environments import cuda_ordinal


def main():
    directory = Path(sys.argv[1]).resolve()
    variant = sys.argv[2]
    sys.path.insert(0, str(directory))

    import torch
    import torchvision

    releases = sys.argv[3:5]
    for module, release in zip((torch, torchvision), releases, strict=True):
        if not Path(module.__file__).resolve().is_relative_to(directory) or module.__version__ != f"{release}+{variant}":
            raise RuntimeError(f"Unexpected module origin or version: {module.__file__} {module.__version__}")
    if variant == "cpu":
        if torch.version.cuda or torch.version.hip or torch.cuda.is_available():
            raise RuntimeError("Expected a CPU-only build of PyTorch")
        device = torch.device("cpu")
    else:
        expected = f"{variant[2:-1]}.{variant[-1]}"
        if torch.version.cuda != expected or not torch.cuda.is_available():
            raise RuntimeError(f"CUDA {expected} is unavailable")
        device = torch.device("cuda", cuda_ordinal(torch, variant))
        torch.cuda.set_device(device)
    x = torch.ones((128, 128), device=device)
    assert (x @ x)[0, 0].item() == 128
    image = torch.ones((1, 3, 32, 32), device=device)
    weight = torch.ones((8, 3, 3, 3), device=device)
    assert torch.nn.functional.conv2d(image, weight)[0, 0, 0, 0].item() == 27
    boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]], device=device)
    scores = torch.tensor([0.9, 0.8], device=device)
    assert torchvision.ops.nms(boxes, scores, 0.5).tolist() == [0]
    q = torch.ones((1, 2, 8, 32), device=device,
                   dtype=torch.float16 if device.type == "cuda" else torch.float32)
    assert torch.nn.functional.scaled_dot_product_attention(q, q, q).float().mean().item() == 1.0
    if device.type == "cuda":
        torch.cuda.synchronize()
    print(torch.cuda.get_device_name(device) if device.type == "cuda" else "CPU", torch.__version__)


if __name__ == "__main__":
    main()
