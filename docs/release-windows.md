# FlowHMR Windows v0.3.3

- **程序更新。** v0.3.2 用户重启后会在后台检查新版，也可点击右上角 **检查更新**。选择下载，校验通过后再重启即可安装。模型、角色库、CUDA／ROCm 环境、历史任务和设置会保留。
- **SAM 自动批次。** 先用一帧实测，再按显存预算和系统内存余量动态增减批次。分配失败会缩批重试。系统资源连单帧都无法满足时，任务会提示缺的是显存还是系统内存。长视频的 SAM 特征分批写入，减少合并时的内存峰值。
- **SAM 手部采样。** 支持逐帧、每 2 帧和每 4 帧。隔帧时通过插值补齐 SAM 手部特征，身体特征仍逐帧计算。建议搭配 HaMeR 手指精修使用。感谢 [@Scar1etCrystal](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/pull/2)。
- **本机 MCP 服务。** 在桌面顶栏启动，支持可选的访问令牌和随程序启动。兼容客户端可导入视频、调整参数、操作任务、控制播放和角色骨骼，以及导出 FBX、VMD、VRMA 和 NPZ。感谢 [@CashewTeam](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/pull/5)。
- **任务进度和导出。** 远程 Web 页面恢复自动进度刷新。修复通过 MCP 导出自动选人的单人任务时，Mixamo、MetaHuman 和 Maya AdvancedSkeleton FBX 被错误拒绝的问题。

在 RTX 5090 D 上用短片实跑，验证了自动增批、SAM 缓存复用和桌面 MCP。回放显存和系统内存收紧的场景，验证了缩批、重试和单帧也无法运行时的提示。RX 7800 XT 仍待实卡验证，AMD Radeon RX 9000 系列的 ROCm 环境继续标为实验支持。

首次安装请完整解压 `FlowHMR-Windows.zip`，双击 `FlowHMR/FlowHMR.exe`。已装 v0.3.2 时可直接使用应用内更新。

[MCP 使用说明](mcp.md) · [Windows 安装说明](windows.md) · [AMD 实验环境](amd-rocm.md)
