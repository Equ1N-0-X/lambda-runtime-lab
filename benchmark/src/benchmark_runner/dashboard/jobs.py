"""画面から依頼された計測を、計測・ログ反映待ち・ログ収集・レポート作成の順に一件ずつ実行する。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from benchmark_runner.dashboard.settings import LaunchSettings

logger = logging.getLogger(__name__)

# CloudWatch Logsへ構造化ログとREPORTが反映されるまでの待ち時間。短すぎると未結合の計測が残る。
LOG_INGESTION_WAIT_SECONDS = 60

# 画面からの誤入力で長時間・高額な計測を始めないよう、指定できる範囲を制限する。
LIMITS = {
    "iterations": (1, 2000),
    "client_concurrency": (1, 32),
    "warmup": (0, 200),
}

# 実行出力の末尾だけを失敗時の手掛かりとして保持する。
OUTPUT_TAIL_LINES = 20

STEPS = (
    ("run", "計測"),
    ("wait", "ログの反映待ち"),
    ("collect", "ログ収集"),
    ("report", "レポート作成"),
)


class LaunchError(ValueError):
    """計測を開始できない理由を、利用者が対処できる文言で通知する。"""


class JobConflict(LaunchError):
    """別の計測が実行中のため、新しい計測を開始できないことを表す。"""


def load_workloads(workload_file: Path) -> list[dict[str, Any]]:
    """ワークロード定義から、画面の選択肢と既定値に必要な項目だけを取り出す。"""
    document = yaml.safe_load(workload_file.read_text(encoding="utf-8")) or {}
    workloads = []
    for name, definition in (document.get("workloads") or {}).items():
        workloads.append(
            {
                "name": name,
                "method": definition.get("method"),
                "path": definition.get("path"),
                "iterations": definition.get("iterations"),
                "client_concurrency": definition.get("client_concurrency"),
                "warmup": definition.get("warmup"),
            }
        )
    return workloads


def _validated_count(values: dict[str, Any], name: str) -> int:
    """画面から受け取った件数が整数で、許可範囲内にあることを確認する。"""
    value = values.get(name)
    minimum, maximum = LIMITS[name]
    # 真偽値や小数、文字列は件数として扱わず、入力エラーとして返す。
    if isinstance(value, bool) or not isinstance(value, int):
        raise LaunchError(f"{name} は整数で指定してください。")
    # 範囲外は誤入力または高額な計測につながるため、開始前に拒否する。
    if not minimum <= value <= maximum:
        raise LaunchError(f"{name} は {minimum} 以上 {maximum} 以下で指定してください。")
    return value


def _validated_targets(values: dict[str, Any], settings: LaunchSettings) -> tuple[str, ...]:
    """計測する実装の指定を検証し、実装一覧の表示順に並べて返す。未指定なら全実装。"""
    available = tuple(implementation.key for implementation in settings.implementations)
    requested = values.get("targets")
    # 指定がなければ、一覧の全実装を計測する。
    if requested is None:
        return available
    # 文字列の配列以外は、画面以外から送られた不正な入力として拒否する。
    if not isinstance(requested, list) or not all(isinstance(key, str) for key in requested):
        raise LaunchError("計測する言語の指定が正しくありません。")
    unknown = [key for key in requested if key not in available]
    # 一覧にない言語は、接続先もロググループもないため計測できない。
    if unknown:
        raise LaunchError(f"計測できない言語が指定されています: {', '.join(unknown)}")
    targets = tuple(key for key in available if key in requested)
    # 比較には二つ以上の言語が必要なため、一つ以下なら開始しない。
    if len(targets) < 2:
        raise LaunchError("比べる言語を二つ以上選んでください。")
    return targets


@dataclass
class Job:
    """一回分の計測依頼と、その進行状態。"""

    run_id: str
    workload: str
    # 計測する実装のキー（実装一覧の表示順）。
    targets: tuple[str, ...]
    iterations: int
    client_concurrency: int
    warmup: int
    # 開始時点で確定した接続先と条件。実行中に設定が変わっても、この計測には影響させない。
    settings: LaunchSettings
    state: str = "running"
    step: str = "run"
    message: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    output: deque[str] = field(default_factory=lambda: deque(maxlen=OUTPUT_TAIL_LINES))

    def public(self) -> dict[str, Any]:
        """画面表示用の状態を返す。"""
        end = self.finished_at or time.time()
        return {
            "run_id": self.run_id,
            "workload": self.workload,
            "targets": list(self.targets),
            "iterations": self.iterations,
            "client_concurrency": self.client_concurrency,
            "warmup": self.warmup,
            "total_requests": len(self.targets) * (self.iterations + self.warmup),
            "state": self.state,
            "step": self.step,
            "steps": [{"key": key, "label": label} for key, label in STEPS],
            "message": self.message,
            "elapsed_seconds": round(end - self.started_at),
            "output": list(self.output),
        }


class JobManager:
    """計測を同時に一件だけ実行し、その状態を画面へ提供する。

    各言語の実装は同じDynamoDBを使うため、計測が重なると互いの結果を歪める。
    そのため実行中は新しい計測を受け付けない。
    """

    def __init__(
        self,
        *,
        settings_provider: Callable[[], LaunchSettings],
        workload_file: Path,
        results_dir: Path,
        pricing_path: Path,
        log_wait_seconds: float = LOG_INGESTION_WAIT_SECONDS,
        command_prefix: list[str] | None = None,
    ) -> None:
        # 設定は計測のたびに取得元から読み直し、デプロイ後も画面を起動し直さずに使えるようにする。
        self.settings_provider = settings_provider
        self.workload_file = workload_file
        self.results_dir = results_dir
        self.pricing_path = pricing_path
        self.log_wait_seconds = log_wait_seconds
        # テストでは実際のCLIの代わりに別コマンドを差し込めるようにする。
        self.command_prefix = command_prefix or [sys.executable, "-m", "benchmark_runner"]
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._job: Job | None = None
        self._process: subprocess.Popen[str] | None = None

    def resolve_settings(self) -> LaunchSettings:
        return self.settings_provider()

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            return self._job.public() if self._job else None

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        """入力を検証して計測を開始し、開始時点の状態を返す。"""
        settings = self.settings_provider()
        missing = settings.missing()
        # 接続先や比較条件が欠けたまま計測すると比較に使えないrunになるため、開始しない。
        if missing:
            raise LaunchError("計測に必要な設定がそろっていません: " + " ".join(missing))
        names = {workload["name"] for workload in load_workloads(self.workload_file)}
        workload = request.get("workload")
        # 定義にないワークロード名は、CLIへ渡す前に入力エラーとして返す。
        if workload not in names:
            raise LaunchError("ワークロードを一覧から選んでください。")
        counts = {name: _validated_count(request, name) for name in LIMITS}
        targets = _validated_targets(request, settings)

        with self._lock:
            # 実行中の計測があれば、DynamoDBとLambdaへの負荷が重ならないよう拒否する。
            if self._job and self._job.state == "running":
                raise JobConflict("別の計測が実行中です。終わるまでお待ちください。")
            self._cancel.clear()
            self._job = Job(
                run_id=str(uuid.uuid4()),
                workload=workload,
                targets=targets,
                settings=settings,
                **counts,
            )
            job = self._job
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job.public()

    def cancel(self) -> dict[str, Any] | None:
        """実行中の計測を中止する。実行中のコマンドは終了させる。"""
        with self._lock:
            # 実行中の計測がなければ、中止する対象がないため現在の状態だけを返す。
            if not self._job or self._job.state != "running":
                return self._job.public() if self._job else None
            self._cancel.set()
            if self._process and self._process.poll() is None:
                self._process.terminate()
            return self._job.public()

    def _commands(self, job: Job) -> dict[str, list[str]]:
        settings = job.settings
        results = ["--results-dir", str(self.results_dir)]
        return {
            "run": [
                "run",
                "--target",
                ",".join(job.targets),
                "--workload",
                job.workload,
                "--workload-file",
                str(self.workload_file),
                "--run-id",
                job.run_id,
                "--region",
                settings.region,
                "--architecture",
                str(settings.architecture),
                "--memory-mb",
                str(settings.memory_mb),
                "--iterations",
                str(job.iterations),
                "--concurrency",
                str(job.client_concurrency),
                "--warmup",
                str(job.warmup),
                *results,
            ],
            "collect": [
                "collect",
                "--run-id",
                job.run_id,
                "--region",
                settings.region,
                *[
                    argument
                    for key in job.targets
                    for argument in ("--log-group", f"{key}={settings.log_groups[key]}")
                ],
                *results,
            ],
            "report": [
                "report",
                "--run-id",
                job.run_id,
                "--pricing",
                str(self.pricing_path),
                *results,
            ],
        }

    def _execute(self, job: Job) -> None:
        commands = self._commands(job)
        try:
            for step, _label in STEPS:
                self._set(job, step=step)
                # ログ反映待ちは中止要求で即座に抜けられるよう、イベント待ちで実装する。
                if step == "wait":
                    if self._cancel.wait(self.log_wait_seconds):
                        break
                    continue
                self._run_command(job, commands[step])
                # 計測コマンドの終了直後に中止要求があれば、後続の工程へ進まない。
                if self._cancel.is_set():
                    break
        except LaunchError as exc:
            self._finish(job, "failed", str(exc))
            return
        # 中止要求で抜けた場合は、途中までの結果を残したまま中止として終える。
        if self._cancel.is_set():
            self._finish(job, "cancelled", "計測を中止しました。途中までの結果は比較に使えません。")
            return
        self._finish(job, "succeeded", "計測とレポート作成が完了しました。")

    def _run_command(self, job: Job, arguments: list[str]) -> None:
        """CLIを子プロセスで実行し、出力の末尾を保持する。失敗時はLaunchErrorにする。"""
        command = [*self.command_prefix, *arguments]
        logger.info("計測工程を開始します: run_id=%s step=%s", job.run_id, job.step)
        env = {
            **os.environ,
            **{
                f"{key.upper()}_API_ENDPOINT": job.settings.endpoints.get(key) or ""
                for key in job.targets
            },
        }
        with self._lock:
            # 起動直前に中止されていれば、子プロセスを作らずに終える。
            if self._cancel.is_set():
                return
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            process = self._process
        assert process.stdout is not None
        for line in process.stdout:
            self._append_output(job, line.rstrip())
        returncode = process.wait()
        with self._lock:
            self._process = None
        # 中止による終了は失敗ではないため、終了コードを確認せずに戻る。
        if self._cancel.is_set():
            return
        # 終了コードが0以外なら、出力末尾を手掛かりとして残し工程失敗として扱う。
        if returncode != 0:
            label = dict(STEPS)[job.step]
            message = f"{label}に失敗しました（終了コード {returncode}）。出力を確認してください。"
            # 計測後の工程の失敗なら測定値は保存済みのため、計測し直さずに再開できる手順を示す。
            if job.step in ("collect", "report"):
                message += (
                    f"計測データは保存済みです。原因を解消したら RUN_ID={job.run_id} make collect"
                    f" と RUN_ID={job.run_id} make report で続きを実行できます。"
                )
            raise LaunchError(message)

    def _append_output(self, job: Job, line: str) -> None:
        # 接続先URLは公開時やスクリーンショットで漏れないよう、画面へ渡す前に伏せる。
        for secret in job.settings.secrets():
            line = line.replace(secret, "<API URL>")
        with self._lock:
            job.output.append(line)

    def _set(self, job: Job, *, step: str) -> None:
        with self._lock:
            job.step = step

    def _finish(self, job: Job, state: str, message: str) -> None:
        with self._lock:
            job.state = state
            job.message = message
            job.finished_at = time.time()
        logger.info("計測を終了しました: run_id=%s state=%s", job.run_id, state)
