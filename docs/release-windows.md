# FlowHMR Windows v0.3.4

**本次是低显存多人对齐修复，主要面向显存 8 GB 及以下的用户。** 如果 v0.3.3 使用正常，可以暂不更新。更大显存的电脑在剩余显存不足时也可能触发分段加载；遇到同样的报错也建议更新。

- 修复多人动作已生成，但对齐站位时报 `Expected a cuda device, but got: cpu` 的问题。对齐时只在采样阶段把 SAM 加载到显卡，完成、取消或失败后归还显存。
- 对齐失败的历史任务，更新后可点 **对齐场景** 继续，无需重新生成动作。
- README 增加最低配置与支持设备表，AMD Radeon RX 9000 / 7000 系列使用 ROCm。

已用分段加载模式验证 65 帧双人任务的自动对齐、手动重试、重启后重试和取消；另从视频完整生成并导出 24 帧双人动作，在 Blender 中成功读回两套动画。验证没有人为限制显卡的显存。

已安装 v0.3.3 时，点右上角 **检查更新**，下载校验后重启安装。首次安装请完整解压 `FlowHMR-Windows.zip`，双击 `FlowHMR/FlowHMR.exe`。

[最低配置与支持设备](../README.md#最低配置与支持设备) · [Windows 安装说明](windows.md) · [上一版 v0.3.3](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/tag/windows-v0.3.3)
