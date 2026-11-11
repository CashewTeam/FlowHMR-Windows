# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · **日本語** · [한국어](ko.md) · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows** は、手元の PC で動画からモーションをキャプチャするアプリです。動画を読み込んで一人または複数の人物を選ぶと、3D の動作を再構成し、元動画と同期してプレビューや書き出しができます。動作の再構成には上流の [FlowHMR](https://github.com/flowhmr/flowhmr) モデルを使用します。このリポジトリでは、Windows ポータブル版、モデルと実行環境の管理、および人物選択から書き出しまでのデスクトップ機能を独立して開発・保守しています。

**[Windows ポータブル版をダウンロード](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [リリースノート](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR の論文](https://arxiv.org/abs/2610.03691)

## Windows デスクトップ版

- **ひとつのウィンドウでモーションキャプチャ。** 人物とフレーム範囲を選び、生成した 3D モーションと元動画を並べて確認できます。再生、シーク、ループはタイムラインで同期します。元動画の音声を再生でき、スキンと骨格の表示も切り替えられます。
- **複数の人物をまとめて確認・書き出し。** 一つの動画から複数人を選べます。プレビューと FBX には、元動画での相対位置とそれぞれの登場タイミングが保持されます。過去のジョブはサムネイル付きの履歴から開き直せます。
- **素材に合わせて動作を調整。** モーションの平滑化、足の接地補正、地面の高さを調整できます。より細かな指の動きが必要な場合は HaMeR を有効にします。既定では固定カメラを使い、カメラが大きく首振り、チルト、ロールする素材には移動カメラを選びます。
- **ローカルで動作。** 推論環境とモデルは必要に応じてインストールし、モデルの読み込みは生成時に行います。セットアップ後はオフラインで処理できます。UI は简体中文、繁體中文、English、日本語、한국어、Русский、Français に対応し、最後に選んだ言語を記憶します。
- **モーションの書き出し。** FlowHMR、Mixamo、MetaHuman の FBX、MMD VMD、NPZ に対応します。FlowHMR と Mixamo の FBX ではスキンと **−1 フレーム**の T ポーズ／A ポーズを選べます。[書き出しガイド](../retarget-export.md)を参照してください。
- **カスタムキャラクター。** PMX やスキン付きの Mixamo／MetaHuman FBX をプレビューできます。人物ごとにモデルとボーンの調整を保存でき、対応する PMX では IK／FK を切り替えられます。

## サンプル動画

サムネイルをクリックすると MP4 が開きます。4 本とも中性の SMPL-H 体型を正面から平行投影で表示した 30 FPS の動画です。選択範囲の元音声を同期して収録しています。

| ドリブルとダンスステップ | ダンス |
| --- | --- |
| [![ドリブルとダンスステップ](../../assets/demos/01-ikun-thumb.png)](../../assets/demos/01-ikun-front.mp4) | [![ダンス](../../assets/demos/02-selei-thumb.png)](../../assets/demos/02-selei-front.mp4) |
| **舞台キャラクターの動き** | **ジェスチャーとステップ** |
| [![舞台キャラクターの動き](../../assets/demos/03-test2-thumb.png)](../../assets/demos/03-test2-front.mp4) | [![ジェスチャーとステップ](../../assets/demos/04-test3-thumb.png)](../../assets/demos/04-test3-front.mp4) |

アプリを試すときは、リポジトリ内の[サンプル素材](../../assets/example/example.mp4)を使用できます。

## クイックスタート

1. 64 ビットの Windows 10 または 11 で `FlowHMR-Windows.zip` をすべて展開します。展開された `FlowHMR` フォルダーを開き、`FlowHMR.exe` をダブルクリックします。基本パッケージには Python、UI、FBX 書き出しコンポーネントが含まれます。
2. GPU に対応したドライバーをインストールし、右上の **実行環境**（运行环境）から対応するランタイムをダウンロードして有効にします。NVIDIA では CUDA を選択します。RTX 50 シリーズには CUDA 12.8 を推奨します。AMD Radeon RX 9000 シリーズのうち `gfx1201` のモデルでは、以下の手順で試験版の ROCm を利用できます。
3. **モデル** で **公開モデルをすべてダウンロード**（下载全部公开模型）を選ぶか、モデルを個別に取得します。公開モデルは検証のうえ `models/` に配置され、SAM 3D Body と VGGT-Omega は ModelScope から取得します。指の補正が不要なら個別に取得して HaMeR を省けます。ログインが必要な公式モデルは下表を参照してください。
4. アプリに動画をドロップし、人物とフレーム範囲を選択して生成します。結果をプレビューしてから、メニューで FBX、VMD、または NPZ に書き出せます。

### AMD Radeon RX 9000 シリーズ（試験対応）

AMD でも NVIDIA と同じ `FlowHMR-Windows.zip` と `FlowHMR.exe` を使います。Windows 11 で Radeon RX 9000 シリーズの `gfx1201` モデルを使用する場合は、初回起動時に **実行環境** から **ROCm 7.14.0 · gfx1201** をダウンロードして有効にします。設定は保存され、対応する AMD GPU が自動で選ばれます。モデルの導入手順は同じです。

AMD 公式パッケージのダウンロード、展開、ライブラリの読み込みは確認済みです。動くカメラの推定や HaMeR を含む一連のモーションキャプチャは、RX 9070 XT 実機では未検証です。[AMD のセットアップと検証手順](../../docs/amd-rocm.md)を参照してください。

### どの公式モデルをダウンロードする？

[MANO 公式ダウンロードページ](https://mano.is.tue.mpg.de/download.php)にログインします。**基本のモーションキャプチャでは、前の手順で入れた公開モデルに加え、下表の公式パッケージのどちらか一方が必要です。`mano_v1_2.zip` をお持ちなら、中性体型のパッケージを別途取得する必要はありません。** 両方をインストールした場合は、既定で中性体型を使用します。

| 用途 | 公式サイトのダウンロード項目 | 手動で展開する場所 |
| --- | --- | --- |
| 基本のモーションキャプチャに使える中性体型 | **Extended SMPL+H model**（AMASS）、`smplh.tar.xz` | `models/body_models/smplh/` |
| 基本のモーションキャプチャに使える男性・女性体型と、指の補正に必要な MANO ファイル | **Models & Code**、`mano_v1_2.zip` | `models/` |

ダウンロード後、**モデル → モデルのアーカイブを読み込む…**（模型 → 导入模型压缩包…）を選び、`mano_v1_2.zip` または `smplh.tar.xz` をそのまま指定します。アプリが自動で展開し、モデルを識別して適切な場所にインストールします。元のアーカイブは残ります。準備済みのグループや不足ファイルはモデル画面で確認できます。すでに展開した場合は **展開済みフォルダーを読み込む…**（导入已解压文件夹…）を選んでください。

指の補正には `mano_v1_2.zip` 内の MANO ファイルと公開 **HaMeR** モデルが必要です。HaMeR はモデルの行から個別に取得するか、**公開モデルをすべてダウンロード** でまとめて取得できます。

手動インストール時のフォルダー構成、入手先、各モデルの用途は[モデルファイルの説明](../../models/README.md)を参照してください。

## ヘルプとクレジット

- [Windows での使い方、更新、ソースからのビルド](../../docs/windows.md) · [問題を報告](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [UI 翻訳のメンテナンス](../../docs/localization.md)
- [FlowHMR 公式プロジェクトページ](https://flowhmr.github.io/) · [上流の推論・学習ドキュメント](https://github.com/flowhmr/flowhmr#readme) · [上流の引用形式](https://github.com/flowhmr/flowhmr#citation)

モーションの再構成は Zhanke Wang らの **FlowHMR: Physically Plausible Motion Capture from Video** に基づきます。研究で使用する場合は、[原論文](https://arxiv.org/abs/2610.03691)を引用してください。FlowHMR のソースコードは [Apache-2.0](../../LICENSE) で公開されています。サードパーティーのコンポーネントやモデルはそれぞれのライセンスで配布されます。詳細はリリースパッケージ内の `THIRD_PARTY_NOTICES.md` と[上流のモデルカード](https://huggingface.co/fafsaf1/flowhmr-0.46B)を参照してください。
