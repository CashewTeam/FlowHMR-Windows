"""Read the array fields in the registered MANO v1.2 files without rewriting them."""

import pickle


class _LegacyArrayState:
    """Hold unused Chumpy state without importing or running Chumpy."""


class _LegacySelectionState(_LegacyArrayState):
    """Hold the selection used by the official SMPL-H shape basis."""


def _selected_shape_basis(value, np):
    """Evaluate only the original numeric slice stored by Chumpy Select."""
    state = vars(value)
    base = state.get("a")
    array = vars(base).get("x") if type(base) is _LegacyArrayState else None
    indices, shape = state.get("idxs"), state.get("preferred_shape")
    if (not isinstance(array, np.ndarray) or array.shape != (6890, 3, 300)
            or array.dtype.kind != "f" or shape != (6890, 3, 10)
            or not isinstance(indices, np.ndarray) or indices.shape != (6890 * 3 * 10,)
            or indices.dtype.kind not in "iu" or indices.min() < 0 or indices.max() >= array.size):
        raise ValueError("Unexpected SMPL-H shape basis")
    return array.take(indices).reshape(shape)


def read_registered_arrays(path, keys):
    import numpy as np
    from numpy.core.multiarray import _reconstruct
    from scipy.sparse import csc_matrix, coo_matrix, issparse

    classes = {
        ("__builtin__", "set"): set,
        ("builtins", "set"): set,
        ("chumpy.ch", "Ch"): _LegacyArrayState,
        ("chumpy.reordering", "Select"): _LegacySelectionState,
        ("numpy", "dtype"): np.dtype,
        ("numpy", "ndarray"): np.ndarray,
        ("numpy.core.multiarray", "_reconstruct"): _reconstruct,
        ("scipy.sparse.coo", "coo_matrix"): coo_matrix,
        ("scipy.sparse.csc", "csc_matrix"): csc_matrix,
    }

    class ArrayReader(pickle.Unpickler):
        def find_class(self, module, name):
            if (module, name) not in classes:
                raise pickle.UnpicklingError(f"Unexpected MANO data type: {module}.{name}")
            return classes[module, name]

    with open(path, "rb") as stream:
        data = ArrayReader(stream, encoding="latin1").load()
    arrays = {}
    for key in keys:
        value = data[key]
        if key == "shapedirs" and type(value) is _LegacySelectionState:
            value = _selected_shape_basis(value, np)
        if issparse(value):
            value = value.toarray()
        if not isinstance(value, np.ndarray) or value.dtype.kind not in "iuf":
            raise ValueError(f"Unexpected MANO array: {key}")
        arrays[key] = value
    return arrays
