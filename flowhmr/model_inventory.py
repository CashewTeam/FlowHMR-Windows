"""Model catalog for the local UI. Checking it only reads file metadata."""

from . import model_files as files
from . import model_integrity
from . import model_sources as sources
from .utils.runtime_hamer import hamer_model_details


def _display_path(path):
    if path.is_relative_to(files.MODEL_ROOT):
        return f"models/{path.relative_to(files.MODEL_ROOT).as_posix()}"
    return str(path)


def _item(key, name, paths, folder, links, *, required=True, issue=None, issue_code=None, archive=None):
    verdicts = [(path, model_integrity.check(path)) for path in paths]
    missing = [_display_path(path) for path, verdict in verdicts if verdict.state == "missing"]
    damaged = [{**model_integrity.issue(verdict), "file": _display_path(path)}
               for path, verdict in verdicts if verdict.state in ("incomplete", "corrupt")]
    if missing:
        status = "missing"
    elif damaged:
        status = "damaged"
    elif issue:
        status = "unavailable"
    else:
        status = "ready"
    return {"id": key, "name": name, "status": status, "required": required,
            "blocking": required and status != "ready", "folder": folder, "missing": missing,
            "damaged": damaged,
            "archive": {"entry": archive[0], "name": archive[1]} if archive else None,
            "issue": issue, "issue_code": issue_code, "links": [{"label": label, "url": url} for label, url in links]}


def model_inventory(device, ckpt=None, config=None):
    hamer_paths, issue_code, issue = hamer_model_details(device)
    bundle = tuple(map(files.resolve_model, (files.SMPLH_FILES["female"], files.SMPLH_FILES["male"], *files.MANO_FILES)))
    mano_link = [("MANO 官网下载（需登录）", sources.MANO_URL)]
    reference = files.smplh_reference()
    mano = _item("mano", "SMPL-H 男／女 · MANO v1.2", bundle, "models/", mano_link,
                 required=reference != "neutral", archive=sources.MANO_DOWNLOAD)
    # Basic generation needs one body reference; hand files and the other
    # gender are not prerequisites when a compatible body is present.
    mano["blocking"] = reference is None
    if reference in ("male", "female") and mano["status"] != "ready":
        mano.update(issue="已有可用于基础动捕的男或女体型；其余文件用于另一体型或手指精修。",
                    issue_code="model_mano_partial")
    return {"directory": str(files.MODEL_ROOT), "items": [
        _item("smplh", "SMPL-H 中性（AMASS）", (files.resolve_model(files.SMPLH_FILES["neutral"]),),
              f"models/{files.SMPLH_FOLDER}/", mano_link, required=False, archive=sources.SMPLH_DOWNLOAD),
        _item("flowhmr", "FlowHMR", files.flowhmr_paths(ckpt, config), "models/flowhmr_latest",
              [("镜像入口", f"{sources.HF_MIRROR}/{sources.FLOWHMR_REPO}/tree/main/flowhmr_latest"),
               ("官方仓库", f"{sources.HF_OFFICIAL}/{sources.FLOWHMR_REPO}/tree/main/flowhmr_latest")]),
        _item("yolox", "YOLOX-L", (files.resolve_model(files.YOLOX),), "models/yolox",
              [("官方权重", sources.YOLOX_URL)]),
        _item("sam", "SAM 3D Body · DINOv3", files.sam3d_paths(), "models/sam-3d-body-dinov3",
              [("魔搭下载", f"{sources.MODELSCOPE}/models/{sources.SAM_REPO}")]),
        _item("vggt", "VGGT-Omega", (files.resolve_model(files.VGGT),), "models/vggt-omega",
              [("魔搭下载", f"{sources.MODELSCOPE}/models/{sources.VGGT_REPO}")]),
        _item("joint-regressor", "SMPL 关节回归文件", (files.resolve_model(files.JOINT_REGRESSOR),),
              "models/body_models", [("直接下载", sources.REGRESSOR_URL)]),
        mano,
        _item("hamer", "HaMeR", hamer_paths, "models/hamer",
              [("作者直链", sources.HAMER_ARCHIVE_URL), ("备用下载", sources.HAMER_DEMO_URL),
               ("官方说明", sources.HAMER_PROJECT_URL)],
              required=False, issue=issue, issue_code=issue_code),
    ]}
