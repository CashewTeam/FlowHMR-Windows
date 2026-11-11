# FlowHMR-Windows

[简体中文](../../README.md) · **繁體中文** · [English](en.md) · [日本語](ja.md) · [한국어](ko.md) · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows** 是一款在本機執行的影片動作擷取軟體。匯入一般影片後，可選取一位或多位人物，重建他們的 3D 人體動作，並同步預覽與匯出。動作重建採用上游 [FlowHMR](https://github.com/flowhmr/flowhmr) 模型；本儲存庫獨立維護 Windows 可攜版、模型與執行環境管理，以及從選人到匯出的桌面工作流程。

**[下載 Windows 可攜版](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [查看發行說明](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 論文](https://arxiv.org/abs/2610.03691)

## Windows 桌面版

- **匯入影片，在同一個視窗完成動作擷取。** 選取要處理的人物和影格範圍，生成後並排查看原始影片和 3D 動作。時間軸支援同步播放、拖曳定位和循環，可播放原聲，也可切換蒙皮和骨架顯示。
- **同時預覽和匯出多位人物。** 同一段影片可選取多位人物；合併預覽和 FBX 保留他們在原始影片中的相對站位和入場時間。舊任務可從附縮圖的歷程中重新開啟。
- **依素材調整動作。** 可調整動作濾波、腳部接觸修正和地面高度。需要更細緻的手指動作時，可啟用 HaMeR 手指精修。預設使用固定機位，鏡頭明顯轉向、俯仰或翻滾時再用運動相機。
- **在本機執行。** 依需要安裝推論環境和模型，開始生成時才載入模型。安裝完成後可離線處理。支援简体中文、繁體中文、English、日本語、한국어、Русский 和 Français，重新開啟時會記住上次選的語言。
- **匯出動作資料。** 支援 NPZ 和 FBX；FBX 可包含蒙皮或只匯出動畫骨架，也可在第 **−1 影格**附上 T Pose 或 A Pose，原本的動作起始影格保持不變。

## 配套 Blender 附加元件：FlowHMR 2 MMR

**[下載 FlowHMR 2 MMR v1.3.1](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR_2_MMR_v1.3.1.zip)** · 作者：**小二, GPT** · 支援 Blender 3.6 以上版本，多人工作流程已於 3.6.22 和 4.5.3 驗證。

匯出 FBX 後，使用此附加元件將來源動作轉移到由 **MMR** 生成的目標控制骨架。多人可在同一場景中配對、預覽和一鍵烘焙；每個目標有自己的 Action，保留各自的入場時間和相對站位。

1. 在 Blender 的 **編輯 → 偏好設定 → 附加元件** 安裝 ZIP。Blender 3.6 點擊 **安裝**；Blender 4.2 以上版本請從右上角選單選擇 **從磁碟安裝**。啟用 **FlowHMR 2 MMR** 後，在 3D 檢視按 **N** 開啟同名頁籤。
2. 匯入 FlowHMR 的 FBX，並準備好由 MMR 生成的目標控制骨架。若要保留檔案中的負影格編號，匯入 FBX 時將 **Animation Offset** 設為 `0`。
3. 單人選定來源和目標後，點擊 **建立实时预览**（建立即時預覽），檢查動作後點擊 **烘焙到新动作**（烘焙至新動作）。多人開啟 **多人模式**，點擊 **识别源骨架**（識別來源骨架），為每位人物選取各自的目標，再點擊 **预览全部**（預覽全部）或 **一键烘焙全部**（一鍵烘焙全部）。

附加元件 ZIP 內附使用說明，包括骨骼對應、姿勢微調和腳底定位。

## 範例影片

點擊縮圖開啟 MP4。四段皆使用中性 SMPL-H 體型，採用 30 FPS 的正交前視圖，保留所選區間內同步的原聲。

| 運球與舞步 | 舞蹈 |
| --- | --- |
| [![運球與舞步](../../assets/demos/01-ikun-thumb.png)](../../assets/demos/01-ikun-front.mp4) | [![舞蹈](../../assets/demos/02-selei-thumb.png)](../../assets/demos/02-selei-front.mp4) |
| **舞台角色動作** | **手勢與步伐** |
| [![舞台角色動作](../../assets/demos/03-test2-thumb.png)](../../assets/demos/03-test2-front.mp4) | [![手勢與步伐](../../assets/demos/04-test3-thumb.png)](../../assets/demos/04-test3-front.mp4) |

想自行試跑，可使用儲存庫中的[範例素材](../../assets/example/example.mp4)。

## 快速開始

1. 在 64 位元 Windows 10 或 11 完整解壓縮 `FlowHMR-Windows.zip`，進入解壓縮後的 `FlowHMR` 資料夾，按兩下 `FlowHMR.exe`。基礎套件內含 Python、介面元件和 FBX 匯出元件。
2. 使用 NVIDIA 顯示卡時，先安裝相容的驅動程式，再點右上角 **執行環境** 下載並啟用 CUDA 版本。RTX 50 系列建議使用 CUDA 12.8。AMD RX 9070 系列可參閱 [ROCm 實驗支援](../../docs/amd-rocm.md)。
3. 開啟 **模型**，選擇 **下載所有公開模型**（下载全部公开模型），或逐項下載。公開模型經驗證後會放入 `models/`，其中 SAM 3D Body 和 VGGT-Omega 由 ModelScope 提供。不需要手指精修時可以略過 HaMeR。需登入下載的官方模型請依下表取得。
4. 將影片拖入軟體，選取要處理的人物和影格範圍，再開始生成。完成後先預覽，再從選單匯出 FBX 或 NPZ。

### 該下載哪個官方模型？

先登入 [MANO 官網下載頁](https://mano.is.tue.mpg.de/download.php)。**除了上一步安裝的公開模型，基本動作擷取只需要下表任一官方套件。若已有 `mano_v1_2.zip`，不必再下載中性套件。** 若兩個套件都已安裝，預設優先使用中性體型。

| 用途 | 官網下載項目 | 手動解壓縮到 |
| --- | --- | --- |
| 可用於基本動作擷取的中性體型 | **Extended SMPL+H model**（AMASS），`smplh.tar.xz` | `models/body_models/smplh/` |
| 可用於基本動作擷取的男性、女性體型；也包含手指精修所需的 MANO 檔案 | **Models & Code**，`mano_v1_2.zip` | `models/` |

下載並解壓縮後，在 **模型** 中點擊 **匯入已解壓縮的資料夾…**，選取解壓縮好的資料夾。程式會辨識其中的模型並複製到正確位置，原始檔案會保留。模型面板會顯示仍缺少的檔案。手指精修需要 `mano_v1_2.zip` 中的 MANO 檔案和公開的 **HaMeR** 模型。HaMeR 可從該列單獨下載，也包含在 **下載所有公開模型** 中。

手動安裝的完整目錄結構、下載來源和模型用途請參閱[模型檔案說明](../../models/README.md)。

## 說明與專案來源

- [Windows 使用、升級和原始碼建置說明](../../docs/windows.md) · [回報問題](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [維護介面語言](../../docs/localization.md)
- [FlowHMR 官方專案頁](https://flowhmr.github.io/) · [上游推論與訓練資料](https://github.com/flowhmr/flowhmr#readme) · [上游論文引用格式](https://github.com/flowhmr/flowhmr#citation)

動作重建以 Zhanke Wang 等人的 **FlowHMR: Physically Plausible Motion Capture from Video** 為基礎。用於研究時請引用[原論文](https://arxiv.org/abs/2610.03691)。FlowHMR 原始碼採用 [Apache-2.0](../../LICENSE)；第三方元件和模型依各自授權發行，詳見發行套件內的 `THIRD_PARTY_NOTICES.md` 與[上游模型卡](https://huggingface.co/fafsaf1/flowhmr-0.46B)。
