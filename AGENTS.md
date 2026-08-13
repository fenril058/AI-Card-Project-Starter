# AI Card Illustration Project

## 実行環境

- Pythonは必ず `uv run python ...` で実行する。
- ComfyUIは `http://127.0.0.1:8188` またはlocalhostだけを使用する。
- ComfyUIをLANまたはインターネットへ公開しない。

## 設定構造

- 共通設定: `config/app.json`
- 生成プロファイル: `config/profiles/*.json`
- 承認済みAPIワークフロー: `workflows/approved/*.json`

プロファイルはモデル単体ではなく生成パイプラインを表す。同じワークフローで動く別Checkpointは、新規プロファイルを作らず既存プロファイルの承認リストへ追加する。

## 許可された確認経路

```powershell
uv run python cardgen.py profiles
uv run python cardgen.py check
uv run python cardgen.py validate
uv run python cardgen.py --profile zimage validate
uv run python cardgen.py validate --all
```

## 許可された生成経路

WAI:

```powershell
uv run python cardgen.py --profile wai-hires generate --prompt "..." --negative "..." --count 1
```

Z-Image-Turbo:

```powershell
uv run python cardgen.py --profile zimage generate --prompt "..." --count 1
```

## 運用規則

- 画像生成は `cardgen.py generate` だけで行う。
- ワークフローまたはプロファイルを変更した後は `validate` を実行する。
- `workflows/approved/` 内のAPI形式ワークフローだけを使用する。
- 使用モデルは選択プロファイルの `approved_models` に含まれるものだけに限定する。
- ユーザーが明示承認しない限り、モデル、LoRA、Custom Node、ワークフローをダウンロード、インストール、更新、削除しない。
- プロジェクト外を探索・走査・保管しない。`--input-image`でユーザーが明示した絶対パスだけは読み取り、localhostのComfyUIへ送信できる。
- 入力画像をこのリポジトリへコピーせず、入力元の絶対パスも生成メタデータへ記録しない。
- ユーザーの明示的な変更依頼なしに、`cardgen.py`、`config/`、`pyproject.toml`、承認済みワークフローを変更しない。
- 生成後は `RESULT:` パス、Seed、プロファイル、ワークフロー、使用モデルを報告する。
- カード枠、カード名、ルール文章、ロゴ、印刷用文字組みは画像生成に含めず後工程で処理する。
- Z-Image-Turboの標準プロファイルはnegativeを `ConditioningZeroOut` するため、独立したネガティブ文章を適用しない。
- このリポジトリは公開可能な汎用パイプラインだけを保持する。原画、参照画像、生成物、ゲーム固有情報を追跡しない。

## 実行記録（メタデータ）

再現に必要な情報は `outputs/*_metadata.json` へ自動で残る。schema_version 6。

- `workflow_sha256` / `queued_workflow_sha256` — 前者はファイル、後者は実際に
  送信したグラフ。ワークフローを書き換えて比較実験をした場合、この2つが
  なければ後から群を区別できない。
- `generation_settings.node_inputs` — 全ノードのリテラル入力
- `model_files` — 使用した重みファイルのSHA-256とサイズ、および `weights_sha256`
- `comfyui` — ComfyUI・Python・PyTorchのバージョン
- `results[].file_sha256` — 出力画像のSHA-256
- `app_config_sha256` / `profile_sha256` / `input_image_sha256`
- `status` / `failure` — 失敗した実行も同じ記録を残す。`status` は `"ok"` または
  `"error"`。`failure` は成功時 `null`、失敗時は `error_type`・`message`・
  `failed_on_image`・`requested_count` を持つ。

`--count 4` の3枚目で落ちても1〜2枚目の画像は `outputs/` に残る。記録が無ければ
どのseedで、どのワークフローで、どの重みで出た画像なのか後から追えない。だから
失敗時も書き出す。`results` には完走した分だけが入る（1枚も無ければ空配列）。
例外は記録後に再送出するため終了コードは変わらず、`METADATA:` 行は送出前に
stdoutへ出るので、標準出力を解析する呼び出し元も失敗した実行を拾える。
Ctrl-Cやネットワーク断も同じ経路で記録する。

**`snapshot_node_inputs()` はノード種別を列挙しない。** 全ノードのリテラル入力を
記録する。列挙方式にすると `SamplerCustomAdvanced` のように設定を自前で持たない
ノードを使うワークフローで cfg・steps・sampler がまるごと記録から抜ける。
FLUX.2のグラフで実際に抜けていた。新しいノード種別を足すたびに同じ穴が空く方式は
採用しない。

記録対象外は3つ。プロンプト本文（`prompt`/`negative` に別途ある）、seed
（`results` にある。テンプレート側の値は古いため記録すると誤解を招く）、
入力画像名（呼び出し元のprivateプロジェクトに属する）。

`model_files` のハッシュには ComfyUI の models ディレクトリが必要。パスは
マシン固有なので、コミットされる `config/app.json` ではなく環境変数
`CARDGEN_COMFYUI_MODELS_DIR` で渡す。未設定なら空リストになり生成は続行する。
ハッシュは `.cache/model-hashes.json` に (size, mtime) で キャッシュする。

### ファイル全体のSHA-256でモデルを同定しないこと

`sha256` はファイル全体のハッシュで、**safetensorsヘッダのパディング1つで変わる**。
WAI v17 を Civitai からダウンロードしたファイルは、Civitai が掲載している
SHA-256 と一致しないが、重みは同一だった。差はヘッダの `__spacer` フィールド。

配布元と突き合わせるときは `weights_sha256` を使う。safetensors ヘッダの
`modelspec.hash_sha256` から読み、Civitai の `AutoV3` と直接比較できる。
`0x` 接頭辞は正規化して除去してある。

`safetensors_weight_identity()` は例外を投げない。modelspec を持たない
safetensors、`.pth` のような別形式、壊れたヘッダはすべて `null` を返す。
**持っていない情報を持っているかのように記録しない**ため。生成は止めない。

用途の使い分け:

- `sha256` — 同じファイルで再現したかの判定に使う
- `weights_sha256` — 配布元との同一性の主張に使う
