// 共通API契約が求める厳密さでJSON本文を解析する。
//
// 標準の JSON.parse は重複キーを後勝ちで受け入れ、1 と 1.0 を区別せず、大きな整数の精度を
// 失う。契約は重複キーを invalid_json とし、整数トークンだけを整数として扱うため、
// 数値は元の表記（リテラル）のまま保持する独自の解析器を使う。

/** 解析した数値。整数か小数かの判定と範囲比較のため、元の表記を保持する。 */
export class JsonNumber {
  constructor(readonly literal: string) {}

  /** 小数点や指数を含まない整数トークンかどうか。 */
  get isIntegerToken(): boolean {
    return !/[.eE]/.test(this.literal);
  }
}

export type JsonValue =
  null | boolean | string | JsonNumber | JsonValue[] | { [key: string]: JsonValue };

class JsonSyntaxError extends Error {}

const WHITESPACE = new Set([" ", "\t", "\n", "\r"]);
const ESCAPES: Record<string, string> = {
  '"': '"',
  "\\": "\\",
  "/": "/",
  b: "\b",
  f: "\f",
  n: "\n",
  r: "\r",
  t: "\t",
};
const NUMBER_PATTERN = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;

/** 構文不正・UTF-8不正・重複キーのいずれかならnullを返し、それ以外は解析結果を返す。 */
export function parseStrictJson(body: Uint8Array): { value: JsonValue } | null {
  let text: string;
  try {
    // 不正なUTF-8を置換文字で読み替えず、本文全体を不正として扱う。
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(body);
  } catch {
    return null;
  }
  try {
    const parser = new Parser(text);
    const value = parser.parseDocument();
    return { value };
  } catch (error) {
    // 構文エラーと重複キーだけを解析失敗とし、それ以外の想定外の例外は呼び出し元へ伝える。
    if (error instanceof JsonSyntaxError) return null;
    throw error;
  }
}

class Parser {
  private position = 0;

  constructor(private readonly text: string) {}

  parseDocument(): JsonValue {
    this.skipWhitespace();
    const value = this.parseValue();
    this.skipWhitespace();
    // 値の後に空白以外が残っていれば、単一のJSON文書ではない。
    if (this.position !== this.text.length) throw new JsonSyntaxError("trailing characters");
    return value;
  }

  private parseValue(): JsonValue {
    const character = this.text[this.position];
    switch (character) {
      case "{":
        return this.parseObject();
      case "[":
        return this.parseArray();
      case '"':
        return this.parseString();
      case "t":
        return this.parseKeyword("true", true);
      case "f":
        return this.parseKeyword("false", false);
      case "n":
        return this.parseKeyword("null", null);
      default:
        return this.parseNumber();
    }
  }

  private parseObject(): { [key: string]: JsonValue } {
    this.position += 1;
    // __proto__ などのキーをプロトタイプとして解釈しないよう、プロトタイプのない対応表を使う。
    const members: { [key: string]: JsonValue } = Object.create(null);
    this.skipWhitespace();
    if (this.text[this.position] === "}") {
      this.position += 1;
      return members;
    }
    for (;;) {
      this.skipWhitespace();
      // キーは必ず文字列でなければならない。
      if (this.text[this.position] !== '"') throw new JsonSyntaxError("object key");
      const key = this.parseString();
      // 同じキーが既に存在する場合は、後勝ちにせずJSON全体を不正とする。
      if (Object.prototype.hasOwnProperty.call(members, key)) {
        throw new JsonSyntaxError("duplicate object member");
      }
      this.skipWhitespace();
      this.expect(":");
      this.skipWhitespace();
      members[key] = this.parseValue();
      this.skipWhitespace();
      // カンマなら次のメンバー、閉じ括弧ならオブジェクトの終わり、それ以外は構文エラー。
      if (this.text[this.position] === ",") {
        this.position += 1;
        continue;
      }
      this.expect("}");
      return members;
    }
  }

  private parseArray(): JsonValue[] {
    this.position += 1;
    const items: JsonValue[] = [];
    this.skipWhitespace();
    if (this.text[this.position] === "]") {
      this.position += 1;
      return items;
    }
    for (;;) {
      this.skipWhitespace();
      items.push(this.parseValue());
      this.skipWhitespace();
      // カンマなら次の要素、閉じ括弧なら配列の終わり、それ以外は構文エラー。
      if (this.text[this.position] === ",") {
        this.position += 1;
        continue;
      }
      this.expect("]");
      return items;
    }
  }

  private parseString(): string {
    this.position += 1;
    let result = "";
    for (;;) {
      const character = this.text[this.position];
      // 閉じる引用符がないまま本文が終わった場合は構文エラー。
      if (character === undefined) throw new JsonSyntaxError("unterminated string");
      // 制御文字は文字列中にそのまま置けない（JSON仕様）。
      if (character < " ") throw new JsonSyntaxError("control character");
      this.position += 1;
      if (character === '"') return result;
      if (character !== "\\") {
        result += character;
        continue;
      }
      const escape = this.text[this.position];
      this.position += 1;
      if (escape === "u") {
        const hex = this.text.slice(this.position, this.position + 4);
        // \u の後は16進4桁でなければならない。
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new JsonSyntaxError("unicode escape");
        result += String.fromCharCode(Number.parseInt(hex, 16));
        this.position += 4;
      } else if (escape !== undefined && escape in ESCAPES) {
        result += ESCAPES[escape];
      } else {
        throw new JsonSyntaxError("escape");
      }
    }
  }

  private parseNumber(): JsonNumber {
    NUMBER_PATTERN.lastIndex = this.position;
    const match = NUMBER_PATTERN.exec(this.text);
    // JSONの数値表記に一致しなければ、どの値の開始にも当たらない構文エラー。
    if (!match) throw new JsonSyntaxError("value");
    this.position += match[0].length;
    return new JsonNumber(match[0]);
  }

  private parseKeyword<T extends JsonValue>(keyword: string, value: T): T {
    if (!this.text.startsWith(keyword, this.position)) throw new JsonSyntaxError("keyword");
    this.position += keyword.length;
    return value;
  }

  private expect(character: string): void {
    if (this.text[this.position] !== character) throw new JsonSyntaxError(`expected ${character}`);
    this.position += 1;
  }

  private skipWhitespace(): void {
    while (WHITESPACE.has(this.text[this.position] ?? "")) this.position += 1;
  }
}
