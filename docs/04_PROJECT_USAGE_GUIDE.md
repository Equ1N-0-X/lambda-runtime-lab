# Lambda Runtime Lab: 目的・使い方・現在地

## この資料の対象

この資料は、Python と Rust で実装した AWS Lambda API を同じ条件で比較する
`lambda-runtime-lab` の概要と、実測まで進めるための手順を説明します。
コードを読まなくても「何を比較し、どこまでできていて、次に何をするか」を
把握するための入口です。

正確なAPI仕様は [02_SPECIFICATION.md](02_SPECIFICATION.md)、作業上の問題は
[WORK-71 静的レビュー](reviews/WORK-71_static_review.md) を正本とします。

## 何を作っているか

同じ5つのAPIを、Python と Rust でそれぞれ1つの AWS Lambda として実装します。
外から見える動き、入力データ、エラー、DynamoDB の読み取り回数、Lambda の設定、
計測条件はそろえます。一方で、内部の実装方法は各言語に自然なものを使います。

そのため、単純なプログラミング言語の速度競争ではなく、実際に小規模APIを採用した
ときの選択材料を作るプロジェクトです。

| 比較するもの | 内容 |
| --- | --- |
| APIの基本処理 | `GET /health`。フレームワークとLambdaの最小的な負荷 |
| JSON処理 | `POST /transform`。入力確認、文字列整形、並び替え |
| CPU処理 | `POST /compute`。同じSHA-256計算 |
| 単一データ取得 | `GET /items/{id}`。DynamoDBを1回読む処理 |
| 複数データ集約 | `POST /aggregate`。DynamoDBを直列または並行で読む処理 |

比較結果として、応答時間、Cold/Warm Start、メモリ、コンテナサイズ、推定コストを
収集します。コンテナサイズはデプロイ成果物の大きさであり、Lambda実行料金そのもの
ではありません。

## いま出来ていること

| 領域 | 状態 | 根拠 |
| --- | --- | --- |
| API実装 | 完了 | Python/Rustとも5 endpointと共通エラー形式を実装 |
| APIの同一性 | 部分確認 | 各実装の単体テストは成功。稼働中コンテナ間の比較は未実行 |
| インフラ定義 | 作成済み・設定確認済み | Terraform planで実リソースとの差分なしを確認 |
| 計測・レポート | 実装済み、実測未実行 | Runner、CloudWatch収集、レポート生成を用意 |
| Docker | 未確認 | このCursor環境にはコンテナがなく、arm64ビルド未実行 |
| コスト比較 | 保留 | 料金設定が未入力で、安全に計算を停止する状態 |

ローカルで成功した検証は、Python 52件、Rust 33件、ベンチマーク23件、契約テスト
ハーネス6件です。Ruff、rustfmt、Clippy、Terraform validateも成功しています。

## 実測までの作業順序

AWS操作は料金が発生するため、対象アカウント、リージョン、費用上限を決めてから
実行します。`make destroy` は作成したAWSリソースを削除します。

1. レビュー指摘を解消する
   - `make benchmark-matrix` が依存関係を確実に利用するよう直す。
   - Pythonイメージの依存ライブラリサイズを正しい場所で計測できるよう直す。
   - AWS公式料金を確認し、`spec/pricing.yaml` に確認日・出典・料金を設定する。
2. コンテナを確認する
   - Docker Buildxを用意し、`make build` で両方の `linux/arm64` イメージを作る。
   - 同じAL2023の最終ベースイメージを使っていること、イメージが起動することを確認する。
3. コンテナ間の動作を確認する
   - DynamoDB Localへテストデータを投入する。
   - Python APIとRust Lambdaコンテナを起動し、`make contract-test` を実行する。
4. AWS上で小さく確認する
   - [AWSデプロイ手順](06_AWSデプロイ手順.md) に従い、ECRへのpush後にLambdaの参照digestを更新する。
   - seed data投入後、1ワークロードを少数回実行して、ログ収集とMarkdown/CSVレポート生成を確認する。
5. 標準比較を実行する
   - arm64、メモリ256/512/1024 MB、全7ワークロードで計測する。
   - 結果・イメージ情報・料金前提を保存し、数値だけで結論を断定しない。
6. 後片付けと公開確認をする
   - `make destroy` で作成リソースを削除し、削除結果を確認する。
   - 実測結果、制約、再現手順をREADMEへ反映する。

## 実行前に必要なもの

- Docker と Buildx（arm64イメージ作成とコンテナ間テスト）
- Python 3.13、uv、Rust stable、Terraform
- AWS CLIと、ECR・Lambda・API Gateway・DynamoDB・CloudWatchを操作できるAWS認証情報
- AWS API GatewayへIAM認証でアクセスする権限

AWS認証情報、アクセスキー、秘密値をリポジトリへ保存してはいけません。

## 成功とみなす条件

次をすべて満たしたとき、MVPを「実測まで完了」とします。

- 両実装が5 endpointと共通API契約を満たす。
- 同一AL2023ベースのarm64コンテナがビルド・起動できる。
- AWSへ2つのLambdaをデプロイし、同一条件で呼び出せる。
- CloudWatch、イメージサイズ、コストを収集し、Markdown/CSVレポートを作れる。
- CIと`make destroy`が確認済みである。

現時点は、AWSインフラの作成とTerraformとの差分なし確認まで完了しています。コンテナ
間の契約確認、データ投入後のAPI疎通、実測結果の収集は残っています。そのため、実測結果
からPython/Rustの優劣を結論づける段階ではありません。
