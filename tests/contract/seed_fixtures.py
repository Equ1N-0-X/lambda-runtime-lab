"""正規テストケースのfixtureを、契約テスト用DynamoDBへ一括投入する。"""

from __future__ import annotations

import argparse
from pathlib import Path

import boto3

from contract_harness import load_fixture_items

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    """接続先を引数から作り、競合検証済みfixtureをID上書き方式で投入する。"""
    parser = argparse.ArgumentParser(description="正規契約テストfixtureを投入します")
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
    print(f"{args.table}へ正規fixtureを{len(items)}件投入しました")
    return 0


# コマンドとして実行された場合だけDynamoDB投入を開始し、import時には副作用を起こさない。
if __name__ == "__main__":
    raise SystemExit(main())
