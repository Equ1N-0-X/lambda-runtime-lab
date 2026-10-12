"""外部I/Oを含まない、変換・計算・集約の決定的なドメイン処理を提供する。"""

from hashlib import sha256

from app.models import AggregateResponse, ComputeResponse, Item, TransformRecord, TransformResponse


def transform_records(records: list[TransformRecord]) -> TransformResponse:
    """各レコードを正規化し、ID昇順のレスポンスを作る。"""
    transformed = [
        TransformRecord(
            id=record.id,
            # 契約では一般的な空白ではなく U+0020 SPACE だけを除去して小文字化する。
            name=record.name.strip(" ").lower(),
            # 同じタグは一件にまとめ、順序の違いでレスポンスが変わらないよう昇順に並べる。
            tags=sorted(set(record.tags)),
            value=record.value,
        )
        for record in records
    ]

    # 入力順に関係なく同じ応答を返すため、正規化後のレコードをIDの昇順で統一する。
    transformed.sort(key=lambda record: record.id)

    return TransformResponse(records=transformed, count=len(transformed))


def compute_checksum(items: int, iterations: int, seed: str) -> ComputeResponse:
    """項目ごとの反復ハッシュを結合し、計測用のチェックサムを計算する。"""
    final_digests: list[bytes] = []
    for index in range(items):
        digest = f"{seed}:{index}".encode()
        for _ in range(iterations):
            digest = sha256(digest).digest()
        final_digests.append(digest)

    checksum = sha256(b"".join(final_digests)).hexdigest()
    return ComputeResponse(processed=items, iterations=iterations, checksum=checksum)


def aggregate_items(items: list[Item]) -> AggregateResponse:
    """項目をID順に集約し、合計値とpayload連結値のチェックサムを返す。"""
    ordered_items = sorted(items, key=lambda item: item.id)
    payload = "".join(item.payload for item in ordered_items).encode()
    return AggregateResponse(
        count=len(ordered_items),
        total_value=sum(item.value for item in ordered_items),
        checksum=sha256(payload).hexdigest(),
    )
