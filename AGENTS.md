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
- プロジェクト外のファイルへアクセスしない。
- ユーザーの明示的な変更依頼なしに、`cardgen.py`、`config/`、`pyproject.toml`、承認済みワークフローを変更しない。
- 生成後は `RESULT:` パス、Seed、プロファイル、ワークフロー、使用モデルを報告する。
- カード枠、カード名、ルール文章、ロゴ、印刷用文字組みは画像生成に含めず後工程で処理する。
- Z-Image-Turboの標準プロファイルはnegativeを `ConditioningZeroOut` するため、独立したネガティブ文章を適用しない。
