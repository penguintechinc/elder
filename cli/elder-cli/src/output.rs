//! Structured output formatting (`json`, `table`, `yaml`, `raw`).
//!
//! All structured command output is written to the provided `stdout` writer;
//! diagnostic logs and interactive prompts are written strictly to `stderr`.

use std::collections::BTreeSet;
use std::fmt;
use std::io::Write;
use std::str::FromStr;

use comfy_table::{presets::UTF8_FULL_CONDENSED, ContentArrangement, Table};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{CliError, Result};

/// Maximum number of columns rendered in auto-generated table views.
const MAX_TABLE_COLUMNS: usize = 8;

/// Maximum cell length before truncation in table output.
const MAX_CELL_CHARS: usize = 64;

/// Preferred columns surfaced first when rendering arrays of JSON objects as tables.
const PREFERRED_COLUMNS: &[&str] = &[
    "id",
    "execution_id",
    "function_id",
    "village_id",
    "service",
    "operation",
    "method",
    "path",
    "name",
    "worker_type",
    "runtime",
    "title",
    "slug",
    "status",
    "cluster",
    "last_heartbeat_at",
    "hostname",
    "version",
    "token_prefix",
    "trigger_type",
    "is_enabled",
    "storage_backend",
    "storage_key",
    "code_sha256",
    "exit_code",
    "duration_ms",
    "type",
    "kind",
    "email",
    "username",
    "summary",
    "created_at",
    "updated_at",
];

/// Supported CLI output formats.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OutputFormat {
    /// Pretty-printed JSON.
    Json,
    /// Human-readable UTF-8 table.
    Table,
    /// YAML document.
    Yaml,
    /// Raw string or compact JSON value.
    Raw,
}

impl OutputFormat {
    /// Return the canonical lowercase format name.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Json => "json",
            Self::Table => "table",
            Self::Yaml => "yaml",
            Self::Raw => "raw",
        }
    }
}

impl fmt::Display for OutputFormat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for OutputFormat {
    type Err = CliError;

    fn from_str(s: &str) -> Result<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "json" => Ok(Self::Json),
            "table" => Ok(Self::Table),
            "yaml" | "yml" => Ok(Self::Yaml),
            "raw" => Ok(Self::Raw),
            other => Err(CliError::Usage(format!(
                "unsupported output format {other:?}; expected one of: json, table, yaml, raw"
            ))),
        }
    }
}

/// Format `value` into a string using `format`.
pub fn format_value(value: &Value, format: OutputFormat) -> Result<String> {
    match format {
        OutputFormat::Json => {
            serde_json::to_string_pretty(value).map_err(|e| CliError::Format(e.to_string()))
        }
        OutputFormat::Yaml => {
            serde_yaml::to_string(value).map_err(|e| CliError::Format(e.to_string()))
        }
        OutputFormat::Raw => format_raw(value),
        OutputFormat::Table => Ok(format_table(value)),
    }
}

/// Write a formatted value to `writer` followed by a trailing newline when non-empty.
pub fn write_output<W: Write>(writer: &mut W, value: &Value, format: OutputFormat) -> Result<()> {
    let rendered = format_value(value, format)?;
    if rendered.is_empty() {
        return Ok(());
    }
    if rendered.ends_with('\n') {
        writer.write_all(rendered.as_bytes())?;
    } else {
        writeln!(writer, "{rendered}")?;
    }
    Ok(())
}

fn format_raw(value: &Value) -> Result<String> {
    match value {
        Value::Null => Ok(String::new()),
        Value::String(s) => Ok(s.clone()),
        Value::Bool(b) => Ok(b.to_string()),
        Value::Number(n) => Ok(n.to_string()),
        Value::Array(_) | Value::Object(_) => {
            serde_json::to_string(value).map_err(|e| CliError::Format(e.to_string()))
        }
    }
}

fn format_table(value: &Value) -> String {
    if let Some(items) = extract_table_rows(value) {
        return render_array_table(items);
    }

    match value {
        Value::Null => "(empty)".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            if map.is_empty() {
                return "(empty)".to_string();
            }
            let mut table = new_table();
            table.set_header(vec!["FIELD", "VALUE"]);
            for (k, v) in map {
                table.add_row(vec![k.clone(), summarize_cell(v)]);
            }
            table.to_string()
        }
        Value::Array(items) => render_array_table(items),
    }
}

fn extract_table_rows(value: &Value) -> Option<&[Value]> {
    match value {
        Value::Array(items) => Some(items.as_slice()),
        Value::Object(map) => {
            for key in ["items", "data", "results", "services", "operations"] {
                if let Some(Value::Array(items)) = map.get(key) {
                    return Some(items.as_slice());
                }
            }
            None
        }
        _ => None,
    }
}

fn render_array_table(items: &[Value]) -> String {
    if items.is_empty() {
        return "(no items)".to_string();
    }

    if items.iter().all(Value::is_object) {
        let columns = select_columns(items);
        if columns.is_empty() {
            return "(empty objects)".to_string();
        }
        let mut table = new_table();
        table.set_header(columns.iter().map(|c| c.to_ascii_uppercase()));
        for item in items {
            let row: Vec<String> = columns
                .iter()
                .map(|col| item.get(col).map(summarize_cell).unwrap_or_default())
                .collect();
            table.add_row(row);
        }
        return table.to_string();
    }

    let mut table = new_table();
    table.set_header(vec!["VALUE"]);
    for item in items {
        table.add_row(vec![summarize_cell(item)]);
    }
    table.to_string()
}

fn select_columns(items: &[Value]) -> Vec<String> {
    let mut all_keys = BTreeSet::new();
    for item in items {
        if let Value::Object(map) = item {
            for k in map.keys() {
                all_keys.insert(k.clone());
            }
        }
    }

    let mut ordered = Vec::new();
    for &preferred in PREFERRED_COLUMNS {
        if all_keys.remove(preferred) {
            ordered.push(preferred.to_string());
        }
    }
    for remaining in all_keys {
        ordered.push(remaining);
    }
    ordered.truncate(MAX_TABLE_COLUMNS);
    ordered
}

fn new_table() -> Table {
    let mut table = Table::new();
    table
        .load_preset(UTF8_FULL_CONDENSED)
        .set_content_arrangement(ContentArrangement::Dynamic);
    table
}

fn summarize_cell(value: &Value) -> String {
    let raw = match value {
        Value::Null => String::new(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.replace('\n', " "),
        Value::Array(arr) => {
            if arr.is_empty() {
                "[]".to_string()
            } else if arr.iter().all(|v| v.is_string() || v.is_number()) {
                arr.iter()
                    .map(|v| match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    })
                    .collect::<Vec<_>>()
                    .join(", ")
            } else {
                format!("[{} items]", arr.len())
            }
        }
        Value::Object(map) => format!("{{{} keys}}", map.len()),
    };

    truncate_chars(&raw, MAX_CELL_CHARS)
}

fn truncate_chars(input: &str, max_chars: usize) -> String {
    let count = input.chars().count();
    if count <= max_chars {
        return input.to_string();
    }
    let prefix: String = input.chars().take(max_chars.saturating_sub(1)).collect();
    format!("{prefix}…")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn formats_json_yaml_raw_and_table() {
        let payload = json!({
            "items": [
                {"id": "1", "name": "Alpha", "status": "active"},
                {"id": "2", "name": "Beta", "status": "paused"}
            ],
            "total": 2
        });

        let json_out = format_value(&payload, OutputFormat::Json).unwrap();
        assert!(json_out.contains("\"Alpha\""));
        assert!(json_out.contains("\"total\": 2"));

        let yaml_out = format_value(&payload, OutputFormat::Yaml).unwrap();
        assert!(yaml_out.contains("name: Alpha"));
        assert!(yaml_out.contains("total: 2"));

        let raw_out = format_value(&json!("hello world"), OutputFormat::Raw).unwrap();
        assert_eq!(raw_out, "hello world");

        let raw_null = format_value(&Value::Null, OutputFormat::Raw).unwrap();
        assert_eq!(raw_null, "");

        let table_out = format_value(&payload, OutputFormat::Table).unwrap();
        assert!(table_out.contains("ID"));
        assert!(table_out.contains("NAME"));
        assert!(table_out.contains("Alpha"));
        assert!(table_out.contains("Beta"));
    }

    #[test]
    fn formats_single_object_as_key_value_table() {
        let payload = json!({"status": "ok", "version": "4.0.0"});
        let table_out = format_value(&payload, OutputFormat::Table).unwrap();
        assert!(table_out.contains("FIELD"));
        assert!(table_out.contains("VALUE"));
        assert!(table_out.contains("4.0.0"));
    }

    #[test]
    fn rejects_invalid_output_format() {
        let err = OutputFormat::from_str("xml").unwrap_err();
        assert_eq!(err.exit_code_u8(), 2);
    }

    #[test]
    fn formats_runs_storage_metadata_in_table_and_json() {
        let payload = json!({
            "items": [{
                "id": 42,
                "tenant_id": 7,
                "village_id": "RUN-42",
                "name": "resize-avatar",
                "description": "Resizes avatars",
                "runtime": "python",
                "code": "print('hi')",
                "is_enabled": true,
                "storage_backend": "s3",
                "storage_key": "runs/7/42/v1/code.py",
                "code_sha256": "deadbeef1234",
                "created_at": "2026-11-20T12:00:00Z"
            }],
            "total": 1,
            "page": 1,
            "per_page": 20,
            "pages": 1
        });

        let items = extract_table_rows(&payload).unwrap();
        let cols = select_columns(items);
        assert!(cols.contains(&"storage_backend".to_string()));
        assert!(cols.contains(&"storage_key".to_string()));
        assert!(cols.contains(&"code_sha256".to_string()));

        let table_out = format_value(&payload, OutputFormat::Table).unwrap();
        assert!(table_out.contains("s3"));
        assert!(table_out.contains("runs/"));
        assert!(table_out.contains("dead"));

        let single_table = format_value(&items[0], OutputFormat::Table).unwrap();
        assert!(single_table.contains("storage_backend"));
        assert!(single_table.contains("storage_key"));
        assert!(single_table.contains("code_sha256"));
        assert!(single_table.contains("runs/7/42/v1/code.py"));
        assert!(single_table.contains("deadbeef1234"));

        let json_out = format_value(&payload, OutputFormat::Json).unwrap();
        assert!(json_out.contains("\"storage_backend\": \"s3\""));
        assert!(json_out.contains("\"storage_key\": \"runs/7/42/v1/code.py\""));
        assert!(json_out.contains("\"code_sha256\": \"deadbeef1234\""));
    }

    #[test]
    fn prioritizes_remote_worker_fleet_columns_in_table_output() {
        let payload = json!({
            "items": [{
                "id": "wrk-01",
                "tenant_id": 7,
                "name": "dal2-runworker-01",
                "worker_type": "runworker",
                "status": "online",
                "cluster": "dal2-alpha",
                "last_heartbeat_at": "2026-11-23T22:00:00Z",
                "hostname": "runworker-pod-01",
                "version": "4.0.12",
                "token_prefix": "ewrk_9a8b",
                "is_enabled": true,
                "created_at": "2026-11-20T12:00:00Z"
            }],
            "total": 1
        });

        let items = extract_table_rows(&payload).unwrap();
        let cols = select_columns(items);
        assert_eq!(
            cols,
            vec![
                "id",
                "name",
                "worker_type",
                "status",
                "cluster",
                "last_heartbeat_at",
                "hostname",
                "version",
            ]
        );

        let without_hostname = json!({
            "items": [{
                "id": "wrk-02",
                "name": "edge-scanner-01",
                "worker_type": "scanner",
                "status": "offline",
                "cluster": "edge-west",
                "token_prefix": "ewrk_1c2d",
                "created_at": "2026-11-20T12:00:00Z"
            }],
            "total": 1
        });
        let items2 = extract_table_rows(&without_hostname).unwrap();
        let cols2 = select_columns(items2);
        assert!(cols2.contains(&"token_prefix".to_string()));

        let table_out = format_value(&payload, OutputFormat::Table).unwrap();
        assert!(table_out.contains("runw"));
        assert!(table_out.contains("dal2"));
        assert!(table_out.contains("4.0"));
    }
}
