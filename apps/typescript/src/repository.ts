// DynamoDBとインメモリ保存先を、共通の項目取得境界として提供する。

import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";

export interface Item {
  id: string;
  value: number;
  payload: string;
}

export interface ItemRepository {
  /** IDで一件を取得し、未登録はundefined、保存先障害は例外として返す。 */
  get(id: string): Promise<Item | undefined>;
}

export class DynamoDbItemRepository implements ItemRepository {
  constructor(
    private readonly client: DynamoDBClient,
    private readonly tableName: string,
  ) {}

  async get(id: string): Promise<Item | undefined> {
    // 強整合読み取りを一回実行する。SDKのエラーはそのまま呼び出し元へ伝える。
    const output = await this.client.send(
      new GetItemCommand({
        TableName: this.tableName,
        Key: { id: { S: id } },
        ConsistentRead: true,
      }),
    );
    if (!output.Item) return undefined;

    const itemId = output.Item.id?.S;
    const value = output.Item.value?.N;
    const payload = output.Item.payload?.S;
    // id・value・payloadが契約どおりの型でそろわなければ、保存データの不正として扱う。
    if (itemId === undefined || value === undefined || payload === undefined) {
      throw new Error("DynamoDB項目の形式が不正です");
    }
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed)) throw new Error("DynamoDB項目のvalueが整数ではありません");
    return { id: itemId, value: parsed, payload };
  }
}

export class InMemoryItemRepository implements ItemRepository {
  private readonly items: Map<string, Item>;

  /** テストで外部I/Oなしに同じ取得契約を再現する項目集合を作る。 */
  constructor(items: Item[] = []) {
    this.items = new Map(items.map((item) => [item.id, item]));
  }

  async get(id: string): Promise<Item | undefined> {
    const item = this.items.get(id);
    return item ? { ...item } : undefined;
  }
}
