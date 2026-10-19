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
  fixtures?: { items?: Item[] };
  cases: {
    name: string;
    request: { method: string; path: string; body?: unknown };
    expected: { status: number; body: unknown };
  }[];
}

const documents: CaseDocument[] = readdirSync(TESTCASES)
  .filter((name) => name.endsWith(".json"))
  .sort()
  .map((name) => JSON.parse(readFileSync(join(TESTCASES, name), "utf8")) as CaseDocument);

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
      const { method, path, body } = testcase.request;
      const response = await app.request(path, {
        method,
        ...(body === undefined
          ? {}
          : { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }),
      });

      expect(response.status, `${testcase.name}: status`).toBe(testcase.expected.status);
      expect(await response.json(), `${testcase.name}: body`).toEqual(testcase.expected.body);
      executed += 1;
    }
  }
  expect(executed).toBeGreaterThanOrEqual(12);
});
