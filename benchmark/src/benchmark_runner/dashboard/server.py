"""計測結果の閲覧・計測開始の画面と、その画面が使うJSON APIを手元のマシンだけに配信する。"""

from __future__ import annotations

import json
import logging
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from benchmark_runner.dashboard.catalog import (
    RunNotFound,
    latest_costs_by_workload,
    list_runs,
    load_run,
)
from benchmark_runner.dashboard.jobs import (
    LIMITS,
    JobConflict,
    JobManager,
    LaunchError,
    load_workloads,
)

logger = logging.getLogger(__name__)

# AWS認証情報を使う操作を持つため、外部から到達できないループバックにだけ待ち受ける。
LISTEN_HOST = "127.0.0.1"
# DNSリバインディング対策として、この名前以外のHostヘッダーを持つ要求は拒否する。
ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")
# 計測開始の要求本文として受け付ける上限。正常な入力は数百バイトに収まる。
MAX_BODY_BYTES = 4096

STATIC_DIR = Path(__file__).parent / "static"
# 配信するファイルを明示し、任意パスの読み出しを受け付けない。
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
RUNS_PREFIX = "/api/runs/"


class RequestRejected(Exception):
    """画面以外からの要求や不正な本文を拒否するときに使う。"""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class DashboardHandler(BaseHTTPRequestHandler):
    """静的ファイル、計測結果API、計測実行APIへの要求を振り分ける。"""

    def __init__(
        self,
        *args: Any,
        results_dir: Path,
        pricing_path: Path,
        jobs: JobManager,
        **kwargs: Any,
    ) -> None:
        self.results_dir = results_dir
        self.pricing_path = pricing_path
        self.jobs = jobs
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - http.serverが要求するメソッド名
        self._dispatch(self._get)

    def do_POST(self) -> None:  # noqa: N802 - http.serverが要求するメソッド名
        self._dispatch(self._post)

    def _dispatch(self, handler: Any) -> None:
        path = urlsplit(self.path).path
        try:
            self._check_host()
            handler(path)
        except RequestRejected as exc:
            self._send_error(exc.status, str(exc))
        except RunNotFound:
            self._send_error(HTTPStatus.NOT_FOUND, "指定された計測結果が見つかりません。")
        # 別の計測が実行中なら、状態の衝突として409で返し画面で待機を促す。
        except JobConflict as exc:
            self._send_error(HTTPStatus.CONFLICT, str(exc))
        # 入力値や設定の不足は、利用者が直せる誤りとして400で返す。
        except LaunchError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        # 結果ファイルの破損などは画面で再試行・確認できるよう、原因を添えて500で返す。
        except (OSError, ValueError, KeyError) as exc:
            logger.exception("要求の処理に失敗しました: method=%s path=%s", self.command, path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"処理に失敗しました: {exc}")

    def _get(self, path: str) -> None:
        # 画面本体とその資産は固定の対応表からだけ返す。
        if path in STATIC_FILES:
            self._send_static(*STATIC_FILES[path])
        # 一覧APIは全runの概要を返す。
        elif path == "/api/runs":
            runs = list_runs(results_dir=self.results_dir, pricing_path=self.pricing_path)
            self._send_json({"runs": runs})
        # 詳細APIはパス末尾のrun IDに対応する比較結果を返す。
        elif path.startswith(RUNS_PREFIX):
            run_id = unquote(path.removeprefix(RUNS_PREFIX))
            self._send_json(
                load_run(run_id, results_dir=self.results_dir, pricing_path=self.pricing_path)
            )
        # 計測開始フォームに必要な設定状況、選択肢、料金の目安、実行中の計測を返す。
        elif path == "/api/launch":
            self._send_json(self._launch_options())
        elif path == "/api/jobs/current":
            self._send_json({"job": self.jobs.current()})
        else:
            raise RequestRejected(HTTPStatus.NOT_FOUND, "ページが見つかりません。")

    def _post(self, path: str) -> None:
        self._check_same_origin()
        # 計測開始は入力を検証してから非同期に実行し、開始時点の状態を返す。
        if path == "/api/jobs":
            self._send_json({"job": self.jobs.start(self._read_json())}, HTTPStatus.ACCEPTED)
        # 中止は実行中の計測だけを対象にし、何もなければ現在の状態を返す。
        elif path == "/api/jobs/current/cancel":
            self._send_json({"job": self.jobs.cancel()})
        else:
            raise RequestRejected(HTTPStatus.NOT_FOUND, "ページが見つかりません。")

    def _launch_options(self) -> dict[str, Any]:
        costs = latest_costs_by_workload(
            results_dir=self.results_dir, pricing_path=self.pricing_path
        )
        workloads = [
            {**workload, "cost_basis": costs.get(workload["name"])}
            for workload in load_workloads(self.jobs.workload_file)
        ]
        return {
            "settings": self.jobs.settings.public(),
            "workloads": workloads,
            "limits": {name: {"min": low, "max": high} for name, (low, high) in LIMITS.items()},
            "log_wait_seconds": self.jobs.log_wait_seconds,
            "job": self.jobs.current(),
        }

    def _check_host(self) -> None:
        """ループバック以外の名前で届いた要求（DNSリバインディング）を拒否する。"""
        hostname = urlsplit(f"//{self.headers.get('Host', '')}").hostname
        if hostname not in ALLOWED_HOSTNAMES:
            raise RequestRejected(
                HTTPStatus.FORBIDDEN, "この画面へは 127.0.0.1 から開いてください。"
            )

    def _check_same_origin(self) -> None:
        """他サイトのページから送られた状態変更の要求（CSRF）を拒否する。"""
        origin = self.headers.get("Origin")
        # ブラウザは別サイトからのPOSTにOriginを付けるため、画面自身以外の値は拒否する。
        if origin is not None and urlsplit(origin).hostname not in ALLOWED_HOSTNAMES:
            raise RequestRejected(HTTPStatus.FORBIDDEN, "他のサイトからの操作は受け付けません。")
        # JSON以外の本文はフォーム送信など事前確認なしで送れる形式のため、受け付けない。
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise RequestRejected(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON形式で送信してください。")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        # 本文が大きすぎる要求は、読み込む前に拒否する。
        if length > MAX_BODY_BYTES:
            raise RequestRejected(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "入力が大きすぎます。")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise RequestRejected(HTTPStatus.BAD_REQUEST, "入力をJSONとして読めません。") from exc
        # 入力はオブジェクトだけを受け付け、配列や数値は形式誤りとする。
        if not isinstance(payload, dict):
            raise RequestRejected(HTTPStatus.BAD_REQUEST, "入力の形式が正しくありません。")
        return payload

    def _send_static(self, name: str, content_type: str) -> None:
        self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), content_type)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 計測後に再読み込みしたとき、古い結果が表示されないようキャッシュさせない。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 親クラスの引数名
        logger.info("%s %s", self.address_string(), format % args)


def create_server(
    *,
    port: int,
    results_dir: Path,
    pricing_path: Path,
    jobs: JobManager,
) -> ThreadingHTTPServer:
    """結果ディレクトリ、料金表、計測実行を束ねたハンドラーで、ループバック専用のサーバーを作る。"""
    handler = partial(
        DashboardHandler, results_dir=results_dir, pricing_path=pricing_path, jobs=jobs
    )
    return ThreadingHTTPServer((LISTEN_HOST, port), handler)
