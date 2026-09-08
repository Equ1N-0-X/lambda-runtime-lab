from hashlib import sha256

from app.models import AggregateResponse, ComputeResponse, Item, TransformRecord, TransformResponse


def transform_records(records: list[TransformRecord]) -> TransformResponse:
    transformed = [
        TransformRecord(
            id=record.id,
            # The contract trims only U+0020 SPACE, not general whitespace.
            name=record.name.strip(" ").lower(),
            tags=sorted(set(record.tags)),
            value=record.value,
        )
        for record in records
    ]
    transformed.sort(key=lambda record: record.id)
    return TransformResponse(records=transformed, count=len(transformed))


def compute_checksum(items: int, iterations: int, seed: str) -> ComputeResponse:
    final_digests: list[bytes] = []
    for index in range(items):
        digest = f"{seed}:{index}".encode()
        for _ in range(iterations):
            digest = sha256(digest).digest()
        final_digests.append(digest)

    checksum = sha256(b"".join(final_digests)).hexdigest()
    return ComputeResponse(processed=items, iterations=iterations, checksum=checksum)


def aggregate_items(items: list[Item]) -> AggregateResponse:
    ordered_items = sorted(items, key=lambda item: item.id)
    payload = "".join(item.payload for item in ordered_items).encode()
    return AggregateResponse(
        count=len(ordered_items),
        total_value=sum(item.value for item in ordered_items),
        checksum=sha256(payload).hexdigest(),
    )
