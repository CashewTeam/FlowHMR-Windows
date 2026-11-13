# Windows AMD ROCm 运行环境

AMD 和 NVIDIA 使用同一个 `FlowHMR-Windows.zip` 和 `FlowHMR.exe`。在 Windows 11 上，Radeon RX 9000 / 7000 系列需要配置与实际显卡匹配的 ROCm 环境。RX 7000 系列已验证可运行，需要手动配置 ROCm 版 PyTorch。软件内可下载的 **ROCm 7.14.0 · gfx1201** 适用于 RX 9000 系列中采用该架构的型号，安装步骤如下。切换环境后，模型、设置和任务历史仍保留。

1. 使用 64 位 **Windows 11**，更新 AMD 驱动。显卡和驱动要求见 [AMD ROCm 7.14.0 说明](https://rocm.docs.amd.com/en/docs-7.14.0/about/release-notes.html)。
2. 打开 `FlowHMR.exe`，在 **运行环境** 下载 **ROCm 7.14.0 · gfx1201**。程序使用 [AMD 官方软件包](https://repo.amd.com/rocm/whl-multi-arch/)，下载约 1.47 GB，需额外磁盘空间供解包和续传。GPU 验证通过后，点 **启用**。
3. 和 CUDA 版本一样，在 **模型** 中装齐必需模型。先用一小段视频在 **固定机位** 下跑一遍，再用含镜头转向的短视频试 **运动相机**，确认相机、SAM 和动作生成都能完成。

双显卡机器不需要按教程固定写 `HIP_VISIBLE_DEVICES=1`。程序会从 ROCm 可见的设备中选 `gfx1201`；若你之前配置过可见设备，请确认未隐藏目标独显。模型加载和显存管理仍由 PyTorch 负责。

本版已检查官方 Windows Python 3.10 包的下载、解包、库加载和 CPU 算子，也确认了会拒绝不匹配的 GPU。**RX 9070 XT 上的完整推理、运动相机以及 HaMeR 尚需真机验证。** 若验证失败，请保留 `logs/web.stderr.log`，并记录显卡、驱动、Windows 版本以及失败阶段。没有通过 GPU 验证的环境不会被启用，已下载的包可复用。
