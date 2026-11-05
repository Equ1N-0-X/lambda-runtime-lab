# ベンチマークレポート利用ガイド

## この資料の目的

この資料は、`results/reports/<run-id>.md` と `<run-id>.csv` を、比較可能性を確認したうえで
各言語（`spec/implementations.yaml` に登録した Python・Rust・TypeScript・Java）の Lambda の
技術判断へ使う手順を説明します。レポートの生成仕様は
[`02_SPECIFICATION.md`](02_SPECIFICATION.md)、実行手順は
[`06_AWSデプロイ手順.md`](06_AWSデプロイ手順.md) を正本とします。

## レポートを作る

ベンチマーク完了時に表示されたrun IDを使い、ログの収集とレポートの生成を行います。
CloudWatchへの反映には時間がかかるため、`COLLECT_WAIT_SECONDS` を指定すると、成功した要求の
ログがすべてそろうまで、その秒数を期限として取得し直します（そろわなければ終了コード3）。

```bash
RUN_ID=<run-id> COLLECT_WAIT_SECONDS=180 make collect
RUN_ID=<run-id> make report
```

期待する成果物は次のとおりです。

- `results/raw/<run-id>/metadata.json`: 実験条件
- `results/raw/<run-id>/samples.jsonl`: クライアント側の測定値
- `results/raw/<run-id>/cloudwatch.jsonl`: 収集した構造化ログとLambda REPORT
- `results/raw/<run-id>/merged.jsonl`: クライアント測定とLambda実行情報の結合結果
- `results/raw/<run-id>/collection.json`: ログ収集の状況（取得方法、そろうべき件数と結合できた件数、未完了か）
- `results/reports/<run-id>.md`: 人が条件と結果を確認するレポート
- `results/reports/<run-id>.csv`: 表計算や複数runの集計に使う一実装一行のデータ

`make collect` 後も全件が`Unclassified`になる場合は、アプリが計測リクエスト番号を含む
新しいイメージへ更新済みか確認します。古いイメージで取得したrunは後から完全には相関できないため、
デプロイ更新後に新しいrun IDで再計測します。

## 最初に有効性を確認する

Markdownレポートを開いたら、性能差より先に次を確認します。一つでも満たさない場合、そのrunを
正式な比較や結論には使用しません。

1. `Experiment conditions` のArchitecture、Lambda memory、Workloadが意図した値である。
2. 比較する言語の条件とサンプル数が同じである。
3. `Comparability` で、比べたい指標が `usable` である（`reference` は参考値、`unusable` は比較不可）。
4. `Summary` の Failed が0である。失敗を含む計測を正式に採用する基準は決まっていないため、
   失敗があれば成功した要求だけの参考値として扱う。
5. `Unclassified`が0であり、成功した要求とLambda REPORTがすべて結合されている。
6. 比較するメトリクスのCountが0ではなく、`null`が残っていない。
7. コストを見る場合は料金の更新日・リージョン・出典が実験条件と一致する。リージョンが料金表と
   違う計測では、コストは計算されません。

`null`は値がゼロという意味ではなく、未取得または計算不能です。欠損した指標を0として比較へ
含めてはいけません。

## 画面で確認する

`make ui` で起動する画面（`http://127.0.0.1:8765/`）は、Markdownレポートと同じ集計結果を
表示し、`Comparability` と同じ判定で、参考値・比較不可の指標には最良（★）や倍率を出しません。
ドライラン、一つの言語のみ、失敗した要求、応答内容の確認前の版、計測前の確認の記録がない計測、
Architecture・Lambda memoryの未記録、ログ未収集、`Unclassified`の残存は、該当するrunを
「要確認」として理由を表示します。条件の意図と料金表の出典は、画面の「計測件数」と
「実験条件」で確認します。画面の「未取得」はレポートの`null`と同じ意味です。
画面の「新しく計測する」から実行した計測は、ログ収集とレポート作成まで自動で行われ、完了後に
同じ確認を経て比較に使います。

## 各章の使い方

### Experiment conditions

run ID、日時、Git revision、リージョン、CPUアーキテクチャ、Lambdaメモリ、ワークロード、
反復回数、並行数、ウォームアップ回数を確認します。異なる条件のrunを比較する場合は、変更した
条件による影響とランタイム差を分離できないため、原則として同じ表へ混ぜません。

### Summary

サンプル数、成功（Succeeded）・失敗（Failed）、Cold/Warm/Unclassifiedの内訳、失敗した要求に
課金された料金（Failed cost）を確認します。成功は、状態200で応答本文が契約どおりの形をしている
要求です。統計とコストは成功した要求だけから求め、Cold/Warm/Unclassifiedも成功した要求の内訳です。
`Unclassified > 0`なら、クライアント側レイテンシーは読めても、Lambda内部の
Duration、メモリ、コストは完全な比較として扱えません。

### Comparability

指標（cost・compute_cost・duration・init・latency・memory）ごとに、比較の根拠として使えるか
（`usable`・`reference`・`unusable`）と理由を示します。コールドスタート（init）は、通常の計測で
偶然起きた少数の観測しかないため、正式に比べるための件数と集め方が決まるまでは `reference` か
`unusable` になります。

### Percentiles

P50は典型的な値、P95/P99は遅い側の裾を確認する値です。平均値だけでは隠れる遅延のばらつきを
把握するため、P50とP95/P99を一緒に見ます。

- `latency_ms`: API Gatewayやネットワークを含む、利用者から見た応答時間
- `duration_ms`: Lambda内の実行時間
- `billed_duration_ms`: Lambdaの課金対象時間
- `init_duration_ms`: Cold Start時の初期化時間
- `max_memory_used_mb`: Lambda REPORTが示す最大メモリ使用量。同じ実行環境で処理した要求をまたいだ
  最大値のため、1リクエストの処理に必要な量そのものではありません

`Scope=all`で全体傾向を確認し、Cold/Warmの差を調べるときだけ各scopeを分けて読みます。

### Estimated cost (Free Tier excluded)

課金対象時間、設定メモリ、料金表から計算した比較用の概算です。Free Tier、税、API Gateway、
DynamoDB、データ転送などは含みません。予算や請求確認にはAWSの請求情報を使用し、この値だけで
実費を断定しません。

### Difference

計測した言語のうち、`spec/implementations.yaml` で最も先頭にある言語を基準にした、各言語の
増減率です。正数は基準より高く、負数は低いことを表します。
時間・メモリ・コストは低い方が通常有利ですが、差が小さい場合やP95/P99だけが大きく異なる場合は、
複数runを取り、時間帯やAWS基盤のばらつきを確認してから判断します。言語は毎回同じ順で計測するため、
一つのrunの差には時間的な変動が混ざりえます。順序を入れ替えた反復はまだ用意していません。

### Image metrics

コンテナイメージのdigestやサイズなど、デプロイ成果物の指標です。実行時間・最大メモリ・料金とは
別の観点であり、イメージが小さいことだけを性能やコストの優位性として扱いません。

### Measurement notes

Cold/Warmの分類条件、欠損値、Free Tier、イメージサイズなど、全章に共通する注意事項です。
レポートを共有するときは削除せず、測定条件と一緒に残します。

## CSVの使い方

CSVは一つのrunにつき、計測した言語ごとに一行です。複数runを表計算や分析コードへ取り込み、同じ
アーキテクチャ・メモリ・ワークロード単位で比較するときに使用します。CSVだけでは実験条件の
全情報を持たないため、対応するMarkdownと`metadata.json`を同じrun IDで保管します。

## 正式に比べるときの進め方と、今の計測方法の限界

一つの run では、言語を `spec/implementations.yaml` の順に一つずつ計測します（順序は
`metadata.json` の `targets` に記録されます）。後に計測する言語ほど時間が後になるため、
時間帯による AWS 側の変動が言語の差に混ざりえます。今の計測方法では、順序の入れ替えや、
同じ条件の反復をまとめる機能はありません。正式に比べるときは次のように扱います。

1. 同じ条件（ワークロード・メモリ・方式・同時実行数・回数）の run を、時間をあけて複数回取る。
2. run ごとの値の範囲（最小〜最大）を示し、一つの run の中央値の差だけで優劣を断定しない。
3. 差が run 間のばらつきより小さい指標は、差がないものとして扱う。
4. 有意差の検定や、順序を入れ替えた計測の自動化は、評価の計画（必要な回数・精度・費用）を
   決めたうえで追加する（未決定）。

## 結論を書くときのルール

- 単一runや`health`だけで言語全体の優劣を断定しない。
- レイテンシー、Lambda実行時間、Cold Start、メモリ、コスト、イメージサイズを分けて述べる。
- 数値にはrun ID、リージョン、アーキテクチャ、メモリ、ワークロード、測定日時を併記する。
- 欠損、エラー、Unclassifiedがあるrunを除外した理由を残す。
- 複数回の結果がばらつく場合は範囲を示し、都合のよいrunだけを選ばない。
