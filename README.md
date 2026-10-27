# AI Card Project Starter

ComfyUI Portableを `127.0.0.1:8188` で動かし、承認済みモデルとAPI形式ワークフローだけを `uv` 管理のPythonから実行する、ローカル画像生成パイプラインです。

このリポジトリは公開可能な汎用パイプラインだけを保持します。原画、参照画像、生成物、ゲーム固有情報、モデル本体は追跡しません。カード枠、カード名、ルール文章、ロゴ、印刷用文字組みも画像生成には含めず、後工程で処理します。

## 構成

```text
AI-Card-Project-Starter/
├─ cardgen.py                  # 検証・生成CLI
├─ project_env.py              # .env の読み込み
├─ config/
│  ├─ app.json                 # 共通設定
│  ├─ profiles/*.json          # 生成パイプライン定義
│  └─ README.md                # プロファイルを分ける基準
├─ workflows/approved/
│  ├─ *.json                   # 承認済みComfyUI APIワークフロー
│  └─ README.md                # ワークフローとプロファイルの対応
├─ docs/metadata-design.md     # メタデータ設計の背景と実例
├─ licenses/                   # モデル出典・ライセンス・ハッシュ台帳（CLI付き）
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

出力の `*` が既定プロファイルで、`config/app.json` の `default_profile` で切り替えます。

現在のプロファイルは次の11種類です。

| ID | 用途 | 入力画像 |
|---|---|---|
| `wai-hires` | WAI Illustrious SDXL、Hires Fix。既定プロファイル | 不要 |
| `wai-hires-latent` | 同上。拡大をlatentで行う比較用 | 不要 |
| `wai-single` | WAI Illustrious SDXL、Single Pass | 不要 |
| `wai-controlnet` | Scribble ControlNet + Hires Fix | ラフ線画 |
| `wai-refine` | 既存画像のアップスケール + ディテール調整 | 仕上げたい画像 |
| `wai-inpaint` | マスクした範囲だけを描き直す。拡大の段は持たない | 直したい画像 + 同寸のマスク |
| `zimage` | Z-Image-Turbo | 不要 |
| `zimage-base` | Z-Image Base系（Z-Anime Base FP8）、ネガティブ文章を適用 | 不要 |
| `lumina2` | Lumina Image 2.0系（NetaYume v4 AIO） | 不要 |
| `flux2-klein-edit` | FLUX.2 Klein Baseによる参照編集 | 編集元画像 |
| `esrgan-upscale` | Real-ESRGANで4倍に拡大するだけ。サンプラーを持たない | 拡大したい画像 |

プロファイルはモデル単体ではなく、1種類の生成パイプラインを表します。

各プロファイルが何を入力に取り、途中で何が起き、出力がどう変わるかは
[docs/profiles-explained.md](docs/profiles-explained.md) にあります。

## 検証

既定プロファイル、個別プロファイル、全プロファイルをそれぞれ検証できます。

```powershell
uv run python cardgen.py validate
uv run python cardgen.py --profile zimage validate
uv run python cardgen.py validate --all
```

検証は次を確認します。

- ワークフローがAPI形式で読み込めること
- プロンプト・seedのbindingが実在するノードと入力を指していること
- 入力画像・マスク画像・denoiseのbindingを持つプロファイルでは、それらも解決できること
- Sampler数とプロファイルの `multi_pass` 指定が一致すること
- negative方式（`text` / `zeroed` / `mixed`）がプロファイルの宣言と一致すること
- ワークフローが参照するモデルが `approved_models` に含まれること

入力画像とマスク画像のbindingはアップロード後に解決されるため、`validate` ではプレースホルダ名で解決だけを試します。node_idやフィールド名が古くなっていれば、生成を始める前に失敗します。

Claude Codeがファイルを編集すると、PostToolUseフック（[.claude/hooks/verify_on_edit.py](.claude/hooks/verify_on_edit.py)）が編集先に応じた検査を自動実行します。`config/` または `workflows/approved/` のJSONなら `validate --all`、`cardgen.py` または `tests/` のPythonなら `pytest` です。失敗するとその場でエラーが差し戻されます。エディタやシェルで手を入れたときはフックが走らないため、自分で該当する検査を実行してください。

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

### 拡大経路の比較（latent vs ピクセル）

`wai-hires-latent` は `wai-hires` の拡大経路だけを差し替えた比較用プロファイルです。`wai-hires` はVAEDecode後にReal-ESRGANでピクセル拡大して戻しますが、こちらはlatentのまま `LatentUpscaleBy`（bislerp、1.5倍）で拡大します。最終解像度はどちらも1536x2016です。

同じseedを渡すと2経路を直接比較できます。

```powershell
uv run python cardgen.py --profile wai-hires generate `
  --prompt "masterpiece, best quality, 1girl, silver hair, fantasy armor" `
  --seed 1000 --count 4

uv run python cardgen.py --profile wai-hires-latent generate `
  --prompt "masterpiece, best quality, 1girl, silver hair, fantasy armor" `
  --seed 1000 --count 4
```

2nd passの `denoise` は両者とも0.45で揃えてあります。sampler設定も一致しているので、変数は拡大経路だけです。この一致はテストで固定してあります。

`--denoise` で2nd passだけを振れます。

```powershell
uv run python cardgen.py --profile wai-hires-latent generate `
  --prompt "masterpiece, best quality, 1girl, silver hair, fantasy armor" `
  --seed 1000 --denoise 0.35
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

既定の `denoise` は0.35です。これは構図を保つ値であって、顔を保つ値ではありません。元絵の顔を残したい場合は `--denoise` で下げてください。

```powershell
uv run python cardgen.py --profile wai-refine generate `
  --prompt "Preserve the composition and add clean print-ready detail" `
  --input-image "C:\path\to\private-project\art\source.png" `
  --denoise 0.25
```

### マスクした範囲だけを描き直す

```powershell
uv run python cardgen.py --profile wai-inpaint generate `
  --prompt "Repair the masked area, matching the surrounding art" `
  --input-image "C:\path\to\private-project\art\source.png" `
  --mask-image "C:\path\to\private-project\art\source-mask.png" `
  --count 1
```

マスクは入力画像と同じ寸法の白黒画像で、**白が描き直す側、黒が残す側**です（`LoadImageMask` はredチャンネルを読みます）。マスクの外の画素はそのまま残ります。

既定の `denoise` は0.8です。マスクの中を構造から描き直すための値で、下げると元の形が残ります。

拡大の段を持たないため、出力の寸法は入力と同じです。印刷解像度へ上げるのは `wai-refine` の仕事で、この工程の後段に置きます。

このプロファイルの既定negativeには被写体の語（`extra arms`、`deformed anatomy` など）を入れていません。多腕や多面をマスクで直す用途では、それらの語が作りたいものと戦うためです。被写体に関する語は `--negative` で渡しますが、`--negative` は既定値へ追加するのではなく置き換えるため、残したい品質語ごと書き直してください。

### 拡大だけを行う

```powershell
uv run python cardgen.py --profile esrgan-upscale generate `
  --input-image "C:\path\to\private-project\art\source.png"
```

サンプラーを通さないため、`--prompt`、`--negative`、`--seed` は書き込む先が無く、渡しても出力は変わりません。seedのbindingを持たないので `--count` は1だけです（同じ入力からは常に同じ出力が出ます）。

出力の寸法はモデルの倍率（x4）まかせです。カード比率へのクロップはこの工程に含めず、後工程で行います。

### Z-Image-Turbo

```powershell
uv run python cardgen.py --profile zimage generate `
  --prompt "A fantasy trading card illustration of a silver-haired knight at sunset, without text, logo, or watermark" `
  --count 1
```

標準Z-Imageワークフローのnegative経路は `ConditioningZeroOut` です。独立したネガティブ文章は適用されないため、除外条件はポジティブプロンプトへ自然文で含めます。

### Z-Anime Base / NetaYume Lumina

両方とも1パス・832×1216・1枚ずつを既定とし、独立したnegative文章が効きます。
`zimage-base` はZ-Anime Base FP8（28 steps / CFG 4 / Euler ancestral / beta）、
`lumina2` はNetaYume v4 AIO（40 steps / CFG 5 / res_multistep / linear_quadratic）です。
Z-AnimeのBaseは、negativeを無効化する既存の `zimage`（Turbo）とは別の経路です。

```powershell
uv run python cardgen.py --profile zimage-base generate --prompt "An original adult knight in silver armor, standing in ancient ruins at sunset, detailed anime illustration" --negative "text, logo, watermark, border, low quality" --count 1

uv run python cardgen.py --profile lumina2 generate --prompt "You are an assistant designed to generate anime images based on textual prompts. <Prompt Start> An original adult knight in silver armor, standing in ancient ruins at sunset, detailed anime illustration" --negative "text, logo, watermark, border, low quality" --count 1
```

モデルの固定revisionと配布元ハッシュは `licenses/registry.toml` に記録しています。
NetaYume AIOに含まれるGemmaの利用規約も適用されるため、パイプライン全体を
Apache-2.0だけで扱わないでください。生成画像の商用利用・販売と、モデル再配布・
生成サービス提供の条件は `licenses/reviews/` に分けて記録しています。

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

`--width` と `--height` は同時に、64以上かつ8の倍数で指定します。Sampler設定は該当入力を持つ全Samplerノードへ適用されますが、プロファイルが同名の `bindings` を持つ項目は指名された1ノードだけに書き込まれます。

`--count` は1回の実行につき1〜8枚です。`--timeout` を指定すると `config/app.json` の `generation_timeout_seconds`（既定900秒）を上書きします。

`--denoise` は常に1ノードにだけ適用されます。`--steps` や `--cfg` を該当入力を持つ全Samplerへ一括適用するのと同じことを denoise でやると base pass まで巻き込みます。空のlatentに対する base pass を1.0未満で回すと絵になりません。そのため `--denoise` はプロファイルの `bindings.denoise` が指す1ノードにだけ適用され、bindingを持たないプロファイルではエラーになります。現在対応しているのは `wai-hires`、`wai-hires-latent`、`wai-refine`、`wai-inpaint` です。

`--steps`、`--cfg`、`--sampler`、`--scheduler` も、プロファイルが同名の binding（`bindings.steps` など）を持つ場合はその1ノードだけに書き込みます。設定をsamplerノードの外に持つグラフのための逃げ道で、`flux2-klein-edit` が steps を `Flux2Scheduler`、cfg を `CFGGuider`、sampler_name を `KSamplerSelect` へ指名しています。どのオプションがどのプロファイルでどこへ落ちるかは [docs/profiles-explained.md](docs/profiles-explained.md#既定値と上書き) にあります。

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

画像と `*_metadata.json` は `outputs/` に保存されます。現在のメタデータschemaはversion 7です。

主な記録内容:

- プロファイル、ワークフロー、プロンプト、negative、seed、結果ファイル
- ワークフローファイルの `workflow_sha256` と実際に送信したグラフの `queued_workflow_sha256`
- 全ノードのリテラル設定 `generation_settings.node_inputs`
- 使用モデルのファイルサイズ、全体 `sha256`、ModelSpec `weights_sha256`
- ComfyUI、Python、PyTorchのバージョン
- 出力画像ごとの `file_sha256`
- `app_config_sha256`、`profile_sha256`、`input_image_sha256`

プロンプト、seed、入力画像名は重複・誤認を避けるため `node_inputs` から除外し、それぞれ専用フィールドへ記録します。モデルハッシュはサイズと更新時刻をキーに `.cache/model-hashes.json` へキャッシュされます。

なぜこの形なのか（全ノードを記録する理由、`sha256` と `weights_sha256` の使い分けなど）は [docs/metadata-design.md](docs/metadata-design.md) にあります。

### 失敗した実行も記録する

`--count 4` の3枚目で落ちた場合、1〜2枚目の画像は既に `outputs/` にあります。これらが記録なしで残ると、どのseedで、どのワークフローで、どの重みで生成されたのか追えません。そのため失敗時も同じ `*_metadata.json` を書き出します。

- `status` — `"ok"` または `"error"`
- `failure` — 成功時は `null`。失敗時は `error_type`、`message`、`failed_on_image`、`requested_count`
- `results` — 完走した分だけが入る（1枚も無ければ空配列）

例外は記録後に再送出されるため、終了コードは従来どおり1です。`METADATA:` 行は例外の送出前にstdoutへ出るので、標準出力を解析する呼び出し元も失敗した実行の記録を拾えます。Ctrl-Cやネットワーク断も同じ経路で記録されます。

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
- 入力画像・マスク画像と生成物を公開リポジトリへ追加しない
- カード固有情報と印刷用文字組みは後工程で扱う

詳細な運用規則は [AGENTS.md](AGENTS.md) を参照してください。
