# AI Card Project Starter

ComfyUI Portableを `127.0.0.1:8188` で動かし、承認済みモデルとAPI形式ワークフローだけを `uv` 管理のPythonから実行する、ローカル画像生成パイプラインです。

このリポジトリは公開可能な汎用パイプラインだけを保持します。原画、参照画像、生成物、ゲーム固有情報、モデル本体は追跡しません。カード枠、カード名、ルール文章、ロゴ、印刷用文字組みも画像生成には含めず、後工程で処理します。

## 構成

```text
AI-Card-Project-Starter/
├─ cardgen.py                  # 検証・生成CLI
├─ config/
│  ├─ app.json                # 共通設定
│  └─ profiles/*.json         # 生成パイプライン定義
├─ workflows/approved/*.json  # 承認済みComfyUI APIワークフロー
├─ licenses/                   # モデル出典・ライセンス・ハッシュ台帳
├─ tests/
├─ outputs/                    # Git追跡外の生成物と実行メタデータ
├─ .env.example
└─ pyproject.toml
```

## セットアップ

PowerShellでプロジェクトルートへ移動し、初回だけ依存関係を準備します。

```powershell
uv sync
```

ComfyUIのモデルをコピーせず、その場でハッシュ確認できるよう `.env` を作ります。

```powershell
Copy-Item .env.example .env
```

`.env` にComfyUIの `models` ディレクトリの絶対パスを指定します。

```dotenv
CARDGEN_COMFYUI_MODELS_DIR=C:\path\to\ComfyUI\models
```

`.env` とモデル本体はGit管理外です。環境変数を直接設定している場合は、その値が `.env` より優先されます。

## ComfyUIをlocalhost限定で起動

ComfyUI Portableのルートから起動します。

```powershell
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager
```

`--listen` は追加しません。このCLIは `http://127.0.0.1:<port>` または `http://localhost:<port>` 以外を拒否します。

接続、GPU、モデルディレクトリを確認します。

```powershell
uv run python cardgen.py check
```

## 生成プロファイル

```powershell
uv run python cardgen.py profiles
```

現在のプロファイルは次の6種類です。

| ID | 用途 | 入力画像 |
|---|---|---|
| `wai-hires` | WAI Illustrious SDXL、Hires Fix。既定プロファイル | 不要 |
| `wai-single` | WAI Illustrious SDXL、Single Pass | 不要 |
| `wai-controlnet` | Scribble ControlNet + Hires Fix | ラフ線画 |
| `wai-refine` | 既存画像のアップスケール + ディテール調整 | 仕上げたい画像 |
| `zimage` | Z-Image-Turbo | 不要 |
| `flux2-klein-edit` | FLUX.2 Klein Baseによる参照編集 | 編集元画像 |

`*` は `config/app.json` の既定プロファイルです。プロファイルはモデル単体ではなく、1種類の生成パイプラインを表します。

## 検証

既定プロファイル、個別プロファイル、全プロファイルをそれぞれ検証できます。

```powershell
uv run python cardgen.py validate
uv run python cardgen.py --profile zimage validate
uv run python cardgen.py validate --all
```

検証ではワークフロー形式、プロンプト・seed・入力画像のbinding、Sampler構成、negative方式、使用モデルがプロファイルの承認リストに含まれることを確認します。

## 生成例

### WAI Hires Fix

`wai-hires` は既定値なので `--profile` を省略できます。

```powershell
uv run python cardgen.py generate `
  --prompt "masterpiece, best quality, 1girl, silver hair, fantasy armor" `
  --negative "text, logo, watermark, low quality" `
  --count 1
```

Single Passを使う場合:

```powershell
uv run python cardgen.py --profile wai-single generate `
  --prompt "masterpiece, best quality, fantasy landscape"
```

`--negative` を省略するとプロファイルの既定値を使います。

### Scribble ControlNet

`--input-image` には生成したい完成画像ではなく、構造を指定するラフ線画を渡します。

```powershell
uv run python cardgen.py --profile wai-controlnet generate `
  --prompt "masterpiece, best quality, an archer drawing a longbow" `
  --input-image "C:\path\to\private-project\references\bow-scribble.png" `
  --count 1
```

ControlNetはbase passだけに適用し、Hires passでは構造を保ちながら仕上げます。

### 既存画像のアップスケール + Refine

```powershell
uv run python cardgen.py --profile wai-refine generate `
  --prompt "Preserve the composition and add clean print-ready detail" `
  --input-image "C:\path\to\private-project\art\source.png" `
  --count 1
```

入力画像はあらかじめ目的の縦横比へクロップしてください。このプロファイルは構図変更ではなく、アップスケールと軽いディテール追加を目的とします。

### Z-Image-Turbo

```powershell
uv run python cardgen.py --profile zimage generate `
  --prompt "A fantasy trading card illustration of a silver-haired knight at sunset, without text, logo, or watermark" `
  --count 1
```

標準Z-Imageワークフローのnegative経路は `ConditioningZeroOut` です。独立したネガティブ文章は適用されないため、除外条件はポジティブプロンプトへ自然文で含めます。

### FLUX.2 Klein参照編集

```powershell
uv run python cardgen.py --profile flux2-klein-edit generate `
  --prompt "Refine the supplied illustration while preserving its composition" `
  --input-image "C:\path\to\private-project\references\source.png" `
  --count 1
```

入力画像はユーザーが明示した既存の絶対パスだけを受け付け、localhostのComfyUIへ送信します。リポジトリへコピーせず、元の絶対パスやファイル名を実行メタデータへ記録しません。

## Seed、枚数、生成設定の上書き

固定seedから4枚生成すると、`1000`、`1001`、`1002`、`1003` を使います。

```powershell
uv run python cardgen.py --profile wai-hires generate `
  --prompt "masterpiece, best quality, fantasy knight" `
  --seed 1000 `
  --count 4
```

解像度とSampler設定も実行時に上書きできます。

```powershell
uv run python cardgen.py --profile wai-single generate `
  --prompt "masterpiece, best quality, fantasy knight" `
  --width 1024 `
  --height 1536 `
  --steps 28 `
  --cfg 5.5 `
  --sampler euler_ancestral `
  --scheduler karras
```

`--width` と `--height` は同時に、64以上かつ8の倍数で指定します。Sampler設定は該当入力を持つ全Samplerノードへ適用されます。

## 承認済みCheckpointの切り替え

同じワークフローで動くCheckpointは、新規プロファイルではなく既存プロファイルの `approved_models.checkpoints` へ追加します。

```json
"checkpoints": [
  "waiIllustriousSDXL_v170.safetensors",
  "anotherIllustriousModel.safetensors"
]
```

承認後、実行時に切り替えます。

```powershell
uv run python cardgen.py --profile wai-single generate `
  --checkpoint "anotherIllustriousModel.safetensors" `
  --prompt "masterpiece, best quality, 1girl"
```

モデル、LoRA、Custom Node、ワークフローは自動ダウンロードしません。

## 設定の役割

`config/app.json` はComfyUI URL、既定プロファイル、プロファイルディレクトリ、出力先、タイムアウトを定義します。

`config/profiles/<id>.json` はワークフロー、能力、binding、negative方式、Multi Passの有無、承認済みCheckpoint・UNet・CLIP・VAE・LoRA・Upscale Model・ControlNet、既定値を定義します。

新しいプロファイルを作るのは生成パイプラインが変わる場合です。解像度、seed、プロンプト、枚数、同一方式で切り替え可能なCheckpointだけの違いでは増やしません。プロファイルまたは承認ワークフローを変更した後は `validate` を実行してください。

## 出力と再現メタデータ

画像と `*_metadata.json` は `outputs/` に保存されます。現在のメタデータschemaはversion 5です。

主な記録内容:

- プロファイル、ワークフロー、プロンプト、negative、seed、結果ファイル
- ワークフローファイルの `workflow_sha256` と実際に送信したグラフの `queued_workflow_sha256`
- 全ノードのリテラル設定 `generation_settings.node_inputs`
- 使用モデルのファイルサイズ、全体 `sha256`、ModelSpec `weights_sha256`
- ComfyUI、Python、PyTorchのバージョン
- 出力画像ごとの `file_sha256`
- `app_config_sha256`、`profile_sha256`、`input_image_sha256`

プロンプト、seed、入力画像名は重複・誤認を避けるため `node_inputs` から除外し、それぞれ専用フィールドへ記録します。モデルハッシュはサイズと更新時刻をキーに `.cache/model-hashes.json` へキャッシュされます。

## モデルライセンスとハッシュ台帳

[licenses/README.md](licenses/README.md) に、使用モデルの出典、レビュー状態、商用生成物の可否、ローカルハッシュ照合結果をまとめています。

```powershell
uv run python -m licenses sync
uv run python -m licenses verify --require-approved --require-local-files
uv run python -m licenses report
```

`sync` は配布元情報を取得し、`.env` で指定したComfyUIモデルをその場で読み取って台帳を再生成します。大容量ファイルをハッシュするため時間がかかる場合があります。

- `File SHA-256`: 同じファイルを使ったか確認する全バイトのハッシュ
- `Weights SHA-256`: safetensorsの `modelspec.hash_sha256`。ヘッダーのパディング差を除いて重み同一性を確認
- `exact_file_match`: ローカルファイルと配布元または固定値が完全一致
- `weights_match`: ファイル全体は異なるが、Civitai AutoV3等と重みが一致

モデルファイルを別名へ置き換えたり更新した場合は、生成前に再度 `sync` と厳格な `verify` を実行してください。

## テスト

`uv sync` で `dev` グループのpytestが入るため、追加指定なしで実行できます。ComfyUIへは接続しません。

```powershell
uv run python -m pytest
```

## 安全上の境界

- ComfyUIはlocalhost以外へ公開しない
- `workflows/approved/` のAPI形式ワークフローだけを使う
- 選択プロファイルの `approved_models` に含まれるモデルだけを使う
- モデル、LoRA、Custom Node、ワークフローを明示承認なく追加・更新・削除しない
- 入力画像と生成物を公開リポジトリへ追加しない
- カード固有情報と印刷用文字組みは後工程で扱う

詳細な運用規則は [AGENTS.md](AGENTS.md) を参照してください。
