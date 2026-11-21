//! Request query-parameter and JSON body/field parsing helpers.

use std::fs;
use std::io::Read;

use serde_json::{Map, Value};

use crate::error::{CliError, Result};

/// Parse repeatable `--query key=val` arguments into `(key, value)` pairs.
pub fn parse_query_pairs<I, S>(pairs: I) -> Result<Vec<(String, String)>>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut out = Vec::new();
    for raw in pairs {
        let s = raw.as_ref();
        let Some((key, val)) = s.split_once('=') else {
            return Err(CliError::Usage(format!(
                "invalid --query parameter {s:?}; expected KEY=VALUE"
            )));
        };
        let trimmed_key = key.trim();
        if trimmed_key.is_empty() {
            return Err(CliError::Usage(format!(
                "invalid --query parameter {s:?}; key cannot be empty"
            )));
        }
        out.push((trimmed_key.to_string(), val.to_string()));
    }
    Ok(out)
}

/// Build an optional JSON request body from `--body` (`<json>`, `@path`, or `-`) and `--field key=val`.
pub fn parse_request_body<R, I, S>(
    body_spec: Option<&str>,
    fields: I,
    stdin: &mut R,
) -> Result<Option<Value>>
where
    R: Read,
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut base: Option<Value> = match body_spec {
        None => None,
        Some(spec) => Some(load_body_spec(spec, stdin)?),
    };

    let field_list: Vec<String> = fields.into_iter().map(|s| s.as_ref().to_string()).collect();
    if field_list.is_empty() {
        return Ok(base);
    }

    let map = match base.as_mut() {
        Some(Value::Object(existing)) => existing,
        Some(_) => {
            return Err(CliError::Usage(
                "--field cannot be combined with a non-object --body payload".into(),
            ));
        }
        None => {
            base = Some(Value::Object(Map::new()));
            match base.as_mut() {
                Some(Value::Object(created)) => created,
                _ => unreachable!(),
            }
        }
    };

    for raw in field_list {
        let Some((key, val)) = raw.split_once('=') else {
            return Err(CliError::Usage(format!(
                "invalid --field parameter {raw:?}; expected KEY=VALUE"
            )));
        };
        let trimmed_key = key.trim();
        if trimmed_key.is_empty() {
            return Err(CliError::Usage(format!(
                "invalid --field parameter {raw:?}; key cannot be empty"
            )));
        }
        map.insert(trimmed_key.to_string(), parse_field_value(val));
    }

    Ok(base)
}

fn load_body_spec<R: Read>(spec: &str, stdin: &mut R) -> Result<Value> {
    let raw_json = if spec == "-" {
        let mut buf = String::new();
        stdin.read_to_string(&mut buf)?;
        buf
    } else if let Some(path) = spec.strip_prefix('@') {
        if path.trim().is_empty() {
            return Err(CliError::Usage(
                "invalid --body '@': file path cannot be empty".into(),
            ));
        }
        fs::read_to_string(path)
            .map_err(|e| CliError::Usage(format!("failed to read --body file '{path}': {e}")))?
    } else {
        spec.to_string()
    };

    serde_json::from_str(&raw_json)
        .map_err(|e| CliError::Usage(format!("invalid JSON in --body: {e}")))
}

/// Auto-parse a `--field` value as boolean, null, integer, float, JSON array/object, or string.
pub fn parse_field_value(raw: &str) -> Value {
    let trimmed = raw.trim();
    match trimmed {
        "true" => return Value::Bool(true),
        "false" => return Value::Bool(false),
        "null" => return Value::Null,
        _ => {}
    }

    if let Ok(i) = trimmed.parse::<i64>() {
        return Value::Number(i.into());
    }
    if let Ok(u) = trimmed.parse::<u64>() {
        return Value::Number(u.into());
    }
    if trimmed.contains('.') || trimmed.contains('e') || trimmed.contains('E') {
        if let Ok(f) = trimmed.parse::<f64>() {
            if let Some(num) = serde_json::Number::from_f64(f) {
                return Value::Number(num);
            }
        }
    }
    if (trimmed.starts_with('[') && trimmed.ends_with(']'))
        || (trimmed.starts_with('{') && trimmed.ends_with('}'))
    {
        if let Ok(parsed) = serde_json::from_str::<Value>(trimmed) {
            return parsed;
        }
    }

    Value::String(raw.to_string())
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use serde_json::json;
    use tempfile::NamedTempFile;

    use super::*;

    #[test]
    fn parses_query_pairs_and_rejects_malformed() {
        let pairs = parse_query_pairs(["page=2", "q=hello=world"]).unwrap();
        assert_eq!(
            pairs,
            vec![
                ("page".into(), "2".into()),
                ("q".into(), "hello=world".into())
            ]
        );
        assert!(parse_query_pairs(["missing_equals"]).is_err());
        assert!(parse_query_pairs(["=value"]).is_err());
    }

    #[test]
    fn parses_inline_file_stdin_and_field_bodies() {
        let mut empty = Cursor::new(Vec::<u8>::new());
        let inline = parse_request_body(
            Some(r#"{"title":"Bug"}"#),
            ["priority=1", "open=true", "tags=[\"a\",\"b\"]"],
            &mut empty,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            inline,
            json!({"title": "Bug", "priority": 1, "open": true, "tags": ["a", "b"]})
        );

        let file = NamedTempFile::new().unwrap();
        fs::write(file.path(), r#"{"from_file":true}"#).unwrap();
        let spec = format!("@{}", file.path().display());
        let from_file = parse_request_body(Some(&spec), Vec::<&str>::new(), &mut empty)
            .unwrap()
            .unwrap();
        assert_eq!(from_file, json!({"from_file": true}));

        let mut stdin = Cursor::new(br#"{"from_stdin":42}"#.to_vec());
        let from_stdin = parse_request_body(Some("-"), Vec::<&str>::new(), &mut stdin)
            .unwrap()
            .unwrap();
        assert_eq!(from_stdin, json!({"from_stdin": 42}));
    }

    #[test]
    fn auto_parses_field_primitives_and_keeps_strings() {
        assert_eq!(parse_field_value("true"), json!(true));
        assert_eq!(parse_field_value("false"), json!(false));
        assert_eq!(parse_field_value("null"), Value::Null);
        assert_eq!(parse_field_value("42"), json!(42));
        assert_eq!(parse_field_value("3.5"), json!(3.5));
        assert_eq!(parse_field_value(r#"{"nested":1}"#), json!({"nested": 1}));
        assert_eq!(parse_field_value("plain text"), json!("plain text"));
    }
}
