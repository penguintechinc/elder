//! Dynamic `clap` command-tree construction from [`elder_sdk::Catalog::embedded`].
//!
//! Combines ergonomic top-level commands (`configure`, `auth`, `id`, `search`,
//! `ai-research`, `api`, `version`, `completions`, `services`, `describe`) with
//! dynamically generated `elder <service> <operation>` subcommands covering every
//! endpoint in `hub-api`.

use clap::{Arg, ArgAction, ArgGroup, Command};
use elder_sdk::{ApiOperation, Catalog, HttpMethod};

/// Flag long-names that are strictly forbidden anywhere in the CLI parser tree
/// per `critical-rules.md` (Token & Secret Hygiene) and `client.md`.
pub const FORBIDDEN_SECRET_FLAGS: &[&str] = &["token", "password", "api-key"];

/// Ergonomic `auth` subcommands that override or extend catalog operations.
const ERGONOMIC_AUTH_SUBCOMMANDS: &[&str] = &["login", "logout", "whoami", "refresh"];

/// Convert a catalog path parameter name (e.g. `village_id`, `token`) into its CLI `--flag` name.
///
/// Replaces underscores with hyphens and prefixes any name in [`FORBIDDEN_SECRET_FLAGS`]
/// with `path-` so no command in the CLI ever exposes `--token`, `--password`, or `--api-key`.
pub fn path_param_flag_name(param: &str) -> String {
    let kebab = param.trim().replace('_', "-").to_ascii_lowercase();
    if FORBIDDEN_SECRET_FLAGS.contains(&kebab.as_str()) {
        format!("path-{kebab}")
    } else {
        kebab
    }
}

/// Build the full `elder` CLI command tree backed by `Catalog::embedded()`.
pub fn build_cli() -> Command {
    build_cli_from_catalog(Catalog::embedded())
}

/// Build the full `elder` CLI command tree from an explicit [`Catalog`].
pub fn build_cli_from_catalog(catalog: &Catalog) -> Command {
    let mut root = Command::new("elder")
        .about("Official pure-rustls CLI for Elder hub-api")
        .version(env!("CARGO_PKG_VERSION"))
        .subcommand_required(true)
        .arg_required_else_help(true)
        .propagate_version(false)
        .arg(
            Arg::new("output")
                .short('o')
                .long("output")
                .global(true)
                .value_name("FORMAT")
                .value_parser(["json", "table", "yaml", "raw"])
                .help("Output format: json, table, yaml, or raw"),
        )
        .arg(
            Arg::new("url")
                .long("url")
                .global(true)
                .value_name("URL")
                .help("Override Elder hub-api base URL (or ELDER_API_URL)"),
        )
        .arg(
            Arg::new("profile")
                .long("profile")
                .global(true)
                .value_name("NAME")
                .help("Named profile from ~/.config/elder/config.toml (or ELDER_PROFILE)"),
        )
        .arg(
            Arg::new("timeout")
                .long("timeout")
                .global(true)
                .value_name("SECS")
                .value_parser(clap::value_parser!(u64).range(1..=3600))
                .help("HTTP request timeout in seconds (1..=3600)"),
        )
        .arg(
            Arg::new("verbose")
                .short('v')
                .long("verbose")
                .global(true)
                .action(ArgAction::Count)
                .help("Increase diagnostic logging verbosity on stderr (-v, -vv)"),
        );

    // Ergonomic top-level commands.
    root = root
        .subcommand(build_configure_cmd())
        .subcommand(build_auth_cmd(catalog))
        .subcommand(build_id_cmd())
        .subcommand(build_search_cmd(catalog))
        .subcommand(build_ai_research_cmd())
        .subcommand(build_runs_cmd())
        .subcommand(build_api_cmd())
        .subcommand(build_version_cmd())
        .subcommand(build_completions_cmd())
        .subcommand(build_services_cmd(catalog))
        .subcommand(build_describe_cmd());

    // Dynamically attach every remaining catalog service as `elder <service> <operation>`.
    for service in catalog.services() {
        if matches!(service, "auth" | "search" | "services" | "runs") {
            continue;
        }
        let mut svc_cmd = Command::new(service.to_string())
            .about(format!("Operations for the '{service}' API service"))
            .subcommand_required(true)
            .arg_required_else_help(true);

        for op in catalog.operations(service) {
            svc_cmd = svc_cmd.subcommand(build_operation_cmd(op));
        }

        root = root.subcommand(svc_cmd);
    }

    root
}

fn build_configure_cmd() -> Command {
    Command::new("configure")
        .about("Configure non-secret Elder CLI profile settings (~/.config/elder/config.toml)")
        .arg(
            Arg::new("default-output")
                .long("default-output")
                .value_name("FORMAT")
                .value_parser(["json", "table", "yaml", "raw"])
                .help("Default output format stored for this profile"),
        )
        .arg(
            Arg::new("set-default")
                .long("set-default")
                .action(ArgAction::SetTrue)
                .help("Set this profile as the default profile"),
        )
        .arg(
            Arg::new("non-interactive")
                .long("non-interactive")
                .action(ArgAction::SetTrue)
                .help("Write settings without prompting interactively"),
        )
}

fn build_auth_cmd(catalog: &Catalog) -> Command {
    let mut cmd = Command::new("auth")
        .about("Authenticate with Elder hub-api using OS secure keyring or env vars")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(
            Command::new("login")
                .about("Authenticate and store credentials in OS secure storage (keyring)")
                .arg(
                    Arg::new("username")
                        .short('u')
                        .long("username")
                        .value_name("USER")
                        .help("Username or email (or ELDER_USERNAME / interactive prompt)"),
                )
                .arg(
                    Arg::new("password-stdin")
                        .long("password-stdin")
                        .action(ArgAction::SetTrue)
                        .help("Read password securely from stdin instead of interactive prompt"),
                )
                .arg(
                    Arg::new("api-key-stdin")
                        .long("api-key-stdin")
                        .action(ArgAction::SetTrue)
                        .help("Read an Elder API key securely from stdin and store in keyring"),
                )
                .arg(
                    Arg::new("portal")
                        .long("portal")
                        .action(ArgAction::SetTrue)
                        .help("Authenticate via portal login (/api/v1/portal-auth/login)"),
                )
                .arg(
                    Arg::new("tenant")
                        .long("tenant")
                        .value_name("TENANT")
                        .help("Optional tenant identifier for portal login"),
                ),
        )
        .subcommand(
            Command::new("logout")
                .about("Revoke session (best-effort) and remove profile credentials from keyring"),
        )
        .subcommand(
            Command::new("whoami")
                .about("Show authenticated user identity and masked credential source"),
        )
        .subcommand(Command::new("refresh").about(
            "Refresh access token using stored keyring refresh token or ELDER_REFRESH_TOKEN",
        ));

    // Attach remaining non-colliding catalog `auth` operations dynamically.
    for op in catalog.operations("auth") {
        if !ERGONOMIC_AUTH_SUBCOMMANDS.contains(&op.operation.as_str()) {
            cmd = cmd.subcommand(build_operation_cmd(op));
        }
    }

    cmd
}

fn build_id_cmd() -> Command {
    Command::new("id")
        .about("Resolve any universal Village ID via GET /api/v1/id/{village_id}")
        .arg(
            Arg::new("village_id")
                .required(true)
                .value_name("VILLAGE_ID")
                .help("Universal Village ID to resolve (e.g. ENT-1234, ISS-42)"),
        )
}

fn build_search_cmd(catalog: &Catalog) -> Command {
    let mut cmd = Command::new("search")
        .about("Universal search across Elder or invoke a 'search' catalog operation")
        .args_conflicts_with_subcommands(true)
        .arg(
            Arg::new("search_term")
                .value_name("QUERY")
                .help("Search query string sent to GET /api/v1/search?q=<QUERY>"),
        )
        .arg(
            Arg::new("query")
                .short('q')
                .long("query")
                .action(ArgAction::Append)
                .value_name("KEY=VAL")
                .help("Additional query string parameter (repeatable)"),
        )
        .arg(
            Arg::new("all-pages")
                .long("all-pages")
                .action(ArgAction::SetTrue)
                .help("Automatically fetch all pages for paginated results"),
        )
        .arg(
            Arg::new("per-page")
                .long("per-page")
                .value_name("N")
                .value_parser(clap::value_parser!(u32).range(1..=1000))
                .help("Page size when --all-pages is enabled"),
        )
        .arg(
            Arg::new("max-pages")
                .long("max-pages")
                .value_name("N")
                .value_parser(clap::value_parser!(u32).range(1..=10_000))
                .help("Maximum pages to fetch when --all-pages is enabled"),
        );

    for op in catalog.operations("search") {
        cmd = cmd.subcommand(build_operation_cmd(op));
    }

    cmd
}

fn build_ai_research_cmd() -> Command {
    Command::new("ai-research")
        .about("Run a read-only AI Researcher query via POST /api/v1/search/ai-research")
        .arg(
            Arg::new("query")
                .required(true)
                .value_name("QUERY")
                .help("Natural-language research question"),
        )
        .arg(
            Arg::new("model")
                .long("model")
                .value_name("MODEL")
                .help("Optional LLM model identifier"),
        )
        .arg(
            Arg::new("max-steps")
                .long("max-steps")
                .value_name("N")
                .value_parser(clap::value_parser!(u32).range(1..=50))
                .help("Maximum reasoning/tool steps"),
        )
}

fn build_runs_cmd() -> Command {
    let runtimes = ["nodejs", "rust", "python", "golang", "ruby"];
    let exec_statuses = ["pending", "running", "completed", "failed", "timed_out"];
    let trigger_types = ["manual", "schedule", "stream"];

    Command::new("runs")
        .about("Manage and invoke Elder Runs serverless functions (/api/v1/runs)")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(
            Command::new("list")
                .about("List serverless functions (GET /api/v1/runs)")
                .arg(
                    Arg::new("page")
                        .long("page")
                        .value_name("N")
                        .value_parser(clap::value_parser!(u32).range(1..=10_000))
                        .help("1-based page number"),
                )
                .arg(
                    Arg::new("per-page")
                        .long("per-page")
                        .value_name("N")
                        .value_parser(clap::value_parser!(u32).range(1..=1000))
                        .help("Items per page"),
                )
                .arg(
                    Arg::new("runtime")
                        .long("runtime")
                        .value_name("RUNTIME")
                        .value_parser(runtimes)
                        .help("Filter by runtime"),
                )
                .arg(
                    Arg::new("search")
                        .long("search")
                        .value_name("TEXT")
                        .help("Search function name or description"),
                )
                .arg(
                    Arg::new("worker-cluster")
                        .long("worker-cluster")
                        .value_name("CLUSTER")
                        .help("Filter by worker cluster"),
                )
                .arg(
                    Arg::new("is-enabled")
                        .long("is-enabled")
                        .value_name("BOOL")
                        .value_parser(clap::value_parser!(bool))
                        .help("Filter by enabled state (true/false)"),
                ),
        )
        .subcommand(
            Command::new("get")
                .about("Get a serverless function with latest version, executions, and storage metadata")
                .arg(
                    Arg::new("func_id")
                        .required(true)
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .help("Numeric function ID"),
                ),
        )
        .subcommand(
            Command::new("create")
                .about("Create a new serverless function (POST /api/v1/runs)")
                .arg(
                    Arg::new("name")
                        .long("name")
                        .required(true)
                        .value_name("NAME")
                        .help("Function name"),
                )
                .arg(
                    Arg::new("runtime")
                        .long("runtime")
                        .required(true)
                        .value_name("RUNTIME")
                        .value_parser(runtimes)
                        .help("Execution runtime"),
                )
                .arg(
                    Arg::new("code")
                        .long("code")
                        .value_name("SOURCE")
                        .help("Inline function source code"),
                )
                .arg(
                    Arg::new("code-file")
                        .long("code-file")
                        .value_name("PATH|-")
                        .help("Read function source code from file path (or '-' for stdin)"),
                )
                .group(
                    ArgGroup::new("code_source")
                        .args(["code", "code-file"])
                        .required(true),
                )
                .arg(
                    Arg::new("description")
                        .long("description")
                        .value_name("TEXT")
                        .help("Optional function description"),
                )
                .arg(
                    Arg::new("entrypoint")
                        .long("entrypoint")
                        .value_name("ENTRYPOINT")
                        .help("Optional entrypoint symbol or command"),
                )
                .arg(
                    Arg::new("timeout-seconds")
                        .long("timeout-seconds")
                        .value_name("SECS")
                        .value_parser(clap::value_parser!(u32).range(1..=3600))
                        .help("Execution timeout in seconds (1..=3600)"),
                )
                .arg(
                    Arg::new("memory-mb")
                        .long("memory-mb")
                        .value_name("MB")
                        .value_parser(clap::value_parser!(u32).range(32..=16384))
                        .help("Memory limit in MiB (32..=16384)"),
                )
                .arg(
                    Arg::new("env")
                        .short('e')
                        .long("env")
                        .action(ArgAction::Append)
                        .value_name("KEY=VAL")
                        .help("Environment variable KEY=VAL (repeatable)"),
                )
                .arg(
                    Arg::new("schedule-cron")
                        .long("schedule-cron")
                        .value_name("EXPR")
                        .help("Optional cron schedule expression"),
                )
                .arg(
                    Arg::new("execution-namespace")
                        .long("execution-namespace")
                        .value_name("NAMESPACE")
                        .help("Kubernetes namespace for executions"),
                )
                .arg(
                    Arg::new("worker-cluster")
                        .long("worker-cluster")
                        .value_name("CLUSTER")
                        .help("Target worker cluster"),
                )
                .arg(
                    Arg::new("is-enabled")
                        .long("is-enabled")
                        .value_name("BOOL")
                        .value_parser(clap::value_parser!(bool))
                        .help("Whether the function starts enabled (true/false)"),
                ),
        )
        .subcommand(
            Command::new("update")
                .about("Update an existing serverless function (PUT /api/v1/runs/{func_id})")
                .arg(
                    Arg::new("func_id")
                        .required(true)
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .help("Numeric function ID"),
                )
                .arg(
                    Arg::new("name")
                        .long("name")
                        .value_name("NAME")
                        .help("Updated function name"),
                )
                .arg(
                    Arg::new("runtime")
                        .long("runtime")
                        .value_name("RUNTIME")
                        .value_parser(runtimes)
                        .help("Updated runtime"),
                )
                .arg(
                    Arg::new("code")
                        .long("code")
                        .value_name("SOURCE")
                        .conflicts_with("code-file")
                        .help("Updated inline source code"),
                )
                .arg(
                    Arg::new("code-file")
                        .long("code-file")
                        .value_name("PATH|-")
                        .conflicts_with("code")
                        .help("Read updated source code from file path (or '-' for stdin)"),
                )
                .arg(
                    Arg::new("description")
                        .long("description")
                        .value_name("TEXT")
                        .help("Updated function description"),
                )
                .arg(
                    Arg::new("entrypoint")
                        .long("entrypoint")
                        .value_name("ENTRYPOINT")
                        .help("Updated entrypoint"),
                )
                .arg(
                    Arg::new("timeout-seconds")
                        .long("timeout-seconds")
                        .value_name("SECS")
                        .value_parser(clap::value_parser!(u32).range(1..=3600))
                        .help("Updated timeout in seconds (1..=3600)"),
                )
                .arg(
                    Arg::new("memory-mb")
                        .long("memory-mb")
                        .value_name("MB")
                        .value_parser(clap::value_parser!(u32).range(32..=16384))
                        .help("Updated memory limit in MiB (32..=16384)"),
                )
                .arg(
                    Arg::new("env")
                        .short('e')
                        .long("env")
                        .action(ArgAction::Append)
                        .value_name("KEY=VAL")
                        .help("Environment variable KEY=VAL (repeatable; replaces env_vars)"),
                )
                .arg(
                    Arg::new("schedule-cron")
                        .long("schedule-cron")
                        .value_name("EXPR")
                        .help("Updated cron schedule expression"),
                )
                .arg(
                    Arg::new("execution-namespace")
                        .long("execution-namespace")
                        .value_name("NAMESPACE")
                        .help("Updated Kubernetes execution namespace"),
                )
                .arg(
                    Arg::new("worker-cluster")
                        .long("worker-cluster")
                        .value_name("CLUSTER")
                        .help("Updated worker cluster"),
                )
                .arg(
                    Arg::new("is-enabled")
                        .long("is-enabled")
                        .value_name("BOOL")
                        .value_parser(clap::value_parser!(bool))
                        .help("Enable or disable the function (true/false)"),
                ),
        )
        .subcommand(
            Command::new("delete")
                .about("Delete a serverless function (DELETE /api/v1/runs/{func_id})")
                .arg(
                    Arg::new("func_id")
                        .required(true)
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .help("Numeric function ID"),
                ),
        )
        .subcommand(
            Command::new("invoke")
                .about("Queue an execution for a serverless function (POST /api/v1/runs/{func_id}/invoke)")
                .arg(
                    Arg::new("func_id")
                        .required(true)
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .help("Numeric function ID"),
                )
                .arg(
                    Arg::new("input")
                        .long("input")
                        .value_name("JSON|@FILE|-")
                        .help("JSON input payload string, @path/to/file.json, or '-' for stdin"),
                )
                .arg(
                    Arg::new("trigger-type")
                        .long("trigger-type")
                        .value_name("TYPE")
                        .value_parser(trigger_types)
                        .help("Execution trigger type (manual, schedule, stream)"),
                ),
        )
        .subcommand(
            Command::new("executions")
                .about("List function executions (GET /api/v1/runs/executions)")
                .arg(
                    Arg::new("func_id")
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .help("Optional numeric function ID filter"),
                )
                .arg(
                    Arg::new("function-id")
                        .long("function-id")
                        .value_name("FUNC_ID")
                        .value_parser(clap::value_parser!(i64))
                        .conflicts_with("func_id")
                        .help("Optional numeric function ID filter"),
                )
                .arg(
                    Arg::new("status")
                        .long("status")
                        .value_name("STATUS")
                        .value_parser(exec_statuses)
                        .help("Filter by execution status"),
                )
                .arg(
                    Arg::new("trigger-type")
                        .long("trigger-type")
                        .value_name("TYPE")
                        .value_parser(trigger_types)
                        .help("Filter by trigger type"),
                )
                .arg(
                    Arg::new("page")
                        .long("page")
                        .value_name("N")
                        .value_parser(clap::value_parser!(u32).range(1..=10_000))
                        .help("1-based page number"),
                )
                .arg(
                    Arg::new("per-page")
                        .long("per-page")
                        .value_name("N")
                        .value_parser(clap::value_parser!(u32).range(1..=1000))
                        .help("Items per page"),
                ),
        )
        .subcommand(
            Command::new("execution")
                .about("Get a single execution record (GET /api/v1/runs/executions/{execution_id})")
                .arg(
                    Arg::new("execution_id")
                        .required(true)
                        .value_name("EXECUTION_ID")
                        .help("Execution UUID"),
                ),
        )
}

fn build_api_cmd() -> Command {
    Command::new("api")
        .about("Raw HTTP escape hatch routed through ElderClient")
        .arg(
            Arg::new("method")
                .required(true)
                .value_name("METHOD")
                .help("HTTP method: GET, POST, PUT, PATCH, or DELETE"),
        )
        .arg(
            Arg::new("path")
                .required(true)
                .value_name("PATH")
                .help("API path (e.g. /api/v1/issues)"),
        )
        .arg(
            Arg::new("query")
                .short('q')
                .long("query")
                .action(ArgAction::Append)
                .value_name("KEY=VAL")
                .help("Query parameter KEY=VAL (repeatable)"),
        )
        .arg(
            Arg::new("body")
                .long("body")
                .value_name("JSON|@FILE|-")
                .help("JSON request body string, @path/to/file.json, or '-' for stdin"),
        )
        .arg(
            Arg::new("field")
                .short('f')
                .long("field")
                .action(ArgAction::Append)
                .value_name("KEY=VAL")
                .help("JSON body field KEY=VAL with automatic primitive/JSON parsing (repeatable)"),
        )
        .arg(
            Arg::new("all-pages")
                .long("all-pages")
                .action(ArgAction::SetTrue)
                .help("Paginate GET requests and return all items"),
        )
        .arg(
            Arg::new("per-page")
                .long("per-page")
                .value_name("N")
                .value_parser(clap::value_parser!(u32).range(1..=1000))
                .help("Page size when --all-pages is enabled"),
        )
        .arg(
            Arg::new("max-pages")
                .long("max-pages")
                .value_name("N")
                .value_parser(clap::value_parser!(u32).range(1..=10_000))
                .help("Maximum pages to fetch when --all-pages is enabled"),
        )
}

fn build_version_cmd() -> Command {
    Command::new("version")
        .about("Show CLI version, embedded catalog summary, and optionally check hub-api updates")
        .arg(
            Arg::new("check-updates")
                .long("check-updates")
                .action(ArgAction::SetTrue)
                .help("Query GET /api/v1/status to compare against server version"),
        )
}

fn build_completions_cmd() -> Command {
    Command::new("completions")
        .about("Generate shell completion scripts")
        .arg(
            Arg::new("shell")
                .required(true)
                .value_name("SHELL")
                .value_parser(["bash", "zsh", "fish", "powershell"])
                .help("Target shell: bash, zsh, fish, or powershell"),
        )
}

fn build_services_cmd(catalog: &Catalog) -> Command {
    let mut cmd = Command::new("services")
        .about("List all API services offline, or invoke a 'services' catalog operation");

    for op in catalog.operations("services") {
        cmd = cmd.subcommand(build_operation_cmd(op));
    }

    cmd
}

fn build_describe_cmd() -> Command {
    Command::new("describe")
        .about("Inspect available operations, methods, paths, and required params offline")
        .arg(
            Arg::new("service")
                .required(true)
                .value_name("SERVICE")
                .help("Service name (e.g. issues, entities, access-reviews)"),
        )
        .arg(
            Arg::new("operation")
                .value_name("OPERATION")
                .help("Optional operation name to inspect in detail"),
        )
}

fn build_operation_cmd(op: &ApiOperation) -> Command {
    let about_text = if op.summary.trim().is_empty() {
        format!("{} {}", op.method, op.path)
    } else {
        format!("{} ({} {})", op.summary, op.method, op.path)
    };

    let mut cmd = Command::new(op.operation.clone()).about(about_text);

    for param in &op.path_params {
        let flag = path_param_flag_name(param);
        cmd = cmd.arg(
            Arg::new(format!("path:{param}"))
                .long(flag)
                .required(true)
                .value_name(param.to_ascii_uppercase())
                .help(format!("Required path parameter {{{param}}}")),
        );
    }

    cmd = cmd.arg(
        Arg::new("query")
            .short('q')
            .long("query")
            .action(ArgAction::Append)
            .value_name("KEY=VAL")
            .help("Query string parameter KEY=VAL (repeatable)"),
    );

    if op.has_body || op.method != HttpMethod::Get {
        cmd = cmd
            .arg(
                Arg::new("body")
                    .long("body")
                    .value_name("JSON|@FILE|-")
                    .help("JSON request body string, @path/to/file.json, or '-' for stdin"),
            )
            .arg(
                Arg::new("field")
                    .short('f')
                    .long("field")
                    .action(ArgAction::Append)
                    .value_name("KEY=VAL")
                    .help(
                        "JSON body field KEY=VAL with automatic primitive/JSON parsing (repeatable)",
                    ),
            );
    }

    if op.method == HttpMethod::Get {
        cmd = cmd
            .arg(
                Arg::new("all-pages")
                    .long("all-pages")
                    .action(ArgAction::SetTrue)
                    .help("Automatically paginate and return all collected items"),
            )
            .arg(
                Arg::new("per-page")
                    .long("per-page")
                    .value_name("N")
                    .value_parser(clap::value_parser!(u32).range(1..=1000))
                    .help("Page size when --all-pages is enabled"),
            )
            .arg(
                Arg::new("max-pages")
                    .long("max-pages")
                    .value_name("N")
                    .value_parser(clap::value_parser!(u32).range(1..=10_000))
                    .help("Maximum pages to fetch when --all-pages is enabled"),
            );
    }

    cmd
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_no_forbidden_flags_recursive(cmd: &Command, trail: &str) {
        for arg in cmd.get_arguments() {
            if let Some(long) = arg.get_long() {
                assert!(
                    !FORBIDDEN_SECRET_FLAGS.contains(&long),
                    "forbidden secret CLI flag '--{long}' found on command '{trail}'"
                );
            }
            if let Some(aliases) = arg.get_all_aliases() {
                for alias in aliases {
                    assert!(
                        !FORBIDDEN_SECRET_FLAGS.contains(&alias),
                        "forbidden secret CLI alias '--{alias}' found on command '{trail}'"
                    );
                }
            }
        }
        for sub in cmd.get_subcommands() {
            let next = format!("{trail} {}", sub.get_name());
            assert_no_forbidden_flags_recursive(sub, &next);
        }
    }

    #[test]
    fn cli_tree_passes_clap_debug_assert_and_has_no_secret_flags() {
        let cmd = build_cli();
        cmd.clone().debug_assert();
        assert_no_forbidden_flags_recursive(&cmd, "elder");
    }

    #[test]
    fn exposes_every_catalog_service_and_operation() {
        let cat = Catalog::embedded();
        let cmd = build_cli();

        for service in cat.services() {
            let svc_cmd = cmd
                .find_subcommand(service)
                .unwrap_or_else(|| panic!("missing service subcommand '{service}'"));
            for op in cat.operations(service) {
                assert!(
                    svc_cmd.find_subcommand(&op.operation).is_some(),
                    "missing operation '{}.{}' in CLI tree",
                    service,
                    op.operation
                );
            }
        }
    }

    #[test]
    fn sanitizes_forbidden_path_param_names() {
        assert_eq!(path_param_flag_name("token"), "path-token");
        assert_eq!(path_param_flag_name("password"), "path-password");
        assert_eq!(path_param_flag_name("api_key"), "path-api-key");
        assert_eq!(path_param_flag_name("village_id"), "village-id");
    }
}
