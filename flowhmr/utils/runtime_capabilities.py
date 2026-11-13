"""Runtime metadata that can be queried without loading the inference engine."""

from functools import lru_cache
from importlib.util import find_spec
from zipfile import BadZipFile


LONG_MOTION_EXPERIMENTAL_THRESHOLD = 5000
SOLE_FIT_REFERENCE_STRENGTH = 0.6  # equivalent to the original on/off option


def requested_frame_range(start_frame=0, end_frame=None, max_frames=None):
    """Combine the requested range and the legacy prefix limit (inclusive source frames)."""
    if max_frames is not None and max_frames > 0:
        limit = int(max_frames) - 1
        end_frame = limit if end_frame is None else min(end_frame, limit)
    return start_frame, end_frame


def detection_request_key(frame_range):
    start, end = frame_range
    return [str(start), "" if end is None else str(end)]


def cached_detection_matches(path, frame_range):
    import numpy as np

    try:
        with np.load(path, allow_pickle=False) as data:
            if "bbox" not in data or "start_end" not in data:
                return False
            if "detection_request" in data:
                return np.array_equal(data["detection_request"], detection_request_key(frame_range))
            # Earlier releases detected the full video and could store a clipped bbox.
            return frame_range == (0, None)
    except (OSError, ValueError, BadZipFile):
        return False


class EmptyPersonRangeError(ValueError):
    """No tracked person in the explicitly selected range."""


class SharedCameraSamplingError(RuntimeError):
    """The requested camera sampling is too dense for the shared-memory route."""


class SAMMemoryError(RuntimeError):
    def __init__(self, resource):
        self.resource = resource
        name = "system memory" if resource == "ram" else "video memory"
        super().__init__(f"SAM stopped this job because there is too little {name}. "
                         "Free up memory and rerun the job.")


class SharedGPUMemoryError(RuntimeError):
    def __init__(self, available, needed):
        self.params = {"available": f"{available / 1024 ** 3:.1f}", "needed": f"{needed / 1024 ** 3:.1f}"}
        super().__init__(f"Not enough system memory for AMD integrated graphics "
                         f"({self.params['available']} GiB usable, {self.params['needed']} GiB estimated).")


class InferenceRAMError(RuntimeError):
    def __init__(self, available, needed):
        self.params = {"available": f"{available / 1024 ** 3:.1f}", "needed": f"{needed / 1024 ** 3:.1f}"}
        super().__init__(f"Not enough system memory for inference "
                         f"({self.params['available']} GiB usable, {self.params['needed']} GiB estimated).")


@lru_cache(maxsize=1)
def native_correction_available():
    try:
        if find_spec("rgbmocap_correction") is None:
            return False
        import motion_correction
        return getattr(motion_correction, "rgb_mocap_anatomical_version", 0) == 1
    except (ImportError, OSError, RuntimeError):
        return False
