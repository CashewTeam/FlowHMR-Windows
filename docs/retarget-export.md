# 动作导出与角色预览

在预览结果右上角点 **导出动作**，或从 **文件 → 导出动作** 选择格式。NPZ 可单独导出。桌面版可在系统保存窗口选择文件名和目录，下次导出会从上一次成功保存的目录打开。

| 格式 | 适合的用途 | 选项 |
| --- | --- | --- |
| FlowHMR FBX | Blender 等软件里的通用重定向源 | 可选蒙皮，可在第 −1 帧插入 T Pose 或 A Pose；可把多人放在同一个 FBX |
| Mixamo FBX | Mixamo 骨名的通用重定向源，或按现有 Mixamo 角色导出动作 | 通用导出支持蒙皮、参考姿势和多人。选择角色模板后，按目标骨架导出一人 |
| MetaHuman FBX | 按现有 MetaHuman 的身体骨架生成动作 | 选择目标身体的 FBX 模板，每次导出一人，只写骨骼动画 |
| Maya AdvancedSkeleton FBX | Maya 的 AdvancedSkeleton 动作库 | 导出一人的动画骨架，第 −1 帧为 T Pose |
| MMD VMD | 给 MMD 人形角色播放动作 | 选用当前预览 PMX 或通用骨架；30 FPS，多人分别写入 VMD 再打成 ZIP，保留时间和相对站位 |
| VRM Animation (.vrma) | 给支持 VRM Animation 的应用提供通用人形动作 | 保留源帧率、身体、手指和根骨位移。多人时保留各自的时间和站位，每人一个 VRMA，打包成 ZIP |

**Mixamo FBX** 可直接导出使用 Mixamo 骨名的通用重定向源。需要把动作直接应用到某个角色时，在同一入口选择该角色的 FBX 模板，导出会保留角色的骨骼层级、轴向、单位和绑定姿势。可使用 Adobe Mixamo 下载的角色 FBX。[Adobe Mixamo 说明](https://helpx.adobe.com/creative-cloud/help/mixamo-rigging-animation.html)

在 iClone 中使用预设导入配置时，可选一份能正常导入的 Mixamo FBX 作为模板。选择 **File → Import → Import External Motion**，为导出的文件选同一个 **Motion Profile**。如需指定 **Motion T-pose**，使用同一角色的 T Pose 文件。[iClone 外部动作导入说明](https://manual.reallusion.com/iClone-8/Content/ENU/8.4/50-Animation/Import-External-Motions/Import-External-Motions.htm)

在 Maya 里使用 AdvancedSkeleton 的 **MoCapLibrary → Motion Capture (Custom)** 时，选择 **Maya AdvancedSkeleton FBX**。在动作库中浏览到保存目录，选中 FBX 后点 **Apply**。动作库会用第 −1 帧对齐源骨架。使用 **MoCapMatcher** 的用户也可导入通用 **Mixamo FBX**，选择 Mixamo 模板后扫描骨架。 [AdvancedSkeleton 官方页面](https://www.animationstudios.com.au/)

在 Maya 中拖入 [ADV 帧率助手](../tools/maya/flowhmr_adv_timing.py) 打开面板。也可在 Script Editor 的 Python 页打开脚本并执行。

1. 点 **准备 30 FPS**，再从 Custom 动作库选中 FlowHMR 的 FBX，点 **Apply**。
2. 点助手里的 **FBX 已应用**，然后在 ADV 中执行 **Bake Body**。在这个阶段保持 30 FPS，检查动作时使用实时 1× 播放。
3. 点 **烘焙完成，恢复原帧率**。Maya 按秒保留烘焙关键帧和播放范围。回到 24 FPS 工程时，部分关键帧会落在小数帧上。

助手的 **提前恢复** 会结束临时帧率设置。Bake Body 按当前场景帧率取样，使用 30 FPS 可以保留 FlowHMR 的每个原始采样点。 [Maya 播放设置](https://help.autodesk.com/cloudhelp/2023/ENU/Maya-Customizing/files/GUID-51A80586-9EEA-43A4-AA1F-BF1370C24A64.htm) · [Maya 时间单位](https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/currentUnit.html)

已回读核对 FBX 的动作帧和时长，并模拟验证了助手的帧率切换。实际 Maya ADV 应用和烘焙前后的动作对照仍待实机验证。

MetaHuman 工作流：在 Unreal Engine 的内容浏览器中找到目标角色的 **Body** Skeletal Mesh，用 **Asset Actions → Export…** 导出身体 FBX。把这个文件作为 FlowHMR 的模板，然后在 Unreal 导入生成的 FBX 时选择**同一个身体 Skeleton**。FlowHMR 保留模板的骨骼层级，写入身体和手指动作；面部动画需要单独制作。参见 [Epic 的 MetaHuman 动画说明](https://dev.epicgames.com/documentation/metahuman/play-a-custom-animation)。

T Pose 和 A Pose 写入 FBX 的第 −1 帧，作为动画关键帧。T Pose 朝正前方。Blender 默认以 +1 帧导入，请在骨架的「姿态位置」查看第 0 帧。若导入时把动画偏移设为 0，则查看第 −1 帧。「静置位置」显示绑定姿势。

v0.3.2 的 VMD 按 30 FPS 逐帧写入动作，并使用 MMD 兼容的线性插值。旧任务可以直接重新导出 VMD。已在 MikuMikuDance 9.26 CHS 的 30 和 60 FPS 播放设置下对照测试。

打开一条任务后，在 3D 视图点 **角色 → 选择模型文件…**，选择角色主体的 **PMX、VRM** 或带蒙皮的 **Mixamo / MetaHuman FBX**，再点「应用角色」。PMX 和 FBX 的外置贴图放在模型旁边或其子文件夹。桌面版应用后会将模型和引用到的贴图转存到 `models/characters`，以后可直接从「角色库」选择。再次打开任务时会恢复上次选择，也可以对某个人点「恢复 SMPL-H」。

VRM 预览支持 0.x 和 1.0 人形模型。程序从文件内加载贴图、MToon 材质和人形骨骼映射，随动作更新 VRM 1.0 的节点旋转、扭转与瞄准约束。

导出 VRMA 后，在支持 VRM Animation 的应用中先打开 VRM 角色，再导入动作。VRMA 写入生成结果的人形骨骼动作和默认头颈修正。角色网格、材质、面部表情和每个预览角色的手动骨骼调节由使用端单独处理。[VRM Animation 格式](https://vrm.dev/en/vrma/)

多人任务中，先在「角色」面板选人物，再为这个人应用模型。每人可以使用不同角色，动作时间和相对站位与原任务一致。新任务沿用同类任务最近的角色选择，多人按人物编号沿用。备份或迁移时可复制整个 `models/characters` 文件夹。

清理角色时，在「角色库」选中角色并点「删除」。确认后会清理转存的模型和贴图，使用该角色的人物会恢复 SMPL-H。原文件仍留在导入前的位置。

Mixamo 使用带蒙皮的角色 FBX。MetaHuman 在 Unreal Engine 中右键 Body 或合并后的 Full Body Skeletal Mesh，选 **Asset Actions → Export… → FBX**；预览身体和手部动作需要蒙皮网格。单独的骨架、动画 FBX、DNA 或 UE 项目文件不能用来显示角色。预览使用模型里可读取的贴图和通用灯光，不重现 Unreal 的材质节点或面部表情。

应用角色后，打开视图顶部的 **骨骼调节**。多人时先选人物。点选人形上的关节，可以对整段动作实时加上 X、Y、Z 轴旋转（−180° 到 180°）。根骨以骨盆为中心转动，也可按米平移，Y 为高度。手指有单独的图，只调整一只手，另一只自动镜像。VRM 的头颈默认姿态已按实测校准，头部与颈部滑块从 0° 开始。每个人的调节按任务和角色记忆，按当前 PMX 角色导出 VMD 会保留各自的调节。

开启「镜像调节」后，拖动成对的身体骨骼滑块时，另一侧会实时镜像。关掉开关可以单独调一侧，已调好的角度会保留。

带兼容 IK 链的 PMX 可以在「骨骼调节」切换 FK / IK，按人物记忆。FK 直接用动捕驱动骨骼；IK 会根据落脚位置驱动原生控制器，并让辅助变形骨跟随求解结果。按当前角色导出 VMD 时，兼容的脚和脚尖 IK 在 IK 模式下保持启用，可以继续编辑控制器；马尾等附属姿态会烘焙。FK 模式导出的 VMD 关闭模型 IK。

面板在 3D 视图左上角展开，可以拖动标题栏移开。关闭再打开会回到左上角。

导出 VMD 时选 **按当前角色**，再把文件导入同一个 PMX。程序沿目标骨架的父子关系重定向腰背和肩膀，并按角色的手臂、腿长重算手腕和脚踝的位置。通用骨架模式使用常见日文人形骨名，以 FK 驱动腿部；脚掌高度和身体比例可能因目标不同而偏离。VMD 只含动作，不含网格、贴图或参考姿势。部分旧版 MMD Tools 需先在模型上开启 **Property Drivers（属性驱动）**，导入的 IK 和显示关键帧才会生效。

PMX 预览面向常见人形骨架，例如原神及 Tda 式模型；表情与衣物物理不由动捕驱动，特殊蒙皮在预览中可能近似。改过骨名或使用特殊控制器的模型，建议先用短片段检查预览和 VMD 回读效果。
