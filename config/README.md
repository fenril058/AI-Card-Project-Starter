# Configuration layout

- `app.json`: 全プロファイル共通の設定
- `profiles/*.json`: 生成方式ごとのワークフロー・承認モデル・能力定義

プロファイルは「モデル1個」ではなく「生成パイプライン1種類」を表します。同じワークフローで切り替えられるCheckpointは、同じプロファイルの `approved_models.checkpoints` に追加してください。

## 新しいプロファイルを追加する基準

別プロファイルにする例:

- SDXLとZ-Image/Fluxなど基盤方式が違う
- Refinerの有無が違う
- ControlNetやインペイントが必須
- アップスケール専用ワークフロー

同じプロファイル内で扱う例:

- 同じSDXL Base + Refinerワークフローで動く別Checkpoint
- 同じワークフローで選べる承認済みLoRA
