// 共有する正規テストベクトル（spec/testcases）をTypeScript実装へ適用する。
//
// 他の言語の実装も同じファイルを使うため、デプロイ環境なしに応答の一致を確かめられる。

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, vi } from "vitest";

import { createApp } from "../src/app.js";
import { InMemoryItemRepository, type Item } from "../src/repository.js";

const TESTCASES = join(import.meta.dirname, "../../../spec/testcases");

interface CaseDocument {
  implementations?: string[];
  fixtures?: { items?: Item[] };
  cases: {
    name: string;
    // raw_body・raw_body_base64は、JSONとして不正な本文や数値の表記をそのまま送るために使う。
    // raw_body_repeatは、上限を超える大きな本文を、文字列とその繰り返し回数で表す。
    request: {
      method: string;
      path: string;
      body?: unknown;
      raw_body?: string;
      raw_body_base64?: string;
      raw_body_repeat?: { text: string; count: number };
    };
    expected: { status: number; body: unknown };
  }[];
}

const documents: CaseDocument[] = readdirSync(TESTCASES)
  .filter((name) => name.endsWith(".json"))
  .sort()
  .map((name) => JSON.parse(readFileSync(join(TESTCASES, name), "utf8")) as CaseDocument)
  // 一部の言語だけで実装済みのケースファイルは、TypeScriptが対象に含まれる場合だけ使う。
  .filter((document) => document.implementations?.includes("typescript") ?? true);

// ケースの本文を送信する形へ変換する。生の本文は組み立て直さずにそのまま送る。
function requestBody(request: CaseDocument["cases"][number]["request"]): BodyInit | undefined {
  if (request.raw_body_repeat !== undefined)
    return request.raw_body_repeat.text.repeat(request.raw_body_repeat.count);
  if (request.raw_body_base64 !== undefined) return Buffer.from(request.raw_body_base64, "base64");
  if (request.raw_body !== undefined) return request.raw_body;
  return request.body === undefined ? undefined : JSON.stringify(request.body);
}

test("every canonical case matches", async () => {
  // 構造化ログは検証対象ではないため、テスト出力へ混ぜない。
  vi.spyOn(process.stdout, "write").mockImplementation(() => true);
  const items = new Map<string, Item>();
  for (const item of documents.flatMap((document) => document.fixtures?.items ?? [])) {
    items.set(item.id, item);
  }
  const app = createApp(new InMemoryItemRepository([...items.values()]));

  let executed = 0;
  for (const document of documents) {
    for (const testcase of document.cases) {
      const { method, path } = testcase.request;
      const body = requestBody(testcase.request);
      const response = await app.request(path, {
        method,
        ...(body === undefined ? {} : { body, headers: { "Content-Type": "application/json" } }),
      });

      // 一つの不一致で止めず、全ケースの不一致をまとめて報告する。
      expect.soft(response.status, `${testcase.name}: status`).toBe(testcase.expected.status);
      expect.soft(await response.json(), `${testcase.name}: body`).toEqual(testcase.expected.body);
      executed += 1;
    }
  }
  expect(executed).toBeGreaterThanOrEqual(12);
});
