//! Source-code endpoint parsers for Flask, FastAPI, Django, Express, and Go.

use std::collections::HashMap;
use std::path::Path;

use regex::Regex;
use serde::{Deserialize, Serialize};

/// Discovered API endpoint serialized for `hub-api` SBOM scan submission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiscoveredEndpoint {
    pub framework: String,
    pub method: String,
    pub path: String,
    pub line_number: usize,
    pub source_file: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub function_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auth_required: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub handler_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub has_middleware: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub middleware: Option<Vec<String>>,
}

/// Parse source files across supported frameworks to extract REST/HTTP endpoints.
pub fn parse_endpoint_file(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let ext = Path::new(rel_path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    match ext.as_str() {
        "py" => {
            let mut out = Vec::new();
            if is_fastapi_file(content) {
                out.extend(parse_fastapi(rel_path, content));
            }
            if is_flask_file(content) {
                out.extend(parse_flask(rel_path, content));
            }
            if is_django_file(rel_path, content) {
                out.extend(parse_django(rel_path, content));
            }
            out
        }
        "js" | "ts" | "mjs" | "cjs" => {
            if is_express_file(content) {
                parse_express(rel_path, content)
            } else {
                Vec::new()
            }
        }
        "go" => {
            if is_go_web_file(content) {
                parse_go(rel_path, content)
            } else {
                Vec::new()
            }
        }
        _ => Vec::new(),
    }
}

fn line_at_offset(src: &str, offset: usize) -> usize {
    src[..offset.min(src.len())]
        .bytes()
        .filter(|&b| b == b'\n')
        .count()
        + 1
}

fn normalize_flask_or_django_params(path: &str) -> String {
    let re =
        Regex::new(r"<(?:[A-Za-z_][A-Za-z0-9_]*:)?([A-Za-z_][A-Za-z0-9_]*)>").expect("valid regex");
    re.replace_all(path, "{$1}").into_owned()
}

fn normalize_colon_params(path: &str) -> String {
    let re = Regex::new(r":([A-Za-z_][A-Za-z0-9_]*)").expect("valid regex");
    re.replace_all(path, "{$1}").into_owned()
}

fn normalize_regex_brace_params(path: &str) -> String {
    let named_re = Regex::new(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>[^)]+\)").expect("valid regex");
    let step1 = named_re.replace_all(path, "{$1}");
    let brace_re = Regex::new(r"\{([A-Za-z_][A-Za-z0-9_]*):[^}]+\}").expect("valid regex");
    brace_re.replace_all(&step1, "{$1}").into_owned()
}

// ---------------------------------------------------------------------------
// Flask
// ---------------------------------------------------------------------------

fn is_flask_file(content: &str) -> bool {
    content.contains("flask")
        || content.contains("Blueprint(")
        || content.contains("@bp.route")
        || content.contains("@app.route")
}

fn parse_flask(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let lines: Vec<&str> = content.lines().collect();
    let route_re = Regex::new(
        r#"^\s*@[A-Za-z_][A-Za-z0-9_]*\.(route|get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]([^)]*)\)"#,
    )
    .expect("valid regex");
    let methods_re = Regex::new(r#"methods\s*=\s*\[([^\]]+)\]"#).expect("valid regex");
    let def_re =
        Regex::new(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(").expect("valid regex");
    let add_res_re =
        Regex::new(r#"add_resource\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*['"]([^'"]+)['"]"#)
            .expect("valid regex");

    let mut out = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        if let Some(caps) = route_re.captures(line) {
            let verb = &caps[1];
            let raw_path = &caps[2];
            let args_tail = &caps[3];
            let methods: Vec<String> = if verb == "route" {
                if let Some(mc) = methods_re.captures(args_tail) {
                    mc[1]
                        .split(',')
                        .map(|s| {
                            s.trim()
                                .trim_matches(|c| c == '\'' || c == '"')
                                .to_ascii_uppercase()
                        })
                        .filter(|s| !s.is_empty())
                        .collect()
                } else {
                    vec!["GET".to_string()]
                }
            } else {
                vec![verb.to_ascii_uppercase()]
            };

            let (func_name, auth_required) = find_next_py_handler_and_auth(&lines, idx, &def_re);
            let norm_path = normalize_flask_or_django_params(raw_path);
            for method in methods {
                out.push(DiscoveredEndpoint {
                    framework: "flask".to_string(),
                    method,
                    path: norm_path.clone(),
                    line_number: idx + 1,
                    source_file: rel_path.to_string(),
                    function_name: func_name.clone(),
                    auth_required: Some(auth_required),
                    view_name: None,
                    url_name: None,
                    handler_name: None,
                    has_middleware: None,
                    middleware: None,
                });
            }
        } else if let Some(caps) = add_res_re.captures(line) {
            out.push(DiscoveredEndpoint {
                framework: "flask".to_string(),
                method: "ANY".to_string(),
                path: normalize_flask_or_django_params(&caps[2]),
                line_number: idx + 1,
                source_file: rel_path.to_string(),
                function_name: Some(caps[1].to_string()),
                auth_required: Some(false),
                view_name: None,
                url_name: None,
                handler_name: None,
                has_middleware: None,
                middleware: None,
            });
        }
    }
    out
}

fn find_next_py_handler_and_auth(
    lines: &[&str],
    decorator_idx: usize,
    def_re: &Regex,
) -> (Option<String>, bool) {
    let mut auth = false;
    let start = decorator_idx.saturating_sub(5);
    let end = (decorator_idx + 12).min(lines.len());
    for l in &lines[start..end] {
        let lower = l.to_ascii_lowercase();
        if lower.contains("login_required")
            || lower.contains("jwt_required")
            || lower.contains("auth_required")
            || lower.contains("require_scope")
            || lower.contains("role_required")
            || lower.contains("admin_required")
            || lower.contains("permission_required")
        {
            auth = true;
        }
    }
    for l in &lines[decorator_idx..end] {
        if let Some(caps) = def_re.captures(l) {
            if l.contains("Depends(") {
                let lower = l.to_ascii_lowercase();
                if lower.contains("auth")
                    || lower.contains("current_user")
                    || lower.contains("token")
                {
                    auth = true;
                }
            }
            return (Some(caps[1].to_string()), auth);
        }
    }
    (None, auth)
}

// ---------------------------------------------------------------------------
// FastAPI
// ---------------------------------------------------------------------------

fn is_fastapi_file(content: &str) -> bool {
    content.contains("fastapi") || content.contains("APIRouter") || content.contains("FastAPI(")
}

fn parse_fastapi(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let mut prefixes: HashMap<String, String> = HashMap::new();
    let prefix_re = Regex::new(
        r#"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*APIRouter\([^)]*prefix\s*=\s*['"]([^'"]+)['"]"#,
    )
    .expect("valid regex");
    for caps in prefix_re.captures_iter(content) {
        prefixes.insert(
            caps[1].to_string(),
            caps[2].trim_end_matches('/').to_string(),
        );
    }

    let lines: Vec<&str> = content.lines().collect();
    let route_re = Regex::new(
        r#"^\s*@([A-Za-z_][A-Za-z0-9_]*)\.(get|post|put|patch|delete|options|head|api_route)\(\s*['"]([^'"]*)['"]"#,
    )
    .expect("valid regex");
    let def_re =
        Regex::new(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(").expect("valid regex");

    let mut out = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        if let Some(caps) = route_re.captures(line) {
            let obj = &caps[1];
            let verb = &caps[2];
            let raw_path = &caps[3];
            let method = if verb == "api_route" {
                "ANY".to_string()
            } else {
                verb.to_ascii_uppercase()
            };

            let prefix = prefixes.get(obj).map(String::as_str).unwrap_or("");
            let full_path = if prefix.is_empty() {
                raw_path.to_string()
            } else if raw_path.starts_with('/') {
                format!("{prefix}{raw_path}")
            } else {
                format!("{prefix}/{raw_path}")
            };

            let (func_name, mut auth_required) =
                find_next_py_handler_and_auth(&lines, idx, &def_re);
            let sig_window_end = (idx + 18).min(lines.len());
            let sig_block = lines[idx..sig_window_end].join(" ").to_ascii_lowercase();
            if sig_block.contains("depends(")
                && (sig_block.contains("current_user")
                    || sig_block.contains("verify_token")
                    || sig_block.contains("oauth2")
                    || sig_block.contains("security"))
            {
                auth_required = true;
            }

            out.push(DiscoveredEndpoint {
                framework: "fastapi".to_string(),
                method,
                path: full_path,
                line_number: idx + 1,
                source_file: rel_path.to_string(),
                function_name: func_name,
                auth_required: Some(auth_required),
                view_name: None,
                url_name: None,
                handler_name: None,
                has_middleware: None,
                middleware: None,
            });
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Django
// ---------------------------------------------------------------------------

fn is_django_file(rel_path: &str, content: &str) -> bool {
    content.contains("django.urls")
        || content.contains("urlpatterns")
        || content.contains("rest_framework")
        || (rel_path.ends_with("urls.py") && content.contains("path("))
}

fn parse_django(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let path_re = Regex::new(
        r#"(?:re_path|path|url)\(\s*r?['"]([^'"]*)['"]\s*,\s*([^,)]+)(?:\s*,\s*name\s*=\s*['"]([^'"]+)['"])?"#,
    )
    .expect("valid regex");
    let reg_re = Regex::new(r#"router\.register\(\s*r?['"]([^'"]+)['"]\s*,\s*([A-Za-z0-9_.]+)"#)
        .expect("valid regex");

    let mut out = Vec::new();
    for caps in path_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let raw_route = &caps[1];
        if raw_route.contains("include(") || caps[2].contains("include(") {
            continue;
        }
        let cleaned = raw_route.trim_start_matches('^').trim_end_matches('$');
        let with_slash = if cleaned.starts_with('/') {
            cleaned.to_string()
        } else {
            format!("/{cleaned}")
        };
        let norm_path =
            normalize_regex_brace_params(&normalize_flask_or_django_params(&with_slash));
        let view_name = caps[2].trim().trim_end_matches(".as_view()").to_string();
        let url_name = caps.get(3).map(|m| m.as_str().to_string());

        out.push(DiscoveredEndpoint {
            framework: "django".to_string(),
            method: infer_django_method(&view_name, url_name.as_deref()),
            path: norm_path,
            line_number: line_at_offset(content, match_start),
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: Some(view_name),
            url_name,
            handler_name: None,
            has_middleware: None,
            middleware: None,
        });
    }

    for caps in reg_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let prefix = caps[1].trim_matches('/');
        let viewset = caps[2].trim().to_string();
        let line_no = line_at_offset(content, match_start);
        out.push(DiscoveredEndpoint {
            framework: "django".to_string(),
            method: "GET".to_string(),
            path: format!("/{prefix}/"),
            line_number: line_no,
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: Some(viewset.clone()),
            url_name: Some(format!("{prefix}-list")),
            handler_name: None,
            has_middleware: None,
            middleware: None,
        });
        out.push(DiscoveredEndpoint {
            framework: "django".to_string(),
            method: "POST".to_string(),
            path: format!("/{prefix}/"),
            line_number: line_no,
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: Some(viewset),
            url_name: Some(format!("{prefix}-create")),
            handler_name: None,
            has_middleware: None,
            middleware: None,
        });
    }

    out
}

fn infer_django_method(view_name: &str, url_name: Option<&str>) -> String {
    let combined = format!("{view_name} {}", url_name.unwrap_or("")).to_ascii_lowercase();
    if combined.contains("create") || combined.contains("post") {
        "POST".to_string()
    } else if combined.contains("update") || combined.contains("put") {
        "PUT".to_string()
    } else if combined.contains("delete") || combined.contains("destroy") {
        "DELETE".to_string()
    } else {
        "GET".to_string()
    }
}

// ---------------------------------------------------------------------------
// Express.js / TypeScript
// ---------------------------------------------------------------------------

fn is_express_file(content: &str) -> bool {
    content.contains("express")
        || content.contains("router.get")
        || content.contains("router.post")
        || content.contains("app.get(")
        || content.contains("app.post(")
        || content.contains(".route(")
}

fn parse_express(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let direct_re = Regex::new(
        r#"(?:app|router|[A-Za-z_][A-Za-z0-9_]*Router)\.(get|post|put|patch|delete|all)\(\s*['"]([^'"]+)['"]\s*,([^;)\n]+)"#,
    )
    .expect("valid regex");
    let chained_re = Regex::new(
        r#"\.route\(\s*['"]([^'"]+)['"]\s*\)((?:\s*\.(?:get|post|put|patch|delete)\([^)]*\))+)"#,
    )
    .expect("valid regex");
    let method_call_re =
        Regex::new(r"\.(get|post|put|patch|delete)\(\s*([^)]+)\)").expect("valid regex");

    let mut out = Vec::new();
    for caps in direct_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let method = match &caps[1] {
            "all" => "ANY".to_string(),
            other => other.to_ascii_uppercase(),
        };
        let path = normalize_colon_params(&caps[2]);
        let args: Vec<&str> = caps[3]
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect();
        let has_mw = args.len() > 1;
        let handler_name = args.last().and_then(|last| extract_js_handler_name(last));

        out.push(DiscoveredEndpoint {
            framework: "express".to_string(),
            method,
            path,
            line_number: line_at_offset(content, match_start),
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: None,
            url_name: None,
            handler_name,
            has_middleware: Some(has_mw),
            middleware: None,
        });
    }

    for caps in chained_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let path = normalize_colon_params(&caps[1]);
        let chain = &caps[2];
        for mc in method_call_re.captures_iter(chain) {
            let args: Vec<&str> = mc[2]
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .collect();
            let has_mw = args.len() > 1;
            let handler_name = args.last().and_then(|l| extract_js_handler_name(l));
            out.push(DiscoveredEndpoint {
                framework: "express".to_string(),
                method: mc[1].to_ascii_uppercase(),
                path: path.clone(),
                line_number: line_at_offset(content, match_start),
                source_file: rel_path.to_string(),
                function_name: None,
                auth_required: None,
                view_name: None,
                url_name: None,
                handler_name,
                has_middleware: Some(has_mw),
                middleware: None,
            });
        }
    }

    out
}

fn extract_js_handler_name(expr: &str) -> Option<String> {
    let trimmed = expr.trim();
    if trimmed.starts_with('(') || trimmed.starts_with("async") || trimmed.starts_with("function") {
        return Some("anonymous".to_string());
    }
    trimmed
        .split(['(', ' '])
        .next()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

// ---------------------------------------------------------------------------
// Go (Gin, Chi, Gorilla Mux, net/http)
// ---------------------------------------------------------------------------

fn is_go_web_file(content: &str) -> bool {
    content.contains("gin-gonic/gin")
        || content.contains("go-chi/chi")
        || content.contains("gorilla/mux")
        || content.contains("net/http")
        || content.contains(".HandleFunc(")
}

fn parse_go(rel_path: &str, content: &str) -> Vec<DiscoveredEndpoint> {
    let gin_chi_re = Regex::new(
        r#"\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any|Get|Post|Put|Patch|Delete)\(\s*"([^"]+)"\s*,\s*([^)\n]+)\)"#,
    )
    .expect("valid regex");
    let gorilla_re = Regex::new(
        r#"\.HandleFunc\(\s*"([^"]+)"\s*,\s*([A-Za-z0-9_.]+)\s*\)(?:\.Methods\(([^)]+)\))?"#,
    )
    .expect("valid regex");
    let nethttp_re = Regex::new(r#"http\.HandleFunc\(\s*"([^"]+)"\s*,\s*([A-Za-z0-9_.]+)\s*\)"#)
        .expect("valid regex");

    let mut out = Vec::new();
    for caps in gin_chi_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let verb = &caps[1];
        let method = if verb.eq_ignore_ascii_case("any") {
            "ANY".to_string()
        } else {
            verb.to_ascii_uppercase()
        };
        let path = normalize_regex_brace_params(&normalize_colon_params(&caps[2]));
        let parts: Vec<String> = caps[3]
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        let handler_name = parts.last().cloned();
        let middleware = if parts.len() > 1 {
            parts[..parts.len() - 1].to_vec()
        } else {
            Vec::new()
        };

        out.push(DiscoveredEndpoint {
            framework: "go".to_string(),
            method,
            path,
            line_number: line_at_offset(content, match_start),
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: None,
            url_name: None,
            handler_name,
            has_middleware: None,
            middleware: Some(middleware),
        });
    }

    for caps in gorilla_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let path = normalize_regex_brace_params(&caps[1]);
        let handler = caps[2].to_string();
        let methods: Vec<String> = if let Some(m) = caps.get(3) {
            m.as_str()
                .split(',')
                .map(|s| s.trim().trim_matches('"').to_ascii_uppercase())
                .filter(|s| !s.is_empty())
                .collect()
        } else {
            vec!["ANY".to_string()]
        };

        let line_no = line_at_offset(content, match_start);
        for method in methods {
            out.push(DiscoveredEndpoint {
                framework: "go".to_string(),
                method,
                path: path.clone(),
                line_number: line_no,
                source_file: rel_path.to_string(),
                function_name: None,
                auth_required: None,
                view_name: None,
                url_name: None,
                handler_name: Some(handler.clone()),
                has_middleware: None,
                middleware: Some(Vec::new()),
            });
        }
    }

    for caps in nethttp_re.captures_iter(content) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        out.push(DiscoveredEndpoint {
            framework: "go".to_string(),
            method: "ANY".to_string(),
            path: caps[1].to_string(),
            line_number: line_at_offset(content, match_start),
            source_file: rel_path.to_string(),
            function_name: None,
            auth_required: None,
            view_name: None,
            url_name: None,
            handler_name: Some(caps[2].to_string()),
            has_middleware: None,
            middleware: Some(Vec::new()),
        });
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_flask_fastapi_and_django_endpoints() {
        let flask = parse_endpoint_file(
            "routes/users.py",
            "from flask import Blueprint\n@bp.route('/users/<int:user_id>', methods=['GET', 'POST'])\n@login_required\ndef user_detail(user_id):\n    pass\n",
        );
        assert_eq!(flask.len(), 2);
        assert_eq!(flask[0].framework, "flask");
        assert_eq!(flask[0].path, "/users/{user_id}");
        assert_eq!(flask[0].auth_required, Some(true));
        assert_eq!(flask[1].method, "POST");

        let fastapi = parse_endpoint_file(
            "api/items.py",
            "from fastapi import APIRouter, Depends\nrouter = APIRouter(prefix='/api/v1')\n@router.post('/items')\nasync def create_item(user=Depends(get_current_user)):\n    pass\n",
        );
        assert_eq!(fastapi.len(), 1);
        assert_eq!(fastapi[0].path, "/api/v1/items");
        assert_eq!(fastapi[0].method, "POST");
        assert_eq!(fastapi[0].auth_required, Some(true));

        let django = parse_endpoint_file(
            "app/urls.py",
            "from django.urls import path\nurlpatterns = [path('orders/<int:pk>/', views.order_detail, name='order-detail')]\n",
        );
        assert_eq!(django.len(), 1);
        assert_eq!(django[0].path, "/orders/{pk}/");
        assert_eq!(django[0].url_name.as_deref(), Some("order-detail"));
    }

    #[test]
    fn parses_express_and_go_endpoints() {
        let express = parse_endpoint_file(
            "routes/orders.ts",
            "import express from 'express';\nrouter.get('/orders/:orderId', authMiddleware, getOrder);\nrouter.route('/health').get(healthCheck).post(updateHealth);\n",
        );
        assert_eq!(express.len(), 3);
        assert_eq!(express[0].path, "/orders/{orderId}");
        assert_eq!(express[0].has_middleware, Some(true));
        assert_eq!(express[0].handler_name.as_deref(), Some("getOrder"));

        let go = parse_endpoint_file(
            "main.go",
            "package main\nimport \"github.com/gin-gonic/gin\"\nfunc setup(r *gin.Engine) {\n\tr.GET(\"/items/:id\", authMw, getItem)\n}\n",
        );
        assert_eq!(go.len(), 1);
        assert_eq!(go[0].path, "/items/{id}");
        assert_eq!(go[0].handler_name.as_deref(), Some("getItem"));
        assert_eq!(
            go[0].middleware.as_deref(),
            Some(&["authMw".to_string()][..])
        );
    }
}
