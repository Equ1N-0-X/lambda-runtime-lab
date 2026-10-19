// 契約が求める厳密なJSON解析（重複キー・整数表記・UTF-8）を検証する。

import { describe, expect, test } from "vitest";

import { JsonNumber, parseStrictJson } from "../src/json.js";

const encode = (text: string) => new TextEncoder().encode(text);

describe("parseStrictJson", () => {
  test("parses nested documents and keeps number literals", () => {
    const parsed = parseStrictJson(encode('{"records":[{"id":"a","tags":["x"],"value":1.0}]}'));

    const value = (parsed?.value as { records: { value: JsonNumber }[] }).records[0]!.value;
    expect(value).toBeInstanceOf(JsonNumber);
    expect(value.literal).toBe("1.0");
    expect(value.isIntegerToken).toBe(false);
  });

  test("rejects duplicate members at any depth", () => {
    expect(parseStrictJson(encode('{"items":1,"items":2}'))).toBeNull();
    expect(parseStrictJson(encode('{"records":[{"id":"a","id":"b"}]}'))).toBeNull();
  });

  test("rejects malformed documents and invalid UTF-8", () => {
    expect(parseStrictJson(encode('{"items":'))).toBeNull();
    expect(parseStrictJson(encode('{"items":1} x'))).toBeNull();
    expect(parseStrictJson(encode("01"))).toBeNull();
    expect(parseStrictJson(new Uint8Array([0xff, 0xfe]))).toBeNull();
  });

  test("does not treat __proto__ as a prototype", () => {
    const parsed = parseStrictJson(encode('{"__proto__":{"polluted":true}}'));

    expect(Object.keys(parsed?.value as object)).toEqual(["__proto__"]);
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });
});
