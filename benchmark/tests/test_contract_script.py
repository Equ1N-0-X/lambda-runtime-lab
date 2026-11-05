"""契約テストの起動確認が、Lambda RIEへ呼び出しを重ねないことを検証する。

RIEは同時に一つの呼び出ししか受け付けず、処理中に次の呼び出しが届くと
ReserveFailed: AlreadyReserved で異常終了する（V-02 で現行のイメージでも再現）。
以前の起動確認は5秒で打ち切って1秒後に呼び出し直していたため、初回が5秒を超えると重なった。
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts/run-contract-test.sh").read_text(encoding="utf-8")


def _wait_functions() -> str:
    start = SCRIPT.index("RUNTIME_WAIT_SECONDS=")
    end = SCRIPT.index("start_dynamodb_if_needed() {")
    health = next(line for line in SCRIPT.splitlines() if line.startswith("HEALTH_EVENT="))
    return f"{health}\n{SCRIPT[start:end]}\nshow_failure_context() {{ :; }}\n"


def test_readiness_waits_for_the_port_then_invokes_only_once(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    # ポートは3回目で開き、呼び出しは時間がかかっても一度で成功する偽のcurl。
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$*" == *invocations* ]]; then echo \'{"statusCode":200}\'; exit 0; fi\n'
        f'count=$(grep -vc invocations "{log}")\n'
        "[[ $count -ge 3 ]]\n"
    )
    (bin_dir / "curl").chmod(0o755)
    (tmp_path / "wait.sh").write_text(_wait_functions())

    result = subprocess.run(
        ["bash", "-c", f"source {tmp_path}/wait.sh; wait_for_runtime java http://127.0.0.1:9"],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CONTRACT_RUNTIME_WAIT_SECONDS": "30"},
        capture_output=True,
        text=True,
    )

    calls = log.read_text().splitlines()
    invocations = [call for call in calls if "invocations" in call]
    assert result.returncode == 0, result.stderr
    # 呼び出しは一度だけで、打ち切りまでの時間は待つ上限の残りにする（5秒で打ち切らない）。
    assert len(invocations) == 1
    max_time = int(invocations[0].split("--max-time ")[1].split()[0])
    assert max_time > 5


def test_stale_local_image_is_rebuilt_before_contract_test():
    # 手元のHEADと別のコミットから作られたイメージは、そのまま検証せずビルドし直す。
    assert 'if [[ "${image_revision}" != "${head_revision}" ]]; then' in SCRIPT
