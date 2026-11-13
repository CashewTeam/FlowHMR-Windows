"""Runtime metadata that can be queried without loading the inference engine."""

from functools import lru_cache
from importlib.util import find_spec


LONG_MOTION_EXPERIMENTAL_THRESHOLD = 5000
SOLE_FIT_REFERENCE_STRENGTH = 0.6  # equivalent to the original on/off option


class SAMMemoryError(RuntimeError):
    def __init__(self, resource):
        self.resource = resource
        name = "system memory" if resource == "ram" else "video memory"
        super().__init__(f"SAM stopped this job because there is too little {name}. "
                         "Free up memory and rerun the job.")


@lru_cache(maxsize=1)
def native_correction_available():
    try:
        if find_spec("rgbmocap_correction") is None:
            return False
        import motion_correction
        return getattr(motion_correction, "rgb_mocap_anatomical_version", 0) == 1
    except (ImportError, OSError, RuntimeError):
        return False
