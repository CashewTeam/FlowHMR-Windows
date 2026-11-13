# Windows AMD ROCm 运行环境

AMD 和 NVIDIA 使用同一个 `FlowHMR-Windows.zip` 和 `FlowHMR.exe`。Windows 11 上，程序会识别 AMD 显卡的架构，在「运行环境」里提供匹配的 ROCm 下载选项。切换环境后，模型、设置和任务历史仍保留。

1. 使用 64 位 **Windows 11**，安装适用于该显卡的 AMD 驱动。较新的 Radeon 核显请参考 [AMD 的 ROCm 7.14.0 兼容表](https://rocm.docs.amd.com/en/docs-7.14.0/compatibility/compatibility-matrix.html)。
2. 打开 `FlowHMR.exe`，在 **运行环境** 选择对应 AMD 显卡的 **ROCm 7.14.0** 项。列表会列出可选架构；显示「未检测到匹配显卡」的项目暂不能下载或启用。环境从 [AMD 官方仓库](https://repo.amd.com/rocm/whl-multi-arch/) 按需下载，体积约 1.08～1.70 GB。GPU 验证通过后，点 **启用**，软件会重启推理服务。
3. 在 **模型** 中装齐必需模型。核显建议先用很短的一段素材，选 **固定机位**、SAM 批次 **自动**。只有镜头转动时才需要开启 **运动相机**。

双显卡电脑会同时显示检测到的设备。启用 ROCm 后，程序按所选架构从 HIP 可见设备中选择；若之前设过 `HIP_VISIBLE_DEVICES`，确认未隐藏要用的 AMD 显卡。找不到核显时，先看 Windows 设备管理器里它是否启用且有 AMD 驱动。

| 设备 | 运行环境 | FlowHMR 验证状态 |
| --- | --- | --- |
| Ryzen 9 9950X 内置 Radeon Graphics | `gfx1036` | 本机短片已验证固定机位生成、HaMeR 和 FBX 回读 |
| 同架构的 Raphael 核显（如带核显的部分 Ryzen 7000 桌面处理器） | `gfx1036` | 有安装入口，待真机验证 |
| Radeon 740M / 760M / 780M | `gfx1103` | 实验支持；安装包已核对，待真机验证 |
| Radeon 880M / 890M、Radeon 8040S～8065S | `gfx1150` / `gfx1151` | 实验支持；安装包已核对，待真机验证 |
| 部分 Radeon 820M / 840M / 860M | `gfx1152` / `gfx1153` | 实验支持；按设备架构匹配，待真机验证 |
| Radeon RX 9000 独显 | RX 9070：`gfx1201`；RX 9060：`gfx1200` | 应用内按架构下载 |
| Radeon RX 7000 独显 | RX 7900：`gfx1100`；RX 7800／7700：`gfx1101`；RX 7600：`gfx1102` | 应用内按架构下载 |
| Radeon RX 5000 独显 | RX 5700／5600 XT：`gfx1010`；RX 5500／5300：`gfx1012` | 实验入口；安装包已核对，待实机验证 |
| Radeon Pro V520（Navi 12） | `gfx1011` | 实验入口；安装包已核对，待实机验证 |

RX 5000 使用 [AMD TheRock 的 Windows 架构包](https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md#rocm-on-windows)，尚未列入 ROCm 7.14.0 的正式产品兼容表。启用前会检查实际显卡上的 FP16、卷积、注意力和稀疏张量算子。Windows 里的 AMD 驱动应与显卡型号对应。

核显和 CPU 共用系统内存。程序会把模型按推理阶段移到 GPU，SAM 自动批次会参考可用系统内存。固定机位跳过相机模型；如果选运动相机，AMD 核显模式会让 CPU 估计相机，自动最多采样 16 帧。此路径已用真实的 CPU 相机进程验证，长视频、多人物和小内存机器的表现仍待验证。若失败，请保留 `logs/web.stderr.log`，并记录显卡、驱动、Windows 版本和失败阶段。

启用匹配的 AMD 核显环境后，支持 AVX-512 BF16 的 CPU 可以在 **属性 → SAM 运行模式** 选择 **CPU + GPU（省显存）**。CPU 负责 DINOv3 图像编码，人物检测、SAM 解码、动作生成仍由 GPU 执行。默认使用 GPU；在 9950X 的短片上，混合模式降低了 PyTorch 的 GPU 内存峰值，但 SAM 耗时增加。切换模式再重跑会重新提取 SAM 特征。混合模式不是纯 CPU，也不能据此判定 4 GB 独显能稳定运行。
