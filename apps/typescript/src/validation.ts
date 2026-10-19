// 共通API契約に従い、解析済みJSONを検証して型付きの要求へ変換する。
//
// 不正入力で返すフィールド位置と理由は契約で固定されているため、検証ライブラリの
// 自動エラーへ委譲せず、他の言語の実装と同じ順序で検査する。

import { AppError, type ErrorDetail } from "./errors.js";
import { JsonNumber, type JsonValue } from "./json.js";

export const MAX_RECORDS = 1_000;
export const MAX_TAGS = 32;
export const MAX_IDS = 100;
export const MAX_COMPUTE_ITEMS = 1_000;
export const MAX_COMPUTE_ITERATIONS = 1_000_000;
export const MAX_COMPUTE_WORK = 1_000_000;
export const MIN_ITEM_VALUE = -1_000_000_000;
export const MAX_ITEM_VALUE = 1_000_000_000;

export const COMPUTE_PROFILES = ["sequential"] as const;
export const AGGREGATE_PROFILES = ["sequential", "concurrent"] as const;

export const BENCHMARK_RUN_ID_FIELD = "headers.X-Benchmark-Run-Id";

export interface TransformRecord {
  id: string;
  name: string;
  tags: string[];
  value: number;
}

export interface TransformRequest {
  records: TransformRecord[];
}

export interface ComputeRequest {
  items: number;
  iterations: number;
  seed: string;
  profile: string;
}

export interface AggregateRequest {
  ids: string[];
  profile: string;
}

type JsonObject = { [key: string]: JsonValue };

interface StringSpec {
  minLength: number;
  maxLength: number;
  pattern: (text: string) => boolean;
}

const ITEM_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const PRINTABLE_ASCII_PATTERN = /^[ -~]*$/;

const ITEM_ID: StringSpec = {
  minLength: 1,
  maxLength: 128,
  pattern: (text) => ITEM_ID_PATTERN.test(text),
};
const RECORD_NAME: StringSpec = {
  minLength: 0,
  maxLength: 256,
  pattern: (text) => PRINTABLE_ASCII_PATTERN.test(text),
};
const TAG: StringSpec = {
  minLength: 0,
  maxLength: 64,
  pattern: (text) => PRINTABLE_ASCII_PATTERN.test(text),
};
const SEED: StringSpec = { minLength: 1, maxLength: 256, pattern: () => true };
const PROFILE: StringSpec = { minLength: 0, maxLength: 256, pattern: () => true };

export function transformRequest(document: JsonValue): TransformRequest {
  const details: ErrorDetail[] = [];
  const root = object(document, "$", details);
  // ルートがオブジェクトでない場合は各項目を読めないため、直ちにルートの検証エラーを返す。
  if (!root) throw AppError.validation(details);
  rejectUnknownMembers(root, ["records"], "", details);

  const records: TransformRecord[] = [];
  let recordCount = 0;
  const items = arrayMember(root, "", "records", 1, MAX_RECORDS, details);
  // recordsが配列かつ件数範囲内の場合だけ、各要素をレコードとして検証・変換する。
  if (items) {
    recordCount = items.length;
    items.forEach((item, index) => {
      const record = transformRecord(item, index, details);
      if (record) records.push(record);
    });
  }

  // 項目エラーが一件でもある、または変換成功数が入力件数と異なる場合は成功要求を作らない。
  if (details.length > 0 || records.length !== recordCount) throw AppError.validation(details);
  return { records };
}

export function computeRequest(document: JsonValue): ComputeRequest {
  const details: ErrorDetail[] = [];
  const root = object(document, "$", details);
  // ルートがオブジェクトでない場合は各計算項目を読めないため、ルートの検証エラーを返す。
  if (!root) throw AppError.validation(details);
  rejectUnknownMembers(root, ["items", "iterations", "seed", "profile"], "", details);

  const items = integerMember(root, "", "items", 1, MAX_COMPUTE_ITEMS, details);
  const iterations = integerMember(root, "", "iterations", 1, MAX_COMPUTE_ITERATIONS, details);
  const seed = stringMember(root, "", "seed", SEED, details);
  const profile = stringMember(root, "", "profile", PROFILE, details);

  // 四つの必須項目がすべて取得でき、追加の検証エラーもない場合だけ計算要求を確定する。
  if (
    items === undefined ||
    iterations === undefined ||
    seed === undefined ||
    profile === undefined ||
    details.length > 0
  ) {
    throw AppError.validation(details);
  }
  return { items, iterations, seed, profile };
}

export function aggregateRequest(document: JsonValue): AggregateRequest {
  const details: ErrorDetail[] = [];
  const root = object(document, "$", details);
  // ルートがオブジェクトでない場合はID一覧とprofileを読めないため、ルートの検証エラーを返す。
  if (!root) throw AppError.validation(details);
  rejectUnknownMembers(root, ["ids", "profile"], "", details);

  const ids: string[] = [];
  let idCount = 0;
  const items = arrayMember(root, "", "ids", 1, MAX_IDS, details);
  // idsが配列かつ件数範囲内の場合だけ、各要素をIDとして検証・変換する。
  if (items) {
    idCount = items.length;
    items.forEach((item, index) => {
      const id = stringValue(item, `ids[${index}]`, ITEM_ID, details);
      if (id !== undefined) ids.push(id);
    });
  }
  const profile = stringMember(root, "", "profile", PROFILE, details);

  // profileが取得でき、全IDの変換にも失敗していない場合だけ集約要求を確定する。
  if (profile === undefined || details.length > 0 || ids.length !== idCount) {
    throw AppError.validation(details);
  }
  return { ids, profile };
}

export function checkItemId(id: string): void {
  checkedString(id, "id", ITEM_ID);
}

/**
 * 計測相関ヘッダーはBenchmark Runnerの実行IDと同じ文字種を受け付けるため、UUIDだけでなく
 * 人が読める実行IDも使用できる。
 */
export function checkBenchmarkRunId(runId: string): void {
  checkedString(runId, BENCHMARK_RUN_ID_FIELD, ITEM_ID);
}

export function checkTransformSemantics(request: TransformRequest): void {
  const details = duplicateDetails(
    request.records.map((record) => record.id),
    (index) => `records[${index}].id`,
  );
  // 重複IDがなければ成功とし、一件でもあれば重複位置をまとめた422エラーを返す。
  if (details.length > 0) throw AppError.validation(details);
}

export function checkComputeSemantics(request: ComputeRequest): void {
  // 項目数と反復回数の積が計測用上限を超える場合は、過度なCPU消費を防ぐため拒否する。
  if (request.items * request.iterations > MAX_COMPUTE_WORK) {
    throw AppError.validation([{ field: "$", reason: `maximum_work:${MAX_COMPUTE_WORK}` }]);
  }
  // プロファイル名が計算APIで許可した方式に含まれない場合は、400エラーを返す。
  if (!(COMPUTE_PROFILES as readonly string[]).includes(request.profile)) {
    throw AppError.unsupportedProfile(COMPUTE_PROFILES);
  }
}

export function checkAggregateSemantics(request: AggregateRequest): void {
  const details = duplicateDetails(request.ids, (index) => `ids[${index}]`);
  // 同じIDを複数回集約すると二重計上になるため、重複が一件でもあれば拒否する。
  if (details.length > 0) throw AppError.validation(details);
  // プロファイル名が直列・並行の許可方式に含まれない場合は、400エラーを返す。
  if (!(AGGREGATE_PROFILES as readonly string[]).includes(request.profile)) {
    throw AppError.unsupportedProfile(AGGREGATE_PROFILES);
  }
}

function checkedString(value: string, field: string, spec: StringSpec): void {
  const details: ErrorDetail[] = [];
  stringValue(value, field, spec, details);
  // 文字列検証で詳細が一件でもあれば422エラーへ変換する。
  if (details.length > 0) throw AppError.validation(details);
}

/** 二回目以降に現れた値の位置を、重複として列挙する。 */
function duplicateDetails(values: string[], field: (index: number) => string): ErrorDetail[] {
  const seen = new Set<string>();
  const details: ErrorDetail[] = [];
  values.forEach((value, index) => {
    if (seen.has(value)) details.push({ field: field(index), reason: "duplicate" });
    seen.add(value);
  });
  return details;
}

function transformRecord(
  document: JsonValue,
  index: number,
  details: ErrorDetail[],
): TransformRecord | undefined {
  const prefix = `records[${index}]`;
  const record = object(document, prefix, details);
  if (!record) return undefined;
  rejectUnknownMembers(record, ["id", "name", "tags", "value"], prefix, details);

  const id = stringMember(record, prefix, "id", ITEM_ID, details);
  const name = stringMember(record, prefix, "name", RECORD_NAME, details);
  const tags = tagMember(record, prefix, details);
  const value = integerMember(record, prefix, "value", MIN_ITEM_VALUE, MAX_ITEM_VALUE, details);

  // 全項目が検証に成功した場合だけ、変換済みレコードとして返す。
  if (id === undefined || name === undefined || tags === undefined || value === undefined) {
    return undefined;
  }
  return { id, name, tags, value };
}

function tagMember(
  record: JsonObject,
  prefix: string,
  details: ErrorDetail[],
): string[] | undefined {
  const items = arrayMember(record, prefix, "tags", 0, MAX_TAGS, details);
  if (!items) return undefined;
  const tags: string[] = [];
  items.forEach((item, index) => {
    const tag = stringValue(item, `${prefix}.tags[${index}]`, TAG, details);
    if (tag !== undefined) tags.push(tag);
  });
  // 一つでも不正なタグがあれば、レコード全体を不成立とする。
  return tags.length === items.length ? tags : undefined;
}

function object(
  document: JsonValue,
  field: string,
  details: ErrorDetail[],
): JsonObject | undefined {
  // JSON値がオブジェクトならメンバーを返し、それ以外は対象フィールドの型不正を記録する。
  if (
    document !== null &&
    typeof document === "object" &&
    !Array.isArray(document) &&
    !(document instanceof JsonNumber)
  ) {
    return document;
  }
  details.push({ field, reason: "type:object" });
  return undefined;
}

function rejectUnknownMembers(
  members: JsonObject,
  allowed: string[],
  prefix: string,
  details: ErrorDetail[],
): void {
  for (const key of Object.keys(members)) {
    // キーが許可リストにない場合は、未定義の入力項目として検証エラーへ追加する。
    if (!allowed.includes(key)) {
      details.push({ field: fieldPath(prefix, key), reason: "additional_property_not_allowed" });
    }
  }
}

function fieldPath(prefix: string, name: string): string {
  // ルート項目なら名前だけ、入れ子項目なら親パスとドットで連結してエラー位置を表す。
  return prefix === "" ? name : `${prefix}.${name}`;
}

function member(
  members: JsonObject,
  prefix: string,
  name: string,
  details: ErrorDetail[],
): JsonValue | undefined {
  // 指定名のメンバーがあれば値（JSONのnullを含む）を返し、なければ必須項目不足として記録する。
  if (Object.prototype.hasOwnProperty.call(members, name)) return members[name] ?? null;
  details.push({ field: fieldPath(prefix, name), reason: "required" });
  return undefined;
}

function stringMember(
  members: JsonObject,
  prefix: string,
  name: string,
  spec: StringSpec,
  details: ErrorDetail[],
): string | undefined {
  const value = member(members, prefix, name, details);
  if (value === undefined) return undefined;
  return stringValue(value, fieldPath(prefix, name), spec, details);
}

function stringValue(
  document: JsonValue,
  field: string,
  spec: StringSpec,
  details: ErrorDetail[],
): string | undefined {
  if (typeof document !== "string") {
    details.push({ field, reason: "type:string" });
    return undefined;
  }
  // 文字数は契約どおりUnicodeのコードポイント数で数える（JSの length はUTF-16単位のため使わない）。
  const length = Array.from(document).length;
  // 文字数が最小値未満、最大値超過、またはパターン不一致なら、その最初の理由を記録して失敗する。
  if (length < spec.minLength) {
    details.push({ field, reason: `min_length:${spec.minLength}` });
    return undefined;
  }
  if (length > spec.maxLength) {
    details.push({ field, reason: `max_length:${spec.maxLength}` });
    return undefined;
  }
  if (!spec.pattern(document)) {
    details.push({ field, reason: "pattern" });
    return undefined;
  }
  return document;
}

function integerMember(
  members: JsonObject,
  prefix: string,
  name: string,
  minimum: number,
  maximum: number,
  details: ErrorDetail[],
): number | undefined {
  const field = fieldPath(prefix, name);
  const value = member(members, prefix, name, details);
  if (value === undefined) return undefined;

  // 数値以外、または小数点・指数表記を含む数値は整数型の不正とする。
  if (!(value instanceof JsonNumber) || !value.isIntegerToken) {
    details.push({ field, reason: "type:integer" });
    return undefined;
  }
  // 大きな整数も精度を失わずに比較するため、BigIntで上下限と比べる。
  const number = BigInt(value.literal);
  if (number < BigInt(minimum)) {
    details.push({ field, reason: `minimum:${minimum}` });
    return undefined;
  }
  if (number > BigInt(maximum)) {
    details.push({ field, reason: `maximum:${maximum}` });
    return undefined;
  }
  return Number(number);
}

function arrayMember(
  members: JsonObject,
  prefix: string,
  name: string,
  minItems: number,
  maxItems: number,
  details: ErrorDetail[],
): JsonValue[] | undefined {
  const field = fieldPath(prefix, name);
  const value = member(members, prefix, name, details);
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) {
    details.push({ field, reason: "type:array" });
    return undefined;
  }
  // 配列件数が最小値未満または最大値超過なら、要素を処理せず配列全体の検証エラーにする。
  if (value.length < minItems) {
    details.push({ field, reason: `min_items:${minItems}` });
    return undefined;
  }
  if (value.length > maxItems) {
    details.push({ field, reason: `max_items:${maxItems}` });
    return undefined;
  }
  return value;
}
