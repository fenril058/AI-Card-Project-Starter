# Approved API workflows

ComfyUIで生成成功したワークフローを **API形式** で書き出し、ここへ保存します。

標準プロファイルが参照するファイル:

```text
wai_sdxl_refiner_api.json
z_image_turbo_api.json
```

この配布物には、提供済みの `z_image_turbo_api.json` を含めています。WAIのAPIワークフローは利用者ごとの実動ワークフローを使用するため、v2で動作確認済みのファイルを `wai_sdxl_refiner_api.json` としてコピーしてください。

API形式の判別点:

- 最上位キーがノードID
- 各ノードに `class_type` がある
- 各ノードに `inputs` がある

通常のUI用ワークフローJSONは使用できません。
