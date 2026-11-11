# FlowHMR · Windows

v0.2.0 加入七种界面语言和语言记忆。**模型 → 导入已解压文件夹…** 会识别并列出模型组，检查文件并复制到正确位置。公开模型可全部或按组一键下载。历史列表可以继续加载较早的任务。Windows 的 JavaScript 模块加载和模型完整性提示也已修复。

HaMeR 有独立的下载按钮。优先下载与作者包 SHA-256 一致的三个所需文件（约 2.69 GB）；下载源不可用时再尝试作者整包。手指精修仍需要官方 MANO v1.2。

新任务默认 **固定机位**。仅平移、推拉或缩放通常可以用固定机位，镜头明显旋转时请选择运动相机。FBX 导出可在第 **−1** 帧添加 T Pose / A Pose，原动作帧号保持不变。Windows 便携包自带 FBX 导出组件。

下载 `FlowHMR-Windows.zip`，完整解压到可写的目录。打开其中的 `FlowHMR` 文件夹，双击 `FlowHMR.exe`。

从旧版升级时，先退出程序，再将新版 `FlowHMR` 文件夹中的内容覆盖到原安装目录。模型、运行环境和任务历史仍保留在原目录。若 v0.1.0 关窗后后台仍在运行，先双击旧目录的 `stop-web.cmd`。

基础包包含 Python 3.10 和界面组件。开始生成前，在右上角 **运行环境** 下载并启用一个版本。NVIDIA 用户可选 CUDA 12.8（推荐）、12.9 或 12.6；RTX 50 系列请选择 12.8 或 12.9。Windows 11 上的 AMD RX 9070 系列可试用 [ROCm 7.14.0 实验环境](amd-rocm.md)，仍需在对应硬件上验证完整推理。软件会校验安装文件并测试 GPU。

点 **模型** 下载公开模型。登录 [MANO 官网下载页](https://mano.is.tue.mpg.de/download.php) 后，下载下表所需文件，解压并选择文件夹导入。基础动捕只需要一种官方体型；已经有男／女体型的用户可以跳过中性包。也可按下表手动安装：

| 用途 | 官网下载项 | 解压到 |
| --- | --- | --- |
| 男／女体型、MANO 手部模型 | **Models & Code**（`mano_v1_2.zip`） | `models/` |
| 可选中性体型 | **Extended SMPL+H model**（AMASS，`smplh.tar.xz`） | `models/body_models/smplh/` |

解压后的中性模型在 `models/body_models/smplh/neutral/model.npz`。更多目录见 `models/README.md`。缺少 WebView2 或 VC++ 运行库时，使用包内的微软安装程序。

`SHA256SUMS.txt` 用于检查下载完整性。源码、补丁和 `FFmpeg-9.0.2-Source.tar.gz` 供开发者使用。第三方组件许可见解包目录的 `THIRD_PARTY_NOTICES.md`。
