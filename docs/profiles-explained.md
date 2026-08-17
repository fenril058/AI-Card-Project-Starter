# プロファイルは何をしているのか

プロファイルは1本の生成パイプラインです。
この文書は、各プロファイルが何を入力に取り、途中で何が起き、出力がどう変わるかを、生成の仕組みを知らない読み手向けに説明します。

この文書の確度：

- 数値（steps、cfg、denoise、解像度、モデル名）は 2026-08-17 時点の `config/profiles/` と `workflows/approved/` に書かれている値です。
- 挙動の説明は、一般的な傾向と、プロファイルJSONのメモ欄 `_notes` に残っている観察にもとづく解釈です。境界値の断定としてではなく、当たりをつける材料として読んでください。
- `--steps` などで上書きした実行では、実際に流れた値が `outputs/*_metadata.json` に残ります。**説明より記録が正です。**

`_notes` は「なぜこの値なのか」を書き残すための人間向けの欄で、`cardgen.py` は読みません。
8つのプロファイルのうち4つにあります（`esrgan-upscale`、`wai-controlnet`、`wai-hires-latent`、`wai-refine`）。
以下で観察を引くときは、どのプロファイルの `_notes` かを示します。

## パラメータの意味

| | 何を決めるか | 上げると | 下げると |
|---|---|---|---|
| **steps** | ノイズを取り除く回数 | 時間がかかる。ある所から見た目が変わらなくなる | 速いが荒くなりやすい |
| **cfg** | プロンプトへの追従の強さ | 指示に寄る。上げすぎると色や構図が硬くなりやすい | 自由になるが指示から外れやすい |
| **denoise** | 入力をどれだけ描き直すか（0〜1） | 入力から離れる | 入力が残る。変化も乗らない |
| **seed** | 乱数の初期値 | — | 同一環境、同一設定で再現するために固定する |
| **sampler / scheduler** | ノイズの取り除きかたの手順 | — | 変えると絵が変わる。比較のときは固定する |

### 既定値と上書き

以下の各プロファイルの表に載せた数値は、すべてリポジトリ上の既定値です。
実行時に上書きできますが、どこへ書き込まれるかはオプションによって違います。

| オプション | 書き込む先 |
|---|---|
| `--steps` / `--cfg` / `--sampler` / `--scheduler` | そのフィールドを持つsamplerノードすべて。2パス構成では両方のパスが同じ値になる。プロファイルがその項目の binding を持つ場合は、指名された1ノードだけ |
| `--denoise` | プロファイルが `bindings.denoise` で指名した1ノードだけ |
| `--width` / `--height` | 空Latentノードすべて（2つを同時に指定する） |
| `--checkpoint` | `CheckpointLoader` すべて（そのプロファイルの承認済みファイルに限る） |
| `--seed` | プロファイルが `bindings.seed` で指名した1ノード |

書き込む先が無いオプションは、黙って無視されるのではなくエラーになります。
例外は `--seed` で、seed を持たないプロファイルでは無視されます。

`--denoise` を受け付けるのは `wai-hires`、`wai-hires-latent`、`wai-refine` の3つで、いずれも指しているのは2パス目です。
1パス目は 1.0 のままでなければ空のlatentに対する img2img になってしまうため、両方のパスへ同じ値を書く作りにはなっていません。

`flux2-klein-edit` は steps、cfg、sampler をそれぞれ別のノード（`Flux2Scheduler`、`CFGGuider`、`KSamplerSelect`）に持ち、samplerノード自身は接続だけを持ちます。
そのためプロファイルが3つを個別に binding していて、`--steps` などはその1ノードへ落ちます。
scheduler 名を持つノードが無いので、`--scheduler` だけはエラーになります。

`wai-refine` と `esrgan-upscale` は空Latentを持たないので、`--width` と `--height` はエラーになります。
`flux2-klein-edit` は空Latentを持つため通りますが、`Flux2Scheduler` が別に持つ width と height は変わらないので、大きく変えると食い違います。

`--count` は最大8です。
`--seed` と併せると2枚目以降は 1 ずつ増えます。

### denoise が決めるもの

denoise は「どれくらい良くするか」ではなく、入力をどれだけ残すかを選ぶ値です。
入力に加えるノイズ量に対応し、大きいほど元の絵から離れます。

`wai-refine` の既定 0.35 について、そのプロファイルの `_notes` は「構図を保つ上限に近い」「顔を保つ値ではない」と書いています。
構図はおおむね残る一方、**顔などの細部は変化し得ます**。
渡した絵をできるだけ残したいなら `--denoise 0.05` のように下げます（変化をかなり抑える方向の値で、変化しないことを保証する値ではありません）。

同じ値でも、どこまで変わるかは入力画像、プロンプト、モデルによって違います。
判断が要るときは seed を固定して denoise だけ振り、並べて比べてください。

### seed と再現性

seed は再現のために固定する条件の1つであって、それだけで同じ結果を保証するものではありません。
PyTorch自身、演算の実装が環境によって変わるため、リリースやプラットフォームをまたぐ完全な再現性は保証しないと明記しています。
ファイルとしてのバイト一致も前提にしないでください。

## 入力画像が要らないプロファイル

文章から作ります。
`--prompt` はどれでも必須です。
`--negative` は `zimage` だけ効きません（理由は下記）。

### `wai-hires`（生成してから拡大し、もう一度描き直す）

| | |
|---|---|
| モデル | WAI Illustrious SDXL v1.7 ＋ RealESRGAN x4plus anime 6B |
| 流れ | 1024×1344 で生成 → ESRGANで4倍 → Lanczosで1536×2016 → もう一度生成 |
| 設定 | 1パス目 steps 28 / cfg 6.0 / euler_ancestral、2パス目 steps 20 / denoise 0.45 |

拡散を2回通すため、sampler の設定も2組あります。
この文書では前半を1パス目、後半を2パス目と呼びます。

`config/app.json` の `default_profile` に指定されているため、`--profile` を省略するとこれが動きます。

既定値では、2パス目の denoise 0.45 は `wai-refine` の既定 0.35 より高く、拡大した絵を鮮明にするのではなく細部を描き足し直す位置にあります。
`--denoise` で動くのはこの2パス目だけです。
一方 `--steps 40` のように上書きすると、1パス目と2パス目の両方が 40 になります。

### `wai-hires-latent`（拡大を latent 側で行う比較用）

| | |
|---|---|
| モデル | WAI Illustrious SDXL v1.7 |
| 流れ | 1024×1344 で生成 → latentのまま `LatentUpscaleBy`（bislerp 1.5倍）→ 2パス目 |
| 設定 | sampler設定は `wai-hires` と同一（2パス目 denoise 0.45、最終 1536×2016） |

違いは拡大を画像側で行うか latent 側で行うかだけです。
`wai-hires` との差を見るためのプロファイルなので、両者のsampler設定（steps、cfg、sampler、scheduler、denoise）が一致していることを `tests/test_cardgen.py` が固定しています。

### `wai-single`（1パスだけで終わる）

| | |
|---|---|
| モデル | WAI Illustrious SDXL v1.7 |
| 流れ | 832×1216 で生成して終わり。拡大も2パス目も無い |
| 設定 | steps 28 / cfg 6.0 / euler_ancestral |

仕上げ解像度は出ませんが速いので、プロンプトの当たりをつけるのに向きます。

### `zimage`（少ない steps で速く出す）

| | |
|---|---|
| モデル | Z-Image-Turbo（テキストエンコーダ Qwen3-4B） |
| 流れ | 1024×1024 で生成して終わり |
| 設定 | steps 8 / cfg 1 / res_multistep |

**negative は効きません。**
ワークフローが `ConditioningZeroOut` で無効化するため、`--negative` に何を書いても結果に反映されません（`negative_prompt_mode: "zeroed"`）。

## 入力画像が要るプロファイル

`--input-image` に絶対パスを渡します。
何を渡すかはプロファイルごとに違います。

### `wai-refine`（既存の絵を印刷解像度へ引き上げる）

| | |
|---|---|
| モデル | WAI Illustrious SDXL v1.7 ＋ RealESRGAN x4plus anime 6B |
| 入力 | 仕上げたい画像そのもの |
| 流れ | ESRGANで4倍 → Lanczosで1480×2016 → denoise ぶんだけ描き直す |
| 設定 | steps 20 / cfg 6.0 / euler_ancestral / denoise 既定 0.35 |

**比率の違う絵を渡すと引き伸ばされます。**
呼ぶ側で先に断ち切り比率へクロップしておいてください。
既定 0.35 の効き方は前掲の「denoise が決めるもの」にあります。

### `flux2-klein-edit`（参照した絵を指示文で描き直す）

| | |
|---|---|
| モデル | FLUX.2 Klein 4B Base（テキストエンコーダ Qwen3-4B、VAE flux2-vae） |
| 入力 | 編集元の画像。`ImageScaleToTotalPixels`（1.4メガピクセル）で揃えてから参照 |
| 設定 | steps 28 / cfg 5.0 / euler / 1024×1344 |

「キャラクターは維持して、舞台だけ置き換える」のような指示に向きます。
denoise ではなく指示文で変化量を決めるため、書きかたで結果が大きく変わります。

### `esrgan-upscale`（拡大だけを行う）

| | |
|---|---|
| モデル | RealESRGAN x4plus anime 6B |
| 入力 | 拡大したい画像 |
| 流れ | 4倍にする。それだけ |

このワークフローには sampler が無く、**拡散モデルを通しません。**
prompt、negative、seed の binding も持たず、`--count` は 1 のみです。

ただし RealESRGAN は Lanczos のような補間器ではなく、学習によって細部を推定する超解像モデルです。
拡散で作り直さないというだけで、**細部の出かたは入力と同じではありません。**
出力の寸法はモデル倍率まかせなので、カード比率へ落とすのは呼ぶ側の仕事です。

### `wai-controlnet`（ラフの線で構図を決める）

| | |
|---|---|
| モデル | WAI Illustrious SDXL v1.7 ＋ xinsir scribble ControlNet ＋ RealESRGAN |
| 入力 | ラフ線画。仕上げたい絵ではありません |
| 流れ | ラフを1024×1344へ中央基準で切り揃える → ControlNetを効かせて生成 → 拡大 → 1536×2016 で2パス目 |
| 設定 | ControlNet strength 0.55 / end_percent 0.7、2パス目 denoise 0.45 |

ControlNet は1パス目にだけ効きます。
1パス目の条件付けだけが `ControlNetApplyAdvanced` を通り、2パス目はプロンプトの条件付けを直接受け取ります。
strength 0.55 は 0.55、0.75、0.95 を同一seedで比べた結果で、強くするほどラフの直線を複写してしまい構造として解釈しなくなりました（`wai-controlnet` の `_notes` にある観察）。
触るなら strength を触ってください。

## 実際の生成条件はどこを見るか

1回の実行につき `outputs/<profile>_<timestamp>_metadata.json` が1つ出ます。
上の表はリポジトリ上の既定値であって、その実行で流れた値ではありません。
実際に流れた値はこのファイルにあります。

| 見たいもの | フィールド |
|---|---|
| どのプロファイルとワークフローで流したか | `profile` / `workflow` |
| 実際の steps、cfg、sampler、denoise | `generation_settings.samplers` |
| 生成解像度 | `generation_settings.latents` |
| 拡大、クロップ、ControlNetなど上記以外の全設定 | `generation_settings.node_inputs` |
| 画像ごとの seed | `results[].seed` |
| どのオプションがどのノードを上書きしたか | `setting_overrides` |
| プロンプトとnegative | `prompt` / `negative` / `negative_prompt_mode` |
| 実際に読んだモデルファイル | `model_files` |

`generation_settings` は上書きを適用したあとのグラフから作られるので、`samplers` に出るのは実際に流れた値です。
`setting_overrides` のほうは、どのフィールドがどのノードで上書きされたかを記録します。値そのものが入るのは width、height、denoise だけです。

`flux2-klein-edit` だけは `samplers` に steps と cfg が出ません。
samplerノードが設定を自前で持たないためで、`--steps` で上書きした場合も含め、値は `node_inputs` の `Flux2Scheduler` と `CFGGuider` にあります。
どのノードへ落ちたかは `setting_overrides.sampler_nodes` で確かめられます。
`esrgan-upscale` は samplerも空Latentも持たないので、`samplers` と `latents` が空になり、`results[].seed` は `null` になります。

失敗した実行にも同じファイルが残ります。
`status` と `failure` を見てください。
記録の全体像は [README「出力と再現メタデータ」](../README.md#出力と再現メタデータ)、なぜこの形なのかは [docs/metadata-design.md](metadata-design.md) にあります。

`model_files` のハッシュを配布元と突き合わせるときは、全体の `sha256` と `weights_sha256` の使い分けがあります。
手順は [README「モデルライセンスとハッシュ台帳」](../README.md#モデルライセンスとハッシュ台帳)にあります。

## ライセンス

各モデルの条件は配布元で確認してください。
このリポジトリはモデル本体もライセンス文も持ちません（`config/profiles/*.json` の `approved_models` が名前だけを持ちます）。
出典とレビュー状態の一覧は [licenses/README.md](../licenses/README.md) にあります。

**未承認のモデルは流れません。**
`approved_models` に無いファイルがワークフローに含まれていると `cardgen.py` が実行前に落とします。
