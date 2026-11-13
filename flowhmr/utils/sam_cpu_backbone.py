"""Keep SAM's image encoder in system RAM while HIP runs the decoders."""

import psutil
import torch


def cpu_backbone_supported():
    capabilities = getattr(torch.cpu, "get_capabilities", lambda: {})()
    return torch.backends.mkldnn.is_available() and bool(
        capabilities.get("avx512_bf16") or capabilities.get("amx_bf16")
    )


class CPUSAMBackbone(torch.nn.Module):
    def __init__(self, backbone):
        super().__init__()
        self.core = backbone.to("cpu")
        self.threads = min(psutil.cpu_count(logical=False) or 8, 16)
        # Preserve layers already using FP32. Only FP16 activations and weights
        # switch to the CPU's native reduced precision (BF16).
        for tensor in (*self.core.parameters(), *self.core.buffers()):
            if tensor.dtype == torch.float16:
                tensor.data = tensor.data.to(torch.bfloat16)

    def _apply(self, fn, recurse=True):
        # The rest of SAM is still moved by the engine's staged placement.
        return self

    def forward(self, x, extra_embed=None):
        previous = torch.get_num_threads()
        if self.threads != previous:
            torch.set_num_threads(self.threads)
        try:
            features = self.core(x.to(device="cpu", dtype=torch.bfloat16), extra_embed=extra_embed)
            return features.to(x)
        finally:
            if self.threads != previous:
                torch.set_num_threads(previous)
