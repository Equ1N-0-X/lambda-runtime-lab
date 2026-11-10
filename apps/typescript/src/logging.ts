// 計測とLambdaの実行記録を結び付けるための、一リクエスト一行の構造化ログを出力する。
//
// ランタイムのconsoleはログ形式JSONのとき本文を message へ包むため、他の言語と同じく
// 標準出力へJSONを一行で直接書き、相関項目をトップレベルに置く。

export interface RequestLog {
  lambdaRequestId: string;
  benchmarkRunId: string;
  benchmarkRequestIndex: number;
  route: string;
  profile: string;
  statusCode: number;
  // 502のときだけ、保存先障害の原因の分類とAWSのエラーコードを残す（本文や例外の全文は出さない）。
  dependencyCause?: { category: string; errorCode: string | null };
}

export function logRequestCompleted(log: RequestLog, write = writeLine): void {
  // 502は外部依存先の障害としてWARN、それ以外の500番台は内部障害としてERROR、
  // それ以外は仕様どおり応答できた処理としてINFOに分類する。全分類で相関項目は共通に保つ。
  const [level, message] =
    log.statusCode === 502
      ? ["WARN", "外部データストアへのアクセスに失敗しました"]
      : log.statusCode >= 500
        ? ["ERROR", "リクエスト処理中に内部エラーが発生しました"]
        : ["INFO", "リクエスト処理が完了しました"];
  write(
    JSON.stringify({
      level,
      message,
      implementation: "typescript",
      lambda_request_id: log.lambdaRequestId,
      benchmark_run_id: log.benchmarkRunId,
      benchmark_request_index: log.benchmarkRequestIndex,
      route: log.route,
      profile: log.profile,
      status_code: log.statusCode,
      ...(log.statusCode === 502 && log.dependencyCause
        ? {
            dependency: "dynamodb",
            dependency_operation: "GetItem",
            dependency_error_category: log.dependencyCause.category,
            dependency_error_code: log.dependencyCause.errorCode,
          }
        : {}),
    }),
  );
}

function writeLine(line: string): void {
  process.stdout.write(`${line}\n`);
}
