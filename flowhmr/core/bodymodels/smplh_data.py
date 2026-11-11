"""Read one original SMPL-H geometry reference for inference, preview, and export."""

from functools import lru_cache
from pathlib import Path
import pickle
from zipfile import BadZipFile

import numpy as np

from ...model_files import SMPLH_REFERENCE_ORDER, smplh_available, smplh_reference_path
from .licensed_data import read_registered_arrays


_SHAPES = {
    "v_template": (6890, 3),
    "J_regressor": (52, 6890),
    "weights": (6890, 52),
    "kintree_table": (2, 52),
    "f": (13776, 3),
    "posedirs": (6890, 3, 459),
}
_KEYS = (*_SHAPES, "shapedirs")


@lru_cache(maxsize=2)
def _load(path, size, modified_ns, inode):
    if path.suffix.lower() == ".pkl":
        arrays = read_registered_arrays(path, _KEYS)
    elif path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as source:
            arrays = {key: source["faces" if key == "f" and key not in source else key] for key in _KEYS}
    else:
        raise ValueError("Unsupported SMPL-H model format")

    for key, shape in _SHAPES.items():
        value = arrays[key]
        if value.shape != shape or value.dtype.kind not in "iuf" or not np.isfinite(value).all():
            raise ValueError(f"Invalid SMPL-H {key}")
    shapedirs = arrays["shapedirs"]
    if (shapedirs.ndim != 3 or shapedirs.shape[:2] != (6890, 3)
            or shapedirs.shape[-1] not in (10, 16)
            or shapedirs.dtype.kind != "f" or not np.isfinite(shapedirs).all()):
        raise ValueError("Invalid SMPL-H shape basis")
    parents = arrays["kintree_table"]
    faces = arrays["f"]
    if (parents.dtype.kind not in "iu" or not np.array_equal(parents[1], np.arange(52))
            or not np.all((parents[0, 1:] >= 0) & (parents[0, 1:] < np.arange(1, 52)))
            or faces.dtype.kind not in "iu" or faces.min() < 0 or faces.max() >= 6890):
        raise ValueError("Invalid SMPL-H topology")
    weights = arrays["weights"]
    if (np.any(weights < -1e-6) or not np.allclose(weights.sum(axis=1), 1., atol=1e-4)
            or not np.all(np.ptp(arrays["v_template"], axis=0) > 0.)
            or not all(np.any(weights[:, a] + weights[:, b] > .6) for a, b in ((7, 10), (8, 11)))):
        raise ValueError("Invalid SMPL-H rest mesh or skin weights")
    return arrays


def load_smplh_data(path=None):
    """Load validated numeric arrays, preserving the official reference and its basis size."""
    path = Path(path).absolute() if path is not None else smplh_reference_path()
    info = path.stat()
    try:
        return _load(path, info.st_size, info.st_mtime_ns, info.st_ino)
    except (ValueError, KeyError, TypeError, EOFError, BadZipFile, pickle.UnpicklingError) as exc:
        raise ValueError(f"Invalid SMPL-H model: {path.name}: {exc}") from exc


def validated_smplh_reference(name=None):
    """Choose a valid installed reference lazily at the start of an inference job."""
    options = (name,) if name is not None else SMPLH_REFERENCE_ORDER
    errors = []
    for candidate in options:
        if not smplh_available(candidate):
            continue
        path = smplh_reference_path(candidate)
        try:
            load_smplh_data(path)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        return candidate, path
    if errors:
        raise ValueError("; ".join(errors))
    raise FileNotFoundError("No compatible SMPL-H body model is installed")
