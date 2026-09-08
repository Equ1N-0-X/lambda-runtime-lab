from __future__ import annotations

import argparse
from pathlib import Path

import boto3

from contract_harness import load_fixture_items

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed canonical Contract Test fixtures")
    parser.add_argument("--table", required=True)
    parser.add_argument("--endpoint-url")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    table = boto3.resource(
        "dynamodb",
        endpoint_url=args.endpoint_url,
        region_name=args.region,
    ).Table(args.table)
    items = load_fixture_items(REPOSITORY_ROOT / "spec" / "testcases")
    with table.batch_writer(overwrite_by_pkeys=["id"]) as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"seeded {len(items)} canonical fixture items into {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
