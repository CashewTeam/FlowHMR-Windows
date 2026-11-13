"""Run the SAM image encoder on Intel XPU, with its pose decoders on the CPU."""

import torch


class XPUSAMBackbone(torch.nn.Module):
    def __init__(self, backbone, device, checkpoint_dtype):
        super().__init__()
        self.device = torch.device(device)
        if self.device.type != "xpu":
            raise ValueError("The Intel SAM backbone requires an XPU device")
        self.core = backbone
        self.checkpoint_dtype = checkpoint_dtype
        self.compute_dtype = None
        self._resident = False
        self._needs_parking = False

    def _choose_precision(self):
        if not torch.xpu.is_available():
            raise RuntimeError("No Intel XPU is available for SAM. Check the Intel graphics driver and runtime.")
        if self.device.index is None:
            self.device = torch.device("xpu", torch.xpu.current_device())
        properties = torch.xpu.get_device_properties(self.device)
        supported = {
            torch.bfloat16: getattr(properties, "has_bfloat16_conversions", False),
            torch.float16: getattr(properties, "has_fp16", False),
            torch.float32: True,
        }
        for dtype in (self.checkpoint_dtype, torch.bfloat16, torch.float16, torch.float32):
            if supported.get(dtype):
                return dtype

    def _load_accelerator(self):
        if self._resident:
            return
        if self.compute_dtype is None:
            dtype = self._choose_precision()
            # Only convert the reduced precision tensors. Keep original FP32
            # normalization and integer buffers as they were in the checkpoint.
            for tensor in (*self.core.parameters(), *self.core.buffers()):
                if tensor.dtype in (torch.bfloat16, torch.float16) and tensor.dtype != dtype:
                    tensor.data = tensor.data.to(dtype=dtype)
            self.compute_dtype = dtype
        # If a transfer runs out of memory midway, park the partially moved
        # encoder before the batch planner retries. Preserve the original error.
        self._needs_parking = True
        try:
            self.core.to(self.device)
        except (RuntimeError, OSError, MemoryError):
            try:
                self.release_accelerator()
            except (RuntimeError, OSError, MemoryError):
                pass
            raise
        self._resident = True
        print(f" [sam3d] {self.device}: DINOv3 encoder on Intel GPU ({self.compute_dtype}); pose decoders on CPU",
              flush=True)

    def release_accelerator(self):
        """Park the encoder in RAM when leaving SAM or on job cleanup."""
        if self._needs_parking:
            self._resident = False
            self.core.to("cpu")
            self._needs_parking = False
            torch.xpu.empty_cache()

    def forward(self, x, extra_embed=None):
        self._load_accelerator()
        with torch.xpu.device(self.device):
            extra = extra_embed.to(device=self.device) if extra_embed is not None else None
            features = self.core(x.to(device=self.device, dtype=self.compute_dtype), extra_embed=extra)
            features = features.to(x)
        if not torch.isfinite(features).all().item():
            raise RuntimeError("Intel XPU returned non-finite SAM features. Try another runtime or update the Intel driver.")
        return features
