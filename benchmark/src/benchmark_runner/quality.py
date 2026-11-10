"""一つのrunの集計結果について、比べる指標ごとに根拠として使えるかを判定する。

判定はこのモジュールだけで行い、Markdownレポート・画面の両方が同じ結果を表示する。
画面側では判定を再計算しない。

指標ごとの状態:
- usable: 言語間の比較の根拠として使える。
- reference: 値は示すが、比較の結論（最良・倍率）には使わない参考値。
- unusable: 値がない、または比べる前提を満たさない。

失敗した要求が1件でもあるrunは正式な比較に使わず、成功分を参考値として示す。
コールドスタートの正式な比較に必要な件数・集め方は未決定のため、その指標は参考値として扱う。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

USABLE = "usable"
REFERENCE = "reference"
UNUSABLE = "unusable"
_RANK = {USABLE: 0, REFERENCE: 1, UNUSABLE: 2}

# 比べる指標。画面の結論・比較表の列と同じキーを使う。
COLUMNS = ("cost", "compute_cost", "duration", "init", "latency", "memory")
# Lambdaのログ（REPORT）から得る指標。ログと結び付けられない計測があると値が欠ける。
LOG_COLUMNS = ("cost", "compute_cost", "duration", "init", "memory")
COST_COLUMNS = ("cost", "compute_cost")
# runの性能を比較可能かを決める指標。料金は料金表の問題でも欠けるため、性能の可否とは分ける。
# コールドスタートは件数の基準が未決定のため、指標ごとの状態だけで示す。
PERFORMANCE_COLUMNS = ("duration", "latency", "memory")
# run全体の注意点として示す理由の出どころ。コールドスタートの理由はその指標の近くにだけ示す。
ISSUE_COLUMNS = tuple(column for column in COLUMNS if column != "init")


class _Column:
    """一つの指標の状態と、その理由を集める。"""

    def __init__(self) -> None:
        self.status = USABLE
        # 理由と、その理由が示す状態の組。表示では重い理由を先に並べる。
        self._reasons: list[tuple[str, str]] = []
        self.notes: list[str] = []

    @property
    def reasons(self) -> list[str]:
        """状態を決めた重い理由から順に返す（同じ重さなら判定した順）。"""
        ordered = sorted(self._reasons, key=lambda entry: -_RANK[entry[0]])
        return [reason for _status, reason in ordered]

    def lower(self, status: str, reason: str) -> None:
        # 既にある状態より悪い場合だけ状態を下げ、理由は重複させずに残す。
        if _RANK[status] > _RANK[self.status]:
            self.status = status
        if reason not in self.reasons:
            self._reasons.append((status, reason))

    def public(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": self.reasons, "notes": self.notes}


def unlinked_cause(run_dir: Path) -> str:
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


def _counts(labels: dict[str, str], implementations: dict[str, Any], field: str) -> str:
    return "、".join(
        f"{labels.get(name, name)} {values[field]}件" for name, values in implementations.items()
    )


def assess_run(report: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """集計結果から、指標ごとの状態・理由と、run全体の比較可否・注意点を返す。"""
    metadata = report["metadata"]
    implementations = report["implementations"]
    labels = report.get("labels") or {}
    collected = (run_dir / "merged.jsonl").is_file()
    columns = {name: _Column() for name in COLUMNS}

    def lower(names: tuple[str, ...], status: str, reason: str) -> None:
        for name in names:
            columns[name].lower(status, reason)

    # 署名の失敗などで途中で止まった計測は、言語ごとの件数がそろわず比較に使えない。
    if metadata.get("aborted"):
        lower(COLUMNS, UNUSABLE, f"計測が途中で中断されました。{metadata['aborted']}")
    # ドライランは実際のAPIを呼んでいないため、どの数値も性能比較に使えない。
    if metadata.get("dry_run"):
        lower(COLUMNS, UNUSABLE, "ドライランの結果です。実際のAPIは呼び出していません。")
    # 実装が一つしかなければ、比べる相手がいないため差分を計算できない。
    if len(implementations) < 2:
        lower(COLUMNS, UNUSABLE, "計測した言語が一つだけのため、比較できません。")

    all_failed = [
        labels.get(name, name)
        for name, values in implementations.items()
        if not values["success_count"]
    ]
    # 成功が一件もない言語があれば、その言語の性能は測れておらず比較自体が成り立たない。
    if all_failed:
        lower(
            COLUMNS,
            UNUSABLE,
            f"{'・'.join(all_failed)} はすべての要求が失敗しました。"
            "失敗の速さは性能として比べられません。",
        )
    failures = [
        {
            "implementation": name,
            "label": labels.get(name, name),
            "error_count": values["error_count"],
            "sample_count": values["sample_count"],
        }
        for name, values in implementations.items()
        if values["error_count"]
    ]
    failure_reason = None
    # 失敗が1件でもある言語があれば、成功分の統計は示すが正式な比較の結論には使わない。
    # 件数と内訳は failures として画面とレポートの先頭で一度だけ示すため、指標ごとの理由は
    # 短くし、run全体の注意点には重ねて載せない。
    if failures and not all_failed:
        failed_labels = "・".join(failure["label"] for failure in failures)
        failure_reason = f"{failed_labels} に失敗した要求があるため、成功した要求だけの参考値です。"
        lower(COLUMNS, REFERENCE, failure_reason)
    unchecked = [
        labels.get(name, name)
        for name, values in implementations.items()
        if not values["response_checked"]
    ]
    # 応答の本文を確かめる前の版で計測した結果は、成功とされた要求が本当に成功したか分からない。
    if unchecked:
        lower(
            COLUMNS,
            REFERENCE,
            "応答の内容を確かめる前の版で計測したため、成功とされた要求が正しく処理されたか"
            "確認できていません。数値は参考値です。",
        )

    verification = metadata.get("verification") or {}
    # 計測前にAWS上の対象・実設定・版を確かめた計測だけを、正式な比較の根拠にする。
    # ローカルAPIの確認や、確認を記録する前の版の計測は参考値として扱う。
    if verification.get("mode") != "aws" and not metadata.get("dry_run"):
        lower(
            COLUMNS,
            REFERENCE,
            "計測前に AWS 上の計測先・実設定・版を確かめた記録がありません"
            "（ローカルでの確認、または確認を記録する前の版の計測です）。数値は参考値です。",
        )

    # アーキテクチャかメモリが記録されていなければ、条件の再現とコスト計算ができない。
    if metadata.get("architecture") is None or metadata.get("memory_mb") is None:
        reason = "アーキテクチャまたはメモリ設定が記録されていません。コストは比較できません。"
        lower(COST_COLUMNS, UNUSABLE, reason)
        lower(tuple(name for name in COLUMNS if name not in COST_COLUMNS), REFERENCE, reason)
    # 料金表を適用できない場合は、理由を料金の指標にだけ示す（性能の指標は比べられる）。
    if report.get("pricing_note"):
        lower(COST_COLUMNS, UNUSABLE, f"料金を計算できません: {report['pricing_note']}")

    # ログ未収集なら、Lambda内部の実行時間・メモリ・コストはまだ存在しない。
    if not collected:
        lower(
            LOG_COLUMNS,
            UNUSABLE,
            "Lambdaのログを収集していません。応答時間以外の指標とコストは表示できません。",
        )
    else:
        unclassified = sum(values["unclassified_count"] for values in implementations.values())
        # 収集後もログと結合できない成功が残れば、Lambda内部の指標は一部欠けている。
        if unclassified:
            total = sum(values["success_count"] for values in implementations.values())
            lower(
                LOG_COLUMNS,
                REFERENCE,
                f"成功した{total}件中{unclassified}件の計測で、Lambda側の記録（実行時間・メモリ・料金）が"
                f"得られていません。{unlinked_cause(run_dir)}",
            )

    _assess_cold(columns["init"], implementations, labels, collected=collected)
    # 料金は計測中に偶発的に起きたコールドスタートも含めた平均のため、その件数を併記する。
    if collected and any(values["cold_count"] for values in implementations.values()):
        note = (
            "料金は、計測中に偶然起きたコールドスタート"
            f"（{_counts(labels, implementations, 'cold_count')}）を含む平均です。"
            "コールドスタートの頻度は計測条件として管理していません。"
        )
        for name in COST_COLUMNS:
            columns[name].notes.append(note)

    issues: list[str] = []
    for name in ISSUE_COLUMNS:
        for reason in columns[name].reasons:
            # 同じ理由が複数の指標に付いていても、run全体の注意点としては一度だけ示す。
            if reason not in issues and reason != failure_reason:
                issues.append(reason)
    return {
        "columns": {name: column.public() for name, column in columns.items()},
        "comparable": all(columns[name].status == USABLE for name in PERFORMANCE_COLUMNS),
        "issues": issues,
        "failures": failures,
    }


def _assess_cold(
    column: _Column, implementations: dict[str, Any], labels: dict[str, str], *, collected: bool
) -> None:
    """コールドスタートの指標を、観測件数と正式な比較の基準の有無から判定する。"""
    # ログがなければ起動の種類を判別できないため、収集の問題として別に扱われている。
    if not collected:
        return
    missing = [
        labels.get(name, name)
        for name, values in implementations.items()
        if not values["cold_count"]
    ]
    # 観測がない言語があれば、その言語の初期化時間がなく比べられない。
    if missing:
        column.lower(
            UNUSABLE, f"{'・'.join(missing)} はこの計測でコールドスタートを観測していません。"
        )
        return
    # 件数があっても、正式な比較に必要な件数と集め方が決まっていないため結論には使わない。
    column.lower(
        REFERENCE,
        f"コールドスタートの観測は {_counts(labels, implementations, 'cold_count')} です。"
        "通常の計測で偶然起きたものだけで、正式に比べるための件数と集め方は決まっていないため参考値です。",
    )
