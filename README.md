# FlowHMR-Windows

**简体中文** · [繁體中文](docs/readme/zh-TW.md) · [English](docs/readme/en.md) · [日本語](docs/readme/ja.md) · [한국어](docs/readme/ko.md) · [Русский](docs/readme/ru.md) · [Français](docs/readme/fr.md)

**FlowHMR-Windows** 是一款在本机运行的视频动捕软件。导入普通视频后，可以选取一个或多个人物，重建他们的 3D 人体动作，同步预览并导出。动作重建采用 [FlowHMR](https://github.com/flowhmr/flowhmr) 上游模型；本仓库独立维护 Windows 便携版、模型与运行环境管理，以及从选人到导出的桌面工作流。

**[下载 Windows 便携版](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [查看发布说明](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 论文](https://arxiv.org/abs/2610.03691)

## v0.3.5 更新

- **纯 CPU 推理（实验）。** Intel 和 AMD x64 处理器无需 GPU 即可使用固定机位或运动相机。此模式暂不支持 HaMeR 手指精修。
- **新增 GPU 运行环境。** 精简 CUDA 推荐，按显卡匹配 AMD ROCm 下载，加入部分 AMD 核显、RX 5000 和 Intel Arc 的实验环境。符合条件的 AMD 核显可把 SAM 图像编码交给 CPU，降低显存开销。
- **人物检测只扫描选定区间。** 更改起止帧后会重新检测，预览和多人对齐仍使用原素材帧号。
- **修复系统代理下的角色选择。** PMX、VRM 和 FBX 文件选择后的请求直连本机，避免被系统代理拦截（[Issue #7](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/7)）。

[本次发布说明](docs/release-windows.md) · [v0.3.4：低显存多人对齐修复](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/tag/windows-v0.3.4)

## 最低配置与支持设备

| 项目 | 最低配置 | 推荐配置 |
| --- | --- | --- |
| 系统 | 64 位 Windows 10 / 11 | 64 位 Windows 11（ROCm 和 Intel XPU 需要） |
| 内存 | 16 GB | 32 GB 或以上 |
| 纯 CPU 模式 | Intel / AMD x64，无需显卡或显存 | 32 GB 或以上系统内存 |
| 独显显存（CUDA / ROCm） | 6 GB（已验证可运行） | 8 GB 或以上 |

| 设备 | 运行环境 | 验证状态 |
| --- | --- | --- |
| NVIDIA GeForce RTX 50 系列 | CUDA 12.8 | 已验证 |
| NVIDIA GeForce RTX 30 / 40 系列 | CUDA 12.1 | 已验证 |
| NVIDIA GeForce RTX 20 系列 | CUDA 12.1 | 实验中 |
| NVIDIA GeForce GTX 10 系列 | CUDA 12.1 | 实验中 |
| AMD Radeon RX 9000 / 7000 系列 | [ROCm](docs/amd-rocm.md) | 已验证 |
| AMD Radeon RX 5000 系列 | [ROCm](docs/amd-rocm.md) | 实验中 |
| AMD Radeon 核显（含 740M / 760M / 780M 及部分 800M） | [ROCm](docs/amd-rocm.md) | 实验中（9950X 核显已验证；建议固定机位） |
| Intel Arc A / B 系列、部分 Core Ultra Arc 核显 | [Intel XPU](docs/windows.md) | 实验中（GPU 仅负责 SAM 图像编码） |
| Intel / AMD x64 处理器 | [纯 CPU](docs/windows.md) | 已验证（AMD Ryzen 9 9950X）；Intel 实验中 |

纯 CPU 和 Intel XPU 暂不支持 HaMeR 手指精修，SAM 自带的手部估计仍会执行。纯 CPU 或核显建议配备至少 32 GB 系统内存并先用短片试跑。

## Windows 桌面版

- **导入视频，在一个窗口完成动捕。** 选择要处理的人物和帧范围，生成后并排看原视频和 3D 动作。时间轴可同步播放、拖动和循环，可播放原声，也可以切换蒙皮和骨架显示。
- **多人一起预览和导出。** 同一条视频可选中多个人；合并预览和 FBX 保留他们在原视频中的相对站位和入场时间。旧任务可从带缩略图的历史里继续查看。
- **按素材调整动作。** 可调动作滤波、脚部接触修正和地面高度；需要更细的手指动作时，可开启 HaMeR 手指精修。相机默认用固定机位，镜头明显转向、俯仰或滚转时再用运动相机。
- **在本机运行。** 程序按需安装推理环境和模型，开始生成时才加载模型；装好后可离线处理。支持简体中文、繁體中文、English、日本語、한국어、Русский 和 Français，重开程序会记住上次选的语言。
- **动作导出。** 支持 FlowHMR、Mixamo、MetaHuman、Maya AdvancedSkeleton FBX，以及 MMD VMD、VRM Animation（VRMA）和 NPZ。FlowHMR 和 Mixamo FBX 可选蒙皮和第 **−1 帧**的 T Pose / A Pose。详见[导出与重定向](docs/retarget-export.md)。
- **自定义角色。** 支持 PMX、VRM 0.x / 1.0 和带蒙皮的 Mixamo / MetaHuman FBX。多人可分别选择角色并记住骨骼调整。兼容的 PMX 支持原生 IK / FK 切换，按角色导出的 VMD 可保留脚部 IK。

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
2. 点右上角 **运行环境**，按上表下载并启用匹配的 CUDA、ROCm 或 Intel XPU 环境；也可选择 **CPU**。使用 GPU 时先安装兼容的显卡驱动。已经安装的 CUDA 12.6 / 12.9 环境可以继续使用。
3. 打开 **模型**，点 **下载全部公开模型**，也可以逐项下载。公开模型会校验后放到 `models/`；SAM 3D Body 和 VGGT-Omega 由魔搭提供。不需要手指精修时，可以跳过 HaMeR。需要登录的官方模型按下表取得。
4. 把视频拖进软件，选择要处理的人物和帧范围，再开始生成。完成后可先预览，再从菜单导出 FBX、VMD、VRMA 或 NPZ。

已安装的便携版会在后台检查版本。也可以点击语言菜单旁的 **检查更新**，查看发布说明、手动导入更新包或选择历史版本。网络连不上 GitHub 时仍可正常使用本机版本。

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
- [MCP 服务使用说明](docs/mcp.md)
- [FlowHMR 官方项目页](https://flowhmr.github.io/) · [上游推理与训练资料](https://github.com/flowhmr/flowhmr#readme) · [上游论文引用格式](https://github.com/flowhmr/flowhmr#citation)

动作重建基于 Zhanke Wang 等人的 **FlowHMR: Physically Plausible Motion Capture from Video**。用于研究时请引用[原论文](https://arxiv.org/abs/2610.03691)。FlowHMR 源码采用 [Apache-2.0](LICENSE)；第三方组件和模型按各自的许可分发，详见发行包内的 `THIRD_PARTY_NOTICES.md` 和[上游模型卡](https://huggingface.co/fafsaf1/flowhmr-0.46B)。
