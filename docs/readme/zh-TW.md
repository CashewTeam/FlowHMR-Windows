# FlowHMR-Windows

[简体中文](../../README.md) · **繁體中文** · [English](en.md) · [日本語](ja.md) · [한국어](ko.md) · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows** 是一款在本機執行的影片動作擷取軟體。匯入一般影片後，可選取一位或多位人物，重建他們的 3D 人體動作，並同步預覽與匯出。動作重建採用上游 [FlowHMR](https://github.com/flowhmr/flowhmr) 模型；本儲存庫獨立維護 Windows 可攜版、模型與執行環境管理，以及從選人到匯出的桌面工作流程。

**[下載 Windows 可攜版](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [查看發行說明](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 論文](https://arxiv.org/abs/2610.03691)

## Windows 桌面版

- **匯入影片，在同一個視窗完成動作擷取。** 選取要處理的人物和影格範圍，生成後並排查看原始影片和 3D 動作。時間軸支援同步播放、拖曳定位和循環，可播放原聲，也可切換蒙皮和骨架顯示。
- **同時預覽和匯出多位人物。** 同一段影片可選取多位人物；合併預覽和 FBX 保留他們在原始影片中的相對站位和入場時間。舊任務可從附縮圖的歷程中重新開啟。
- **依素材調整動作。** 可調整動作濾波、腳部接觸修正和地面高度。需要更細緻的手指動作時，可啟用 HaMeR 手指精修。預設使用固定機位，鏡頭明顯轉向、俯仰或翻滾時再用運動相機。
- **在本機執行。** 依需要安裝推論環境和模型，開始生成時才載入模型。安裝完成後可離線處理。支援简体中文、繁體中文、English、日本語、한국어、Русский 和 Français，重新開啟時會記住上次選的語言。
- **動作匯出。** 支援 FlowHMR / Mixamo / MetaHuman FBX、MMD VMD 和 NPZ。FlowHMR 和 Mixamo FBX 可選蒙皮，以及第 **−1 幀**的 T Pose / A Pose。見[匯出與重定向](../retarget-export.md)。
- **自訂角色。** 支援 PMX 和含蒙皮的 Mixamo / MetaHuman FBX；多人可各自選角，並記住骨骼調整。相容的 PMX 可切換原生 IK / FK，依角色匯出的 VMD 可保留腳部 IK。

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
2. 安裝相容的顯示卡驅動程式，再從右上角 **執行環境** 下載並啟用對應版本。NVIDIA 使用者請選 CUDA，RTX 50 系列建議使用 CUDA 12.8。AMD Radeon RX 9000 系列中採用 `gfx1201` 的型號，可依下方步驟試用 ROCm。
3. 開啟 **模型**，選擇 **下載所有公開模型**（下载全部公开模型），或逐項下載。公開模型經驗證後會放入 `models/`，其中 SAM 3D Body 和 VGGT-Omega 由 ModelScope 提供。不需要手指精修時可以略過 HaMeR。需登入下載的官方模型請依下表取得。
4. 將影片拖入軟體，選取要處理的人物和影格範圍，再開始生成。完成後先預覽，再從選單匯出 FBX、VMD 或 NPZ。

### AMD Radeon RX 9000 系列（實驗支援）

AMD 和 NVIDIA 使用同一個 `FlowHMR-Windows.zip` 和 `FlowHMR.exe`。在 Windows 11 上使用 Radeon RX 9000 系列的 `gfx1201` 型號時，首次開啟軟體後，請在 **執行環境** 下載並啟用 **ROCm 7.14.0 · gfx1201**。軟體會記住這項選擇，並自動選用相容的 AMD 顯示卡。模型安裝步驟相同。

已驗證 AMD 官方套件的下載、解壓縮及程式庫載入。RX 9070 XT 上的完整動作擷取流程，包括移動相機與 HaMeR，仍待實機驗證。請參閱 [AMD 安裝與驗證說明](../../docs/amd-rocm.md)。

### 該下載哪個官方模型？

先登入 [MANO 官網下載頁](https://mano.is.tue.mpg.de/download.php)。**除了上一步安裝的公開模型，基本動作擷取只需要下表任一官方套件。若已有 `mano_v1_2.zip`，不必再下載中性套件。** 若兩個套件都已安裝，預設優先使用中性體型。

| 用途 | 官網下載項目 | 手動解壓縮到 |
| --- | --- | --- |
| 可用於基本動作擷取的中性體型 | **Extended SMPL+H model**（AMASS），`smplh.tar.xz` | `models/body_models/smplh/` |
| 可用於基本動作擷取的男性、女性體型；也包含手指精修所需的 MANO 檔案 | **Models & Code**，`mano_v1_2.zip` | `models/` |

下載後，點擊 **模型 → 匯入模型壓縮檔…**，直接選取 `mano_v1_2.zip` 或 `smplh.tar.xz`。程式會自動解壓縮、辨識模型並安裝到正確位置，保留原始壓縮檔；模型面板會顯示哪些模型群組已就緒、哪些仍缺少檔案。若已解壓縮，也可點擊 **匯入已解壓縮的資料夾…**，選取解壓縮後的資料夾。

手指精修需要 `mano_v1_2.zip` 中的 MANO 檔案和公開的 **HaMeR** 模型。HaMeR 可從該列單獨下載，也包含在 **下載所有公開模型** 中。

手動安裝的完整目錄結構、下載來源和模型用途請參閱[模型檔案說明](../../models/README.md)。

## 說明與專案來源

- [Windows 使用、升級和原始碼建置說明](../../docs/windows.md) · [回報問題](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [維護介面語言](../../docs/localization.md)
- [FlowHMR 官方專案頁](https://flowhmr.github.io/) · [上游推論與訓練資料](https://github.com/flowhmr/flowhmr#readme) · [上游論文引用格式](https://github.com/flowhmr/flowhmr#citation)

動作重建以 Zhanke Wang 等人的 **FlowHMR: Physically Plausible Motion Capture from Video** 為基礎。用於研究時請引用[原論文](https://arxiv.org/abs/2610.03691)。FlowHMR 原始碼採用 [Apache-2.0](../../LICENSE)；第三方元件和模型依各自授權發行，詳見發行套件內的 `THIRD_PARTY_NOTICES.md` 與[上游模型卡](https://huggingface.co/fafsaf1/flowhmr-0.46B)。
