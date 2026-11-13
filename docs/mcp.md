# MCP 服务

FlowHMR 提供本机 Streamable HTTP MCP 服务。Agent 可以导入视频、调整生成参数、查询任务、导出动作，以及控制桌面预览和角色骨骼。

## 启动和连接

1. 启动 FlowHMR 桌面版，点击顶栏 **MCP**。
2. 点击 **启动服务**，再点击 **验证连接**。
3. 将面板中的地址填入 MCP 客户端。默认地址为 `http://127.0.0.1:60551/mcp`。

服务默认不自动启动，访问令牌默认关闭。可以在面板中开启 **自动开启 MCP** 或 **启用访问令牌**。启用令牌后，客户端需要发送 `Authorization: Bearer <令牌>`，令牌可在面板中查看和复制。端口及令牌设置需在服务停止时修改。

MCP 的启停、令牌和配置由桌面窗口管理。启用令牌后，本机客户端也需要携带令牌才能连接 MCP。

客户端连接配置示例（具体格式以客户端为准）：

```json
{
  "url": "http://127.0.0.1:60551/mcp",
  "headers": {"Authorization": "Bearer <访问令牌>"}
}
```

未启用访问令牌时省略 `headers`。服务只监听本机，视频、角色和导出路径均为运行 FlowHMR 的电脑上的绝对路径。

MCP 创建的任务会显示在桌面任务历史中。队列、任务进度、角色和导出状态通过事件通知界面更新。MCP 面板显示最近操作和导出记录。

## 工具

工具参数和返回值可由 MCP 客户端通过工具列表查看。`flowhmr_get_parameter_schema` 返回生成参数和导出选项的 schema 与默认值。

| 工具 | 用途 |
| --- | --- |
| `flowhmr_get_status` | 查询后端、运行环境和模型状态 |
| `flowhmr_get_parameter_schema` | 查询生成参数、默认值和导出选项 |
| `flowhmr_import_video` | 导入本机视频并创建生成任务 |
| `flowhmr_list_jobs` | 分页查询任务历史 |
| `flowhmr_get_job` | 查询任务状态、参数、候选人物和结果人物 |
| `flowhmr_select_people` | 为等待选人的任务选择一个或多个人物 |
| `flowhmr_rerun_job` | 使用新参数创建重跑任务 |
| `flowhmr_cancel_job` | 请求取消排队中或运行中的任务 |
| `flowhmr_prepare_scene` | 为需要对齐的多人结果创建场景 |
| `flowhmr_export_motion` | 提交 FBX、VMD、VRMA 或 NPZ 导出 |
| `flowhmr_get_export` | 查询导出状态和文件信息 |
| `flowhmr_import_character` | 导入 PMX、VRM 或带蒙皮的 FBX 角色 |
| `flowhmr_list_characters` | 查询角色库 |
| `flowhmr_select_character` | 为已完成任务的人物选择角色，`character_id` 为 `null` 时恢复 SMPL-H |
| `flowhmr_get_character_pose` | 读取所选角色、保存的偏移和骨骼控制项 |
| `flowhmr_update_character_pose` | 更新角色预览的骨骼偏移 |
| `flowhmr_get_viewer_snapshot` | 读取界面当前帧、播放状态和预览骨架 |
| `flowhmr_get_motion_keyframes` | 读取原始 SMPL-H 动作的逐帧数据 |
| `flowhmr_control_playback` | 控制界面跳帧、播放和暂停 |

## 视频和生成参数

`flowhmr_import_video` 接收本机视频的绝对路径，导入后立即排队。支持 MP4、MOV、AVI、MKV、WebM 和 M4V，文件大小不超过 1 GiB。使用返回的 `job_id` 查询进度。

`selection_policy` 默认为 `explicit`，检测到多人时等待界面或 Agent 选择，超时后任务失败；设为 `automatic` 时自动选择默认人物。`flowhmr_select_people` 使用从 0 开始的候选索引；角色和导出使用 `flowhmr_get_job` 返回的 `person_ids`。

自动选择的单人结果返回 `person_ids: [0]`。已有选人记录的单人结果沿用原索引，多人结果沿用场景人物编号。

```json
{
  "name": "flowhmr_import_video",
  "arguments": {
    "video_path": "D:\\Videos\\dance.mp4",
    "selection_policy": "explicit",
    "params": {"steps": 20, "cfg_scale": 1.0, "static_camera": true}
  }
}
```

生成参数放在 `params` 中。导入时省略的参数使用后端默认值；重跑时省略的参数继承原任务，重跑会创建新任务。

| 界面设置 | 参数 | 说明 |
| --- | --- | --- |
| 引导强度 | `cfg_scale` | 0–10 |
| 步数 | `steps` | 1–100 |
| 种子 | `seed` | 0–2147483647 |
| 相机模式 | `static_camera`、`vggt_interval` | 固定机位设 `static_camera: true`；运动相机设为 `false`，`vggt_interval` 为 0 时自动采样，1–120 时手动指定间隔 |
| 处理区间 | `start_frame`、`end_frame` | 标准化 30 FPS 视频的零基帧号，结束帧包含在内；`end_frame` 可为 `null` |
| 滤波强度 | `smoothing_frames` | 单位为帧；界面强度 0.04 对应 0.12 帧 |
| HaMeR 手指精修 | `hamer`、`hamer_stride` | 开关和采样间隔；间隔为 1、2、3 或 4 |
| 接触修正 | `postprocess` | 布尔开关 |
| 脚步锁定 | `correction_preset` | `normal` 标准或 `strong` 加强 |
| 脚掌贴地强度 | `sole_fit_strength` | 0–1 |
| 修正用地面 | `correction_floor_m` | `null` 为自动估计；数字为手动高度，单位米 |
| SAM 批次 | `sam_chunk_size` | `null` 为自动；正整数为手动批次大小 |
| SAM 手部采样 | `sam_hand_stride` | 1 为逐帧，2 或 4 为隔帧采样，间隔内的手部特征由插值补齐 |

`auto_frames` 和 `max_frames` 也可用于控制处理帧数。HaMeR 和接触修正需要相应运行环境和模型。MCP 参数在导入或重跑时生效；角色预览偏移可单独修改。

## 导出动作

`flowhmr_export_motion` 的参数放在 `options` 中。`output_path` 的父目录必须存在；默认不覆盖已有文件。

```json
{
  "name": "flowhmr_export_motion",
  "arguments": {
    "options": {
      "job_id": "任务ID",
      "format": "fbx",
      "output_path": "D:\\Exports\\dance.fbx",
      "fbx_profile": "flowhmr",
      "include_mesh": true,
      "reference_pose": "t"
    }
  }
}
```

提交后使用 `flowhmr_get_export` 和返回的 `export_id` 查询；状态为 `done` 时文件已导出。多人 VMD、VRMA 使用 `.zip` 输出；NPZ 导出完整结果，不支持按人物筛选。FBX 支持 `flowhmr`、`mixamo`、`metahuman`、`maya_adv` 和 `mixamo_iclone` 预设。模板和预设选项见 [导出与重定向](retarget-export.md)。

## 角色和骨骼

角色导入后，通过 `flowhmr_list_characters` 获取角色 ID，再使用 `flowhmr_select_character` 选择。多人任务需传入结果中的 `person_id`；单人任务可省略。

`flowhmr_get_character_pose` 返回当前角色、保存的偏移和 37 个预览控制项。`flowhmr_update_character_pose` 的 `patch` 可包含：

| 字段 | 说明 |
| --- | --- |
| `rotations` | 控制项 ID `"0"`–`"36"` 对应的 XYZ 旋转，每轴 −180–180 度 |
| `root_rotation` | 根节点 XYZ 旋转，单位度 |
| `root_translation` | 根节点 XYZ 位移，每轴 −5–5 米 |
| `mirror` | 界面镜像编辑偏好，不自动修改另一侧关节 |
| `ik_mode` | `fk` 或 `ik`；IK 适用于支持原生 IK 的 PMX |

省略的字段保留原值，零向量可清除指定偏移。手指控制项 22–36 为双手共用控制项。调整会同步到预览，但不会修改原始动作，也不会自动烘焙到通用 MCP 动作导出。

## 播放和逐帧数据

`flowhmr_get_viewer_snapshot` 按需读取打开的界面。`frame` 是结果内从 0 开始的帧号，`display_frame` 是界面显示的帧号，`source_frame` 是视频源帧号。多人结果还包含各人物的动作帧号。未打开界面时返回错误；未加载动作时返回 `available: false`。

快照包含 SMPL-H 关节位置，以及已加载角色骨架的局部和世界变换。PMX 使用模型骨架顺序，FBX 使用预览 rig 顺序，VRM 使用预览 rig 顺序而非原始 glTF 节点索引。角色预览骨架包含已应用的偏移。

`flowhmr_get_motion_keyframes` 接收 `job_id`、可选 `person_id`、`start_frame` 和 `count`，返回原始 SMPL-H 的 52 个骨骼索引、每帧局部轴角旋转（弧度）和根位移（米）。帧号相对于该人物的动作，从 0 开始；`count` 默认为 1，最多 120。这些是密集逐帧采样，不含角色预览偏移。

`flowhmr_control_playback` 接收当前 `job_id` 和 `action`：`seek` 跳转并暂停，`play` 播放，`pause` 暂停。`seek` 还需传入结果内从 0 开始的 `frame`。

```json
{
  "name": "flowhmr_control_playback",
  "arguments": {"job_id": "任务ID", "action": "seek", "frame": 10}
}
```

多个窗口连接时，快照使用第一个响应的窗口，控制命令由第一个认领的窗口处理。控制命令超时返回 `outcome_unknown`，可先检查界面状态再决定是否重试。
