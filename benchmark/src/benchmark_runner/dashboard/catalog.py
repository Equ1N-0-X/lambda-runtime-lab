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
    return [summary for summary, _run in _scan_runs(results_dir, pricing_path)]


def _scan_runs(
    results_dir: Path, pricing_path: Path
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """全runを一度だけ読み、新しい順の概要と、読み込んだ比較結果の組を返す。

    同じ要求の中で概要と詳細の両方が必要な処理（料金の目安など）が、同じrunを読み直さない
    ようにする。要求をまたいで保持はしないため、結果や料金表の更新は次の要求で反映される。
    読み込めなかったrunの比較結果はNoneとする。
    """
    scanned: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for run_dir in _run_dirs(results_dir):
        try:
            run = load_run(run_dir.name, results_dir=results_dir, pricing_path=pricing_path)
        # 壊れたrunが一つあっても一覧全体は表示し、そのrunだけ読込失敗として示す。
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            scanned.append(
                (
                    {
                        "run_id": run_dir.name,
                        "started_at": None,
                        "workload": None,
                        "architecture": None,
                        "memory_mb": None,
                        "iterations": None,
                        "region": None,
                        "experiment_id": None,
                        "client_concurrency": None,
                        "image_digests": {},
                        "languages": [],
                        "comparable": False,
                        "issues": [f"結果ファイルを読み込めません: {exc}"],
                    },
                    None,
                )
            )
            continue

        metadata = run["metadata"]
        workload = metadata.get("workload") or {}
        scanned.append(
            (
                {
                    "run_id": run["run_id"],
                    "started_at": metadata.get("started_at"),
                    "workload": workload.get("name"),
                    "architecture": metadata.get("architecture"),
                    "memory_mb": metadata.get("memory_mb"),
                    "iterations": workload.get("iterations"),
                    # 料金の根拠を選ぶとき、同じ地域・同じ実験・同じ版の計測かを照合するために使う。
                    # 記録がない古い計測ではNoneのままとし、推測で補わない。
                    "region": metadata.get("region"),
                    "experiment_id": metadata.get("experiment_id"),
                    "client_concurrency": workload.get("client_concurrency"),
                    "image_digests": _image_digests(metadata),
                    # 履歴で計測同士を見分けられるよう、計測した言語の表示名を表示順で返す。
                    "languages": list(run["labels"].values()),
                    "comparable": run["comparable"],
                    "issues": run["issues"],
                },
                run,
            )
        )
    # 開始日時がないrunは並べようがないため、一覧の末尾へ置く。
    scanned.sort(key=lambda entry: entry[0]["started_at"] or "", reverse=True)
    return scanned


def _image_digests(metadata: dict[str, Any]) -> dict[str, str]:
    """計測前に確かめた、言語ごとのイメージdigestを返す。記録がない言語は含めない。"""
    deployments = (metadata.get("verification") or {}).get("deployments") or {}
    return {
        key: deployment["image_digest"]
        for key, deployment in deployments.items()
        if isinstance(deployment, dict) and deployment.get("image_digest")
    }


def latest_costs_by_workload(
    *,
    results_dir: Path,
    pricing_path: Path,
    architecture: str | None,
    memory_mb: int | None,
    region: str,
    experiments: dict[str, str],
) -> dict[str, Any]:
    """これから計測する条件に合う実測から、ワークロード・言語ごとの1リクエストあたり料金を返す。

    計測開始前の料金の目安に使う。方式・メモリ・地域が予定と同じで、処理の内容
    （experiments: ワークロード名から実験の識別子）が同じ計測だけを根拠にする。
    言語を後から追加した場合も目安を出せるよう、言語ごとに条件に合う最新の値を使う。
    条件に合う実測がない言語は含めない（古い条件の料金で代用しない）。
    """
    basis: dict[str, Any] = {}
    # 予定の方式・メモリが分からなければ、どの実測が予定と同じ条件か決められない。
    if architecture is None or memory_mb is None:
        return basis
    # 一覧は新しい順なので、各ワークロード・言語で最初に見つかった値が最新になる。
    for summary, run in _scan_runs(results_dir, pricing_path):
        workload = summary["workload"]
        # 比較不能な計測や、予定と異なる条件・処理内容の計測は料金の根拠に使わない。
        if (
            not summary["comparable"]
            or workload not in experiments
            or summary["experiment_id"] != experiments[workload]
            or summary["architecture"] != architecture
            or summary["memory_mb"] != memory_mb
            or summary["region"] != region
        ):
            continue
        # 料金の指標を根拠に使えない計測（料金表の地域違いなど）は採用しない。
        if run is None or run["quality"]["columns"]["cost"]["status"] != "usable":
            continue
        entry = basis.setdefault(
            workload,
            {
                "started_at": summary["started_at"],
                "client_concurrency": summary["client_concurrency"],
                "cost_per_request": {},
                "sources": {},
            },
        )
        for name, values in run["implementations"].items():
            cost = (values.get("cost") or {}).get("estimated_cost_per_request")
            # 料金を計算できた言語のうち、まだ値がない（より新しい実測がない）ものだけ採用する。
            if cost is not None and name not in entry["cost_per_request"]:
                entry["cost_per_request"][name] = cost
                entry["sources"][name] = summary["run_id"]
                # 目安の根拠として、採用した実測のうち最も古い日時を示す。
                entry["started_at"] = summary["started_at"]
    return basis
