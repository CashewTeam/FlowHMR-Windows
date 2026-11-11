# FlowHMR Windows v0.3.0

这一版扩展了动作导出和自定义角色预览。

- **动作导出**：新增 Mixamo FBX、MetaHuman 身体 FBX 和 MMD VMD，保留 FlowHMR FBX 与 NPZ。FlowHMR 和 Mixamo FBX 可选蒙皮及第 −1 帧的 T Pose / A Pose；MetaHuman 使用目标身体 FBX 作为模板。多人 VMD 分角色写入 ZIP，保留时间和站位。
- **自定义角色**：可直接选择 PMX 或带蒙皮的 Mixamo / MetaHuman FBX。角色库保存模型、贴图和配置，支持删除转存资源。多人可以各用不同角色，重新打开任务会恢复角色和骨骼调节。
- **骨骼和脚部**：可实时调整全身关节、根骨旋转和平移，以及对称手指；开启镜像后成对关节同步调整。PMX 贴地在短暂接触中断时平滑过渡。IK 模式驱动模型原生脚部控制器，按当前 PMX 导出 VMD 时保留兼容的脚和脚尖 IK。
- **播放与默认设置**：修复新安装后预览偶发卡播和时间轴获焦后空格不响应。新任务默认 20 步、滤波 0.04、手动选人开启。接触修正组件可用时默认开启，脚步锁定为加强、脚掌贴地强度为 0.6。已有任务和个人设置保留。

安装：完整解压 `FlowHMR-Windows.zip`，打开 `FlowHMR/FlowHMR.exe`。升级时先退出程序，再覆盖旧安装目录中的程序文件；保留原有 `models`、已安装的运行环境和任务数据。首次安装仍在软件内下载推理环境和模型。AMD Radeon RX 9000 系列中的 `gfx1201` 型号可在 Windows 11 上试用 [ROCm 实验环境](amd-rocm.md)。

[模型文件说明](../models/README.md) · [动作导出与角色预览](retarget-export.md)
