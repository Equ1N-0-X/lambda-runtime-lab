"""計測結果の閲覧画面と、その画面が使うJSON APIを手元のマシンだけに配信する。"""

from __future__ import annotations

import json
import logging
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from benchmark_runner.dashboard.catalog import RunNotFound, list_runs, load_run

logger = logging.getLogger(__name__)

# AWS認証情報を使う操作を将来載せるため、外部から到達できないループバックにだけ待ち受ける。
LISTEN_HOST = "127.0.0.1"

STATIC_DIR = Path(__file__).parent / "static"
# 配信するファイルを明示し、任意パスの読み出しを受け付けない。
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
RUNS_PREFIX = "/api/runs/"


class DashboardHandler(BaseHTTPRequestHandler):
    """静的ファイルと計測結果APIへのGETを振り分ける。"""

    def __init__(self, *args: Any, results_dir: Path, pricing_path: Path, **kwargs: Any) -> None:
        self.results_dir = results_dir
        self.pricing_path = pricing_path
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - http.serverが要求するメソッド名
        path = urlsplit(self.path).path
        try:
            # 画面本体とその資産は固定の対応表からだけ返す。
            if path in STATIC_FILES:
                self._send_static(*STATIC_FILES[path])
            # 一覧APIは全runの概要を返す。
            elif path == "/api/runs":
                self._send_json(
                    {
                        "runs": list_runs(
                            results_dir=self.results_dir, pricing_path=self.pricing_path
                        )
                    }
                )
            # 詳細APIはパス末尾のrun IDに対応する比較結果を返す。
            elif path.startswith(RUNS_PREFIX):
                run_id = unquote(path.removeprefix(RUNS_PREFIX))
                self._send_json(
                    load_run(run_id, results_dir=self.results_dir, pricing_path=self.pricing_path)
                )
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "ページが見つかりません。")
        except RunNotFound:
            self._send_error(HTTPStatus.NOT_FOUND, "指定された計測結果が見つかりません。")
        # 結果ファイルの破損などは画面で再試行・確認できるよう、原因を添えて500で返す。
        except (OSError, ValueError, KeyError) as exc:
            logger.exception("計測結果の読み込みに失敗しました: path=%s", path)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"計測結果を読み込めませんでした: {exc}"
            )

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


def create_server(*, port: int, results_dir: Path, pricing_path: Path) -> ThreadingHTTPServer:
    """結果ディレクトリと料金表を束ねたハンドラーで、ループバック専用のサーバーを作る。"""
    handler = partial(DashboardHandler, results_dir=results_dir, pricing_path=pricing_path)
    return ThreadingHTTPServer((LISTEN_HOST, port), handler)
