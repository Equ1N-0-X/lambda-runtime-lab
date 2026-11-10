"""CloudWatchのLambda実行ログを取得し、HTTP測定レコードへ実行時間・メモリ情報を結合する。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_PATTERN = re.compile(
    r"REPORT RequestId:\s*(?P<request_id>\S+)"
    r"\s+Duration:\s*(?P<duration>[\d.]+)\s*ms"
    r"\s+Billed Duration:\s*(?P<billed>[\d.]+)\s*ms"
    r"\s+Memory Size:\s*(?P<memory>\d+)\s*MB"
    r"\s+Max Memory Used:\s*(?P<max_memory>\d+)\s*MB"
    r"(?:\s+Init Duration:\s*(?P<init>[\d.]+)\s*ms)?"
)


def _parse_json_report(message: str) -> dict[str, Any] | None:
    """ログ形式JSONのLambdaが出すplatform.reportを、テキスト形式REPORTと同じ項目へ変換する。"""
    opening = message.find("{")
    # JSON開始文字がない行は、テキスト形式のREPORTか別種のログとして扱う。
    if opening < 0:
        return None
    try:
        value = json.loads(message[opening:])
    # JSONとして読めない行はplatform.reportではないため、別種のログとして扱う。
    except json.JSONDecodeError:
        return None
    # typeがplatform.reportでなければ、アプリログや開始ログなので実行レポートとして扱わない。
    if not isinstance(value, dict) or value.get("type") != "platform.report":
        return None

    record = value.get("record")
    metrics = record.get("metrics") if isinstance(record, dict) else None
    # リクエストIDか指標がなければ計測と結合できないため、壊れたレポートとして読み飛ばす。
    if not isinstance(metrics, dict) or not isinstance(record.get("requestId"), str):
        return None
    try:
        init = metrics.get("initDurationMs")
        return {
            "lambda_request_id": record["requestId"],
            "duration_ms": float(metrics["durationMs"]),
            "billed_duration_ms": float(metrics["billedDurationMs"]),
            "memory_size_mb": int(metrics["memorySizeMB"]),
            "max_memory_used_mb": int(metrics["maxMemoryUsedMB"]),
            "init_duration_ms": float(init) if init is not None else None,
        }
    # 必須指標の欠落や非数値は、誤った料金やメモリを出さないためレポートなしとして扱う。
    except (KeyError, TypeError, ValueError):
        return None


def parse_report(message: str) -> dict[str, Any] | None:
    """LambdaのREPORT（テキスト・JSON形式）を認識し、課金時間・メモリ・Cold Start情報を数値化する。

    Lambdaのログ形式がJSONの場合、REPORT行はテキストではなくtype=platform.reportのJSONで出力される。
    """
    json_report = _parse_json_report(message)
    # JSON形式のレポートとして読めた場合は、テキスト形式の解析を行わずそのまま返す。
    if json_report:
        return json_report

    match = REPORT_PATTERN.search(message)
    # REPORT形式に一致しないログ行は、別種のログとして扱えるようNoneを返す。
    if not match:
        return None
    values = match.groupdict()
    return {
        "lambda_request_id": values["request_id"],
        "duration_ms": float(values["duration"]),
        "billed_duration_ms": float(values["billed"]),
        "memory_size_mb": int(values["memory"]),
        "max_memory_used_mb": int(values["max_memory"]),
        "init_duration_ms": float(values["init"]) if values["init"] else None,
    }


def parse_structured_log(message: str) -> dict[str, Any] | None:
    """ログ行内のJSONを解析し、測定相関に必要な公開フィールドだけを取り出す。"""
    opening = message.find("{")
    # JSON開始文字がないログ行はアプリケーション構造化ログではないため読み飛ばす。
    if opening < 0:
        return None
    try:
        value = json.loads(message[opening:])
    # JSONとして解析できないログ行はLambdaシステムログなどの可能性があるため読み飛ばす。
    except json.JSONDecodeError:
        return None
    # JSONルートがオブジェクトでなければ相関フィールドを取得できないため読み飛ばす。
    if not isinstance(value, dict):
        return None
    request_id_value = (
        value.get("lambda_request_id") or value.get("request_id") or value.get("aws_request_id")
    )
    run_id = value.get("benchmark_run_id") or value.get("run_id")
    request_id = (
        request_id_value if isinstance(request_id_value, str) and request_id_value else None
    )
    request_index_value = value.get("benchmark_request_index")
    try:
        # 言語ごとのJSON表現差（数値か文字列か）を吸収し、測定は正数、ウォームアップは負数にする。
        request_index = int(request_index_value)
    except (TypeError, ValueError):
        request_index = None
    if request_index == 0:
        request_index = None

    # 実行IDと、Lambda IDまたは計測番号の一方がないログは、誤結合を防ぐため採用しない。
    if not isinstance(run_id, str) or (request_id is None and request_index is None):
        return None
    return {
        "lambda_request_id": request_id,
        "benchmark_request_index": request_index,
        "benchmark_run_id": run_id,
        "implementation": value.get("implementation"),
        "route": value.get("route"),
        "profile": value.get("profile"),
        "status_code": value.get("status_code"),
    }


def correlate_samples(
    samples: list[dict[str, Any]],
    messages: list[str],
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lambda IDまたは計測番号をキーに、API測定・REPORT・アプリログを結合する。"""
    reports: dict[str, dict[str, Any]] = {}
    structured_by_request_id: dict[str, dict[str, Any]] = {}
    structured_by_index: dict[int, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for message in messages:
        report = parse_report(message)
        # REPORT行ならリクエストID別に保持し、同じ行を構造化ログとして二重解析しない。
        if report:
            reports[report["lambda_request_id"]] = report
            events.append({"type": "report", **report})
            continue
        app_log = parse_structured_log(message)
        # 構造化ログの実行IDが収集対象と一致する場合だけ、別実行のログ混入を防いで保持する。
        if app_log and app_log["benchmark_run_id"] == run_id:
            if app_log["lambda_request_id"] is not None:
                structured_by_request_id[app_log["lambda_request_id"]] = app_log
            if app_log["benchmark_request_index"] is not None:
                structured_by_index[app_log["benchmark_request_index"]] = app_log
            events.append({"type": "structured", **app_log})

    merged: list[dict[str, Any]] = []
    for sample in samples:
        request_id = sample.get("lambda_request_id")
        request_index = sample.get("request_index")
        app_log = structured_by_request_id.get(request_id, {}) if request_id else {}

        # API Gateway応答にLambda IDがない場合は、ランナーとアプリログが共有する計測番号で補う。
        if not app_log and isinstance(request_index, int):
            app_log = structured_by_index.get(request_index, {})
        resolved_request_id = app_log.get("lambda_request_id") or request_id
        report = reports.get(resolved_request_id, {}) if resolved_request_id else {}
        merged.append(
            {
                **sample,
                "lambda_request_id": resolved_request_id,
                "benchmark_run_id": app_log.get("benchmark_run_id"),
                "duration_ms": report.get("duration_ms"),
                "billed_duration_ms": report.get("billed_duration_ms"),
                "init_duration_ms": report.get("init_duration_ms"),
                "memory_size_mb": report.get("memory_size_mb"),
                "max_memory_used_mb": report.get("max_memory_used_mb"),
                "start_type": (
                    "cold"
                    if report.get("init_duration_ms") is not None
                    else "warm"
                    if report
                    else None
                ),
                "log_matched": bool(app_log),
                "report_matched": bool(report),
            }
        )
    return merged, events


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """空行を許容しつつ、各行がJSONオブジェクトであることを検証して読み込む。"""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            # 空行はレコードとして扱わず読み飛ばす。
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONLが不正です: {path}:{line_number}") from exc

            # JSON値がオブジェクトでなければ測定項目を取得できないため、行番号付きで拒否する。
            if not isinstance(value, dict):
                raise ValueError(
                    f"JSONLレコードはオブジェクトである必要があります: {path}:{line_number}"
                )
            records.append(value)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """収集イベントまたは結合結果を、一レコード一行のJSONLで保存する。"""
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _epoch(iso_value: str, offset_seconds: int) -> int:
    """ISO時刻をCloudWatch検索用epoch秒へ変換し、検索余白を加える。"""
    value = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    # タイムゾーン情報がない値はUTCとして扱い、実行端末のローカル時刻へ依存させない。
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp()) + offset_seconds


# Logs Insightsが一回のクエリで返す結果行の上限。これに達した結果は末尾が欠けている。
INSIGHTS_RESULT_LIMIT = 10_000


class InsightsTruncated(RuntimeError):
    """Logs Insightsの結果が上限に達し、全件を取得できていないことを表す。"""


def _insights_query(run_id: str) -> str:
    """結合に必要な行（実行レポートと、このrunのアプリログ）だけを時系列で取り出すクエリ。

    実行レポートにはrun IDが含まれないため、REPORTは全件を残し、アプリログはrun IDで絞る。
    REPORTだけに絞ると、計測番号からLambdaのリクエストIDを引けなくなり結合できない。
    """
    return (
        "fields @timestamp, @message"
        ' | filter @message like "REPORT RequestId"'
        ' or @message like "platform.report"'
        f' or @message like "{run_id}"'
        " | sort @timestamp asc"
    )


def _insights_messages(
    logs_client: object,
    log_group: str,
    start_time: int,
    end_time: int,
    run_id: str,
) -> list[str]:
    """Logs Insightsを開始し、最大60秒ポーリングして時系列のメッセージを返す。

    結果が上限件数に達した場合は末尾が欠けているため、InsightsTruncatedを送出する。
    """
    response = logs_client.start_query(  # type: ignore[attr-defined]
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString=_insights_query(run_id),
        limit=INSIGHTS_RESULT_LIMIT,
    )
    query_id = response["queryId"]
    for _ in range(120):
        result = logs_client.get_query_results(queryId=query_id)  # type: ignore[attr-defined]
        status = result["status"]
        # 状態がCompleteになった場合だけ結果行から@messageを抽出して返す。
        if status == "Complete":
            rows = result.get("results", [])
            # 上限件数ちょうどの結果は、それ以降の行が返されていない可能性がある。
            if len(rows) >= INSIGHTS_RESULT_LIMIT:
                raise InsightsTruncated(
                    f"Logs Insightsの結果が上限（{INSIGHTS_RESULT_LIMIT}件）に達しました"
                )
            return [
                next(
                    (field["value"] for field in row if field["field"] == "@message"),
                    "",
                )
                for row in rows
            ]
        # 終了系の失敗状態なら再試行せず、状態を示してフォールバック判断へ渡す。
        if status in {"Cancelled", "Failed", "Timeout", "Unknown"}:
            raise RuntimeError(f"CloudWatch Logs Insightsクエリが失敗状態で終了しました: {status}")
        time.sleep(0.5)
    raise TimeoutError("CloudWatch Logs Insightsクエリが制限時間内に完了しませんでした")


def _filter_messages(
    logs_client: object,
    log_group: str,
    start_time: int,
    end_time: int,
) -> list[str]:
    """FilterLogEventsをページングし、対象時間内の全ログメッセージを返す。"""
    messages: list[str] = []
    token: str | None = None
    while True:
        arguments: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_time * 1000,
            "endTime": end_time * 1000,
        }
        # 継続トークンがある場合だけ次ページを要求する。
        if token:
            arguments["nextToken"] = token
        response = logs_client.filter_log_events(**arguments)  # type: ignore[attr-defined]
        messages.extend(event.get("message", "") for event in response.get("events", []))
        next_token = response.get("nextToken")
        # 次トークンがない、または前回と同じ場合は全ページ取得済みとして終了する。
        if not next_token or next_token == token:
            return messages
        token = next_token


def _messages(
    logs_client: object,
    log_group: str,
    *,
    strategy: str,
    start_time: int,
    end_time: int,
    run_id: str,
) -> tuple[list[str], str, str | None]:
    """ロググループのメッセージを取得し、取得方法と、切り替えた理由を返す。"""
    # filter固定時はInsightsを使わず、ページングで全件を取得する。
    if strategy == "filter":
        return _filter_messages(logs_client, log_group, start_time, end_time), "filter", None
    try:
        messages = _insights_messages(logs_client, log_group, start_time, end_time, run_id)
        return messages, "insights", None
    # Insightsが利用不能、または上限で末尾が欠ける場合は、全件をページングで取得し直す。
    # insights固定のときは、欠けた結果で結合しないよう失敗をそのまま返す。
    except Exception as exc:
        if strategy == "insights":
            raise
        reason = str(exc) if isinstance(exc, InsightsTruncated) else type(exc).__name__
        return _filter_messages(logs_client, log_group, start_time, end_time), "filter", reason


def _collect_once(
    *,
    run_id: str,
    samples: list[dict[str, Any]],
    log_groups: dict[str, str],
    logs_client: object,
    strategy: str,
    start_time: int,
    end_time: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """全ロググループを一度取得して結合し、結合結果・イベント・実装別の収集状況を返す。"""
    all_events: list[dict[str, Any]] = []
    all_merged: list[dict[str, Any]] = []
    status: dict[str, Any] = {}
    for implementation in sorted(log_groups):
        messages, source, fallback_reason = _messages(
            logs_client,
            log_groups[implementation],
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
            run_id=run_id,
        )
        selected = [row for row in samples if row["implementation"] == implementation]
        merged, events = correlate_samples(selected, messages, run_id=run_id)
        all_merged.extend(merged)
        all_events.extend(
            {"implementation": implementation, "source": source, **event} for event in events
        )
        # 成功した要求はLambdaが処理しているため、実行レポートが必ずあるはずの要求として数える。
        # 失敗した要求は、API Gatewayで止まりLambdaが動いていない場合もあるため数えない。
        expected = [row for row in merged if row.get("success") is True]
        status[implementation] = {
            "source": source,
            "fallback_reason": fallback_reason,
            "message_count": len(messages),
            "expected_reports": len(expected),
            "matched_reports": sum(1 for row in expected if row["report_matched"]),
        }
    return all_merged, all_events, status


def collect_run(
    *,
    run_id: str,
    results_dir: Path,
    log_groups: dict[str, str],
    logs_client: object,
    strategy: str = "auto",
    dry_run: bool = False,
    wait_seconds: float = 0,
    poll_seconds: float = 10,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """指定実行のログを実装別に収集し、API測定へ結合したJSONLを保存する。

    CloudWatchへの反映には時間がかかるため、成功した要求のすべてに実行レポートが
    結び付くまで、wait_seconds を期限として取得し直す。期限を過ぎても欠けていれば
    status=incomplete として返し、欠けた値は0で埋めずに未結合のまま残す。
    """
    run_dir = results_dir / "raw" / run_id
    metadata_path = run_dir / "metadata.json"
    samples_path = run_dir / "samples.jsonl"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    start_time = _epoch(metadata["started_at"], -300)
    end_time = _epoch(metadata["completed_at"], 300)
    plan = {
        "run_id": run_id,
        "log_groups": log_groups,
        "strategy": strategy,
        "start_time": start_time,
        "end_time": end_time,
        "wait_seconds": wait_seconds,
        "dry_run": dry_run,
    }
    # ドライランではCloudWatchや結果ファイルへアクセスせず、検索条件だけを返す。
    if dry_run:
        return plan

    samples = _read_jsonl(samples_path)
    deadline = clock() + wait_seconds
    attempts = 0
    while True:
        attempts += 1
        merged, events, status = _collect_once(
            run_id=run_id,
            samples=samples,
            log_groups=log_groups,
            logs_client=logs_client,
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
        )
        missing = sum(
            item["expected_reports"] - item["matched_reports"] for item in status.values()
        )
        remaining = deadline - clock()
        # 欠けがなければ完了。期限を過ぎていれば、欠けたまま未完了として終える。
        if not missing or remaining <= 0:
            break
        sleep(min(poll_seconds, remaining))

    implementations = set(log_groups)
    merged.extend(
        {
            **sample,
            "duration_ms": None,
            "billed_duration_ms": None,
            "init_duration_ms": None,
            "memory_size_mb": None,
            "max_memory_used_mb": None,
            "start_type": None,
            "log_matched": False,
            "report_matched": False,
        }
        for sample in samples
        if sample["implementation"] not in implementations
    )
    merged.sort(key=lambda row: (row["implementation"], row["request_index"]))
    collection = {
        "status": "incomplete" if missing else "complete",
        "attempts": attempts,
        "missing_reports": missing,
        "implementations": status,
    }
    _write_jsonl(run_dir / "cloudwatch.jsonl", events)
    _write_jsonl(run_dir / "merged.jsonl", merged)
    (run_dir / "collection.json").write_text(
        json.dumps(collection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**plan, "sample_count": len(merged), "event_count": len(events), **collection}
