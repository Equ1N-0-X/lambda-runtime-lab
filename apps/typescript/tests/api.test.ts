// 正規テストケースで網羅しない入力境界、ログ相関、依存先障害の扱いを検証する。

import { afterEach, describe, expect, test, vi } from "vitest";

import { benchmarkRequestIndex, createApp } from "../src/app.js";
import { InMemoryItemRepository, type ItemRepository } from "../src/repository.js";

function captureLogs(): string[] {
  const lines: string[] = [];
  vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
    lines.push(String(chunk));
    return true;
  });
  return lines;
}

const app = createApp(
  new InMemoryItemRepository([{ id: "item-001", value: 123, payload: "payload-001" }]),
);

const post = (path: string, body: string, headers: Record<string, string> = {}) =>
  app.request(path, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json", ...headers },
  });

afterEach(() => vi.restoreAllMocks());

describe("validation boundaries", () => {
  test("rejects floats and strings for integers without coercion", async () => {
    captureLogs();
    const response = await post(
      "/compute",
      '{"items":1.0,"iterations":"1","seed":"s","profile":"sequential"}',
    );

    expect(response.status).toBe(422);
    expect((await response.json()).error.details).toEqual([
      { field: "items", reason: "type:integer" },
      { field: "iterations", reason: "type:integer" },
    ]);
  });

  test("reports huge integers as range violations, not type errors", async () => {
    captureLogs();
    const response = await post(
      "/compute",
      '{"items":99999999999999999999,"iterations":-99999999999999999999,"seed":"s","profile":"sequential"}',
    );

    expect((await response.json()).error.details).toEqual([
      { field: "items", reason: "maximum:1000" },
      { field: "iterations", reason: "minimum:1" },
    ]);
  });

  test("duplicate JSON members are invalid_json", async () => {
    captureLogs();
    const response = await post("/transform", '{"records":[],"records":[]}');

    expect(response.status).toBe(422);
    expect((await response.json()).error.details).toEqual([{ field: "$", reason: "invalid_json" }]);
  });

  test("counts seed length in Unicode code points", async () => {
    captureLogs();
    const seed = "😀".repeat(256);
    const response = await post(
      "/compute",
      JSON.stringify({ items: 1, iterations: 1, seed, profile: "sequential" }),
    );

    expect(response.status).toBe(200);
  });

  test("validates the benchmark run header before the body", async () => {
    captureLogs();
    const response = await post("/transform", "{", { "X-Benchmark-Run-Id": "-not a run id" });

    expect((await response.json()).error.details).toEqual([
      { field: "headers.X-Benchmark-Run-Id", reason: "pattern" },
    ]);
  });

  test("unknown routes return the canonical not-found body", async () => {
    captureLogs();
    const response = await app.request("/unknown");

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({
      error: { code: "NOT_FOUND", message: "item not found", details: [] },
    });
  });
});

describe("dependency failures", () => {
  test("storage errors become 502 and are logged as WARN", async () => {
    const lines = captureLogs();
    const failing: ItemRepository = {
      get: () => Promise.reject(new Error("timeout")),
    };

    const response = await createApp(failing).request("/items/item-001");

    expect(response.status).toBe(502);
    expect((await response.json()).error.code).toBe("DEPENDENCY_ERROR");
    expect(JSON.parse(lines[0]!)).toMatchObject({ level: "WARN", status_code: 502 });
  });
});

describe("benchmark log correlation", () => {
  test("writes one JSON line with correlation fields at the top level", async () => {
    const lines = captureLogs();

    await post("/aggregate", '{"ids":["item-001"],"profile":"concurrent"}', {
      "X-Benchmark-Run-Id": "arm64-512-health-20260101T000000Z",
      "X-Benchmark-Request-Index": "-3",
    });

    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0]!)).toMatchObject({
      level: "INFO",
      implementation: "typescript",
      benchmark_run_id: "arm64-512-health-20260101T000000Z",
      benchmark_request_index: -3,
      route: "/aggregate",
      profile: "concurrent",
      status_code: 200,
    });
  });

  test("request index distinguishes measurement, warm-up and invalid values", () => {
    expect(benchmarkRequestIndex("7")).toBe(7);
    expect(benchmarkRequestIndex("-3")).toBe(-3);
    expect(benchmarkRequestIndex("invalid")).toBe(0);
    expect(benchmarkRequestIndex(undefined)).toBe(0);
  });
});
