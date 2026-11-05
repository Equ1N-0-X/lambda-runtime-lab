// DynamoDBとインメモリ保存先を、共通の項目取得境界として提供する。

import { type AttributeValue, DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";

export interface Item {
  id: string;
  value: number;
  payload: string;
}

/** 保存先の障害の原因の分類。調査用の内部ログにだけ残し、公開する応答には含めない。 */
export type DependencyCategory =
  "permission" | "table" | "throttling" | "network" | "invalid_item" | "unknown";

const PERMISSION_CODES = new Set([
  "AccessDeniedException",
  "UnrecognizedClientException",
  "ExpiredTokenException",
  "InvalidSignatureException",
]);
const THROTTLING_CODES = new Set([
  "ProvisionedThroughputExceededException",
  "ThrottlingException",
  "RequestLimitExceeded",
]);
// 保存値の数値は、DynamoDBが正規化して返す10進整数の表記だけを受け付ける（全言語で共通）。
const INTEGER_PATTERN = /^-?[0-9]+$/;
const ITEM_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const MAX_PAYLOAD_CODE_POINTS = 4096;
const MIN_ITEM_VALUE = -1_000_000_000;
const MAX_ITEM_VALUE = 1_000_000_000;

/** 保存先の通信失敗または保存データ不正を、原因の分類とともに伝える。 */
export class RepositoryError extends Error {
  constructor(
    readonly category: DependencyCategory,
    readonly errorCode: string | null = null,
  ) {
    super(category);
  }
}

/** AWS SDKの例外を、エラーコードから権限・テーブル・流量制限・通信・その他に分類する。 */
export function classifySdkError(error: unknown): RepositoryError {
  const metadata = (error as { $metadata?: { httpStatusCode?: number } })?.$metadata;
  const name = error instanceof Error ? error.name : null;
  // AWSが応答していない（HTTP状態がない）例外は、通信やタイムアウトの問題として扱う。
  if (metadata?.httpStatusCode === undefined) return new RepositoryError("network", null);
  if (name !== null && PERMISSION_CODES.has(name)) return new RepositoryError("permission", name);
  if (name === "ResourceNotFoundException") return new RepositoryError("table", name);
  if (name !== null && THROTTLING_CODES.has(name)) return new RepositoryError("throttling", name);
  return new RepositoryError("unknown", name);
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
    // 強整合読み取りを一回実行する。SDKのエラーは原因を分類して呼び出し元へ伝える。
    let output;
    try {
      output = await this.client.send(
        new GetItemCommand({
          TableName: this.tableName,
          Key: { id: { S: id } },
          ConsistentRead: true,
        }),
      );
    } catch (error) {
      throw classifySdkError(error);
    }
    if (!output.Item) return undefined;
    return storedItem(output.Item);
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

/**
 * DynamoDBの属性を公開するItemへ変換する。公開するItemの境界（IDの形式・値の範囲・
 * payloadの長さ）を満たさない保存値は、成功の応答にせず保存データの不正とする。
 */
export function storedItem(attributes: Record<string, AttributeValue>): Item {
  const itemId = attributes.id?.S;
  const value = attributes.value?.N;
  const payload = attributes.payload?.S;
  // id・value・payloadが契約どおりの型と形式でそろわなければ、保存データの不正として扱う。
  if (
    itemId === undefined ||
    value === undefined ||
    payload === undefined ||
    !ITEM_ID_PATTERN.test(itemId) ||
    !INTEGER_PATTERN.test(value) ||
    [...payload].length > MAX_PAYLOAD_CODE_POINTS
  ) {
    throw new RepositoryError("invalid_item");
  }
  const parsed = Number(value);
  // 値が範囲外（64bitに収まらない表記を含む）なら、契約外の保存値として扱う。
  if (!Number.isSafeInteger(parsed) || parsed < MIN_ITEM_VALUE || parsed > MAX_ITEM_VALUE) {
    throw new RepositoryError("invalid_item");
  }
  return { id: itemId, value: parsed, payload };
}
