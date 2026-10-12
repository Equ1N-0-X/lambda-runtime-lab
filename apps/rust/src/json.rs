//! 重複するオブジェクトメンバーを拒否するJSONデコーダー。
//!
//! `serde_json`は重複キーの最後の値を残すため、独自の値ビジターで検出する。重複は通常の
//! 解析失敗として返し、API層が `invalid_json` へ変換する。

use std::fmt;

use serde::{
    Deserialize, Deserializer,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Map, Number, Value};

pub struct StrictValue(pub Value);

/// 構文不正、UTF-8不正、重複キーのいずれかならNoneを返し、それ以外はJSON値を返す。
pub fn parse(body: &[u8]) -> Option<Value> {
    serde_json::from_slice::<StrictValue>(body)
        .ok()
        .map(|parsed| parsed.0)
}

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictValueVisitor)
    }
}

struct StrictValueVisitor;

impl<'de> Visitor<'de> for StrictValueVisitor {
    type Value = StrictValue;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("a JSON value without duplicate object members")
    }

    fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Null))
    }

    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Bool(value)))
    }

    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Number(value.into())))
    }

    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Number(value.into())))
    }

    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Self::Value, E> {
        Ok(StrictValue(
            Number::from_f64(value).map_or(Value::Null, Value::Number),
        ))
    }

    fn visit_str<E: de::Error>(self, value: &str) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::String(value.to_owned())))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
        let mut items = Vec::with_capacity(access.size_hint().unwrap_or(0));
        while let Some(StrictValue(item)) = access.next_element()? {
            items.push(item);
        }
        Ok(StrictValue(Value::Array(items)))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
        let mut members = Map::new();
        while let Some(key) = access.next_key::<String>()? {
            let StrictValue(value) = access.next_value()?;
            // 同じキーが既に存在する場合は、後勝ちにせずJSON全体を不正として解析を中断する。
            if members.insert(key, value).is_some() {
                return Err(de::Error::custom("duplicate object member"));
            }
        }
        Ok(StrictValue(Value::Object(members)))
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
    }
}
