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
    report = build_report(run_id=run_id, results_dir=results_dir, pricing_path=pricing_path)
    # 比較可否と注意点は、Markdownレポートと同じ判定（quality.assess_run）の結果を使う。
    return {
        **report,
        "metadata": _public_metadata(report["metadata"]),
        "collected": (run_dir / "merged.jsonl").is_file(),
        "issues": report["quality"]["issues"],
        "comparable": report["quality"]["comparable"],
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
                    "languages": [],
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
                # 履歴で計測同士を見分けられるよう、計測した言語の表示名を表示順で返す。
                "languages": list(run["labels"].values()),
                "comparable": run["comparable"],
                "issues": run["issues"],
            }
        )
    # 開始日時がないrunは並べようがないため、一覧の末尾へ置く。
    runs.sort(key=lambda run: run["started_at"] or "", reverse=True)
    return runs


def latest_costs_by_workload(*, results_dir: Path, pricing_path: Path) -> dict[str, Any]:
    """ワークロードごとに、言語別の最新の実測から1リクエストあたり推定料金を返す。

    計測開始前の料金の目安は、同じワークロードの実測から算出する。言語を後から追加した場合も
    目安を出せるよう、言語ごとに比較可能な最新runの値を使う。実測がない言語は含めない。
    """
    basis: dict[str, Any] = {}
    # 一覧は新しい順なので、各ワークロード・言語で最初に見つかった値が最新になる。
    for summary in list_runs(results_dir=results_dir, pricing_path=pricing_path):
        # 比較不能なrunは料金や条件が欠けるため使わない。
        if not summary["comparable"]:
            continue
        run = load_run(summary["run_id"], results_dir=results_dir, pricing_path=pricing_path)
        entry = basis.setdefault(
            summary["workload"],
            {"started_at": summary["started_at"], "cost_per_request": {}},
        )
        for name, values in run["implementations"].items():
            cost = (values.get("cost") or {}).get("estimated_cost_per_request")
            # 料金を計算できた言語のうち、まだ値がない（より新しい実測がない）ものだけ採用する。
            if cost is not None and name not in entry["cost_per_request"]:
                entry["cost_per_request"][name] = cost
                # 目安の根拠として、採用した実測のうち最も古い日時を示す。
                entry["started_at"] = summary["started_at"]
    return basis
