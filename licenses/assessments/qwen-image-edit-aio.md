# Qwen-Image-Edit AIO ライセンス調査

- 調査日: 2026-08-31
- 対象: Qwen公式モデル、prithivMLmodsのAIO-FP8、Phr00tのRapid-AIO
- 位置づけ: 公開情報に基づくプロジェクト内の採用判断。法的助言ではない。

## 結論

「Qwen-Image-Edit-AIO」という名前だけでライセンスを判断してはならない。同名に近い配布物でも、中身とリスクが大きく異なる。

| 対象 | プロジェクト判断 | 理由 |
|---|---|---|
| Qwen公式 `Qwen-Image-Edit` / `2509` / `2511` | 採用候補にできる | 公式配布と公式GitHubがApache License 2.0を明示している |
| `prithivMLmods/Qwen-Image-Edit-AIO-FP8` | 条件付きで採用候補にできる | 説明上は公式モデルのFP8圧縮版。ただし配布物に独立したLICENSE/NOTICEが見当たらず、再配布条件の整備が弱い |
| `Phr00t/Qwen-Image-Edit-Rapid-AIO` | 現状は採用しない | 多数の第三者LoRA等をマージしており、全構成物の出所・バージョン・ライセンス・許諾証跡を追跡できない |

このプロジェクトでは、Phr00t Rapid-AIOを `licenses/registry.toml`、`config/profiles/`、または承認済みワークフローへ追加しない。採用を再検討する場合は、対象ファイルを一つに固定し、全構成物の許諾チェーンを確認してから別途レビューする。

## 1. Qwen公式モデル

Qwen公式の次のモデルカードはいずれもApache License 2.0を明示している。

- [Qwen/Qwen-Image-Edit](https://huggingface.co/Qwen/Qwen-Image-Edit)
- [Qwen/Qwen-Image-Edit-2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509)
- [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)
- [QwenLM/Qwen-Image LICENSE](https://github.com/QwenLM/Qwen-Image/blob/main/LICENSE)

Apache License 2.0は、条件を守る限り、商用利用、社内利用、改変、量子化、ホスティング、再配布を許容する。重みまたは派生物を再配布するときは、少なくとも次を確認する。

- Apache License 2.0全文を受領者へ提供する
- 変更したファイルに変更表示を付ける
- 上流の著作権、特許、商標、帰属表示を保持する
- 上流にNOTICEが存在する場合は必要な通知を引き継ぐ
- Qwen、Alibabaその他の商標について公式提携や推奨を示唆しない

ただしApache License 2.0は、学習データ、入力画像、生成物に含まれる第三者の著作権、商標、肖像、パブリシティ等について非侵害を保証しない。

### 2511に関する留保

`Qwen-Image-Edit-2511` のモデルカードは、コミュニティ作成LoRAを選定してベースモデルへ統合したと説明しているが、個々のLoRA名、取得時バージョン、ライセンス、許諾処理は列挙していない。公式配布者がモデル全体をApache License 2.0で提供していることは採用判断の強い根拠になる一方、Apache License 2.0には権原・非侵害の保証や補償はない。高額な商用案件やモデル再配布では、必要に応じて配布者へプロベナンスや補償範囲を確認する。

## 2. prithivMLmods Qwen-Image-Edit-AIO-FP8

[prithivMLmods/Qwen-Image-Edit-AIO-FP8](https://huggingface.co/prithivMLmods/Qwen-Image-Edit-AIO-FP8) は、モデルカード上で `Qwen-Image-Edit-2509` と `2511` のFP8圧縮版と説明され、リポジトリメタデータもApache License 2.0を指定している。公開説明からは、Rapid-AIOのように多数の第三者LoRAを独自に追加した配布物とは読み取れない。

そのため、ローカルまたは社内での画像生成については比較的低リスクと判断する。ただし、[ファイル一覧](https://huggingface.co/prithivMLmods/Qwen-Image-Edit-AIO-FP8/tree/main) には調査日時点で独立した `LICENSE` または `NOTICE` が見当たらず、モデルカードのライセンス指定だけに依存している。

採用する場合は次を必須とする。

- 使用する2509版または2511版を区別し、revision、ファイル名、File SHA-256を固定する
- 公式Qwenモデルとの対応関係とFP8変換であることをレビューへ記録する
- 再配布または製品同梱では、Apache License 2.0全文、必要なNOTICE、変更表示を自分の配布物へ追加する
- 実ファイルを `licenses sync` で照合し、`licenses/reviews/*.toml` で人間の最終確認を行う

## 3. Phr00t Qwen-Image-Edit-Rapid-AIO

[Phr00t/Qwen-Image-Edit-Rapid-AIO](https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO) は単純なFP8変換ではない。モデルカード自身が、Qwen本体に加えてVAE、CLIP、複数の高速化LoRA、写実・肌補正・顔交換・NSFW系LoRA等をバージョンごとに追加、削除、再調整していると説明している。

主な問題は次のとおり。

- 全構成物について、作者、配布URL、取得時revision、ファイルハッシュ、取得時ライセンスを対応付けたSBOMがない
- バージョンごとにマージ内容が変化し、`Rapid-AIO` という名前だけでは同じ権利状態を指さない
- Hugging Face上の `apache-2.0` 表示だけでは、マージされた第三者構成物すべてをApache License 2.0へ再許諾できる証拠にならない
- 一部の構成物は公開ページからライセンスまたは配布条件を確認できない
- 同梱コードには、モデル重みとは別のライセンス確認が必要なものがある

### SNOFSとの衝突

Rapid-AIOのモデルカードと作者回答は、少なくとも一部のNSFW版にSNOFSをマージしたことを明記している。

- [Rapid-AIO README](https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/blob/main/README.md)
- [作者による「v14 NSFW mergeにSNOFSを含む」との回答](https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/discussions/178?not-for-all-audiences=true)
- [Ashen3/SNOFS](https://huggingface.co/Ashen3/SNOFS)

SNOFSの現行ライセンスは、別途有償ライセンスを得ない限り、モデルのマージ、派生モデル作成、重みの再配布、第三者向け生成サービスを禁止している。Rapid-AIO作成時に異なる条件が適用されていた、または作者から個別許諾を得ていた可能性はあるが、その証拠は公開情報から確認できない。

したがって本調査は「Rapid-AIOが違反している」と断定しない。プロジェクト判断は、商用デューデリジェンスに必要な許諾チェーンを確認できないため採用しない、である。SFW版についても、SNOFSを含まないことだけでは他の全構成物の許諾が確認できたことにならない。

### 同梱コード

Rapid-AIOはComfyUI標準ノードを修正した `nodes_qwen.py` を配布していると説明する。[ComfyUIのライセンス](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE) はGPLv3であり、このコードをモデル重みと一緒に取得・再配布する場合は、Apache License 2.0表示だけで処理せず、コード部分を分離してGPLv3の条件を確認する。

## プロジェクトでの運用判断

| 利用形態 | 判断 |
|---|---|
| 公式Qwenモデルをローカルで使用 | 採用候補。正確なファイルとrevisionを登録してレビューする |
| prithivMLmods FP8版をローカルで使用 | 条件付き候補。公式版との対応とハッシュを固定する |
| prithivMLmods FP8版を再配布・製品同梱 | Apache License 2.0全文、NOTICE、変更表示を整備した場合のみ検討する |
| Phr00t Rapid-AIOを個人環境で試用 | プロジェクトの承認経路外。本リポジトリには登録しない |
| Phr00t Rapid-AIOを商用生成、顧客案件、SaaS/APIに使用 | 採用しない |
| Phr00t Rapid-AIOの重みを再配布・販売 | 許諾証跡なしでは行わない |

最も安全な構成は、公式Qwenモデルを基礎に、出所、revision、ハッシュ、ライセンスが個別に確認できる高速化LoRAだけを別ファイルとして組み合わせることである。マージ済みAIOを使う場合でも、AIO配布ページのライセンスラベルではなく、全構成物の許諾チェーンを確認する。

## 採用時チェックリスト

- [ ] 配布元URL、revision、ファイル名を固定した
- [ ] File SHA-256またはWeights SHA-256を記録した
- [ ] ベースモデル、VAE、CLIP、LoRA、Custom Nodeをすべて列挙した
- [ ] 各構成物の取得時ライセンスと作者を確認した
- [ ] マージ、派生作成、再配布、ホスティングの各権利を別々に確認した
- [ ] LICENSE、NOTICE、帰属、変更表示の義務を配布形態へ反映した
- [ ] 入力画像と生成物に関する第三者権利を別途確認した
- [ ] `licenses/reviews/*.toml` を人間が最終確認した

## 再調査条件

次のいずれかが起きた場合は、この判断をそのまま流用せず再調査する。

- モデルのrevisionまたはファイルが変わった
- Rapid-AIOの構成表、SBOM、個別許諾証跡が公開された
- SNOFSまたは他の構成物のライセンスが変更された
- ローカル生成から再配布、製品同梱、第三者向けサービスへ利用形態が変わった

## 参照

- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- [Qwen-Image公式GitHub](https://github.com/QwenLM/Qwen-Image)
- [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)
- [prithivMLmods/Qwen-Image-Edit-AIO-FP8](https://huggingface.co/prithivMLmods/Qwen-Image-Edit-AIO-FP8)
- [Phr00t/Qwen-Image-Edit-Rapid-AIO](https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO)
- [Ashen3/SNOFS](https://huggingface.co/Ashen3/SNOFS)
