"""Adapt the pinned SAM 3D Body sources for explicit inference placement."""

import argparse
from pathlib import Path


_MHR = '''        # Load MHR itself
        if MOMENTUM_ENABLED:
            self.mhr = MHR.from_files(
                device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
                lod=1,
            )
        else:
            self.mhr = torch.jit.load(
                mhr_model_path,
                map_location=("cuda" if torch.cuda.is_available() else "cpu"),
            )'''

_MHR_WINDOWS = _MHR[:_MHR.index("            self.mhr = torch.jit.load(")] + '''            device = "cuda" if torch.cuda.is_available() else "cpu"
            if os.name == "nt" and not os.fspath(mhr_model_path).isascii():
                with open(mhr_model_path, "rb") as model_file:
                    self.mhr = torch.jit.load(model_file, map_location=device)
            else:
                self.mhr = torch.jit.load(mhr_model_path, map_location=device)'''

_MHR_PLACEMENT = '''        # Honor the loader's device before either MHR copy is materialized.
        device = torch.device(mhr_device if mhr_device is not None else
                              ("cuda" if torch.cuda.is_available() else "cpu"))
        if MOMENTUM_ENABLED:
            self.mhr = MHR.from_files(device=device, lod=1)
        elif os.name == "nt" and not os.fspath(mhr_model_path).isascii():
            # The TorchScript Windows filename overload cannot open Unicode paths.
            with open(mhr_model_path, "rb") as model_file:
                self.mhr = torch.jit.load(model_file, map_location=device)
        else:
            self.mhr = torch.jit.load(mhr_model_path, map_location=device)'''


def adapt_sam(root):
    vendor = root / "third_party/sam-3d-body/sam_3d_body"
    updated = {}

    def edit(path, before, after, *, count=1):
        path = vendor / path
        code = updated.get(path)
        if code is None:
            code = path.read_text(encoding="utf-8")
        if code.count(after) == count:
            return
        versions = (before,) if isinstance(before, str) else before
        old = next((candidate for candidate in versions if code.count(candidate) == count), None)
        if old is None:
            raise RuntimeError(f"Pinned SAM source changed: {path}; review the placement adapter.")
        updated[path] = code.replace(old, after)

    edit("build_models.py", "    model_cfg.MODEL.MHR_HEAD.MHR_MODEL_PATH = mhr_path\n",
         "    model_cfg.MODEL.MHR_HEAD.MHR_MODEL_PATH = mhr_path\n"
         "    model_cfg.MODEL.MHR_HEAD.LOAD_DEVICE = str(torch.device(device))\n")
    edit("models/heads/__init__.py", "            enable_hand_model=enable_hand_model,\n",
         "            enable_hand_model=enable_hand_model,\n"
         '            mhr_device=cfg.MODEL.MHR_HEAD.get("LOAD_DEVICE"),\n')
    edit("models/heads/mhr_head.py", "        enable_hand_model=False,\n",
         "        enable_hand_model=False,\n        mhr_device=None,\n")
    edit("models/heads/mhr_head.py", (_MHR, _MHR_WINDOWS), _MHR_PLACEMENT)
    meta = "models/meta_arch/sam3d_body.py"
    edit(meta, '                torch.meshgrid(torch.arange(H), torch.arange(W), indexing="xy"), dim=2\n'
               "            )[None, None, :, :, :]\n            .repeat(B, N, 1, 1, 1)\n            .cuda()",
         '                torch.meshgrid(torch.arange(H, device=batch["img"].device),\n'
         '                               torch.arange(W, device=batch["img"].device), indexing="xy"), dim=2\n'
         "            )[None, None, :, :, :]\n            .repeat(B, N, 1, 1, 1)")
    for hand in ("lhand", "rhand"):
        edit(meta, f'recursive_to(batch_{hand}, "cuda")', f'recursive_to(batch_{hand}, batch["img"].device)')
    for ids, occurrences in (("76, 40", 2), ("77, 41", 2)):
        edit(meta, f"torch.LongTensor([{ids}]).cuda()",
             f"torch.tensor([{ids}], device=joint_rotations.device)", count=occurrences)
    edit("sam_3d_body_estimator.py", 'batch = recursive_to(batch, "cuda")', 'batch = recursive_to(batch, self.device)')

    for path, code in updated.items():
        path.write_text(code, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[2])
    adapt_sam(parser.parse_args().root.resolve())
