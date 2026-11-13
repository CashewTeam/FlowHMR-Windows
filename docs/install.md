# Installation

Run all commands from the FlowHMR-Windows repository root, not from `docs/`. For the portable app and its GPU runtimes, see the [Windows desktop guide](windows.md). See [resources.md](resources.md) for file locations and [params.md](params.md) for configuration options.

## Setups

| Setup | Supports | Steps |
|---|---|---|
| **V2M** | Inference, web demo, base model training | 1, 2, 4.1 (SMPL-H only), 4.3, 5.1–5.2 |
| **Full pipeline** | V2M + PHC+ physics simulator (GRPO post-training, physics tracking) | All |

The two Python environments never share imports; the training process and the PHC+
simulator communicate through files on `/dev/shm`.

Steps marked **[manual]** require license acceptance or account access and must be done by the user.

## 1. Clone

```bash
git clone --recursive https://github.com/xiaoerofficial021-collab/FlowHMR-Windows.git FlowHMR
cd FlowHMR
# in an existing clone:
git submodule update --init --recursive
```

## 2. V2M environment

```bash
conda create -n flowhmr python=3.10 -y
conda activate flowhmr
pip install -r requirements.txt -r tools/web/requirements.txt
pip install --no-build-isolation --no-deps -e third_party/YOLOX
python tools/windows/adapt_sam.py
```

Validated with Python 3.10, PyTorch 2.5.1, and CUDA driver ≥ 470. `requirements.txt` pins
the cu118 build of torch and torchvision. YOLOX is installed with `--no-deps`; its runtime
dependencies are listed in `tools/web/requirements.txt`, which keeps `numpy<2`. If you use a
plain `venv` instead of conda, run `pip install wheel` before installing YOLOX.

Check: `python -c "import torch, yolox; print(torch.__version__, torch.cuda.is_available())"`
prints `2.5.1+cu118 True`.

[models/README.md](../models/README.md) lists the complete model directory layout. Run
`python tools/windows/download_models.py` to install the public weights. The downloader uses
[Meta's SAM 3D Body DINOv3](https://www.modelscope.cn/models/facebook/sam-3d-body-dinov3)
and [VGGT-Omega](https://www.modelscope.cn/models/facebook/VGGT-Omega) on ModelScope
and verifies the files against the known SHA-256 values. Both must be installed;
fixed-camera mode skips VGGT inference.

## 4. Data

### 4.1 Body models [manual]

Download the SMPL-family models after registering:

- **V2M training / neutral reference**: Sign in to the [MANO downloads page](https://mano.is.tue.mpg.de/download.php), choose **Extended SMPL+H model** (AMASS, `smplh.tar.xz`), and extract it into `models/body_models/smplh/` to install `neutral/model.npz`.
- **Desktop inference**: The neutral reference is optional if a male or female SMPL-H model from **Models & Code** (`mano_v1_2.zip`) is installed. Extract it into `models/` and keep the `mano_v1_2/models/` directory. The same pack supplies the official MANO hand file for HaMeR.
- **Full pipeline**: also [SMPL](https://smpl.is.tue.mpg.de/) v1.1.0 under `data/smpl/`, read by
  `smpl_sim`. Rename `basicmodel_{neutral,m,f}_lbs_10_207_0_v1.1.0.pkl` to
  `SMPL_{NEUTRAL,MALE,FEMALE}.pkl`. SMPL-X is not needed (the simulator uses the `smpl_humanoid` robot).

The joint regressor is public:

```bash
wget -O models/body_models/smpl_neutral_J_regressor.pt \
    https://raw.githubusercontent.com/zju3dv/GVHMR/main/hmr4d/utils/body_model/smpl_neutral_J_regressor.pt
```

```
models/body_models/                 # V2M, required
├── smplh/neutral/model.npz
└── smpl_neutral_J_regressor.pt

data/smpl/                          # full pipeline only
└── SMPL_{NEUTRAL,MALE,FEMALE}.pkl
```

### 4.2 PHC+ checkpoints (full pipeline only)

```bash
pip install gdown
mkdir -p output/HumanoidIm/phc_comp_3 output/HumanoidIm/phc_3
gdown "https://drive.google.com/uc?id=1JbK9Vzo1bEY8Pig6D92yAUv8l-1rKWo3" -O output/HumanoidIm/phc_comp_3/Humanoid.pth  # ~400 MB
gdown "https://drive.google.com/uc?id=1pS1bRUbKFDp6o6ZJ9XSFaBlXv6_PrhNc" -O output/HumanoidIm/phc_3/Humanoid.pth       # ~419 MB
# PHC AMASS shape statistics, loaded by the simulator
gdown "https://drive.google.com/uc?id=1bLp4SNIZROMB7Sxgt0Mh4-4BLOPGV9_U" -O assets/example/   # amass_isaac_gender_betas.pkl, ~3 MB
gdown "https://drive.google.com/uc?id=1arpCsue3Knqttj75Nt9Mwo32TKC4TYDx" -O assets/example/   # amass_isaac_gender_betas_unique.pkl, ~70 KB
```

- `phc_comp_3`: composite controller, loaded by the tracking evaluator and the GRPO reward server.
- `phc_3`: MCP controller, referenced by `env.models`.
- `amass_isaac_gender_betas{,_unique}.pkl`: AMASS shape statistics from the PHC release
  (same files as PHC's `download_data.sh`), subject to the AMASS license.

If Google Drive rate-limits the download, fetch the files manually from the PHC release.

### 4.3 FlowHMR checkpoints

```bash
huggingface-cli download fafsaf1/flowhmr-0.46B --local-dir models
# or only the recommended model:
huggingface-cli download fafsaf1/flowhmr-0.46B --include "flowhmr_latest/*" "config.json" --local-dir models
```

```
models/
├── flowhmr_base/{flowhmr_base.ckpt, config.yml}
└── flowhmr_latest/{flowhmr_latest.ckpt, config.yml}
```

Keep each `.ckpt` next to its `config.yml`. Pass the `.ckpt` path via `--ckpt`, and
`config.yml` is loaded automatically. These `config.yml` files are meant for
inference only; for training, use the configs in `configs/` (see
[training.md](training.md)).

## 5. Smoke tests

### 5.1 Inference

Runs detection, camera estimation, SAM 3D Body features, and FlowHMR on the bundled example:

```bash
conda activate flowhmr
python run_v2m_demo_generation.py assets/example/example.mp4
```

Expected: the log ends with `=== Done | ok=1 failed=0 ===` and the script writes
`output/example/flowhmr_latest.npz` (`poses (T, 52, 3)`, `trans (T, 3)`). If any
checkpoint or body model is missing, the script lists it and exits. With the default
`--vggt_interval 1`, this example peaks at about 30 GB of GPU memory (measured on an A100 40GB);
on smaller GPUs add `--vggt_interval 10` (see [params.md](params.md)).

### 5.2 Web demo

```bash
python app.py --port 8080 --device cuda:0
```

Expected: after about 1 minute, the log prints `FlowHMR web demo ready`. Open
`http://<host>:8080` and upload `assets/example/example.mp4`. See [web_demo.md](web_demo.md)
and [params.md](params.md#web-demo) for launch options.

### 5.3 Simulator (full pipeline only)

Scores the 5.1 output with PHC+, the same way the GRPO reward does. Pick a free GPU for
`--device-id`:

```bash
conda activate phc
python tools/phc_score_server.py --ctrl-dir /tmp/flowhmr_phc_smoke --num-envs 4 \
    --device-id 0 --idle-timeout 120 > /tmp/flowhmr_phc_smoke.log 2>&1 &
# wait until /tmp/flowhmr_phc_smoke/ready exists (~30 s), then:
python tools/phc_smoke_test.py --ctrl-dir /tmp/flowhmr_phc_smoke \
    --motion output/example/flowhmr_latest.npz
```

Expected: a line like `{"example": {"tracking_score": 0.77..., "terminated": false,
"survival": 1.0, ...}}` followed by `PHC_SMOKE_OK`. `PHC_SMOKE_FAIL` or a crash in
`/tmp/flowhmr_phc_smoke.log` means a previous step is incomplete. The server exits
automatically after `--idle-timeout` seconds.
