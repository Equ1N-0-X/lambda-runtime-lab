//! Canonical request validation.
//!
//! The contract fixes which field paths and reason tokens a rejected request
//! produces, so requests are validated against a decoded JSON document rather
//! than through `serde` derive errors. That keeps failures identical to the
//! Python implementation, including the order in which checks stop.

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
    let mut details = Vec::new();
    let Some(root) = object(document, "$", &mut details) else {
        return Err(AppError::validation(details));
    };
    reject_unknown_members(root, &["records"], "", &mut details);

    let mut records = Vec::new();
    let mut record_count = 0;
    if let Some(items) = array_member(root, "", "records", 1, MAX_RECORDS, &mut details) {
        record_count = items.len();
        for (index, item) in items.iter().enumerate() {
            if let Some(record) = transform_record(item, index, &mut details) {
                records.push(record);
            }
        }
    }

    if !details.is_empty() || records.len() != record_count {
        return Err(AppError::validation(details));
    }
    Ok(TransformRequest { records })
}

pub fn compute_request(document: &Value) -> Result<ComputeRequest, AppError> {
    let mut details = Vec::new();
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
    let Some(root) = object(document, "$", &mut details) else {
        return Err(AppError::validation(details));
    };
    reject_unknown_members(root, &["ids", "profile"], "", &mut details);

    let mut ids = Vec::new();
    let mut id_count = 0;
    if let Some(items) = array_member(root, "", "ids", 1, MAX_IDS, &mut details) {
        id_count = items.len();
        for (index, item) in items.iter().enumerate() {
            if let Some(id) = string_value(item, &format!("ids[{index}]"), &ITEM_ID, &mut details) {
                ids.push(id);
            }
        }
    }
    let profile = string_member(root, "", "profile", &PROFILE, &mut details);

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

/// The correlation header accepts the same characters as a Benchmark Runner run
/// identifier, so readable run IDs work as well as UUIDs.
pub fn benchmark_run_id(run_id: &str) -> Result<(), AppError> {
    checked_string(run_id, BENCHMARK_RUN_ID_FIELD, &ITEM_ID)
}

fn checked_string(value: &str, field: &str, spec: &StringSpec) -> Result<(), AppError> {
    let mut details = Vec::new();
    string_value(&Value::String(value.to_owned()), field, spec, &mut details);
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
    if details.is_empty() {
        Ok(())
    } else {
        Err(AppError::validation(details))
    }
}

pub fn compute_semantics(request: &ComputeRequest) -> Result<(), AppError> {
    if request.items.saturating_mul(request.iterations) > MAX_COMPUTE_WORK {
        return Err(AppError::validation(vec![ErrorDetail::new(
            "$",
            format!("maximum_work:{MAX_COMPUTE_WORK}"),
        )]));
    }
    if !COMPUTE_PROFILES.contains(&request.profile.as_str()) {
        return Err(AppError::unsupported_profile(&COMPUTE_PROFILES));
    }
    Ok(())
}

pub fn aggregate_semantics(request: &AggregateRequest) -> Result<(), AppError> {
    let details = duplicate_details(request.ids.iter().map(String::as_str), |index| {
        format!("ids[{index}]")
    });
    if !details.is_empty() {
        return Err(AppError::validation(details));
    }
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
        if !allowed.contains(&key.as_str()) {
            details.push(ErrorDetail::new(
                field_path(prefix, key),
                "additional_property_not_allowed",
            ));
        }
    }
}

fn field_path(prefix: &str, name: &str) -> String {
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
            // Values outside i64 are still JSON integers when written without a
            // fraction or exponent, so they are reported as range violations
            // rather than type errors.
            let literal = number.to_string();
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
