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

`outputs/*_metadata.json` は `cardgen.py` が自動で書く。記録内容はREADMEの「出力と再現メタデータ」を見る。

失敗した実行にも同じファイルが残る。エラー終了を「記録が無い」と報告しない。`status` と `failure` を読み、途中まで出た画像を報告する。

`cardgen.py` の記録処理を変更するときの制約:

- `snapshot_node_inputs()` でノード種別を列挙しない。全ノードのリテラル入力を記録する。
- モデルの同定に全体 `sha256` を使わない。配布元と突き合わせるときは `weights_sha256` を使う。
- `safetensors_weight_identity()` を例外送出へ変えない。判定できない入力は `null` を返す。
- モデルのハッシュ元は環境変数 `CARDGEN_COMFYUI_MODELS_DIR` で渡す。マシン固有のパスを `config/app.json` へ書かない。

これら4つの理由と実例は [docs/metadata-design.md](docs/metadata-design.md)。仕様を変える前に読む。
