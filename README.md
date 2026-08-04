# AI Card Project Starter

ComfyUI Portableを `127.0.0.1:8188` で動かし、`uv` 管理のPythonから承認済みAPIワークフローだけを実行するローカル画像生成スターターです。

```text
AI-Card-Project-Starter-v3/
├─ cardgen.py
├─ config/
│  ├─ app.json
│  └─ profiles/
│     ├─ wai-hires.json
│     ├─ wai-single.json
│     └─ zimage.json
├─ workflows/approved/
│  ├─ wai_sdxl_hires_api.json     # WAI Hires Fix
│  ├─ wai_sdxl_single_api.json    # WAI Single Pass
│  └─ z_image_turbo_api.json      # 同梱済み
├─ outputs/
├─ licenses/
├─ CLAUDE.md
└─ pyproject.toml
```

## 2. uv環境を準備

PowerShellでprojectのルートへ移動し、初回だけ実行します。

```powershell
uv sync
```

グローバルPythonや仮想環境の手動有効化は不要です。

## 3. プロファイルを確認

```powershell
uv run python cardgen.py profiles
```

例:

```text
* wai-hires: WAI Illustrious SDXL (Hires Fix) [ready]
  wai-single: WAI Illustrious SDXL (Single Pass) [ready]
  zimage: Z-Image-Turbo [ready]
* = default profile
```

`*` は `config/app.json` の既定プロファイルです。

## 4. 検証

既定のWAI Hires Fix:

```powershell
uv run python cardgen.py validate
```

Z-Image-Turbo:

```powershell
uv run python cardgen.py --profile zimage validate
```

全プロファイル:

```powershell
uv run python cardgen.py validate --all
```

必要なモデルまたはワークフローが未配置の場合、`validate --all` は該当プロファイルで停止します。その場合は準備済みのプロファイルを個別検証してください。

## 5. ComfyUI接続確認

ComfyUI Portableを起動してから実行します。

```powershell
uv run python cardgen.py check
```

ComfyUIは次のようにlocalhost限定で起動してください。

```powershell
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager
```

`--listen` は追加しません。

## 6. WAIで生成

WAI Hires Fixは既定プロファイルなので、`--profile wai-hires` を省略できます。Real-ESRGAN 4x+ Anime6Bを使い、ベンダー推奨解像度のHires Fixを実行します。

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

`--negative` を省略すると、選択したプロファイルの既定値を使います。

## 7. Z-Image-Turboで生成

```powershell
uv run python cardgen.py --profile zimage generate `
  --prompt "A fantasy trading card illustration of a silver-haired knight at sunset, without text, logo, or watermark" `
  --count 1
```

標準Z-Imageワークフローのnegative経路は `ConditioningZeroOut` です。独立したネガティブプロンプトは適用されないため、除外条件はポジティブプロンプトへ自然文で含めます。

### FLUX.2 Kleinで参照編集

入力画像を所有・管理する別プロジェクト内の絶対パスを明示します。この公開スターターには
入力画像を保管しません。

```powershell
uv run python cardgen.py --profile flux2-klein-edit generate `
  --prompt "Refine the supplied illustration while preserving its composition" `
  --input-image "C:\path\to\private-project\references\source.png" `
  --count 1
```

入力画像はlocalhostのComfyUIへ送信するために読み取るだけで、このリポジトリへのコピーや
メタデータへの絶対パス記録は行いません。相対パスは受け付けません。

## 8. Seedと複数生成

固定Seed:

```powershell
uv run python cardgen.py --profile wai-hires generate `
  --prompt "masterpiece, best quality, 1girl" `
  --seed 123456789
```

連番Seedで4枚:

```powershell
uv run python cardgen.py --profile wai-hires generate `
  --prompt "masterpiece, best quality, fantasy knight" `
  --seed 1000 `
  --count 4
```

この場合は `1000`、`1001`、`1002`、`1003` を使用します。

## 9. 解像度とSampler設定の上書き

生成時に解像度、ステップ数、CFG、Sampler、Schedulerを上書きできます。

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

`--width` と `--height` は必ず同時に、64以上かつ8の倍数で指定します。Sampler設定は、対象入力を持つすべてのSamplerノードへ適用されます。複数Samplerのワークフローで `--steps` を指定しても、`start_at_step` と `end_at_step` の分割点は変更されません。

## 10. 同じWAI方式でCheckpointを追加

同じWAIワークフローで動くCheckpointなら、新規プロファイルを作らず、使用するWAIプロファイルの `approved_models.checkpoints` へ承認ファイル名を追加します。

```json
"checkpoints": [
  "waiIllustriousSDXL_v170.safetensors",
  "anotherIllustriousModel.safetensors"
]
```

実行時に切り替えます。

```powershell
uv run python cardgen.py --profile wai-single generate `
  --checkpoint "anotherIllustriousModel.safetensors" `
  --prompt "masterpiece, best quality, 1girl"
```

`--checkpoint` はワークフロー内の全Checkpoint Loaderを同じ承認済みファイルへ変更します。

## 11. 新しいプロファイルを作る基準

新しいJSONを `config/profiles/` に追加するのは、生成方式が変わる場合です。

- SDXLとZ-Image/Flux
- Single PassとHires Fixなど、生成パスが異なる方式
- ControlNet必須
- インペイント専用
- アップスケール専用

解像度、Seed、プロンプト、生成枚数だけの違いでプロファイルを増やさないでください。

## 設定ファイルの役割

### `config/app.json`

全プロファイル共通です。

- ComfyUI URL
- 既定プロファイル
- プロファイルディレクトリ
- 出力先
- HTTP・生成タイムアウト

### `config/profiles/<id>.json`

1つの生成パイプラインを定義します。

- APIワークフロー
- negativeの方式
- Multi Passの有無
- 承認済みCheckpoint・UNet・CLIP・VAE・LoRA・Upscale Model
- 既定ネガティブプロンプト

## 出力

画像とメタデータは `outputs/` に保存されます。メタデータには次が記録されます。

- 使用プロファイル
- ワークフロー
- モデルファイル
- プロンプト
- Seed
- 実際に使用した解像度とSampler設定
- ComfyUI prompt ID
- 保存画像パス

元画像はComfyUI側のoutputフォルダにも残ります。

## 対応範囲

- `KSampler` / `KSamplerAdvanced`
- Single Pass / Multi Pass（Hires Fix）
- `EmptyLatentImage` / `EmptySD3LatentImage` の解像度上書き
- Steps、CFG、Sampler、Schedulerの実行時上書き
- `CLIPTextEncode`
- `CLIPTextEncodeSDXL`
- `CLIPTextEncodeSDXLRefiner`
- negative側の `ConditioningZeroOut`
- Checkpoint、UNet、CLIP、VAE、LoRAのカテゴリ別承認
- Upscale Modelの承認
- 承認済みCheckpointの実行時切り替え

ControlNet、Conditioning Combine、地域プロンプトなどは、自動プロンプト置換の対象外です。
