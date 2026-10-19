// 外部I/Oを含まない変換・計算・集約処理を定義する。
//
// 同じ入力に対して他の言語の実装と同じ出力を返すため、並び順、ハッシュ入力、未登録IDの
// 選び方を決定的にする。

import { createHash } from "node:crypto";

import { AppError } from "./errors.js";
import type { Item, ItemRepository } from "./repository.js";
import type { ComputeRequest, TransformRecord, TransformRequest } from "./validation.js";

export interface TransformResponse {
  records: TransformRecord[];
  count: number;
}

export interface ComputeResponse {
  processed: number;
  iterations: number;
  checksum: string;
}

export interface AggregateResponse {
  count: number;
  total_value: number;
  checksum: string;
}

/** UTF-8バイト列の辞書順で比較する。IDとタグはASCIIのため符号単位順と一致する。 */
function byteOrder(left: string, right: string): number {
  if (left < right) return -1;
  return left > right ? 1 : 0;
}

export function transform(request: TransformRequest): TransformResponse {
  const records = request.records.map((record) => ({
    id: record.id,
    // 一般的な空白ではなくU+0020 SPACEだけを取り除き、名前を小文字へ正規化する。
    name: record.name.replace(/^ +| +$/g, "").toLowerCase(),
    // タグの重複を除去し、入力順に依存しない昇順の一覧にする。
    tags: [...new Set(record.tags)].sort(byteOrder),
    value: record.value,
  }));
  // 入力順に関係なく同じレスポンスを返すため、全レコードをID昇順に統一する。
  records.sort((left, right) => byteOrder(left.id, right.id));
  return { records, count: records.length };
}

export function compute(request: ComputeRequest): ComputeResponse {
  // 全項目のダイジェストを最後にまとめてハッシュ化するための集約器を用意する。
  const finalHash = createHash("sha256");
  // 項目数の範囲で、seedと添字から独立した初期値を作り、指定回数だけSHA-256を適用する。
  for (let index = 0; index < request.items; index += 1) {
    let digest = createHash("sha256").update(`${request.seed}:${index}`, "utf8").digest();
    // 二回目以降は、直前の32バイトの生のダイジェストを入力にする。
    for (let round = 1; round < request.iterations; round += 1) {
      digest = createHash("sha256").update(digest).digest();
    }
    finalHash.update(digest);
  }
  return {
    processed: request.items,
    iterations: request.iterations,
    checksum: finalHash.digest("hex"),
  };
}

export async function getItem(repository: ItemRepository, id: string): Promise<Item> {
  const item = await readOrDependencyError(repository, id);
  // 保存先が成功しても項目がない場合は、未登録として404を返す。
  if (!item) throw AppError.notFound(id);
  return item;
}

export async function aggregateSequential(
  repository: ItemRepository,
  ids: string[],
): Promise<AggregateResponse> {
  const found: Item[] = [];
  const missing: string[] = [];
  // ID順に一件ずつ読み、見つかった項目と未登録IDに分ける。
  for (const id of ids) {
    const item = await readOrDependencyError(repository, id);
    if (item) found.push(item);
    else missing.push(id);
  }
  return aggregate(found, missing);
}

export async function aggregateConcurrent(
  repository: ItemRepository,
  ids: string[],
): Promise<AggregateResponse> {
  // 全IDの読み取りを同時に開始し、直列方式と同じくIDごとに一回だけ外部I/Oを行う。
  const results = await Promise.all(
    ids.map(async (id) => ({ id, item: await readOrDependencyError(repository, id) })),
  );
  const found = results.flatMap(({ item }) => (item ? [item] : []));
  const missing = results.flatMap(({ id, item }) => (item ? [] : [id]));
  return aggregate(found, missing);
}

/**
 * 読み取り結果を、実行方式に依存しない共通の集約結果へ正規化する。
 *
 * 未登録IDは完了順ではなく辞書順で選び、直列・並行のどちらでも同じ失敗応答にする。
 */
export function aggregate(items: Item[], missing: string[]): AggregateResponse {
  // 未登録IDが一件以上ある場合は、辞書順で最初のIDを404として返す。
  const [firstMissing] = [...missing].sort(byteOrder);
  if (firstMissing !== undefined) throw AppError.notFound(firstMissing);

  // 全項目が存在する場合だけ、ID順を固定して値の合計とpayload連結順を決定する。
  const ordered = [...items].sort((left, right) => byteOrder(left.id, right.id));
  const checksum = createHash("sha256");
  let totalValue = 0;
  for (const item of ordered) {
    totalValue += item.value;
    checksum.update(item.payload, "utf8");
  }
  // 合計が安全に表せる整数の範囲を超えた場合は、誤った合計を返さず内部エラーとする。
  if (!Number.isSafeInteger(totalValue)) throw AppError.internal();
  return { count: ordered.length, total_value: totalValue, checksum: checksum.digest("hex") };
}

async function readOrDependencyError(
  repository: ItemRepository,
  id: string,
): Promise<Item | undefined> {
  try {
    return await repository.get(id);
  } catch {
    // 保存先の呼び出し失敗は、利用者の入力ではなく外部依存の障害として502にする。
    throw AppError.dependency();
  }
}
