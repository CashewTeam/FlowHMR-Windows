# FlowHMR 模型文件

打开 FlowHMR 右上角的 **模型**，点 **下载全部公开模型**，或点某一行的 **下载**。程序会校验文件并放到对应目录，停止后可继续。**HaMeR** 包含在全部公开模型中；只做基础动捕时，可按行下载其他公开组。需要登录的官方包请手动下载。桌面推理开始生成时才加载模型。

登录 [MANO 官网下载页](https://mano.is.tue.mpg.de/download.php)，按下表下载官方压缩包。打开 **模型 → 导入模型压缩包…**，直接选中下载的文件。程序会内置解压、识别模型组、复制到正确位置并列出结果，原包保留。若已自行解压，也可用 **导入已解压文件夹…**，或按表手动放置。

| 用途 | 官网条目和压缩包 | 解压到 | 解压后的位置 |
| --- | --- | --- | --- |
| **男／女体型，亦可用于手指精修** | **Models & Code**（模型和代码），`mano_v1_2.zip` | `models/` | `models/mano_v1_2/models/` 内的 `.pkl` |
| **可选中性体型** | **Extended SMPL+H model**（用于 AMASS 数据集），`smplh.tar.xz` | `models/body_models/smplh/` | `models/body_models/smplh/neutral/model.npz` |

基础动捕安装一个官方体型即可：有 `mano_v1_2.zip` 里的男或女体型，就不必再下载中性包。两个包都装好时，新任务优先使用中性；已有任务保留当时使用的参考体型。手动解压如果只得到 `smplh.tar`，可直接在软件中导入该 TAR。`models/body_models/smpl_neutral_J_regressor.pt` 是单独的公开文件，程序可一键下载。

```text
models/
├── flowhmr_latest/
│   ├── flowhmr_latest.ckpt
│   └── config.yml
├── yolox/
│   └── yolox_l.pth
├── sam-3d-body-dinov3/
│   ├── model.ckpt
│   ├── model_config.yaml
│   └── assets/mhr_model.pt
├── vggt-omega/
│   └── vggt_omega_1b_512.pt
├── body_models/
│   ├── smpl_neutral_J_regressor.pt
│   └── smplh/neutral/model.npz   ← 可选的中性体型
├── mano_v1_2/models/            ← 官方男／女体型和 MANO 手部模型
└── hamer/
    ├── hamer.ckpt
    ├── model_config.yaml
    └── mano_mean_params.npz
```

`download-models.cmd` 下载公共文件。其中 [SAM 3D Body DINOv3](https://www.modelscope.cn/models/facebook/sam-3d-body-dinov3) 和 [VGGT-Omega](https://www.modelscope.cn/models/facebook/VGGT-Omega) 从魔搭下载并校验 SHA-256，无需 Hugging Face 授权。两者都须安装；固定机位跳过 VGGT 推理。

### HaMeR 手指精修

1. 在 **运行环境** 启用可用的 GPU 推理环境。
2. 登录 [MANO 官网](https://mano.is.tue.mpg.de/download.php)，下载 **Models & Code**（`mano_v1_2.zip`）。在 **模型 → 导入模型压缩包…** 直接选择 ZIP。精修读取原版 `models/mano_v1_2/models/MANO_RIGHT.pkl`；可选中性包 `smplh.tar.xz` 不包含它。
3. 在 **模型** 面板点击 **HaMeR** 一行的下载按钮。程序优先从[镜像](https://huggingface.co/Leo-TX/hamer/tree/f64df318d49e7e014a6a7f5d0547cba87d6e4317)下载所需的三个文件（约 2.69 GB），逐个核对与作者包相同的 SHA-256。若镜像不可用，会尝试[作者的 `hamer_demo_data.tar.gz`](https://www.cs.utexas.edu/~pavlakos/hamer/data/hamer_demo_data.tar.gz)（约 6.04 GB）。已有作者压缩包时也可直接导入。
4. HaMeR 显示 **已就绪** 后，在生成设置中开启 **HaMeR 手指精修**。如果提示 MANO 文件错误或不完整，请用官方包内的 `MANO_RIGHT.pkl` 原文件替换后点 **刷新**。
