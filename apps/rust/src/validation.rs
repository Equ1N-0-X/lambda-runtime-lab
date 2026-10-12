//! 共通API契約に従い、デコード済みJSONを検証する。
//!
//! 不正入力で返すフィールド位置と理由は契約で固定されているため、serdeの自動エラーへ
//! 委譲せず、Python実装と同じ順序で検査する。

use serde_json::{Map, Value};

use crate::{
    error::{AppError, ErrorDetail},
    model::{AggregateRequest, ComputeRequest, TransformRecord, TransformRequest},
};

pub const MAX_RECORDS: usize = 1_000;
pub const MAX_TAGS: usize = 32;
pub const MAX_IDS: usize = 100;
pub const MAX_ID_CHARS: usize = 128;
pub const MAX_NAME_CHARS: usize = 256;
pub const MAX_TAG_CHARS: usize = 64;
pub const MAX_SEED_CHARS: usize = 256;
pub const MAX_COMPUTE_ITEMS: i64 = 1_000;
pub const MAX_COMPUTE_ITERATIONS: i64 = 1_000_000;
pub const MAX_COMPUTE_WORK: i64 = 1_000_000;
pub const MIN_ITEM_VALUE: i64 = -1_000_000_000;
pub const MAX_ITEM_VALUE: i64 = 1_000_000_000;

pub const COMPUTE_PROFILES: [&str; 1] = ["sequential"];
pub const AGGREGATE_PROFILES: [&str; 2] = ["sequential", "concurrent"];

pub const BENCHMARK_RUN_ID_FIELD: &str = "headers.X-Benchmark-Run-Id";

const ITEM_ID: StringSpec = StringSpec {
    min_length: 1,
    max_length: MAX_ID_CHARS,
    pattern: Pattern::ItemId,
};
const RECORD_NAME: StringSpec = StringSpec {
    min_length: 0,
    max_length: MAX_NAME_CHARS,
    pattern: Pattern::PrintableAscii,
};
const TAG: StringSpec = StringSpec {
    min_length: 0,
    max_length: MAX_TAG_CHARS,
    pattern: Pattern::PrintableAscii,
};
const SEED: StringSpec = StringSpec {
    min_length: 1,
    max_length: MAX_SEED_CHARS,
    pattern: Pattern::Any,
};
const PROFILE: StringSpec = StringSpec {
    min_length: 0,
    max_length: MAX_SEED_CHARS,
    pattern: Pattern::Any,
};

#[derive(Clone, Copy)]
enum Pattern {
    Any,
    ItemId,
    PrintableAscii,
}

impl Pattern {
    fn matches(self, text: &str) -> bool {
        // パターン種別に応じて、無条件許可・ID形式・表示可能ASCIIだけのいずれかを判定する。
        match self {
            Self::Any => true,
            Self::ItemId => {
                let mut characters = text.chars();
                characters
                    .next()
                    .is_some_and(|first| first.is_ascii_alphanumeric())
                    && characters.all(|character| {
                        character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
                    })
            }
            Self::PrintableAscii => text
                .chars()
                .all(|character| (' '..='~').contains(&character)),
        }
    }
}

struct StringSpec {
    min_length: usize,
    max_length: usize,
    pattern: Pattern,
}

pub fn transform_request(document: &Value) -> Result<TransformRequest, AppError> {
    // ルートがオブジェクトでない場合は各項目を読めないため、直ちにルートの検証エラーを返す。
    let mut details = Vec::new();
    let Some(root) = object(document, "$", &mut details) else {
        return Err(AppError::validation(details));
    };
    reject_unknown_members(root, &["records"], "", &mut details);

    let mut records = Vec::new();
    let mut record_count = 0;
    // recordsが配列かつ件数範囲内の場合だけ、各要素をレコードとして検証・変換する。
    if let Some(items) = array_member(root, "", "records", 1, MAX_RECORDS, &mut details) {
        record_count = items.len();
        for (index, item) in items.iter().enumerate() {
            // 個別要素が全項目の検証に成功した場合だけ、変換済みレコードへ追加する。
            if let Some(record) = transform_record(item, index, &mut details) {
                records.push(record);
            }
        }
    }

    // 項目エラーが一件でもある、または変換成功数が入力件数と異なる場合は成功要求を作らない。
    if !details.is_empty() || records.len() != record_count {
        return Err(AppError::validation(details));
    }
    Ok(TransformRequest { records })
}

pub fn compute_request(document: &Value) -> Result<ComputeRequest, AppError> {
    let mut details = Vec::new();

    // ルートがオブジェクトでない場合は各計算項目を読めないため、ルートの検証エラーを返す。
    let Some(root) = object(document, "$", &mut details) else {
        return Err(AppError::validation(details));
    };
    reject_unknown_members(
        root,
        &["items", "iterations", "seed", "profile"],
        "",
        &mut details,
    );

    let items = integer_member(root, "", "items", 1, MAX_COMPUTE_ITEMS, &mut details);
    let iterations = integer_member(
        root,
        "",
        "iterations",
        1,
        MAX_COMPUTE_ITERATIONS,
        &mut details,
    );
    let seed = string_member(root, "", "seed", &SEED, &mut details);
    let profile = string_member(root, "", "profile", &PROFILE, &mut details);

    // 四つの必須項目がすべて取得でき、追加の検証エラーもない場合だけ計算要求を確定する。
    match (items, iterations, seed, profile) {
        (Some(items), Some(iterations), Some(seed), Some(profile)) if details.is_empty() => {
            Ok(ComputeRequest {
                items,
                iterations,
                seed,
                profile,
            })
        }
        _ => Err(AppError::validation(details)),
    }
}

pub fn aggregate_request(document: &Value) -> Result<AggregateRequest, AppError> {
    let mut details = Vec::new();

    // ルートがオブジェクトでない場合はID一覧とprofileを読めないため、ルートの検証エラーを返す。
    let Some(root) = object(document, "$", &mut details) else {
        return Err(AppError::validation(details));
    };
    reject_unknown_members(root, &["ids", "profile"], "", &mut details);

    let mut ids = Vec::new();
    let mut id_count = 0;
    // idsが配列かつ件数範囲内の場合だけ、各要素をIDとして検証・変換する。
    if let Some(items) = array_member(root, "", "ids", 1, MAX_IDS, &mut details) {
        id_count = items.len();
        for (index, item) in items.iter().enumerate() {
            // 個別要素がIDの文字数・文字種を満たす場合だけ、検証済みID一覧へ追加する。
            if let Some(id) = string_value(item, &format!("ids[{index}]"), &ITEM_ID, &mut details) {
                ids.push(id);
            }
        }
    }
    let profile = string_member(root, "", "profile", &PROFILE, &mut details);

    // profileが取得でき、全IDの変換にも失敗していない場合だけ集約要求を確定する。
    match profile {
        Some(profile) if details.is_empty() && ids.len() == id_count => {
            Ok(AggregateRequest { ids, profile })
        }
        _ => Err(AppError::validation(details)),
    }
}

pub fn item_id(id: &str) -> Result<(), AppError> {
    checked_string(id, "id", &ITEM_ID)
}

/// 計測相関ヘッダーはBenchmark Runnerの実行IDと同じ文字種を受け付けるため、UUIDだけでなく
/// 人が読める実行IDも使用できる。
pub fn benchmark_run_id(run_id: &str) -> Result<(), AppError> {
    checked_string(run_id, BENCHMARK_RUN_ID_FIELD, &ITEM_ID)
}

fn checked_string(value: &str, field: &str, spec: &StringSpec) -> Result<(), AppError> {
    let mut details = Vec::new();
    string_value(&Value::String(value.to_owned()), field, spec, &mut details);
    // 文字列検証で詳細が一件もなければ成功とし、一件でもあれば422エラーへ変換する。
    if details.is_empty() {
        Ok(())
    } else {
        Err(AppError::validation(details))
    }
}

pub fn transform_semantics(request: &TransformRequest) -> Result<(), AppError> {
    let details = duplicate_details(
        request.records.iter().map(|record| record.id.as_str()),
        |index| format!("records[{index}].id"),
    );
    // 重複IDがなければ成功とし、一件でもあれば重複位置をまとめた422エラーを返す。
    if details.is_empty() {
        Ok(())
    } else {
        Err(AppError::validation(details))
    }
}

pub fn compute_semantics(request: &ComputeRequest) -> Result<(), AppError> {
    // 項目数と反復回数の積が計測用上限を超える場合は、過度なCPU消費を防ぐため拒否する。
    if request.items.saturating_mul(request.iterations) > MAX_COMPUTE_WORK {
        return Err(AppError::validation(vec![ErrorDetail::new(
            "$",
            format!("maximum_work:{MAX_COMPUTE_WORK}"),
        )]));
    }
    // プロファイル名が計算APIで許可した方式に含まれない場合は、400エラーを返す。
    if !COMPUTE_PROFILES.contains(&request.profile.as_str()) {
        return Err(AppError::unsupported_profile(&COMPUTE_PROFILES));
    }
    Ok(())
}

pub fn aggregate_semantics(request: &AggregateRequest) -> Result<(), AppError> {
    let details = duplicate_details(request.ids.iter().map(String::as_str), |index| {
        format!("ids[{index}]")
    });
    // 同じIDを複数回集約すると二重計上になるため、重複が一件でもあれば拒否する。
    if !details.is_empty() {
        return Err(AppError::validation(details));
    }
    // プロファイル名が直列・並行の許可方式に含まれない場合は、400エラーを返す。
    if !AGGREGATE_PROFILES.contains(&request.profile.as_str()) {
        return Err(AppError::unsupported_profile(&AGGREGATE_PROFILES));
    }
    Ok(())
}

fn duplicate_details<'a>(
    values: impl Iterator<Item = &'a str>,
    field: impl Fn(usize) -> String,
) -> Vec<ErrorDetail> {
    let mut seen = std::collections::HashSet::new();
    values
        .enumerate()
        .filter(|(_, value)| !seen.insert(*value))
        .map(|(index, _)| ErrorDetail::new(field(index), "duplicate"))
        .collect()
}

fn transform_record(
    document: &Value,
    index: usize,
    details: &mut Vec<ErrorDetail>,
) -> Option<TransformRecord> {
    let prefix = format!("records[{index}]");
    let record = object(document, &prefix, details)?;
    reject_unknown_members(record, &["id", "name", "tags", "value"], &prefix, details);

    let id = string_member(record, &prefix, "id", &ITEM_ID, details);
    let name = string_member(record, &prefix, "name", &RECORD_NAME, details);
    let tags = tag_member(record, &prefix, details);
    let value = integer_member(
        record,
        &prefix,
        "value",
        MIN_ITEM_VALUE,
        MAX_ITEM_VALUE,
        details,
    );

    Some(TransformRecord {
        id: id?,
        name: name?,
        tags: tags?,
        value: value?,
    })
}

fn tag_member(
    record: &Map<String, Value>,
    prefix: &str,
    details: &mut Vec<ErrorDetail>,
) -> Option<Vec<String>> {
    let items = array_member(record, prefix, "tags", 0, MAX_TAGS, details)?;
    let mut tags = Vec::with_capacity(items.len());
    for (index, item) in items.iter().enumerate() {
        // 個別要素がタグの文字数・文字種を満たす場合だけ、検証済みタグ一覧へ追加する。
        if let Some(tag) = string_value(item, &format!("{prefix}.tags[{index}]"), &TAG, details) {
            tags.push(tag);
        }
    }
    (tags.len() == items.len()).then_some(tags)
}

fn object<'a>(
    document: &'a Value,
    field: &str,
    details: &mut Vec<ErrorDetail>,
) -> Option<&'a Map<String, Value>> {
    // JSON値がオブジェクトならメンバーを返し、それ以外は対象フィールドの型不正を記録する。
    match document {
        Value::Object(members) => Some(members),
        _ => {
            details.push(ErrorDetail::new(field, "type:object"));
            None
        }
    }
}

fn reject_unknown_members(
    members: &Map<String, Value>,
    allowed: &[&str],
    prefix: &str,
    details: &mut Vec<ErrorDetail>,
) {
    for key in members.keys() {
        // キーが許可リストにない場合は、未定義の入力項目として検証エラーへ追加する。
        if !allowed.contains(&key.as_str()) {
            details.push(ErrorDetail::new(
                field_path(prefix, key),
                "additional_property_not_allowed",
            ));
        }
    }
}

fn field_path(prefix: &str, name: &str) -> String {
    // ルート項目なら名前だけ、入れ子項目なら親パスとドットで連結してエラー位置を表す。
    if prefix.is_empty() {
        name.to_owned()
    } else {
        format!("{prefix}.{name}")
    }
}

fn member<'a>(
    members: &'a Map<String, Value>,
    prefix: &str,
    name: &str,
    details: &mut Vec<ErrorDetail>,
) -> Option<&'a Value> {
    // 指定名のメンバーがあれば値を返し、なければ必須項目不足として記録する。
    match members.get(name) {
        Some(value) => Some(value),
        None => {
            details.push(ErrorDetail::new(field_path(prefix, name), "required"));
            None
        }
    }
}

fn string_member(
    members: &Map<String, Value>,
    prefix: &str,
    name: &str,
    spec: &StringSpec,
    details: &mut Vec<ErrorDetail>,
) -> Option<String> {
    let value = member(members, prefix, name, details)?;
    string_value(value, &field_path(prefix, name), spec, details)
}

fn string_value(
    document: &Value,
    field: &str,
    spec: &StringSpec,
    details: &mut Vec<ErrorDetail>,
) -> Option<String> {
    let Value::String(text) = document else {
        details.push(ErrorDetail::new(field, "type:string"));
        return None;
    };

    let length = text.chars().count();
    // 文字数が最小値未満、最大値超過、またはパターン不一致なら、その最初の理由を記録して失敗する。
    if length < spec.min_length {
        details.push(ErrorDetail::new(
            field,
            format!("min_length:{}", spec.min_length),
        ));
        return None;
    }
    if length > spec.max_length {
        details.push(ErrorDetail::new(
            field,
            format!("max_length:{}", spec.max_length),
        ));
        return None;
    }
    if !spec.pattern.matches(text) {
        details.push(ErrorDetail::new(field, "pattern"));
        return None;
    }
    Some(text.clone())
}

fn integer_member(
    members: &Map<String, Value>,
    prefix: &str,
    name: &str,
    minimum: i64,
    maximum: i64,
    details: &mut Vec<ErrorDetail>,
) -> Option<i64> {
    let field = field_path(prefix, name);
    let value = member(members, prefix, name, details)?;

    let Value::Number(number) = value else {
        details.push(ErrorDetail::new(field, "type:integer"));
        return None;
    };

    // i64へ変換できる値は上下限と比較し、変換不能な数値は表記から型不正か範囲外かを判定する。
    match number.as_i64() {
        Some(number) if number < minimum => {
            details.push(ErrorDetail::new(field, format!("minimum:{minimum}")));
            None
        }
        Some(number) if number > maximum => {
            details.push(ErrorDetail::new(field, format!("maximum:{maximum}")));
            None
        }
        Some(number) => Some(number),
        None => {
            // i64範囲外でも小数点・指数表記がなければJSON整数であるため、型不正ではなく
            // 上限または下限違反として報告する。
            let literal = number.to_string();

            // 小数・指数表記なら整数型不正、負の巨大整数なら下限、正の巨大整数なら上限違反にする。
            let reason = if literal.contains(['.', 'e', 'E']) {
                "type:integer".to_owned()
            } else if literal.starts_with('-') {
                format!("minimum:{minimum}")
            } else {
                format!("maximum:{maximum}")
            };
            details.push(ErrorDetail::new(field, reason));
            None
        }
    }
}

fn array_member<'a>(
    members: &'a Map<String, Value>,
    prefix: &str,
    name: &str,
    min_items: usize,
    max_items: usize,
    details: &mut Vec<ErrorDetail>,
) -> Option<&'a Vec<Value>> {
    let field = field_path(prefix, name);
    let value = member(members, prefix, name, details)?;

    let Value::Array(items) = value else {
        details.push(ErrorDetail::new(field, "type:array"));
        return None;
    };
    // 配列件数が最小値未満または最大値超過なら、要素を処理せず配列全体の検証エラーにする。
    if items.len() < min_items {
        details.push(ErrorDetail::new(field, format!("min_items:{min_items}")));
        return None;
    }
    if items.len() > max_items {
        details.push(ErrorDetail::new(field, format!("max_items:{max_items}")));
        return None;
    }
    Some(items)
}
