# Approved API workflows

ComfyUIで生成成功したワークフローを **API形式** で書き出し、ここへ保存します。
`cardgen.py` はここに置かれたファイルだけを実行します。

## プロファイルとの対応

各ファイルは `config/profiles/<id>.json` の `workflow` から参照されます。

| ワークフロー | プロファイル | 内容 |
|---|---|---|
| `wai_sdxl_hires_api.json` | `wai-hires` | WAI SDXL、Real-ESRGANでピクセル拡大してから2nd pass |
| `wai_sdxl_hires_latent_api.json` | `wai-hires-latent` | 同上をlatent拡大（`LatentUpscaleBy` bislerp 1.5倍）で行う比較用 |
| `wai_sdxl_single_api.json` | `wai-single` | WAI SDXL、Single Pass |
| `wai_sdxl_controlnet_hires_api.json` | `wai-controlnet` | Scribble ControlNetをbase passへ適用し、Hires passで仕上げ |
| `wai_sdxl_refine_api.json` | `wai-refine` | 既存画像のアップスケール + ディテール調整 |
| `z_image_turbo_api.json` | `zimage` | Z-Image-Turbo。negativeは `ConditioningZeroOut` |
| `flux2_klein_4b_edit_api.json` | `flux2-klein-edit` | FLUX.2 Klein Baseによる参照編集 |

`wai_sdxl_hires_api.json` と `wai_sdxl_hires_latent_api.json` は拡大経路だけが違い、
最終解像度はどちらも1536x2016、sampler設定と2nd passの `denoise` (0.45) も一致します。
この一致は `tests/test_cardgen.py` で固定してあるので、片方だけ編集すると失敗します。
比較の前提を崩さずに振りたい場合は `--denoise` を使ってください。

## API形式の判別点

- 最上位キーがノードID
- 各ノードに `class_type` がある
- 各ノードに `inputs` がある

通常のUI用ワークフローJSONは使用できません。ComfyUIの
「Workflow → Export (API)」で書き出してください。

## 追加・変更したとき

ワークフローを追加または変更したら、参照するプロファイルを更新し、検証します。

```powershell
uv run python cardgen.py validate --all
```

`validate` はワークフロー形式、prompt・seed・入力画像・denoiseのbinding、Sampler数と
プロファイルの `multi_pass` の一致、negative方式、使用モデルが
`approved_models` に含まれることを確認します。
