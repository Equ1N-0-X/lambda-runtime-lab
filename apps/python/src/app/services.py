import asyncio

from app.domain import aggregate_items
from app.errors import not_found
from app.models import AggregateResponse, Item
from app.repositories import ItemRepository


class ItemService:
    def __init__(self, repository: ItemRepository) -> None:
        self._repository = repository

    async def get_item(self, item_id: str) -> Item:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise not_found(item_id)
        return item

    async def aggregate(self, ids: list[str], profile: str) -> AggregateResponse:
        # Both profiles perform exactly one read per requested ID so that the
        # external I/O count stays identical across profiles and languages.
        if profile == "sequential":
            items = [await self._repository.get_item(item_id) for item_id in ids]
        else:
            items = list(
                await asyncio.gather(*(self._repository.get_item(item_id) for item_id in ids))
            )

        missing_ids = sorted(
            item_id for item_id, item in zip(ids, items, strict=True) if item is None
        )
        if missing_ids:
            raise not_found(missing_ids[0])
        return aggregate_items([item for item in items if item is not None])
