// HTTP入力を共通API契約のドメイン処理へ接続し、構造化ログを出力する。
//
// フレームワーク固有のリクエスト形式・エラーをここで正規化し、他の言語の実装と同じ応答と
// ベンチマーク相関項目を提供する。

import { Hono, type Context } from "hono";
import type { LambdaContext, LambdaEvent } from "hono/aws-lambda";

import * as domain from "./domain.js";
import { AppError } from "./errors.js";
import { parseStrictJson, type JsonValue } from "./json.js";
import { logRequestCompleted } from "./logging.js";
import type { ItemRepository } from "./repository.js";
import * as validation from "./validation.js";

const BENCHMARK_RUN_ID_HEADER = "x-benchmark-run-id";
const BENCHMARK_REQUEST_INDEX_HEADER = "x-benchmark-request-index";

type AppEnv = {
  Bindings: { event?: LambdaEvent; lambdaContext?: LambdaContext };
  // ログ出力用に、契約上の経路名と計測プロファイルをリクエストごとに保持する。
  Variables: { route: string; profile: string };
};

export function createApp(repository: ItemRepository): Hono<AppEnv> {
  const app = new Hono<AppEnv>();

  // 全リクエストについて、応答後に計測との相関項目を含む構造化ログを一行出力する。
  app.use(async (c, next) => {
    c.set("route", c.req.path);
    c.set("profile", "unknown");
    await next();
    logRequestCompleted({
      lambdaRequestId: c.env?.lambdaContext?.awsRequestId ?? "",
      benchmarkRunId: c.req.header(BENCHMARK_RUN_ID_HEADER) ?? "",
      benchmarkRequestIndex: benchmarkRequestIndex(c.req.header(BENCHMARK_REQUEST_INDEX_HEADER)),
      route: c.get("route"),
      profile: c.get("profile"),
      statusCode: c.res.status,
    });
  });

  app.get("/health", (c) => {
    // 計測IDだけを検証し、外部I/OなしでLambdaの到達性を返す。
    c.set("profile", "not_applicable");
    checkBenchmarkRunId(c);
    return c.json({ status: "ok" });
  });

  app.post("/transform", async (c) => {
    // 本文のJSON解析、構造検証、業務検証をこの順で行い、成功時だけレコードを正規化する。
    c.set("profile", "not_applicable");
    checkBenchmarkRunId(c);
    const request = validation.transformRequest(await parseBody(c));
    validation.checkTransformSemantics(request);
    return c.json(domain.transform(request));
  });

  app.post("/compute", async (c) => {
    // 検証に成功した計算プロファイルをログ用に保存し、指定されたCPU計算を実行する。
    checkBenchmarkRunId(c);
    const request = validation.computeRequest(await parseBody(c));
    c.set("profile", request.profile);
    validation.checkComputeSemantics(request);
    return c.json(domain.compute(request));
  });

  app.get("/items/:id", async (c) => {
    // パスIDを検証してから一件だけ取得し、保存先障害と未登録をHTTPエラーへ変換する。
    c.set("route", "/items/{id}");
    c.set("profile", "not_applicable");
    checkBenchmarkRunId(c);
    const id = c.req.param("id");
    validation.checkItemId(id);
    return c.json({ item: await domain.getItem(repository, id) });
  });

  app.post("/aggregate", async (c) => {
    checkBenchmarkRunId(c);
    const request = validation.aggregateRequest(await parseBody(c));
    c.set("profile", request.profile);
    validation.checkAggregateSemantics(request);
    // profile が sequential の場合はID順に読み、concurrent の場合は全IDを同時に読み取る。
    // どちらもIDごとの読み取り回数は一回に固定し、比較条件を揃える。
    const response =
      request.profile === "sequential"
        ? await domain.aggregateSequential(repository, request.ids)
        : await domain.aggregateConcurrent(repository, request.ids);
    return c.json(response);
  });

  // 登録済み経路と一致しない場合は、契約で定めた未検出エラーを返す。
  app.notFound((c) => c.json(AppError.routeNotFound().body(), 404));

  // 契約上のエラーはその状態と本文で返し、想定外の例外は詳細を隠して500にする。
  app.onError((error, c) => {
    const appError = error instanceof AppError ? error : AppError.internal();
    return c.json(appError.body(), appError.status);
  });

  return app;
}

/** 本文を読む前に計測相関ヘッダーを検証し、他の言語の実装と同じ検証順序を守る。 */
function checkBenchmarkRunId(c: Context<AppEnv>): void {
  const runId = c.req.header(BENCHMARK_RUN_ID_HEADER);
  // ヘッダーが未指定の場合は通常リクエストとして通し、指定されている場合だけ検証する。
  if (runId !== undefined) validation.checkBenchmarkRunId(runId);
}

async function parseBody(c: Context<AppEnv>): Promise<JsonValue> {
  const parsed = parseStrictJson(new Uint8Array(await c.req.arrayBuffer()));
  // 構文、UTF-8、重複キーのいずれかが不正なら、本文全体のinvalid_jsonとして扱う。
  if (!parsed) throw AppError.invalidJson();
  return parsed.value;
}

/** ランナーが付与した計測番号をログ相関用に正規化し、通常リクエストや不正値は0とする。 */
export function benchmarkRequestIndex(value: string | undefined): number {
  // 符号付き整数の表記だけを計測番号として採用する。
  if (value === undefined || !/^[+-]?\d+$/.test(value)) return 0;
  const index = Number(value);
  return Number.isSafeInteger(index) ? index : 0;
}
