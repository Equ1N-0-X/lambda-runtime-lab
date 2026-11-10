// 保存値から公開するItemへの変換と、保存先障害の原因の分類を、全言語共通のケースで検証する。

import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { AttributeValue, DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { expect, test, vi } from "vitest";

import { createApp } from "../src/app.js";
import { DynamoDbItemRepository, RepositoryError, type ItemRepository } from "../src/repository.js";

interface StoredItemCase {
  name: string;
  attributes: Record<string, AttributeValue>;
  expected: { item?: unknown; error?: string };
}

const CASES = (
  JSON.parse(
    readFileSync(
      join(import.meta.dirname, "../../../spec/repository-cases/stored-items.json"),
      "utf8",
    ),
  ) as { cases: StoredItemCase[] }
).cases;

function repositoryReturning(result: () => Promise<unknown>): DynamoDbItemRepository {
  const client = { send: vi.fn(result) } as unknown as DynamoDBClient;
  return new DynamoDbItemRepository(client, "items");
}

test.each(CASES.map((testcase) => [testcase.name, testcase] as const))(
  "stored item %s follows the shared case",
  async (_name, testcase) => {
    const repository = repositoryReturning(async () => ({ Item: testcase.attributes }));
    // 全言語で同じ保存値の境界を使い、契約外の保存値を成功の応答にしない。
    if (testcase.expected.error) {
      await expect(repository.get("item-001")).rejects.toMatchObject({
        category: testcase.expected.error,
      });
    } else {
      expect(await repository.get("item-001")).toEqual(testcase.expected.item);
    }
  },
);

function sdkError(name: string, httpStatusCode?: number): Error {
  return Object.assign(new Error("secret detail"), {
    name,
    $metadata: httpStatusCode === undefined ? {} : { httpStatusCode },
  });
}

test.each([
  ["AccessDeniedException", 400, "permission"],
  ["ResourceNotFoundException", 400, "table"],
  ["ProvisionedThroughputExceededException", 400, "throttling"],
  ["InternalServerError", 500, "unknown"],
  ["TimeoutError", undefined, "network"],
] as const)("SDK error %s is classified", async (name, status, category) => {
  const repository = repositoryReturning(async () => {
    throw sdkError(name, status);
  });

  const error = await repository.get("item-001").catch((caught: unknown) => caught);

  expect(error).toBeInstanceOf(RepositoryError);
  expect((error as RepositoryError).category).toBe(category);
});

test("dependency error log keeps the cause but not the detail", async () => {
  const lines: string[] = [];
  vi.spyOn(process.stdout, "write").mockImplementation((line) => {
    lines.push(String(line));
    return true;
  });
  const repository: ItemRepository = {
    get: async () => {
      throw new RepositoryError("permission", "AccessDeniedException");
    },
  };

  const response = await createApp(repository).request("/items/item-001");

  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({
    error: { code: "DEPENDENCY_ERROR", message: "dependency error", details: [] },
  });
  const log = JSON.parse(lines.at(-1) ?? "{}");
  expect(log.dependency_error_category).toBe("permission");
  expect(log.dependency_error_code).toBe("AccessDeniedException");
  expect(lines.join("")).not.toContain("secret detail");
  vi.restoreAllMocks();
});
