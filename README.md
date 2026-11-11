# FlowHMR-Windows

**简体中文** · [繁體中文](docs/readme/zh-TW.md) · [English](docs/readme/en.md) · [日本語](docs/readme/ja.md) · [한국어](docs/readme/ko.md) · [Русский](docs/readme/ru.md) · [Français](docs/readme/fr.md)

**FlowHMR-Windows** 是一款在本机运行的视频动捕软件。导入普通视频后，可以选取一个或多个人物，重建他们的 3D 人体动作，同步预览并导出。动作重建采用 [FlowHMR](https://github.com/flowhmr/flowhmr) 上游模型；本仓库独立维护 Windows 便携版、模型与运行环境管理，以及从选人到导出的桌面工作流。

**[下载 Windows 便携版](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [查看发布说明](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 论文](https://arxiv.org/abs/2610.03691)

## Windows 桌面版

- **导入视频，在一个窗口完成动捕。** 选择要处理的人物和帧范围，生成后并排看原视频和 3D 动作。时间轴可同步播放、拖动和循环，可播放原声，也可以切换蒙皮和骨架显示。
- **多人一起预览和导出。** 同一条视频可选中多个人；合并预览和 FBX 保留他们在原视频中的相对站位和入场时间。旧任务可从带缩略图的历史里继续查看。
- **按素材调整动作。** 可调动作滤波、脚部接触修正和地面高度；需要更细的手指动作时，可开启 HaMeR 手指精修。相机默认用固定机位，镜头明显转向、俯仰或滚转时再用运动相机。
- **在本机运行。** 程序按需安装推理环境和模型，开始生成时才加载模型；装好后可离线处理。支持简体中文、繁體中文、English、日本語、한국어、Русский 和 Français，重开程序会记住上次选的语言。
- **导出动作数据。** 支持 NPZ 和 FBX；FBX 可选择带蒙皮或只导出动画骨架，也可在第 **−1 帧**附上 T Pose 或 A Pose，原动作起始帧保持不变。

## 配套 Blender 插件：FlowHMR 2 MMR

**[下载 FlowHMR 2 MMR v1.3.1](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR_2_MMR_v1.3.1.zip)** · 作者：**小二, GPT** · 支持 Blender 3.6 及以上，多人流程已在 3.6.22 和 4.5.3 验证。

有了 FBX 后，用此插件把源动作转到由 **MMR** 生成的目标控制骨架。多人可以在同一个场景配对、预览和一键烘焙；每个目标各有独立的 Action，保留各自的入场时间和相对站位。

1. 在 Blender 的 **编辑 → 偏好设置 → 插件** 安装 ZIP。Blender 3.6 点击 **安装**；Blender 4.2 及以上从右上角菜单选择 **从磁盘安装**。启用 **FlowHMR 2 MMR** 后，在 3D 视图按 **N** 打开同名页签。
2. 导入 FlowHMR 的 FBX，并准备好由 MMR 生成的目标控制骨架。若需要保留文件中的负帧号，导入 FBX 时把 **Animation Offset** 设为 `0`。
3. 单人选定源和目标后，点击 **建立实时预览**，检查动作再 **烘焙到新动作**。多人开启 **多人模式**，点击 **识别源骨架**，为每个人选择各自的目标，再 **预览全部** 或 **一键烘焙全部**。

插件 ZIP 内附使用说明，包括骨骼映射、姿态微调和脚底定位。

## 示例视频

点击缩略图打开 MP4。四段均使用中性 SMPL-H 体型，以 30 FPS 的正交正视图展示，保留选定区间内同步的原声。

| 运球与舞步 | 舞蹈 |
| --- | --- |
| [![运球与舞步](assets/demos/01-ikun-thumb.png)](assets/demos/01-ikun-front.mp4) | [![舞蹈](assets/demos/02-selei-thumb.png)](assets/demos/02-selei-front.mp4) |
| **舞台角色动作** | **手势与步伐** |
| [![舞台角色动作](assets/demos/03-test2-thumb.png)](assets/demos/03-test2-front.mp4) | [![手势与步伐](assets/demos/04-test3-thumb.png)](assets/demos/04-test3-front.mp4) |

想自己试跑，可以用仓库里的 [示例素材](assets/example/example.mp4)。

## 快速开始

1. 在 64 位 Windows 10 / 11 上完整解压 `FlowHMR-Windows.zip`，进入解包后的 `FlowHMR` 文件夹，双击 `FlowHMR.exe`。基础包自带 Python、界面组件和 FBX 导出组件。
2. 安装兼容的显卡驱动，再在右上角 **运行环境** 下载并启用对应版本。NVIDIA 用户选 CUDA，RTX 50 系列推荐 CUDA 12.8。AMD RX 9070 系列按下方步骤选择 ROCm。
3. 打开 **模型**，点 **下载全部公开模型**，也可以逐项下载。公开模型会校验后放到 `models/`；SAM 3D Body 和 VGGT-Omega 由魔搭提供。不需要手指精修时，可以跳过 HaMeR。需要登录的官方模型按下表取得。
4. 把视频拖进软件，选择要处理的人物和帧范围，再开始生成。完成后可先预览，再从菜单导出 FBX 或 NPZ。

### AMD RX 9070 系列（实验支持）

AMD 和 NVIDIA 使用同一个 `FlowHMR-Windows.zip` 和 `FlowHMR.exe`。Windows 11 上使用 RX 9070 系列显卡（`gfx1201`）时，首次打开软件后，在 **运行环境** 下载并启用 **ROCm 7.14.0 · gfx1201**。软件会记住这项选择，并自动选用兼容的 AMD 显卡。模型安装步骤相同。

已验证 AMD 官方包的下载、解包和运行库加载。RX 9070 XT 上的完整动捕流程，包括运动相机和 HaMeR，仍待实机验证。见 [AMD 安装与验证说明](docs/amd-rocm.md)。

### 官方模型该下载哪个？

先登录 [MANO 官网下载页](https://mano.is.tue.mpg.de/download.php)。**在上一步安装的公开模型之外，基础动捕只需下表任意一个官方包。已有 `mano_v1_2.zip` 就不必再下中性包。** 如果两种都装了，默认优先使用中性体型。

| 用途 | 官网下载项 | 手动解压到 |
| --- | --- | --- |
| 可用于基础动捕的中性体型 | **Extended SMPL+H model**（AMASS），`smplh.tar.xz` | `models/body_models/smplh/` |
| 可用于基础动捕的男性、女性体型；也包含手指精修所需的 MANO 文件 | **Models & Code**，`mano_v1_2.zip` | `models/` |

下载后，点击 **模型 → 导入模型压缩包…**，直接选择 `mano_v1_2.zip` 或 `smplh.tar.xz`。软件会自动解压、识别模型并安装到正确位置，保留原压缩包；模型面板会显示哪些模型组已就绪、哪些仍缺文件。如果已经解压过，也可以点击 **导入已解压文件夹…**，选择解压后的文件夹。

使用手指精修时，需同时安装 `mano_v1_2.zip` 中的 MANO 文件和公开的 **HaMeR** 模型；HaMeR 可从对应一行下载，也包含在 **下载全部公开模型** 中。

手动安装的完整目录结构、下载来源和模型用途见 [模型文件说明](models/README.md)。

## 帮助与项目来源

- [Windows 使用、升级和源码构建说明](docs/windows.md) · [报告问题](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [维护界面语言](docs/localization.md)
- [FlowHMR 官方项目页](https://flowhmr.github.io/) · [上游推理与训练资料](https://github.com/flowhmr/flowhmr#readme) · [上游论文引用格式](https://github.com/flowhmr/flowhmr#citation)

动作重建基于 Zhanke Wang 等人的 **FlowHMR: Physically Plausible Motion Capture from Video**。用于研究时请引用[原论文](https://arxiv.org/abs/2610.03691)。FlowHMR 源码采用 [Apache-2.0](LICENSE)；第三方组件和模型按各自的许可分发，详见发行包内的 `THIRD_PARTY_NOTICES.md` 和[上游模型卡](https://huggingface.co/fafsaf1/flowhmr-0.46B)。
