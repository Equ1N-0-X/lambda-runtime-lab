"""`python -m benchmark_runner` をCLIのmain関数へ接続する。"""

from benchmark_runner.cli import main

# モジュールがコマンドとして実行された場合だけCLIを起動し、import時には副作用を起こさない。
if __name__ == "__main__":
    raise SystemExit(main())
