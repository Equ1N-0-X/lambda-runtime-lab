"""測定欠損をゼロにせず、Markdown・CSVへ同じ集計結果を出すことを検証する。"""

import csv
import json

from benchmark_runner.report import generate_report


def test_report_generates_markdown_and_csv_with_null_cost(tmp_path):
    run_dir = tmp_path / "raw/run-1"
    run_dir.mkdir(parents=True)
    metadata = {
        "run_id": "run-1",
        "started_at": "2026-09-01T00:00:00+00:00",
        "completed_at": "2026-09-01T00:01:00+00:00",
        "git_revision": None,
        "region": "ap-northeast-1",
        "architecture": "arm64",
        "memory_mb": 256,
        "workload": {
            "name": "health",
            "iterations": 1,
            "client_concurrency": 1,
            "warmup": 1,
        },
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    record = {
        "run_id": "run-1",
        "implementation": "python",
        "request_index": 1,
        "success": True,
        "latency_ms": 10,
        "duration_ms": None,
        "billed_duration_ms": None,
        "init_duration_ms": None,
        "memory_size_mb": None,
        "max_memory_used_mb": None,
        "start_type": None,
    }
    (run_dir / "merged.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    markdown, csv_path = generate_report(
        run_id="run-1",
        results_dir=tmp_path,
        pricing_path=tmp_path / "missing-pricing.yaml",
    )

    content = markdown.read_text(encoding="utf-8")
    assert "Cost: null" in content
    assert "結合できないサンプルはUnclassified" in content
    assert "`Unclassified`が1件でもある場合" in content
    assert "このrunは相関不完全" in content
    assert "`latency_ms`: クライアントから見たAPI全体の応答時間" in content
    assert "AWS請求額そのものではありません" in content
    assert "[ベンチマークレポート利用ガイド]" in content
    with csv_path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["implementation"] == "python"
    assert rows[0]["duration_ms_p50"] == ""
    assert rows[0]["estimated_cost_per_request"] == ""
