//! Native SBOM dependency parsers across Python, Node.js, Go, Rust, Java, .NET, and Ruby.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use regex::Regex;
use roxmltree::Document;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Parsed software dependency component matching `hub-api`'s `/sbom/scans/<id>/results` schema.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SbomComponent {
    pub name: String,
    pub version: Option<String>,
    pub purl: Option<String>,
    pub package_type: String,
    pub scope: String,
    pub direct: bool,
    pub source_file: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub license_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
}

/// Dispatch dependency file parsing based on normalized filename / relative path.
pub fn parse_dependency_file(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let filename = Path::new(rel_path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(rel_path);
    let lower = filename.to_ascii_lowercase();

    if lower == "package.json" {
        return parse_package_json(rel_path, content);
    }
    if lower == "package-lock.json" {
        return parse_package_lock_json(rel_path, content);
    }
    if lower == "yarn.lock" {
        return parse_yarn_lock(rel_path, content);
    }
    if lower == "pnpm-lock.yaml" {
        return parse_pnpm_lock_yaml(rel_path, content);
    }
    if lower == "go.mod" {
        return parse_go_mod(rel_path, content);
    }
    if lower == "go.sum" {
        return parse_go_sum(rel_path, content);
    }
    if lower == "cargo.toml" {
        return parse_cargo_toml(rel_path, content);
    }
    if lower == "cargo.lock" {
        return parse_cargo_lock(rel_path, content);
    }
    if lower == "pom.xml" {
        return parse_pom_xml(rel_path, content);
    }
    if lower == "build.gradle" || lower == "build.gradle.kts" {
        return parse_gradle(rel_path, content);
    }
    if lower.ends_with(".csproj") {
        return parse_csproj(rel_path, content);
    }
    if lower == "packages.config" {
        return parse_packages_config(rel_path, content);
    }
    if lower == "pyproject.toml" {
        return parse_pyproject_toml(rel_path, content);
    }
    if lower == "pipfile" {
        return parse_pipfile(rel_path, content);
    }
    if lower == "pipfile.lock" {
        return parse_pipfile_lock(rel_path, content);
    }
    if lower == "poetry.lock" {
        return parse_poetry_lock(rel_path, content);
    }
    if lower == "uv.lock" {
        return parse_uv_lock(rel_path, content);
    }
    if lower == "setup.py" {
        return parse_setup_py(rel_path, content);
    }
    if is_requirements_file(&lower) {
        return parse_requirements_txt(rel_path, content);
    }
    if lower == "gemfile" {
        return parse_gemfile(rel_path, content);
    }
    if lower == "gemfile.lock" {
        return parse_gemfile_lock(rel_path, content);
    }

    Vec::new()
}

fn is_requirements_file(lower_name: &str) -> bool {
    lower_name == "requirements.txt"
        || (lower_name.starts_with("requirements") && lower_name.ends_with(".txt"))
        || lower_name.ends_with("-requirements.txt")
}

fn clean_version(raw: &str) -> Option<String> {
    let stripped = raw
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .trim_start_matches(['=', '~', '^', '>', '<', '!', 'v']);
    let v = stripped.split(',').next().unwrap_or(stripped).trim();
    if v.is_empty() || v == "*" || v.eq_ignore_ascii_case("latest") {
        None
    } else {
        Some(v.to_string())
    }
}

fn build_purl(pkg_type: &str, name: &str, version: Option<&str>) -> String {
    let encoded_name = if pkg_type == "npm" && name.starts_with('@') {
        name.replacen('@', "%40", 1)
    } else if pkg_type == "pypi" {
        name.to_ascii_lowercase().replace('_', "-")
    } else {
        name.to_string()
    };

    match version {
        Some(ver) if !ver.is_empty() => format!("pkg:{pkg_type}/{encoded_name}@{ver}"),
        _ => format!("pkg:{pkg_type}/{encoded_name}"),
    }
}

fn make_component(
    pkg_type: &str,
    name: impl Into<String>,
    version: Option<String>,
    scope: &str,
    direct: bool,
    source_file: &str,
) -> SbomComponent {
    let name_s = name.into();
    let purl = Some(build_purl(pkg_type, &name_s, version.as_deref()));
    SbomComponent {
        name: name_s,
        version,
        purl,
        package_type: pkg_type.to_string(),
        scope: scope.to_string(),
        direct,
        source_file: source_file.to_string(),
        license_id: None,
        metadata: None,
    }
}

// ---------------------------------------------------------------------------
// Python Parsers
// ---------------------------------------------------------------------------

fn parse_requirements_txt(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let mut out = Vec::new();
    let re = Regex::new(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*([=<>!~]{1,3}\s*([^\s;#]+))?")
        .expect("valid regex");
    let scope = if rel_path.to_ascii_lowercase().contains("dev")
        || rel_path.to_ascii_lowercase().contains("test")
    {
        "dev"
    } else {
        "runtime"
    };

    for line in content.lines() {
        let trimmed = line.split('#').next().unwrap_or("").trim();
        if trimmed.is_empty() || trimmed.starts_with('-') || trimmed.starts_with("git+") {
            continue;
        }
        // Strip environment markers `; python_version < ...`
        let req_part = trimmed.split(';').next().unwrap_or("").trim();
        if let Some(caps) = re.captures(req_part) {
            let name = caps[1].to_string();
            let version = caps.get(3).and_then(|m| clean_version(m.as_str()));
            out.push(make_component("pypi", name, version, scope, true, rel_path));
        }
    }
    out
}

fn parse_pip_specifier(spec: &str, scope: &str, rel_path: &str) -> Option<SbomComponent> {
    let re = Regex::new(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*(.*)$").expect("valid regex");
    let cleaned = spec.split(';').next().unwrap_or("").trim();
    let caps = re.captures(cleaned)?;
    let name = caps.get(1)?.as_str().to_string();
    let ver = caps.get(2).and_then(|m| clean_version(m.as_str()));
    Some(make_component("pypi", name, ver, scope, true, rel_path))
}

fn parse_pyproject_toml(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();

    // PEP 621 `[project].dependencies`
    if let Some(deps) = doc
        .get("project")
        .and_then(|p| p.get("dependencies"))
        .and_then(|d| d.as_array())
    {
        for item in deps {
            if let Some(spec) = item.as_str() {
                if let Some(comp) = parse_pip_specifier(spec, "runtime", rel_path) {
                    out.push(comp);
                }
            }
        }
    }

    // PEP 621 `[project.optional-dependencies]`
    if let Some(opt_table) = doc
        .get("project")
        .and_then(|p| p.get("optional-dependencies"))
        .and_then(|d| d.as_table())
    {
        for (group, deps) in opt_table {
            let scope = if group == "dev" || group == "test" {
                "dev"
            } else {
                "optional"
            };
            if let Some(arr) = deps.as_array() {
                for item in arr {
                    if let Some(spec) = item.as_str() {
                        if let Some(comp) = parse_pip_specifier(spec, scope, rel_path) {
                            out.push(comp);
                        }
                    }
                }
            }
        }
    }

    // Poetry `[tool.poetry.dependencies]`
    if let Some(poetry_deps) = doc
        .get("tool")
        .and_then(|t| t.get("poetry"))
        .and_then(|p| p.get("dependencies"))
        .and_then(|d| d.as_table())
    {
        for (name, val) in poetry_deps {
            if name.eq_ignore_ascii_case("python") {
                continue;
            }
            let ver = extract_toml_dep_version(val);
            out.push(make_component("pypi", name, ver, "runtime", true, rel_path));
        }
    }

    // `[dependency-groups]`
    if let Some(groups) = doc.get("dependency-groups").and_then(|d| d.as_table()) {
        for (group, arr) in groups {
            let scope = if group == "dev" || group == "test" {
                "dev"
            } else {
                "optional"
            };
            if let Some(items) = arr.as_array() {
                for item in items {
                    if let Some(spec) = item.as_str() {
                        if let Some(comp) = parse_pip_specifier(spec, scope, rel_path) {
                            out.push(comp);
                        }
                    }
                }
            }
        }
    }

    out
}

fn parse_pipfile(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (section, scope) in [("packages", "runtime"), ("dev-packages", "dev")] {
        if let Some(table) = doc.get(section).and_then(|v| v.as_table()) {
            for (name, val) in table {
                let ver = extract_toml_dep_version(val);
                out.push(make_component("pypi", name, ver, scope, true, rel_path));
            }
        }
    }
    out
}

fn parse_pipfile_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = serde_json::from_str::<Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (section, scope) in [("default", "runtime"), ("develop", "dev")] {
        if let Some(obj) = doc.get(section).and_then(|v| v.as_object()) {
            for (name, meta) in obj {
                let ver = meta
                    .get("version")
                    .and_then(|v| v.as_str())
                    .and_then(clean_version);
                out.push(make_component("pypi", name, ver, scope, false, rel_path));
            }
        }
    }
    out
}

fn parse_poetry_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if let Some(pkgs) = doc.get("package").and_then(|v| v.as_array()) {
        for pkg in pkgs {
            if let Some(name) = pkg.get("name").and_then(|v| v.as_str()) {
                let ver = pkg
                    .get("version")
                    .and_then(|v| v.as_str())
                    .and_then(clean_version);
                let cat = pkg
                    .get("category")
                    .and_then(|v| v.as_str())
                    .unwrap_or("main");
                let scope = if cat == "dev" { "dev" } else { "runtime" };
                out.push(make_component("pypi", name, ver, scope, false, rel_path));
            }
        }
    }
    out
}

fn parse_uv_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if let Some(pkgs) = doc.get("package").and_then(|v| v.as_array()) {
        for pkg in pkgs {
            if let Some(name) = pkg.get("name").and_then(|v| v.as_str()) {
                let ver = pkg
                    .get("version")
                    .and_then(|v| v.as_str())
                    .and_then(clean_version);
                out.push(make_component(
                    "pypi", name, ver, "runtime", false, rel_path,
                ));
            }
        }
    }
    out
}

fn parse_setup_py(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let re = Regex::new(r#"(?s)install_requires\s*=\s*\[(.*?)\]"#).expect("valid regex");
    let item_re = Regex::new(r#"['"]([^'"]+)['"]"#).expect("valid regex");
    let mut out = Vec::new();
    if let Some(caps) = re.captures(content) {
        for m in item_re.captures_iter(&caps[1]) {
            if let Some(comp) = parse_pip_specifier(&m[1], "runtime", rel_path) {
                out.push(comp);
            }
        }
    }
    out
}

fn extract_toml_dep_version(val: &toml::Value) -> Option<String> {
    match val {
        toml::Value::String(s) => clean_version(s),
        toml::Value::Table(t) => t
            .get("version")
            .and_then(|v| v.as_str())
            .and_then(clean_version),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Node.js Parsers
// ---------------------------------------------------------------------------

fn parse_package_json(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = serde_json::from_str::<Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let sections = [
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "optional"),
        ("optionalDependencies", "optional"),
    ];
    for (key, scope) in sections {
        if let Some(obj) = doc.get(key).and_then(|v| v.as_object()) {
            for (name, ver_val) in obj {
                let ver = ver_val.as_str().and_then(clean_version);
                out.push(make_component("npm", name, ver, scope, true, rel_path));
            }
        }
    }
    out
}

fn parse_package_lock_json(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = serde_json::from_str::<Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut seen = HashSet::new();

    // Lockfile v2/v3 `packages` map
    if let Some(pkgs) = doc.get("packages").and_then(|v| v.as_object()) {
        for (pkg_path, meta) in pkgs {
            if pkg_path.is_empty() {
                continue;
            }
            let name = meta
                .get("name")
                .and_then(|v| v.as_str())
                .map(str::to_string)
                .unwrap_or_else(|| {
                    pkg_path
                        .rsplit("node_modules/")
                        .next()
                        .unwrap_or(pkg_path)
                        .to_string()
                });
            let ver = meta
                .get("version")
                .and_then(|v| v.as_str())
                .and_then(clean_version);
            let scope = if meta.get("dev").and_then(|v| v.as_bool()).unwrap_or(false) {
                "dev"
            } else {
                "runtime"
            };
            if seen.insert((name.clone(), ver.clone())) {
                let mut comp = make_component("npm", name, ver, scope, false, rel_path);
                comp.license_id = meta
                    .get("license")
                    .and_then(|v| v.as_str())
                    .map(str::to_string);
                out.push(comp);
            }
        }
    } else if let Some(deps) = doc.get("dependencies").and_then(|v| v.as_object()) {
        // Lockfile v1 `dependencies` tree
        collect_npm_v1_deps(deps, rel_path, &mut seen, &mut out);
    }

    out
}

fn collect_npm_v1_deps(
    deps: &serde_json::Map<String, Value>,
    rel_path: &str,
    seen: &mut HashSet<(String, Option<String>)>,
    out: &mut Vec<SbomComponent>,
) {
    for (name, meta) in deps {
        let ver = meta
            .get("version")
            .and_then(|v| v.as_str())
            .and_then(clean_version);
        let scope = if meta.get("dev").and_then(|v| v.as_bool()).unwrap_or(false) {
            "dev"
        } else {
            "runtime"
        };
        if seen.insert((name.clone(), ver.clone())) {
            out.push(make_component("npm", name, ver, scope, true, rel_path));
        }
        if let Some(nested) = meta.get("dependencies").and_then(|v| v.as_object()) {
            collect_npm_v1_deps(nested, rel_path, seen, out);
        }
    }
}

fn parse_yarn_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    let header_re = Regex::new(r#"^"?((?:@[^/@\s]+/)?[^@\s:]+)@[^:]+:"#).expect("valid regex");
    let ver_re = Regex::new(r#"^\s+version(?::\s*|\s+)["']?([^"'\s]+)["']?"#).expect("valid regex");

    let mut current_name: Option<String> = None;
    for line in content.lines() {
        if !line.starts_with(' ') && !line.starts_with('#') && !line.trim().is_empty() {
            current_name = header_re.captures(line).map(|c| c[1].to_string());
        } else if let Some(name) = &current_name {
            if let Some(caps) = ver_re.captures(line) {
                let ver = clean_version(&caps[1]);
                if seen.insert((name.clone(), ver.clone())) {
                    out.push(make_component(
                        "npm",
                        name.clone(),
                        ver,
                        "runtime",
                        false,
                        rel_path,
                    ));
                }
                current_name = None;
            }
        }
    }
    out
}

fn parse_pnpm_lock_yaml(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = serde_norway::from_str::<serde_norway::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut seen = HashSet::new();

    if let Some(pkgs) = doc.get("packages").and_then(|v| v.as_mapping()) {
        for (key_val, _) in pkgs {
            let Some(raw_key) = key_val.as_str() else {
                continue;
            };
            // pnpm v6/v9 keys look like `/foo@1.2.3`, `/@scope/foo@1.2.3`, or `foo@1.2.3(peer)`
            let trimmed = raw_key
                .trim_start_matches('/')
                .split('(')
                .next()
                .unwrap_or("");
            if let Some((name, ver_part)) = trimmed.rsplit_once('@') {
                if !name.is_empty() {
                    let ver = clean_version(ver_part);
                    if seen.insert((name.to_string(), ver.clone())) {
                        out.push(make_component("npm", name, ver, "runtime", false, rel_path));
                    }
                }
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Go Parsers
// ---------------------------------------------------------------------------

fn parse_go_mod(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let mut out = Vec::new();
    let mut in_require = false;

    for raw_line in content.lines() {
        let line = raw_line.split("//").next().unwrap_or("").trim();
        let is_indirect = raw_line.contains("// indirect");
        if line.is_empty() {
            continue;
        }
        if line == "require (" {
            in_require = true;
            continue;
        }
        if in_require && line == ")" {
            in_require = false;
            continue;
        }

        let spec = if in_require {
            line
        } else if let Some(rest) = line.strip_prefix("require ") {
            rest.trim()
        } else {
            continue;
        };

        let mut parts = spec.split_whitespace();
        if let (Some(module), Some(ver)) = (parts.next(), parts.next()) {
            let clean_ver = ver.trim_start_matches('v').to_string();
            let scope = if is_indirect { "transitive" } else { "runtime" };
            out.push(make_component(
                "golang",
                module,
                Some(clean_ver),
                scope,
                !is_indirect,
                rel_path,
            ));
        }
    }
    out
}

fn parse_go_sum(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();

    for line in content.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 2 {
            continue;
        }
        let module = parts[0];
        let raw_ver = parts[1].split('/').next().unwrap_or(parts[1]);
        let clean_ver = raw_ver.trim_start_matches('v').to_string();
        if seen.insert((module.to_string(), clean_ver.clone())) {
            out.push(make_component(
                "golang",
                module,
                Some(clean_ver),
                "runtime",
                false,
                rel_path,
            ));
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Rust Parsers
// ---------------------------------------------------------------------------

fn parse_cargo_toml(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let sections = [
        ("dependencies", "runtime"),
        ("dev-dependencies", "dev"),
        ("build-dependencies", "build"),
    ];
    for (sec, scope) in sections {
        if let Some(table) = doc.get(sec).and_then(|v| v.as_table()) {
            for (name, val) in table {
                let pkg_name = val
                    .as_table()
                    .and_then(|t| t.get("package"))
                    .and_then(|p| p.as_str())
                    .unwrap_or(name);
                let ver = extract_toml_dep_version(val);
                out.push(make_component(
                    "cargo", pkg_name, ver, scope, true, rel_path,
                ));
            }
        }
    }
    out
}

fn parse_cargo_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = toml::from_str::<toml::Value>(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if let Some(pkgs) = doc.get("package").and_then(|v| v.as_array()) {
        for pkg in pkgs {
            if let Some(name) = pkg.get("name").and_then(|v| v.as_str()) {
                let ver = pkg
                    .get("version")
                    .and_then(|v| v.as_str())
                    .map(str::to_string);
                out.push(make_component(
                    "cargo", name, ver, "runtime", false, rel_path,
                ));
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Java Parsers
// ---------------------------------------------------------------------------

fn parse_pom_xml(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = Document::parse(content) else {
        return Vec::new();
    };

    let mut props: HashMap<String, String> = HashMap::new();
    for node in doc.descendants() {
        if node.has_tag_name("properties") {
            for child in node.children().filter(|n| n.is_element()) {
                if let Some(val) = child.text() {
                    props.insert(child.tag_name().name().to_string(), val.trim().to_string());
                }
            }
        }
    }
    if let Some(project_ver) = doc
        .root_element()
        .children()
        .find(|n| n.has_tag_name("version"))
        .and_then(|n| n.text())
    {
        props.insert("project.version".into(), project_ver.trim().to_string());
    }

    let mut out = Vec::new();
    for dep in doc.descendants().filter(|n| n.has_tag_name("dependency")) {
        let group = dep
            .children()
            .find(|n| n.has_tag_name("groupId"))
            .and_then(|n| n.text())
            .map(str::trim)
            .unwrap_or("");
        let artifact = dep
            .children()
            .find(|n| n.has_tag_name("artifactId"))
            .and_then(|n| n.text())
            .map(str::trim)
            .unwrap_or("");
        if group.is_empty() || artifact.is_empty() {
            continue;
        }
        let raw_ver = dep
            .children()
            .find(|n| n.has_tag_name("version"))
            .and_then(|n| n.text())
            .map(str::trim);
        let resolved_ver = raw_ver.and_then(|v| resolve_maven_prop(v, &props));
        let scope_str = dep
            .children()
            .find(|n| n.has_tag_name("scope"))
            .and_then(|n| n.text())
            .map(str::trim)
            .unwrap_or("runtime");
        let scope = match scope_str {
            "test" => "test",
            "provided" | "compile" | "runtime" => "runtime",
            _ => "optional",
        };
        let coord = format!("{group}/{artifact}");
        out.push(make_component(
            "maven",
            coord,
            resolved_ver,
            scope,
            true,
            rel_path,
        ));
    }
    out
}

fn resolve_maven_prop(raw: &str, props: &HashMap<String, String>) -> Option<String> {
    if let Some(key) = raw.strip_prefix("${").and_then(|s| s.strip_suffix('}')) {
        props.get(key).cloned().or_else(|| Some(raw.to_string()))
    } else {
        clean_version(raw)
    }
}

fn parse_gradle(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let re = Regex::new(
        r#"(?m)(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor)\s*\(?\s*['"]([^:'"]+):([^:'"]+):([^'"\s)]+)['"]"#,
    )
    .expect("valid regex");

    let mut out = Vec::new();
    for caps in re.captures_iter(content) {
        let cfg = &caps[1];
        let scope = if cfg.starts_with("test") {
            "test"
        } else {
            "runtime"
        };
        let coord = format!("{}/{}", &caps[2], &caps[3]);
        let ver = clean_version(&caps[4]);
        out.push(make_component("maven", coord, ver, scope, true, rel_path));
    }
    out
}

// ---------------------------------------------------------------------------
// .NET Parsers
// ---------------------------------------------------------------------------

fn parse_csproj(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = Document::parse(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for node in doc
        .descendants()
        .filter(|n| n.has_tag_name("PackageReference"))
    {
        let Some(name) = node
            .attribute("Include")
            .or_else(|| node.attribute("Update"))
        else {
            continue;
        };
        let ver = node
            .attribute("Version")
            .map(str::to_string)
            .or_else(|| {
                node.children()
                    .find(|c| c.has_tag_name("Version"))
                    .and_then(|c| c.text())
                    .map(|s| s.trim().to_string())
            })
            .and_then(|v| clean_version(&v));
        out.push(make_component(
            "nuget",
            name.trim(),
            ver,
            "runtime",
            true,
            rel_path,
        ));
    }
    out
}

fn parse_packages_config(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let Ok(doc) = Document::parse(content) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for node in doc.descendants().filter(|n| n.has_tag_name("package")) {
        let Some(name) = node.attribute("id") else {
            continue;
        };
        let ver = node.attribute("version").and_then(clean_version);
        let dev = node
            .attribute("developmentDependency")
            .is_some_and(|v| v.eq_ignore_ascii_case("true"));
        out.push(make_component(
            "nuget",
            name.trim(),
            ver,
            if dev { "dev" } else { "runtime" },
            true,
            rel_path,
        ));
    }
    out
}

// ---------------------------------------------------------------------------
// Ruby Parsers
// ---------------------------------------------------------------------------

fn parse_gemfile(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let re = Regex::new(r#"^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?"#)
        .expect("valid regex");
    let mut out = Vec::new();
    for line in content.lines() {
        let trimmed = line.split('#').next().unwrap_or("");
        if let Some(caps) = re.captures(trimmed) {
            let name = caps[1].to_string();
            let ver = caps.get(2).and_then(|m| clean_version(m.as_str()));
            out.push(make_component("gem", name, ver, "runtime", true, rel_path));
        }
    }
    out
}

fn parse_gemfile_lock(rel_path: &str, content: &str) -> Vec<SbomComponent> {
    let re = Regex::new(r"^    ([A-Za-z0-9_.-]+)\s+\(([^)]+)\)").expect("valid regex");
    let mut out = Vec::new();
    let mut in_specs = false;

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "specs:" {
            in_specs = true;
            continue;
        }
        if !line.starts_with(' ') && !trimmed.is_empty() {
            in_specs = false;
            continue;
        }
        if in_specs {
            if let Some(caps) = re.captures(line) {
                out.push(make_component(
                    "gem",
                    caps[1].to_string(),
                    clean_version(&caps[2]),
                    "runtime",
                    false,
                    rel_path,
                ));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_python_requirements_pyproject_and_lockfiles() {
        let reqs = parse_dependency_file(
            "requirements.txt",
            "quart==0.20.0\npydantic>=2.7.0 # inline comment\n-r base.txt\n",
        );
        assert_eq!(reqs.len(), 2);
        assert_eq!(reqs[0].purl.as_deref(), Some("pkg:pypi/quart@0.20.0"));

        let pyproj = parse_dependency_file(
            "pyproject.toml",
            r#"
[project]
dependencies = ["httpx==0.28.1"]
[project.optional-dependencies]
dev = ["pytest==8.3.0"]
"#,
        );
        assert_eq!(pyproj.len(), 2);
        assert_eq!(pyproj[1].scope, "dev");
    }

    #[test]
    fn parses_node_package_json_lock_yarn_and_pnpm() {
        let pkg = parse_dependency_file(
            "web/package.json",
            r#"{"dependencies":{"@tanstack/react-query":"^5.60.0"},"devDependencies":{"vitest":"~2.1.0"}}"#,
        );
        assert_eq!(pkg.len(), 2);
        assert_eq!(
            pkg[0].purl.as_deref(),
            Some("pkg:npm/%40tanstack/react-query@5.60.0")
        );
        assert_eq!(pkg[1].scope, "dev");

        let pnpm = parse_dependency_file(
            "pnpm-lock.yaml",
            "packages:\n  /@types/node@22.5.0:\n    resolution: {}\n  lodash@4.17.21:\n    resolution: {}\n",
        );
        assert_eq!(pnpm.len(), 2);
    }

    #[test]
    fn parses_go_rust_java_dotnet_and_ruby_manifests() {
        let gomod = parse_dependency_file(
            "go.mod",
            "module example.com/app\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.10.0\n\tgolang.org/x/net v0.33.0 // indirect\n)\n",
        );
        assert_eq!(gomod.len(), 2);
        assert_eq!(
            gomod[0].purl.as_deref(),
            Some("pkg:golang/github.com/gin-gonic/gin@1.10.0")
        );
        assert!(!gomod[1].direct);

        let cargo = parse_dependency_file(
            "Cargo.toml",
            "[dependencies]\nserde = { version = \"1.0.219\", features = [\"derive\"] }\n[dev-dependencies]\ntempfile = \"3.27\"\n",
        );
        assert_eq!(cargo.len(), 2);
        assert_eq!(cargo[0].purl.as_deref(), Some("pkg:cargo/serde@1.0.219"));

        let pom = parse_dependency_file(
            "pom.xml",
            r#"<project><properties><jackson.version>2.18.0</jackson.version></properties><dependencies><dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId><version>${jackson.version}</version></dependency></dependencies></project>"#,
        );
        assert_eq!(pom.len(), 1);
        assert_eq!(
            pom[0].purl.as_deref(),
            Some("pkg:maven/com.fasterxml.jackson.core/jackson-databind@2.18.0")
        );

        let csproj = parse_dependency_file(
            "App.csproj",
            r#"<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13.0.3" /></ItemGroup></Project>"#,
        );
        assert_eq!(csproj.len(), 1);
        assert_eq!(
            csproj[0].purl.as_deref(),
            Some("pkg:nuget/Newtonsoft.Json@13.0.3")
        );

        let gem_lock = parse_dependency_file(
            "Gemfile.lock",
            "GEM\n  specs:\n    rails (7.2.1)\n    rack (3.1.8)\n",
        );
        assert_eq!(gem_lock.len(), 2);
        assert_eq!(gem_lock[0].purl.as_deref(), Some("pkg:gem/rails@7.2.1"));
    }
}
