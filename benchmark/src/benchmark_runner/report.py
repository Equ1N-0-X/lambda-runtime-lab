"""集計済み測定値・料金・イメージ情報から、MarkdownとCSVの比較レポートを作成する。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from benchmark_runner.analyzer import METRICS, read_records, summarize
from benchmark_runner.cost import PricingUnavailable, load_pricing, summarize_costs

PERCENTILES = ("p50", "p95", "p99")


def _display(value: object, digits: int = 6) -> str:
    """欠損はnull、浮動小数は指定有効桁、それ以外は文字列として表示する。"""

    # 欠損をゼロと誤認させないため、Noneは明示的なnullとして表示する。
    if value is None:
        return "null"
    # 浮動小数だけ桁数を制御し、件数や識別子は元の表現を維持する。
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _difference(reference: object, comparison: object) -> float | None:
    """Pythonを基準としたRustの増減率を計算し、比較不能ならNoneを返す。"""

    # 基準値が真偽値を除く数値でなければ、意味のある比率を計算できないため欠損とする。
    if not isinstance(reference, int | float) or isinstance(reference, bool):
        return None
    # 比較値も真偽値を除く数値でなければ、片側欠損として比率を作らない。
    if not isinstance(comparison, int | float) or isinstance(comparison, bool):
        return None
    # 基準値がゼロの場合は除算不能かつ増減率が誤解を招くため、欠損として扱う。
    if reference == 0:
        return None
    return (float(comparison) - float(reference)) / float(reference) * 100


def _load_optional_json(path: Path) -> object | None:
    """任意成果物が存在する場合だけJSONとして読み、未収集ならNoneを返す。"""
    # ファイルがなければ異常終了せず、レポート内で未収集と明示できるようNoneを返す。
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(*, run_id: str, results_dir: Path, pricing_path: Path) -> dict[str, Any]:
    """一実行分の測定・料金・イメージ情報を集計し、出力形式に依存しない比較結果を返す。

    Markdown・CSVと画面表示が同じ計算結果を使うよう、集計はこの関数だけで行う。
    """
    run_dir = results_dir / "raw" / run_id
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    records = read_records(run_dir)
    analysis = summarize(records)
    image_metrics = _load_optional_json(run_dir / "image-metrics.json")

    pricing_note: str | None = None
    pricing_metadata: dict[str, Any] | None = None
    # 料金が確認済みなら推定コストを計算し、未設定なら理由を注記して性能レポートは継続する。
    try:
        pricing = load_pricing(pricing_path)
        costs = summarize_costs(
            records,
            architecture=metadata.get("architecture"),
            default_memory_mb=metadata.get("memory_mb"),
            pricing=pricing,
        )
        pricing_metadata = {
            "updated_at": pricing.updated_at,
            "region": pricing.region,
            "source": pricing.source,
        }
    except PricingUnavailable as exc:
        costs = {}
        pricing_note = str(exc)

    implementations = analysis["implementations"]
    for name, values in implementations.items():
        # 対応する料金集計がない実装は、ゼロではなく未計算としてNoneを保持する。
        values["cost"] = costs.get(name)

    differences: dict[str, dict[str, float | None]] | None = None
    # PythonとRustの両方がある場合だけ、Pythonを基準にしたRustの増減率を指標・百分位ごとに求める。
    if {"python", "rust"} <= implementations.keys():
        differences = {
            metric: {
                percentile_name: _difference(
                    implementations["python"]["metrics"][metric][percentile_name],
                    implementations["rust"]["metrics"][metric][percentile_name],
                )
                for percentile_name in PERCENTILES
            }
            for metric in METRICS
        }

    return {
        "run_id": run_id,
        "metadata": metadata,
        "implementations": implementations,
        "differences": differences,
        "pricing": pricing_metadata,
        "pricing_note": pricing_note,
        "image_metrics": image_metrics,
    }


def generate_report(
    *,
    run_id: str,
    results_dir: Path,
    pricing_path: Path,
) -> tuple[Path, Path]:
    """一実行分の測定を集計し、人向けMarkdownと機械処理向けCSVを同時に生成する。"""
    report = build_report(run_id=run_id, results_dir=results_dir, pricing_path=pricing_path)
    metadata = report["metadata"]
    implementations = report["implementations"]
    differences = report["differences"]
    pricing_metadata = report["pricing"]
    pricing_note = report["pricing_note"]
    image_metrics = report["image_metrics"]

    reports_dir = results_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = reports_dir / f"{run_id}.md"
    csv_path = reports_dir / f"{run_id}.csv"

    lines = [
        f"# Benchmark report: {run_id}",
        "",
        "このレポートは、同じ条件で実行したPython/Rust Lambdaの観測値を比較するための資料です。",
        "最初に実験条件とSummaryの欠損を確認し、比較可能な場合だけPercentiles以降を読みます。",
        "詳しい判断基準は [ベンチマークレポート利用ガイド]"
        "(../../docs/07_BENCHMARK_REPORT_GUIDE.md) を参照してください。",
        "",
        "## Experiment conditions（実験条件）",
        "",
        "比較対象を再現するための実行条件です。条件が異なるrun同士の数値は直接比較しません。",
        "ArchitectureまたはLambda memoryが`null`なら、コストを含む正式な比較には使用できません。",
        "",
        f"- Run ID: `{run_id}`",
        f"- Started: {_display(metadata.get('started_at'))}",
        f"- Completed: {_display(metadata.get('completed_at'))}",
        f"- Git revision: {_display(metadata.get('git_revision'))}",
        f"- AWS region: {_display(metadata.get('region'))}",
        f"- Architecture: {_display(metadata.get('architecture'))}",
        f"- Lambda memory: {_display(metadata.get('memory_mb'))} MB",
        f"- Workload: {_display(metadata.get('workload', {}).get('name'))}",
        f"- Iterations: {_display(metadata.get('workload', {}).get('iterations'))}",
        f"- Client concurrency: {_display(metadata.get('workload', {}).get('client_concurrency'))}",
        f"- Warm-up: {_display(metadata.get('workload', {}).get('warmup'))}",
        "",
        "## Summary（概要）",
        "",
        "測定件数、Cold/Warm分類、エラー率を確認する入口です。`Unclassified`が1件でもある場合は、",
        "CloudWatchの実行情報と結合できていないため、Lambda実行時間・メモリ・コストを判断しません。",
        "",
        "| Implementation | Samples | Cold | Warm | Unclassified | Error rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in implementations.items():
        error_rate = values["error_rate"]
        rendered_rate = "null" if error_rate is None else f"{error_rate:.2%}"
        lines.append(
            f"| {name} | {values['sample_count']} | {values['cold_count']} | "
            f"{values['warm_count']} | {values['unclassified_count']} | "
            f"{rendered_rate} |"
        )

    # 未分類が残るレポートでは、クライアント側レイテンシー以外を比較しないよう明示する。
    if any(values["unclassified_count"] for values in implementations.values()):
        lines.extend(
            [
                "",
                "> 注意: Unclassifiedが残っています。このrunは相関不完全なため、"
                "Lambda内部メトリクスやコストの比較には使用しません。",
            ]
        )

    lines.extend(
        [
            "",
            "## Percentiles（パーセンタイル）",
            "",
            "値の分布をP50（中央値）、P95、P99で示します。`all`は全件、`cold`と`warm`は",
            "Lambda REPORTによる分類別です。Countが0の行の`null`は、",
            "ゼロではなく未取得を意味します。",
            "",
            "- `latency_ms`: クライアントから見たAPI全体の応答時間",
            "- `duration_ms`: Lambda内でハンドラーを実行した時間",
            "- `billed_duration_ms`: Lambda料金計算に使われる課金対象時間",
            "- `init_duration_ms`: Cold Start時の初期化時間",
            "- `max_memory_used_mb`: 実行中に観測された最大メモリ使用量",
            "",
            "| Implementation | Scope | Metric | Count | P50 | P95 | P99 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in implementations.items():
        for scope, key in (
            ("all", "metrics"),
            ("cold", "cold_metrics"),
            ("warm", "warm_metrics"),
        ):
            for metric in METRICS:
                stats = values[key][metric]
                lines.append(
                    f"| {name} | {scope} | {metric} | {stats['count']} | "
                    f"{_display(stats['p50'])} | {_display(stats['p95'])} | "
                    f"{_display(stats['p99'])} |"
                )

    lines.extend(
        [
            "",
            "## Estimated cost（推定コスト、Free Tier除外）",
            "",
            "Billed Duration、設定メモリ、料金表から求めた概算です。Free Tier、税、API Gateway、",
            "DynamoDB等は含まず、AWS請求額そのものではありません。`null`なら理由を解消するまで比較しません。",
            "Compute cost/1M requestsは、実装によらず同額のリクエスト料金を除いた料金です。",
            "",
        ]
    )
    # 料金注記がある場合は推定値を載せず、ない場合だけ出典と実装別料金表を出力する。
    if pricing_note:
        lines.append(f"Cost: null — {pricing_note}")
    else:
        lines.extend(
            [
                f"- Pricing updated: {_display(pricing_metadata['updated_at'])}",
                f"- Pricing region: {_display(pricing_metadata['region'])}",
                f"- Pricing source: {_display(pricing_metadata['source'])}",
                "",
                "| Implementation | Priced samples | Cost/request | Cost/1M requests "
                "| Compute cost/1M requests |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name, values in implementations.items():
            cost = values["cost"]
            # 有効な料金サンプルがない実装は、金額ゼロとせずnullとして表示する。
            if cost is None:
                lines.append(f"| {name} | 0 | null | null | null |")
            else:
                lines.append(
                    f"| {name} | {cost['priced_sample_count']} | "
                    f"{_display(cost['estimated_cost_per_request'], 10)} | "
                    f"{_display(cost['estimated_cost_per_1m'], 10)} | "
                    f"{_display(cost['estimated_compute_cost_per_1m'], 10)} |"
                )

    lines.extend(
        [
            "",
            "## Python/Rust difference（差分）",
            "",
            "Pythonを基準にRustの値が何%増減したかを示します。正数はRustが高く、負数はRustが低い値です。",
            "時間・メモリ・コストは通常低い方が有利ですが、欠損やサンプル数を確認せず優劣を断定しません。",
            "",
        ]
    )
    # 相対差を計算できた場合だけ表を出し、片方の実装しかなければ比較不能と明示する。
    if differences is not None:
        lines.extend(
            [
                "正数はRustがPythonより高い値、負数は低い値であることを表します。",
                "",
                "| Metric | Percentile | Difference |",
                "|---|---|---:|",
            ]
        )
        for metric in METRICS:
            for percentile_name in PERCENTILES:
                difference = differences[metric][percentile_name]
                lines.append(
                    f"| {metric} | {percentile_name.upper()} | "
                    f"{'null' if difference is None else f'{difference:.2f}%'} |"
                )
    else:
        lines.append("Comparison: null — both implementations are required.")

    lines.extend(
        [
            "",
            "## Image metrics（イメージ指標）",
            "",
            "デプロイしたコンテナイメージのdigestやサイズを確認する章です。イメージサイズは配布・保管上の",
            "指標であり、Lambda実行料金やメモリ使用量とは別に評価します。",
            "",
        ]
    )
    # イメージ計測ファイルがなければ未収集と明示し、あれば加工せずJSONで掲載する。
    if image_metrics is None:
        lines.append("Image metrics: null — image-metrics.json was not collected.")
    else:
        lines.append("```json")
        lines.append(json.dumps(image_metrics, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")

    lines.extend(
        [
            "",
            "## Measurement notes（測定上の注意）",
            "",
            "欠損値の扱いと、このレポートを解釈するときの共通ルールです。比較結果を引用・公開する場合も",
            "この注記を前提として併記します。",
            "",
            "- 未取得の測定値は`null`で示し、ゼロへ置き換えません。",
            "- Lambda REPORTにInit DurationがあるサンプルだけをColdと分類します。",
            "- REPORTがありInit DurationがないサンプルはWarm、結合できないサンプルは"
            "Unclassifiedです。",
            "- コストはAWS Free Tierを差し引かずに正規化します。",
            "- イメージサイズは成果物の指標であり、Lambda実行料金には含めません。",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    fieldnames = [
        "run_id",
        "implementation",
        "sample_count",
        "cold_count",
        "warm_count",
        "unclassified_count",
        "error_count",
        "error_rate",
    ]
    for metric in METRICS:
        fieldnames.extend(f"{metric}_{percentile}" for percentile in ("p50", "p95", "p99"))
    fieldnames.extend(
        [
            "priced_sample_count",
            "estimated_cost_per_request",
            "estimated_cost_per_1m",
            "estimated_compute_cost_per_1m",
        ]
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        # CSVは一実装一行とし、Markdownと同じ集計値を後続処理が再利用できる形で保存する。
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for name, values in implementations.items():
            row: dict[str, Any] = {
                "run_id": run_id,
                "implementation": name,
                "sample_count": values["sample_count"],
                "cold_count": values["cold_count"],
                "warm_count": values["warm_count"],
                "unclassified_count": values["unclassified_count"],
                "error_count": values["error_count"],
                "error_rate": values["error_rate"],
            }
            for metric in METRICS:
                for percentile_name in ("p50", "p95", "p99"):
                    row[f"{metric}_{percentile_name}"] = values["metrics"][metric][percentile_name]
            cost = values["cost"] or {}
            row.update(
                {
                    "priced_sample_count": cost.get("priced_sample_count"),
                    "estimated_cost_per_request": cost.get("estimated_cost_per_request"),
                    "estimated_cost_per_1m": cost.get("estimated_cost_per_1m"),
                    "estimated_compute_cost_per_1m": cost.get("estimated_compute_cost_per_1m"),
                }
            )
            writer.writerow(row)
    return markdown_path, csv_path
