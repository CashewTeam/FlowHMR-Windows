# FlowHMR Windows 桌面版

适用于 64 位 Windows 10 / 11，见 [最低配置与支持设备](../README.md#最低配置与支持设备)。基础包带 Python 3.10 和界面依赖，可以直接打开软件。生成前需按显卡安装推理环境：NVIDIA 使用 CUDA，AMD Radeon RX 9000 / 7000 系列在 Windows 11 上配置 [ROCm](amd-rocm.md)。

1. 下载 [发布说明](release-windows.md)中的 `FlowHMR-Windows.zip`，完整解压到一个有写入权限的目录。
2. 在解包后的 `FlowHMR` 文件夹里双击 `FlowHMR.exe`。程序使用独立窗口，不需要用浏览器。缺少 WebView2 或 VC++ 运行库时，程序会运行包里的微软安装程序。
3. NVIDIA 用户可在右上角 **运行环境** 下载并启用 CUDA 12.6、12.8 或 12.9，RTX 50 系列推荐 12.8。AMD 用户按 [ROCm 说明](amd-rocm.md)配置。软件内下载支持续传，安装会校验文件和 GPU 运算。版本分别存入 `runtime/environments`，启用时重启推理服务。
4. 点击右上角 **模型** → **下载全部公开模型**，或逐行选择要下载的模型。登录 [MANO 官网下载页](https://mano.is.tue.mpg.de/download.php) 取得 **Models & Code**（`mano_v1_2.zip`，男／女体型及 MANO）或 **Extended SMPL+H model**（`smplh.tar.xz`，中性体型）。基础动捕有其中一种官方体型即可。点 **导入模型压缩包…** 直接选择下载的文件，程序会自动解压、识别并显示结果；也可选择已解压的文件夹。详见 [模型目录说明](../models/README.md)。开始生成时才会加载推理模型。

Windows 便携包自带 FBX 导出组件。安装运行环境和模型后，可离线生成、预览和导出 FBX、VMD、VRMA 或 NPZ。程序设置、日志和任务历史留在解包目录。可双击 `check-install.cmd` 检查环境和模型。

便携版每次启动时在后台检查 GitHub，发现新版本会打开更新面板，显示版本号和发布说明。可以关闭面板继续工作，也可以从右上角语言下拉框旁的 **检查更新** 重新打开。下载由用户发起，校验完成后在下次启动时安装。更新源暂时无法访问时，仍可使用当前版本。模型、角色库、任务历史、设置和已安装的 CUDA／ROCm 环境继续使用原来的目录。

打开 **检查更新** 面板，点 **手动更新** 可以直接选择已下载的官方 Windows ZIP，程序在后台校验和准备安装。**历史版本** 会列出 GitHub 发布的版本和本机缓存的上一个程序版本。本机缓存可离线切换。较早发布的 ZIP 可从列表下载并单独解压运行。

相机默认使用 **固定机位**。素材仅平移、推拉或缩放时通常可用此模式；镜头明显转向、俯仰或滚转时，选 **运动相机 · 自动**。软件会分阶段使用 GPU，优先保留正常的相机分辨率和采样；可用显存不足时才降低分辨率或调整自动采样。手动采样保留用户设置的帧间隔。

软件按所选区间内的实际人物轨迹确定处理帧数。单条轨迹超过 5000 帧时，任务面板会标出实验性长动作。

开启 **自动选择 SAM 批次** 后，软件先运行一帧，再根据实际峰值、显存预算和系统内存余量逐批调整。分配失败时会缩批并重试同一段素材。单帧也无法处理时，任务会提示需要释放哪种内存。可切换到手动批次指定帧数。SAM 手部采样可选逐帧、每 2 帧或每 4 帧。建议在开启 HaMeR 手指精修时使用隔帧采样。

导出 FBX 时可选 **不添加 / T Pose / A Pose**。参考姿势写在 FBX 第 **−1** 帧，原动作帧号和多人的入场时刻保持不变。在 Blender 导入时将 **Animation Offset** 设为 `0`，可直接看到文件里的负帧号。

## 从源码运行

下面的命令在项目根目录执行。源码版需要 Python 3.10 x64 和支持 CUDA 12.8 的 NVIDIA 驱动。先初始化依赖的 Git 子模块：

```powershell
git submodule update --init third_party/YOLOX third_party/sam-3d-body third_party/vggt-omega
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt -c requirements-windows.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e ./third_party/YOLOX
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation ./third_party/KimodoMotionCorrection ./third_party/RGBMocapCorrection
.\.venv\Scripts\python.exe tools\windows\install_fbx.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\build-exe.ps1
```

接触修正的源码构建需要 MSVC C++ 工具链，预编译的便携版已包含扩展。HaMeR 的推理代码和模型可在界面内下载。可先运行 `check-install.cmd`，再双击根目录的 `FlowHMR.exe`。

## 打包

在已验证的 Python 3.10 x64 / cu128 环境下，准备微软签名的 [WebView2 x64 离线安装程序](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) 和 [Visual C++ x64 可再发行组件](https://aka.ms/vs/17/release/vc_redist.x64.exe)，使用下列名称保存：`MicrosoftEdgeWebView2RuntimeInstallerX64.exe`、`vc_redist.x64.exe`。还需要固定版本的 HaMeR 和 DINOv3 推理代码。打包只带代码和环境，不带模型权重。

将 `tools/windows/install_fbx.py` 指定的 `fbxsdkpy` wheel 放入同一先决条件目录，文件名保持为 `fbxsdkpy-2020.1.post2-cp310-none-win_amd64.whl`。打包会校验 SHA-256，并只提取运行所需的 `fbx.pyd`。

将 Microsoft 的 [.NET Runtime 10.0.15 x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.15/dotnet-runtime-10.0.15-win-x64.zip) 和 [Windows Desktop Runtime 10.0.15 x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/10.0.15/windowsdesktop-runtime-10.0.15-win-x64.zip) 也放入该目录，分别保存为 `dotnet-runtime-win-x64.zip` 和 `windowsdesktop-runtime-win-x64.zip`。便携包从 `runtime/dotnet` 加载桌面运行环境，打包时校验两个文件的 SHA-512。

```powershell
git clone https://github.com/facebookresearch/dinov3.git .cache\dinov3
git -C .cache\dinov3 checkout 6876159a11b4df116f30f667f8c9888617df0751
curl.exe --fail --location https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/946fcce07b --output .cache\FFmpeg-9.0.2-Source.tar.gz
.\.venv\Scripts\python.exe -B tools\windows\package_release.py stage .cache\release-stage --prerequisites .cache\prerequisites --dinov3-source .cache\dinov3 --version X.Y.Z
.\.venv\Scripts\python.exe -B tools\windows\package_release.py pack .cache\release-stage dist\FlowHMR-Windows --ffmpeg-source .cache\FFmpeg-9.0.2-Source.tar.gz
```

暂存和输出目录必须尚不存在。打包会生成 `FlowHMR-Windows.zip`、校验文件、源码和相对当前 Git 提交的补丁。需要制作预装 cu128 的离线包时，可给 `stage` 加 `--bundle-cuda`；超出单附件大小的离线包才会分卷。

将 `X.Y.Z` 换成即将发布的版本号，发布标签使用 `windows-vX.Y.Z`。将生成的 `FlowHMR-Windows.zip` 和 `FlowHMR-Windows-Update.json` 一起作为发布附件上传，历史版本列表会据此显示应用内安装入口。下载校验使用 GitHub 提供的 SHA-256。首次从未含更新器的版本升级，以及启动器本身升级时，请完整解压发布包。
