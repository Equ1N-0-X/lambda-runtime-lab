#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


# The canonical contract types `payload` as a string, and `/aggregate` hashes
# those bytes directly, so payloads are fixed-length strings to keep both the
# checksum and the transferred size deterministic.
PAYLOAD_LENGTH = 256


def generate_items(seed: str, count: int = 100) -> list[dict[str, Any]]:
    if count < 1 or count > 999:
        raise ValueError("count must be between 1 and 999")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in items:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create deterministic benchmark items")
    parser.add_argument("--table", help="DynamoDB table name")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--seed", default="benchmark-seed")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    items = generate_items(args.seed, args.count)
    if args.output:
        _write_jsonl(args.output, items)
    if args.dry_run:
        if not args.output:
            for item in items:
                print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.table:
        parser.error("--table is required unless --dry-run is used")

    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    with table.batch_writer(overwrite_by_pkeys=["id"]) as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"seeded {len(items)} items into {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
