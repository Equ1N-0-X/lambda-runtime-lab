# ベンチマークレポート利用ガイド

## この資料の目的

この資料は、`results/reports/<run-id>.md` と `<run-id>.csv` を、比較可能性を確認したうえで
Python/Rust Lambdaの技術判断へ使う手順を説明します。レポートの生成仕様は
[`02_SPECIFICATION.md`](02_SPECIFICATION.md)、実行手順は
[`06_AWSデプロイ手順.md`](06_AWSデプロイ手順.md) を正本とします。

## レポートを作る

ベンチマーク完了時に表示されたrun IDを使い、CloudWatchログの反映後に収集と生成を行います。

```bash
RUN_ID=<run-id> make collect
RUN_ID=<run-id> make report
```

期待する成果物は次のとおりです。

- `results/raw/<run-id>/metadata.json`: 実験条件
- `results/raw/<run-id>/samples.jsonl`: クライアント側の測定値
- `results/raw/<run-id>/cloudwatch.jsonl`: 収集した構造化ログとLambda REPORT
- `results/raw/<run-id>/merged.jsonl`: クライアント測定とLambda実行情報の結合結果
- `results/reports/<run-id>.md`: 人が条件と結果を確認するレポート
- `results/reports/<run-id>.csv`: 表計算や複数runの集計に使う一実装一行のデータ

`make collect` 後も全件が`Unclassified`になる場合は、アプリが計測リクエスト番号を含む
新しいイメージへ更新済みか確認します。古いイメージで取得したrunは後から完全には相関できないため、
デプロイ更新後に新しいrun IDで再計測します。

## 最初に有効性を確認する

Markdownレポートを開いたら、性能差より先に次を確認します。一つでも満たさない場合、そのrunを
正式な比較や結論には使用しません。

1. `Experiment conditions` のArchitecture、Lambda memory、Workloadが意図した値である。
2. Python/Rustの条件とサンプル数が同じである。
3. `Summary` のError rateが許容範囲内である。通常の比較では0%を期待する。
4. `Unclassified`が0であり、測定サンプルとLambda REPORTがすべて結合されている。
5. 比較するメトリクスのCountが0ではなく、`null`が残っていない。
6. コストを見る場合は料金の更新日・リージョン・出典が実験条件と一致する。

`null`は値がゼロという意味ではなく、未取得または計算不能です。欠損した指標を0として比較へ
含めてはいけません。

## 画面で確認する

`make ui` で起動する閲覧画面（`http://127.0.0.1:8765/`）は、Markdownレポートと同じ集計結果を
表示します。上の確認項目のうち、ドライラン、片方の実装のみ、Architecture・Lambda memoryの
未記録、ログ未収集、`Unclassified`の残存は画面が自動で判定し、該当するrunを「要確認」として
理由を表示します。「比較可能」と表示されたrunでも、Error rate、条件の意図、料金表の出典は
画面の「計測件数」と「実験条件」で確認します。画面の「未取得」はレポートの`null`と同じ意味です。

## 各章の使い方

### Experiment conditions

run ID、日時、Git revision、リージョン、CPUアーキテクチャ、Lambdaメモリ、ワークロード、
反復回数、並行数、ウォームアップ回数を確認します。異なる条件のrunを比較する場合は、変更した
条件による影響とランタイム差を分離できないため、原則として同じ表へ混ぜません。

### Summary

サンプル数、Cold/Warm/Unclassifiedの内訳、エラー率を確認します。ここはレポート全体の
品質ゲートです。`Unclassified > 0`なら、クライアント側レイテンシーは読めても、Lambda内部の
Duration、メモリ、コストは完全な比較として扱えません。

### Percentiles

P50は典型的な値、P95/P99は遅い側の裾を確認する値です。平均値だけでは隠れる遅延のばらつきを
把握するため、P50とP95/P99を一緒に見ます。

- `latency_ms`: API Gatewayやネットワークを含む、利用者から見た応答時間
- `duration_ms`: Lambda内の実行時間
- `billed_duration_ms`: Lambdaの課金対象時間
- `init_duration_ms`: Cold Start時の初期化時間
- `max_memory_used_mb`: Lambda REPORTが示す最大メモリ使用量

`Scope=all`で全体傾向を確認し、Cold/Warmの差を調べるときだけ各scopeを分けて読みます。

### Estimated cost (Free Tier excluded)

課金対象時間、設定メモリ、料金表から計算した比較用の概算です。Free Tier、税、API Gateway、
DynamoDB、データ転送などは含みません。予算や請求確認にはAWSの請求情報を使用し、この値だけで
実費を断定しません。

### Python/Rust difference

Pythonを基準にしたRustの増減率です。正数はRustの値が高く、負数は低いことを表します。
時間・メモリ・コストは低い方が通常有利ですが、差が小さい場合やP95/P99だけが大きく異なる場合は、
複数runを取り、時間帯やAWS基盤のばらつきを確認してから判断します。

### Image metrics

コンテナイメージのdigestやサイズなど、デプロイ成果物の指標です。実行時間・最大メモリ・料金とは
別の観点であり、イメージが小さいことだけを性能やコストの優位性として扱いません。

### Measurement notes

Cold/Warmの分類条件、欠損値、Free Tier、イメージサイズなど、全章に共通する注意事項です。
レポートを共有するときは削除せず、測定条件と一緒に残します。

## CSVの使い方

CSVは一つのrunにつきPython/Rust各一行です。複数runを表計算や分析コードへ取り込み、同じ
アーキテクチャ・メモリ・ワークロード単位で比較するときに使用します。CSVだけでは実験条件の
全情報を持たないため、対応するMarkdownと`metadata.json`を同じrun IDで保管します。

## 結論を書くときのルール

- 単一runや`health`だけで言語全体の優劣を断定しない。
- レイテンシー、Lambda実行時間、Cold Start、メモリ、コスト、イメージサイズを分けて述べる。
- 数値にはrun ID、リージョン、アーキテクチャ、メモリ、ワークロード、測定日時を併記する。
- 欠損、エラー、Unclassifiedがあるrunを除外した理由を残す。
- 複数回の結果がばらつく場合は範囲を示し、都合のよいrunだけを選ばない。
