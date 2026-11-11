"""Resolve local model files without importing or loading any model runtime."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = PROJECT_ROOT / "models"
HAMER_RUNTIME = PROJECT_ROOT / "third_party/hamer_inference"
FLOWHMR_CKPT = "flowhmr_latest/flowhmr_latest.ckpt"
FLOWHMR_CONFIG = "flowhmr_latest/config.yml"
YOLOX = "yolox/yolox_l.pth"
SAM3D_FILES = (
    "sam-3d-body-dinov3/model.ckpt",
    "sam-3d-body-dinov3/model_config.yaml",
    "sam-3d-body-dinov3/assets/mhr_model.pt",
)
VGGT = "vggt-omega/vggt_omega_1b_512.pt"
SMPLH_FOLDER = "body_models/smplh"
MANO_BUNDLE = "mano_v1_2"
MANO_FILES = tuple(f"{MANO_BUNDLE}/models/MANO_{side}.pkl" for side in ("LEFT", "RIGHT"))
SMPLH_FILES = {
    "neutral": f"{SMPLH_FOLDER}/neutral/model.npz",
    "female": f"{MANO_BUNDLE}/models/SMPLH_female.pkl",
    "male": f"{MANO_BUNDLE}/models/SMPLH_male.pkl",
}
JOINT_REGRESSOR = "body_models/smpl_neutral_J_regressor.pt"
HAMER_FILES = ("hamer/hamer.ckpt", "hamer/model_config.yaml", "hamer/mano_mean_params.npz")
MODEL_DESTINATIONS = (FLOWHMR_CKPT, FLOWHMR_CONFIG, YOLOX, *SAM3D_FILES, VGGT,
                      *SMPLH_FILES.values(), JOINT_REGRESSOR, *MANO_FILES, *HAMER_FILES)


def ensure_model_folders():
    for folder in {(MODEL_ROOT / relative).parent for relative in MODEL_DESTINATIONS}:
        folder.mkdir(parents=True, exist_ok=True)


def file_present(path):
    """Require the file and check published metadata for pinned models.

    Existence alone is not enough: an interrupted download leaves a file that
    looks present and then fails much later inside torch.load. Custom models
    and the editable FlowHMR configuration are not pinned to a published hash.
    """
    from . import model_integrity

    return model_integrity.check(path).ok


def resolve_model(relative):
    return MODEL_ROOT / relative


def sam3d_paths():
    return tuple(map(resolve_model, SAM3D_FILES))


def flowhmr_paths(ckpt=None, config=None):
    checkpoint = Path(ckpt).absolute() if ckpt else resolve_model(FLOWHMR_CKPT)
    config = Path(config).absolute() if config else checkpoint.with_name("config.yml")
    return checkpoint, config


def core_model_paths(ckpt=None, config=None):
    return (*flowhmr_paths(ckpt, config), resolve_model(YOLOX), *sam3d_paths(), resolve_model(VGGT),
            resolve_model(SMPLH_FILES["neutral"]), resolve_model(JOINT_REGRESSOR))


def missing_model_paths(paths):
    return [str(path) for path in paths if not file_present(path)]
