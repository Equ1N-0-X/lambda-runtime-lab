# WORK-71 静的レビュー

### 判定

条件付き承認

理由: Criticalはありません。ただし、標準の計測マトリクス実行経路が依存関係不足で
この環境では起動せず、Pythonの依存ライブラリサイズも正しく計測できません。
Docker・AWSを使う実行前にError 1と2を修正し、料金比較まで行う場合はError 3を
解消する必要があります。

### 確認した範囲

- 対象: `main`、初期コミット `7ff847e` から `5624fcd` までの97ファイル、課題 `WORK-71`
- 正本: `docs/01_REQUIREMENTS.md`、`docs/02_SPECIFICATION.md`、`docs/03_CURSOR_BUILD_PROMPT.md`、`spec/openapi.yaml`、`spec/workloads.yaml`
- 差分: Python/Rust API、Benchmark Runner、Dockerfile、Terraform、CI、Makefile、関連テスト
- 実施した検証: Python 52件、Rust 33件、Benchmark 23件、Contract harness 6件、Ruff、rustfmt、Clippy、Terraform validate。シェル構文確認、Docker/計測マトリクスのドライラン、Benchmark Runnerのドライラン
- 未検証: Docker arm64ビルド・起動、DynamoDB Localを使った統合Contract Test、AWSデプロイ、CloudWatch収集、GitHub Actions、`make destroy`

### 指摘

#### Critical

なし

#### Error

1. **標準の計測マトリクスが依存関係なしのPythonで起動する**

   - 種類: 正しさ
   - 内容: `scripts/benchmark-matrix.sh:14` は `python3 -m benchmark_runner` を直接実行します。Makefileは通常の実行で `uv run --project benchmark` を使う構成ですが、この経路だけはuv環境を使いません。この環境で同じコマンドを実行すると `ModuleNotFoundError: No module named 'boto3'` となり、AWS呼出し前に停止しました。
   - 改善提案: Runner配列を `uv run --project "${ROOT}/benchmark" python -m benchmark_runner` に統一し、`make benchmark-matrix` のスモークテストを追加してください。

2. **Python依存ライブラリサイズを存在しない場所から測定している**

   - 種類: 正しさ
   - 内容: `apps/python/Dockerfile:35` は依存ライブラリを `/var/task` へ配置しますが、`scripts/collect-image-metrics.sh:80-82` は `/var/lang/lib/python3.13/site-packages` を測定します。結果は`null`となり、要件で求めるPython依存ライブラリのフットプリントを比較できません。
   - 改善提案: 依存ライブラリをアプリケーションソースと別ディレクトリへコピーしてそのディレクトリを測定する、またはDockerfileと収集スクリプトで同じ配置先を扱うよう修正してください。

3. **コスト比較に必要な料金が未設定である**

   - 種類: 正しさ
   - 内容: `spec/pricing.yaml:2,6-9` がすべて `null` です。安全に計算を止める実装は正しい一方、現状はコスト比較を生成できず、MVPの完了条件を満たしません。
   - 改善提案: AWS公式の東京リージョン料金を確認した日付・出典とともに設定し、料金設定ありのレポート生成テストを追加してください。料金を設定できない間は、公開資料で「コスト比較は未実施」と明示してください。

#### Warning

4. **DockerとAWSの統合経路は未実証である**

   - 種類: テスト不足
   - 内容: Docker arm64ビルド、稼働中コンテナ間のContract Test、AWSデプロイ、CloudWatch相関、ECR計測、`make destroy` は未実行です。単体テストとTerraform validateはこれらの実行時差異を検出しません。
   - 改善提案: Error 1・2の修正後、DockerによるCIのcontract job相当をローカルまたはGitHub Actionsで実行し、その後に最小AWSスモークテストを実施してください。

#### Info

なし

### 残リスク

- Dockerのない環境では、コンテナの起動、arm64互換性、同じ最終ベースイメージの実効性を確認できません。
- AWSの認証・課金・外部サービスを要するため、実測と削除確認は対象アカウントと費用上限の合意後に実施する必要があります。
- 料金は変動し得る情報です。設定後も、実測のたびに出典と確認日を見直す必要があります。

### 指摘サマリー

| # | 重要度 | 種類 | 要約 | 改善提案 |
| --- | --- | --- | --- | --- |
| 1 | Error | 正しさ | マトリクス実行が依存関係のない`python3`を使う | uv経由へ統一しスモークテストを追加 |
| 2 | Error | 正しさ | Python依存ライブラリのサイズ計測先が誤っている | 配置先と計測先を一致させる |
| 3 | Error | 正しさ | 料金未設定でコスト比較を生成できない | 公式料金と出典・確認日を設定 |
| 4 | Warning | テスト不足 | Docker/AWS統合経路が未実証 | コンテナCI相当とAWSスモークテストを実施 |
