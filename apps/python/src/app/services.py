"""項目取得と集約に必要な、リポジトリ呼び出し順序を管理するサービス層。"""

import asyncio

from app.domain import aggregate_items
from app.errors import not_found
from app.models import AggregateResponse, Item
from app.repositories import ItemRepository


class ItemService:
    """API層が保存先の実装詳細を知らずに項目処理を呼び出せるようにする。"""

    def __init__(self, repository: ItemRepository) -> None:
        self._repository = repository

    async def get_item(self, item_id: str) -> Item:
        """IDで1件を取得し、存在しない場合はAPI契約の404エラーへ変換する。"""
        item = await self._repository.get_item(item_id)

        # 保存先から項目が返らなかった場合は、空の成功応答にせず、指定IDが未登録という404にする。
        if item is None:
            raise not_found(item_id)

        return item

    async def aggregate(self, ids: list[str], profile: str) -> AggregateResponse:
        """全IDを一度ずつ読み、未登録を検査してから決定的な集約結果を返す。"""

        # 両方式でIDごとの外部I/O回数を同じ一回に固定し、言語・実行方式間の計測条件を揃える。
        # profile が sequential の場合はID順に一件ずつ読み、concurrent の場合は全件を並行して読む。
        if profile == "sequential":
            items = [await self._repository.get_item(item_id) for item_id in ids]
        else:
            items = list(
                await asyncio.gather(*(self._repository.get_item(item_id) for item_id in ids))
            )

        missing_ids = sorted(
            item_id for item_id, item in zip(ids, items, strict=True) if item is None
        )

        # 未登録IDが一件以上ある場合は、並行実行の完了順に左右されないよう、
        # 辞書順で最初のIDを404にする。
        if missing_ids:
            raise not_found(missing_ids[0])

        return aggregate_items([item for item in items if item is not None])
