//! 重複するオブジェクトメンバーを拒否し、数値の表記を失わないJSONデコーダー。
//!
//! `serde_json`は重複キーの最後の値を残すため、独自の値ビジターで検出する。重複は通常の
//! 解析失敗として返し、API層が `invalid_json` へ変換する。
//!
//! また`serde_json`は64bitに収まらない整数の表記を浮動小数として渡すため、そのままでは
//! 「整数だが範囲外」と「小数・指数の表記」を区別できない。正本では前者は範囲の違反、
//! 後者は型の違反なので、本文の数値の表記を先に出現順で読み、ビジターへ一緒に渡す。

use std::cell::RefCell;
use std::collections::VecDeque;
use std::fmt;

use serde::de::{self, DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

/// 本文中の一つの数値の表記。出現順にビジターが一つずつ取り出す。
#[derive(Clone, Copy)]
struct NumberToken {
    /// 小数点・指数がない、JSONの整数の表記か。
    integer: bool,
    negative: bool,
}

/// 構文不正、UTF-8不正、重複キーのいずれかならNoneを返し、それ以外はJSON値を返す。
///
/// 64bitに収まらない整数の表記は、正の値はu64の最大値、負の値はi64の最小値に置き換える。
/// どちらも全ての項目の上限・下限の外にあるため、検証では元の値と同じ範囲の違反になる。
pub fn parse(body: &[u8]) -> Option<Value> {
    let tokens = RefCell::new(number_tokens(body)?);
    let mut deserializer = serde_json::Deserializer::from_slice(body);
    let value = StrictSeed { tokens: &tokens }
        .deserialize(&mut deserializer)
        .ok()?;
    // 値の後に余分な文字があれば、JSON全体としては不正である。
    deserializer.end().ok()?;
    Some(value)
}

/// 文字列の外にある数値の表記を、本文の出現順に読む。UTF-8として読めなければNone。
fn number_tokens(body: &[u8]) -> Option<VecDeque<NumberToken>> {
    let text = std::str::from_utf8(body).ok()?;
    let bytes = text.as_bytes();
    let mut tokens = VecDeque::new();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            // 文字列の中の数字は数値ではないため、閉じる引用符まで読み飛ばす（エスケープを考慮）。
            b'"' => {
                index += 1;
                while index < bytes.len() && bytes[index] != b'"' {
                    index += if bytes[index] == b'\\' { 2 } else { 1 };
                }
                index += 1;
            }
            // 数値は符号か数字で始まり、数字・符号・小数点・指数の文字が続く。
            b'-' | b'0'..=b'9' => {
                let start = index;
                while index < bytes.len()
                    && matches!(bytes[index], b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E')
                {
                    index += 1;
                }
                let literal = &bytes[start..index];
                tokens.push_back(NumberToken {
                    integer: !literal
                        .iter()
                        .any(|byte| matches!(byte, b'.' | b'e' | b'E')),
                    negative: literal[0] == b'-',
                });
            }
            _ => index += 1,
        }
    }
    Some(tokens)
}

/// 数値の表記の列を共有しながら、入れ子の値を読み進める。
#[derive(Clone, Copy)]
struct StrictSeed<'a> {
    tokens: &'a RefCell<VecDeque<NumberToken>>,
}

impl StrictSeed<'_> {
    /// 次の数値の表記を取り出す。数え方の食い違いは解析の失敗として扱う。
    fn next_token<E: de::Error>(&self) -> Result<NumberToken, E> {
        self.tokens
            .borrow_mut()
            .pop_front()
            .ok_or_else(|| de::Error::custom("number token mismatch"))
    }
}

impl<'de> DeserializeSeed<'de> for StrictSeed<'_> {
    type Value = Value;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Value, D::Error> {
        deserializer.deserialize_any(self)
    }
}

impl<'de> Visitor<'de> for StrictSeed<'_> {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("a JSON value without duplicate object members")
    }

    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Value, E> {
        self.next_token::<E>()?;
        Ok(Value::Number(value.into()))
    }

    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Value, E> {
        self.next_token::<E>()?;
        Ok(Value::Number(value.into()))
    }

    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        let token = self.next_token::<E>()?;
        // 整数の表記なのに浮動小数で渡されたのは、64bitに収まらない整数である。
        // 型の違反ではなく範囲の違反として検証されるよう、範囲外の整数へ置き換える。
        if token.integer {
            return Ok(Value::Number(if token.negative {
                i64::MIN.into()
            } else {
                u64::MAX.into()
            }));
        }
        Ok(Number::from_f64(value).map_or(Value::Null, Value::Number))
    }

    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
        let mut items = Vec::with_capacity(access.size_hint().unwrap_or(0));
        while let Some(item) = access.next_element_seed(self)? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
        let mut members = Map::new();
        while let Some(key) = access.next_key::<String>()? {
            let value = access.next_value_seed(self)?;
            // 同じキーが既に存在する場合は、後勝ちにせずJSON全体を不正として解析を中断する。
            if members.insert(key, value).is_some() {
                return Err(de::Error::custom("duplicate object member"));
            }
        }
        Ok(Value::Object(members))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_documents_are_parsed() {
        let value = parse(br#"{"records":[{"id":"a","tags":["x"],"value":1}]}"#).unwrap();

        assert_eq!(value["records"][0]["id"], "a");
        assert_eq!(value["records"][0]["value"], 1);
    }

    #[test]
    fn duplicate_members_are_rejected_at_any_depth() {
        assert!(parse(br#"{"items":1,"items":2}"#).is_none());
        assert!(parse(br#"{"records":[{"id":"a","id":"b"}]}"#).is_none());
    }

    #[test]
    fn malformed_documents_are_rejected() {
        assert!(parse(br#"{"items":"#).is_none());
        assert!(parse(&[0xff, 0xfe]).is_none());
        assert!(parse(br#"{"items":1} x"#).is_none());
    }

    #[test]
    fn integers_beyond_64_bits_stay_integers_outside_every_range() {
        let value =
            parse(br#"{"a":18446744073709551616,"b":-9223372036854775809,"c":1.5}"#).unwrap();

        assert_eq!(value["a"].as_u64(), Some(u64::MAX));
        assert_eq!(value["b"].as_i64(), Some(i64::MIN));
        assert!(value["c"].is_f64());
    }

    #[test]
    fn digits_inside_strings_are_not_number_tokens() {
        let value = parse(br#"{"s":"1e5 \" 2","n":18446744073709551616}"#).unwrap();

        assert_eq!(value["s"], "1e5 \" 2");
        assert_eq!(value["n"].as_u64(), Some(u64::MAX));
    }
}
