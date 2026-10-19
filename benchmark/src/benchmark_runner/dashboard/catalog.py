"""保存済みの計測runを列挙し、画面表示用に比較可否と注意点を付けて返す。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from benchmark_runner.report import build_report

# run IDはディレクトリ名として使うため、パス区切りや親参照を含まない文字だけを許可する。
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# 画面に出すと公開時やスクリーンショットで環境情報が漏れるため、メタデータから除く項目。
HIDDEN_METADATA_KEYS = ("endpoints",)


class RunNotFound(LookupError):
    """指定されたrun IDの計測結果が存在しないことを表す。"""


def _run_dirs(results_dir: Path) -> list[Path]:
    """計測メタデータと測定値の両方を持つディレクトリだけをrunとして返す。"""
    raw_dir = results_dir / "raw"
    # 結果ディレクトリ自体がなければ、まだ一度も計測していない空状態として扱う。
    if not raw_dir.is_dir():
        return []
    return [
        path
        for path in raw_dir.iterdir()
        if path.is_dir()
        and RUN_ID_PATTERN.match(path.name)
        and (path / "metadata.json").is_file()
        and (path / "samples.jsonl").is_file()
    ]


def _unlinked_cause(run_dir: Path) -> str:
    """収集済みLambdaログの内容から、計測と結び付けられなかった理由と対処を返す。"""
    events_path = run_dir / "cloudwatch.jsonl"
    events = []
    # 収集イベントがあれば読み、なければ理由を特定できないため一般的な文言に任せる。
    if events_path.is_file():
        with events_path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
    reports = [event for event in events if event.get("type") == "report"]
    app_logs = [event for event in events if event.get("type") == "structured"]

    # 実行レポートが一件もなければ、実行時間・メモリ・課金時間の元データ自体を読めていない。
    if not reports:
        return (
            "Lambdaの実行レポート（実行時間・課金時間・メモリ）をログから読み取れませんでした。"
            "ログを再収集してください。"
        )
    # アプリログに計測番号がなければ、デプロイ中のLambdaが計測番号を出力しない古い版である。
    if app_logs and all(event.get("benchmark_request_index") is None for event in app_logs):
        return (
            "デプロイ中のLambdaが計測番号をログに出力していないため、どのリクエストの記録か特定できません。"
            "最新のアプリをデプロイしてから計測し直してください。"
        )
    return "対応するLambdaのログが見つかりませんでした。"


def _issues(report: dict[str, Any], *, collected: bool, run_dir: Path) -> list[str]:
    """各実装を同じ条件で比較できない理由を、利用者が対処できる文言で列挙する。"""
    metadata = report["metadata"]
    implementations = report["implementations"]
    issues: list[str] = []

    # ドライランは実際のAPIを呼んでいないため、どの数値も性能比較に使えない。
    if metadata.get("dry_run"):
        issues.append("ドライランの結果です。実際のAPIは呼び出していません。")
    # 実装が一つしかなければ、比べる相手がいないため差分を計算できない。
    if len(implementations) < 2:
        issues.append("計測した言語が一つだけのため、比較できません。")
    # アーキテクチャかメモリが記録されていなければ、条件の再現とコスト計算ができない。
    if metadata.get("architecture") is None or metadata.get("memory_mb") is None:
        issues.append(
            "アーキテクチャまたはメモリ設定が記録されていません。コストは比較できません。"
        )
    # ログ未収集なら、Lambda内部の実行時間・メモリ・コストはまだ存在しない。
    if not collected:
        issues.append(
            "Lambdaのログを収集していません。応答時間以外の指標とコストは表示できません。"
        )
    else:
        unclassified = sum(values["unclassified_count"] for values in implementations.values())
        # 収集後もログと結合できない計測が残れば、Lambda内部の指標は一部欠けている。
        if unclassified:
            total = sum(values["sample_count"] for values in implementations.values())
            issues.append(
                f"{total}件中{unclassified}件の計測で、Lambda側の記録（実行時間・メモリ・料金）が"
                f"得られていません。{_unlinked_cause(run_dir)}"
            )
    return issues


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """画面に返してよい実行条件だけを残したメタデータを返す。"""
    return {key: value for key, value in metadata.items() if key not in HIDDEN_METADATA_KEYS}


def load_run(run_id: str, *, results_dir: Path, pricing_path: Path) -> dict[str, Any]:
    """一つのrunの比較結果に、比較可否と注意点を加えて返す。"""
    # 形式外のIDや存在しないrunは、ファイルを読む前に未検出として扱う。
    if not RUN_ID_PATTERN.match(run_id) or run_id not in {
        path.name for path in _run_dirs(results_dir)
    }:
        raise RunNotFound(run_id)

    run_dir = results_dir / "raw" / run_id
    collected = (run_dir / "merged.jsonl").is_file()
    report = build_report(run_id=run_id, results_dir=results_dir, pricing_path=pricing_path)
    issues = _issues(report, collected=collected, run_dir=run_dir)
    return {
        **report,
        "metadata": _public_metadata(report["metadata"]),
        "collected": collected,
        "issues": issues,
        "comparable": not issues,
    }


def list_runs(*, results_dir: Path, pricing_path: Path) -> list[dict[str, Any]]:
    """全runを開始日時の新しい順に並べ、一覧表示に必要な概要だけを返す。"""
    runs: list[dict[str, Any]] = []
    for run_dir in _run_dirs(results_dir):
        try:
            run = load_run(run_dir.name, results_dir=results_dir, pricing_path=pricing_path)
        # 壊れたrunが一つあっても一覧全体は表示し、そのrunだけ読込失敗として示す。
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            runs.append(
                {
                    "run_id": run_dir.name,
                    "started_at": None,
                    "workload": None,
                    "architecture": None,
                    "memory_mb": None,
                    "iterations": None,
                    "comparable": False,
                    "issues": [f"結果ファイルを読み込めません: {exc}"],
                }
            )
            continue

        metadata = run["metadata"]
        workload = metadata.get("workload") or {}
        runs.append(
            {
                "run_id": run["run_id"],
                "started_at": metadata.get("started_at"),
                "workload": workload.get("name"),
                "architecture": metadata.get("architecture"),
                "memory_mb": metadata.get("memory_mb"),
                "iterations": workload.get("iterations"),
                "comparable": run["comparable"],
                "issues": run["issues"],
            }
        )
    # 開始日時がないrunは並べようがないため、一覧の末尾へ置く。
    runs.sort(key=lambda run: run["started_at"] or "", reverse=True)
    return runs


def latest_costs_by_workload(*, results_dir: Path, pricing_path: Path) -> dict[str, Any]:
    """ワークロードごとに、比較可能な最新runの1リクエストあたり推定料金を返す。

    計測開始前の料金の目安は、同じワークロードの実測から算出する。実測がなければ目安を出さない。
    """
    basis: dict[str, Any] = {}
    # 一覧は新しい順なので、各ワークロードで最初に見つかった比較可能runを採用する。
    for summary in list_runs(results_dir=results_dir, pricing_path=pricing_path):
        workload = summary["workload"]
        # 比較不能なrunは料金や条件が欠けるため使わない。
        # 採用済みのワークロードは、それより古いrunなので飛ばす。
        if not summary["comparable"] or workload in basis:
            continue
        run = load_run(summary["run_id"], results_dir=results_dir, pricing_path=pricing_path)
        costs = {
            name: (values.get("cost") or {}).get("estimated_cost_per_request")
            for name, values in run["implementations"].items()
        }
        # いずれかの実装の料金が計算できていなければ、目安の根拠にしない。
        if None in costs.values():
            continue
        basis[workload] = {
            "run_id": summary["run_id"],
            "started_at": summary["started_at"],
            "cost_per_request": costs,
        }
    return basis
