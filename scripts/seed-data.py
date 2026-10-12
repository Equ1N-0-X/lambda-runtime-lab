#!/usr/bin/env python3
"""同じseedから決定的な比較用項目を生成し、JSONLまたはDynamoDBへ出力する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


# 契約上payloadは文字列であり、集約APIはそのバイト列を直接ハッシュ化する。チェックサムと
# 転送量を毎回同じにするため、全payloadを固定長にする。
PAYLOAD_LENGTH = 256


def generate_items(seed: str, count: int = 100) -> list[dict[str, Any]]:
    """seedと連番IDから、再実行しても同じ値・固定長payloadになる項目を生成する。"""

    # IDを三桁で表現できる1〜999件だけを許可し、範囲外なら生成前に拒否する。
    if count < 1 or count > 999:
        raise ValueError("countは1以上999以下で指定してください")
    items = []
    for index in range(1, count + 1):
        item_id = f"item-{index:03d}"
        digest = hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()
        prefix = f"{item_id}:group-{(index - 1) % 10:02d}:"
        filler = digest * (PAYLOAD_LENGTH // len(digest) + 1)
        items.append(
            {
                "id": item_id,
                "value": int(digest[:8], 16) % 10_000,
                "payload": (prefix + filler)[:PAYLOAD_LENGTH],
            }
        )
    return items


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    """親ディレクトリを作り、一項目一行の再利用可能なJSONLとして保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in items:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    """引数を検証し、ドライラン出力またはDynamoDBへの一括投入を行う。"""
    parser = argparse.ArgumentParser(description="決定的なベンチマーク項目を作成します")
    parser.add_argument("--table", help="DynamoDB table name")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--seed", default="benchmark-seed")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    items = generate_items(args.seed, args.count)
    # 出力先が指定された場合は、AWS操作の有無に関係なく生成結果をJSONLへ保存する。
    if args.output:
        _write_jsonl(args.output, items)
    # ドライランではDynamoDBへ書き込まず、出力ファイルもない場合だけ標準出力へ全項目を表示する。
    if args.dry_run:
        # JSONL出力先がない場合だけ標準出力へ表示し、同じ大量データの二重出力を避ける。
        if not args.output:
            for item in items:
                print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return 0
    # 実行モードでテーブル名がなければ書き込み先が不明なため、AWS SDK読込前に引数エラーにする。
    if not args.table:
        parser.error("--dry-runを使わない場合は--tableの指定が必要です")

    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    with table.batch_writer(overwrite_by_pkeys=["id"]) as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"{args.table}へ{len(items)}件の項目を投入しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
