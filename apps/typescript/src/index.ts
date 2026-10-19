// TypeScript Lambdaの起動処理として、AWSクライアントとルーターを組み立てる。
//
// 初期化はコールドスタート時に一度だけ行い、ウォームスタートでは同じクライアントを再利用する。

import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { handle } from "hono/aws-lambda";

import { createApp } from "./app.js";
import { DynamoDbItemRepository } from "./repository.js";

const tableName = process.env.DYNAMODB_TABLE_NAME;
// テーブル名が未設定なら保存先を決められないため、Lambdaの初期化を失敗させる。
if (!tableName) {
  throw new Error("DYNAMODB_TABLE_NAMEの設定が必要です");
}

const repository = new DynamoDbItemRepository(new DynamoDBClient({}), tableName);

export const handler = handle(createApp(repository));
