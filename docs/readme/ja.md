# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · **日本語** · [한국어](ko.md) · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows** は、手元の PC で動画からモーションをキャプチャするアプリです。動画を読み込んで一人または複数の人物を選ぶと、3D の動作を再構成し、元動画と同期してプレビューや書き出しができます。動作の再構成には上流の [FlowHMR](https://github.com/flowhmr/flowhmr) モデルを使用します。このリポジトリでは、Windows ポータブル版、モデルと実行環境の管理、および人物選択から書き出しまでのデスクトップ機能を独立して開発・保守しています。

**[Windows ポータブル版をダウンロード](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [リリースノート](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR の論文](https://arxiv.org/abs/2610.03691)

## v0.3.4 の変更点

VRAM が少ない環境で、複数人のモーション生成後に立ち位置を合わせると `Expected a cuda device, but got: cpu` が発生する問題を修正しました。**主に VRAM 8 GB 以下の環境向けです。問題がなければ v0.3.3 のまま使えます。** 更新後は履歴から既存のタスクを開いて **シーンの位置合わせ** を実行すれば、モーションを再生成せずに再試行できます。

[今回のリリースノート](../release-windows.md) · [v0.3.3：SAM バッチの自動調整とローカル MCP](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/tag/windows-v0.3.3)

## 最小動作環境と対応 GPU

| 項目 | 最小 | 推奨 |
| --- | --- | --- |
| OS | 64 ビット版 Windows 10 / 11 | 64 ビット版 Windows 11 |
| メモリ | 16 GB | 32 GB 以上 |
| VRAM | 6 GB | 8 GB 以上 |

| GPU | 実行環境 |
| --- | --- |
| NVIDIA GeForce RTX 20 / 30 / 40 / 50 | アプリで対応する CUDA を有効にします。RTX 50 シリーズは 12.8 または 12.9 を選択してください。 |
| AMD Radeon RX 9000 / 7000 | Windows 11 で GPU に対応する [ROCm 実行環境](../amd-rocm.md)を設定します。 |

## Windows デスクトップ版

- **ひとつのウィンドウでモーションキャプチャ。** 人物とフレーム範囲を選び、生成した 3D モーションと元動画を並べて確認できます。再生、シーク、ループはタイムラインで同期します。元動画の音声を再生でき、スキンと骨格の表示も切り替えられます。
- **複数の人物をまとめて確認・書き出し。** 一つの動画から複数人を選べます。プレビューと FBX には、元動画での相対位置とそれぞれの登場タイミングが保持されます。過去のジョブはサムネイル付きの履歴から開き直せます。
- **素材に合わせて動作を調整。** モーションの平滑化、足の接地補正、地面の高さを調整できます。より細かな指の動きが必要な場合は HaMeR を有効にします。既定では固定カメラを使い、カメラが大きく首振り、チルト、ロールする素材には移動カメラを選びます。
- **ローカルで動作。** 推論環境とモデルは必要に応じてインストールし、モデルの読み込みは生成時に行います。セットアップ後はオフラインで処理できます。UI は简体中文、繁體中文、English、日本語、한국어、Русский、Français に対応し、最後に選んだ言語を記憶します。
- **モーションの書き出し。** FlowHMR、Mixamo、MetaHuman、Maya AdvancedSkeleton の FBX、MMD VMD、VRM Animation（VRMA）、NPZ に対応します。FlowHMR と Mixamo の FBX ではスキンと **−1 フレーム**の T ポーズ／A ポーズを選べます。[書き出しガイド](../retarget-export.md)を参照してください。
- **カスタムキャラクター。** PMX、VRM 0.x／1.0、スキン付きの Mixamo／MetaHuman FBX をプレビューできます。人物ごとにモデルとボーンの調整を保存でき、対応する PMX では IK／FK を切り替えられます。

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
2. 対応する GPU ドライバーをインストールし、上の表に従って CUDA または ROCm を設定します。
3. **モデル** で **公開モデルをすべてダウンロード**（下载全部公开模型）を選ぶか、モデルを個別に取得します。公開モデルは検証のうえ `models/` に配置され、SAM 3D Body と VGGT-Omega は ModelScope から取得します。指の補正が不要なら個別に取得して HaMeR を省けます。ログインが必要な公式モデルは下表を参照してください。
4. アプリに動画をドロップし、人物とフレーム範囲を選択して生成します。結果をプレビューしてから、メニューで FBX、VMD、VRMA、または NPZ に書き出せます。

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
