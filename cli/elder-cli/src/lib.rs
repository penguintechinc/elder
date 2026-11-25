//! Official pure-`rustls` Rust CLI (`elder`) for Elder `hub-api`.
//!
//! `elder-cli` is a thin, AWS-CLI-style wrapper over [`elder_sdk::Catalog`] and
//! [`elder_sdk::ElderClient`]. All validation, authz, RBAC, tenant isolation,
//! and domain logic live in `hub-api`.

pub mod body;
pub mod cli;
pub mod config;
pub mod credentials;
pub mod error;
pub mod output;

use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::{self, BufRead, IsTerminal, Read, Write};
use std::path::PathBuf;
use std::time::Duration;

use clap::ArgMatches;
use clap_complete::{generate, Shell};
use elder_sdk::{
    Catalog, ClientConfig, CreateRunFunctionRequest, ElderClient, HttpMethod, InvokeRunRequest,
    ListExecutionsParams, ListRemoteWorkersParams, ListRunFunctionsParams,
    OnboardRemoteWorkerRequest, UpdateRemoteWorkerRequest, UpdateRunFunctionRequest,
};
use serde_json::{json, Map, Value};
use tracing::{debug, info};

use crate::body::{parse_query_pairs, parse_request_body};
use crate::cli::{build_cli, path_param_flag_name};
use crate::config::{config_file_path, ConfigFile, ProfileConfig, ResolvedSettings};
use crate::credentials::{
    clear_profile_secrets, mask_secret, resolve_auth, CredentialStore, KeyringStore, ResolvedAuth,
    SecretKind,
};
use crate::error::{CliError, Result};
use crate::output::{write_output, OutputFormat};

/// Runtime I/O and storage dependencies injected into CLI execution.
///
/// Using an explicit context makes every subcommand testable against a local
/// mock HTTP server without touching the host user's keyring or `~/.config`.
pub struct RunContext<'a, R: Read, W: Write, E: Write> {
    /// Path to `config.toml`.
    pub config_path: PathBuf,
    /// Platform secure credential store (or in-memory mock in tests).
    pub store: &'a dyn CredentialStore,
    /// Standard input reader (for `--body -`, `--password-stdin`, prompts).
    pub stdin: &'a mut R,
    /// Standard output writer (receives ONLY structured data or completions).
    pub stdout: &'a mut W,
    /// Standard error writer (receives interactive prompts and human hints).
    pub stderr: &'a mut E,
    /// Whether `stdout` is an interactive terminal (affects default output format).
    pub stdout_is_tty: bool,
    /// Whether `stdin` is an interactive terminal (affects interactive password prompt).
    pub stdin_is_tty: bool,
    /// Environment variable lookup closure.
    pub env_lookup: &'a dyn Fn(&str) -> Option<String>,
}

/// Execute the CLI using process arguments, real OS keyring, and stdio.
pub async fn run_process() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let config_path = config_file_path()?;
    let store = KeyringStore;
    let mut stdin = io::stdin().lock();
    let mut stdout = io::stdout().lock();
    let mut stderr = io::stderr().lock();
    let stdout_is_tty = io::stdout().is_terminal();
    let stdin_is_tty = io::stdin().is_terminal();
    let env_lookup = |k: &str| std::env::var(k).ok();

    let mut ctx = RunContext {
        config_path,
        store: &store,
        stdin: &mut stdin,
        stdout: &mut stdout,
        stderr: &mut stderr,
        stdout_is_tty,
        stdin_is_tty,
        env_lookup: &env_lookup,
    };

    run_with_args(args, &mut ctx).await
}

/// Parse `args` and execute the matched command using `ctx`.
pub async fn run_with_args<I, T, R, W, E>(args: I, ctx: &mut RunContext<'_, R, W, E>) -> Result<()>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
    R: Read,
    W: Write,
    E: Write,
{
    let cmd = build_cli();
    let matches = match cmd.try_get_matches_from(args) {
        Ok(m) => m,
        Err(err) => {
            use clap::error::ErrorKind;
            match err.kind() {
                ErrorKind::DisplayHelp | ErrorKind::DisplayVersion => {
                    write!(ctx.stdout, "{err}")?;
                    return Ok(());
                }
                _ => return Err(CliError::Usage(err.to_string().trim_end().to_string())),
            }
        }
    };

    execute_matches(&matches, ctx).await
}

/// Dispatch already-parsed [`ArgMatches`] using `ctx`.
pub async fn execute_matches<R, W, E>(
    matches: &ArgMatches,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let config_file = ConfigFile::load_from(&ctx.config_path)?;
    let output_override = matches
        .get_one::<String>("output")
        .map(|s| s.parse::<OutputFormat>())
        .transpose()?;
    let url_override = matches.get_one::<String>("url").map(String::as_str);
    let profile_override = matches.get_one::<String>("profile").map(String::as_str);
    let timeout_override = matches.get_one::<u64>("timeout").copied();

    let settings = config_file.resolve(
        profile_override,
        url_override,
        timeout_override,
        output_override,
        ctx.stdout_is_tty,
        ctx.env_lookup,
    )?;

    let (sub_name, sub_matches) = matches
        .subcommand()
        .ok_or_else(|| CliError::Usage("a subcommand is required; run 'elder --help'".into()))?;

    debug!(
        command = %sub_name,
        profile = %settings.profile,
        url = %settings.url,
        output = %settings.output,
        "dispatching elder CLI command"
    );

    match sub_name {
        "configure" => handle_configure(sub_matches, &settings, ctx),
        "auth" => handle_auth(sub_matches, &settings, ctx).await,
        "id" => handle_id(sub_matches, &settings, ctx).await,
        "search" => handle_search(sub_matches, &settings, ctx).await,
        "ai-research" => handle_ai_research(sub_matches, &settings, ctx).await,
        "runs" => handle_runs(sub_matches, &settings, ctx).await,
        "workers" => handle_workers(sub_matches, &settings, ctx).await,
        "api" => handle_api(sub_matches, &settings, ctx).await,
        "version" => handle_version(sub_matches, &settings, ctx).await,
        "completions" => handle_completions(sub_matches, ctx),
        "services" => handle_services(sub_matches, &settings, ctx).await,
        "describe" => handle_describe(sub_matches, &settings, ctx),
        service => handle_catalog_service(service, sub_matches, &settings, ctx).await,
    }
}

fn build_client(
    settings: &ResolvedSettings,
    store: &dyn CredentialStore,
    env_lookup: &dyn Fn(&str) -> Option<String>,
) -> Result<(ElderClient, ResolvedAuth)> {
    let resolved_auth = resolve_auth(&settings.profile, store, env_lookup)?;
    let cfg = ClientConfig::new(&settings.url)?
        .with_auth(resolved_auth.auth.clone())
        .with_timeout(Duration::from_secs(settings.timeout_secs))
        .with_user_agent(format!("elder-cli/{}", env!("CARGO_PKG_VERSION")));
    let client = ElderClient::new(cfg)?;
    Ok((client, resolved_auth))
}

fn handle_configure<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let mut cfg = ConfigFile::load_from(&ctx.config_path)?;
    let existing = cfg.profile(&settings.profile).cloned().unwrap_or_default();

    let non_interactive = matches.get_flag("non-interactive") || !ctx.stdin_is_tty;
    let explicit_default_output = matches
        .get_one::<String>("default-output")
        .map(|s| s.parse::<OutputFormat>())
        .transpose()?;

    let mut url = settings.url.clone();
    let mut default_output = explicit_default_output.or(existing.default_output);
    let timeout_secs = matches
        .get_one::<u64>("timeout")
        .copied()
        .or(existing.timeout_secs);

    if !non_interactive {
        let mut reader = io::BufReader::new(&mut *ctx.stdin);

        write!(ctx.stderr, "Elder API URL [{}]: ", url)?;
        ctx.stderr.flush()?;
        let mut line = String::new();
        reader.read_line(&mut line)?;
        let trimmed = line.trim();
        if !trimmed.is_empty() {
            url = trimmed.to_string();
        }

        let current_fmt = default_output.unwrap_or(OutputFormat::Table);
        write!(
            ctx.stderr,
            "Default output format (json/table/yaml/raw) [{}]: ",
            current_fmt
        )?;
        ctx.stderr.flush()?;
        line.clear();
        reader.read_line(&mut line)?;
        let trimmed_fmt = line.trim();
        if !trimmed_fmt.is_empty() {
            default_output = Some(trimmed_fmt.parse::<OutputFormat>()?);
        }
    }

    // Validate URL via SDK config before persisting.
    let validated = ClientConfig::new(&url)?;
    let profile_cfg = ProfileConfig {
        url: validated.base_url.clone(),
        default_output,
        timeout_secs,
    };

    cfg.upsert_profile(settings.profile.clone(), profile_cfg.clone());
    if matches.get_flag("set-default") || cfg.profiles.len() == 1 {
        cfg.default_profile = settings.profile.clone();
    }
    cfg.save_to(&ctx.config_path)?;

    let out = json!({
        "configured": true,
        "profile": settings.profile,
        "default_profile": cfg.default_profile,
        "url": profile_cfg.url,
        "default_output": profile_cfg.default_output.map(|f| f.as_str()),
        "timeout_secs": profile_cfg.timeout_secs,
        "config_path": ctx.config_path.display().to_string(),
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_auth<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (sub, sub_matches) = matches
        .subcommand()
        .ok_or_else(|| CliError::Usage("auth requires a subcommand".into()))?;

    match sub {
        "login" => handle_auth_login(sub_matches, settings, ctx).await,
        "logout" => handle_auth_logout(settings, ctx).await,
        "whoami" => handle_auth_whoami(settings, ctx).await,
        "refresh" => handle_auth_refresh(settings, ctx).await,
        op_name => invoke_catalog_operation("auth", op_name, sub_matches, settings, ctx).await,
    }
}

async fn handle_auth_login<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    if matches.get_flag("api-key-stdin") {
        let mut raw_key = String::new();
        ctx.stdin.read_to_string(&mut raw_key)?;
        let api_key = raw_key
            .lines()
            .next()
            .map(str::trim)
            .unwrap_or_default()
            .to_string();
        if api_key.is_empty() {
            return Err(CliError::Usage(
                "no API key provided on stdin for --api-key-stdin".into(),
            ));
        }

        clear_profile_secrets(&settings.profile, ctx.store)?;
        ctx.store
            .set_secret(&settings.profile, SecretKind::ApiKey, &api_key)?;

        let out = json!({
            "authenticated": true,
            "mode": "api_key",
            "profile": settings.profile,
            "url": settings.url,
            "credential": mask_secret(&api_key),
            "storage": "keyring",
        });
        return write_output(ctx.stdout, &out, settings.output);
    }

    let username = if let Some(u) = matches.get_one::<String>("username") {
        u.trim().to_string()
    } else if let Some(u) = (ctx.env_lookup)("ELDER_USERNAME")
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
    {
        u
    } else if ctx.stdin_is_tty {
        write!(ctx.stderr, "Username / Email: ")?;
        ctx.stderr.flush()?;
        let mut reader = io::BufReader::new(&mut *ctx.stdin);
        let mut line = String::new();
        reader.read_line(&mut line)?;
        line.trim().to_string()
    } else {
        return Err(CliError::Usage(
            "username required: pass --username or set ELDER_USERNAME".into(),
        ));
    };

    if username.is_empty() {
        return Err(CliError::Usage("username cannot be empty".into()));
    }

    let password = if matches.get_flag("password-stdin") {
        let mut buf = String::new();
        ctx.stdin.read_to_string(&mut buf)?;
        buf.lines().next().unwrap_or_default().to_string()
    } else if let Some(p) = (ctx.env_lookup)("ELDER_PASSWORD").filter(|s| !s.is_empty()) {
        p
    } else if ctx.stdin_is_tty {
        rpassword::prompt_password("Password: ")
            .map_err(|e| CliError::Io(io::Error::other(format!("password prompt failed: {e}"))))?
    } else {
        return Err(CliError::Usage(
            "password required: use --password-stdin, ELDER_PASSWORD, or an interactive terminal"
                .into(),
        ));
    };

    if password.is_empty() {
        return Err(CliError::Usage("password cannot be empty".into()));
    }

    let cfg = ClientConfig::new(&settings.url)?
        .with_timeout(Duration::from_secs(settings.timeout_secs))
        .with_user_agent(format!("elder-cli/{}", env!("CARGO_PKG_VERSION")));
    let client = ElderClient::new(cfg)?;

    let tokens = if matches.get_flag("portal") {
        let tenant = matches.get_one::<String>("tenant").map(String::as_str);
        client.portal_login(&username, &password, tenant).await?
    } else {
        client.login(&username, &password).await?
    };

    ctx.store.set_secret(
        &settings.profile,
        SecretKind::AccessToken,
        &tokens.access_token,
    )?;
    if let Some(ref refresh) = tokens.refresh_token {
        ctx.store
            .set_secret(&settings.profile, SecretKind::RefreshToken, refresh)?;
    }
    let _ = ctx
        .store
        .delete_secret(&settings.profile, SecretKind::ApiKey);

    info!(profile = %settings.profile, "stored authentication tokens in secure keyring");

    let out = json!({
        "authenticated": true,
        "profile": settings.profile,
        "url": settings.url,
        "token_type": tokens.token_type.unwrap_or_else(|| "Bearer".to_string()),
        "expires_in": tokens.expires_in,
        "access_token": mask_secret(&tokens.access_token),
        "has_refresh_token": tokens.refresh_token.is_some(),
        "storage": "keyring",
        "user": tokens.user,
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_auth_logout<R, W, E>(
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (client, resolved_auth) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let mut remote_revoked = false;
    if resolved_auth.auth.is_authenticated() {
        match client
            .request(HttpMethod::Post, "/api/v1/auth/logout", None, None)
            .await
        {
            Ok(_) => remote_revoked = true,
            Err(err) => {
                debug!(error = %err, "best-effort remote logout failed; clearing local credentials anyway");
            }
        }
    }

    clear_profile_secrets(&settings.profile, ctx.store)?;
    let out = json!({
        "logged_out": true,
        "profile": settings.profile,
        "remote_revoked": remote_revoked,
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_auth_whoami<R, W, E>(
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (client, resolved_auth) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let identity = client
        .request(HttpMethod::Get, "/api/v1/auth/me", None, None)
        .await?;

    let out = json!({
        "profile": settings.profile,
        "url": settings.url,
        "auth_source": resolved_auth.source.as_str(),
        "credential": resolved_auth.masked_preview,
        "identity": identity,
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_auth_refresh<R, W, E>(
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let refresh_token = (ctx.env_lookup)("ELDER_REFRESH_TOKEN")
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or(ctx
            .store
            .get_secret(&settings.profile, SecretKind::RefreshToken)?)
        .ok_or_else(|| {
            CliError::Auth(format!(
                "no refresh token found for profile '{}'; run 'elder auth login' first",
                settings.profile
            ))
        })?;

    let cfg = ClientConfig::new(&settings.url)?
        .with_timeout(Duration::from_secs(settings.timeout_secs))
        .with_user_agent(format!("elder-cli/{}", env!("CARGO_PKG_VERSION")));
    let client = ElderClient::new(cfg)?;
    let tokens = client.refresh_token(&refresh_token).await?;

    ctx.store.set_secret(
        &settings.profile,
        SecretKind::AccessToken,
        &tokens.access_token,
    )?;
    if let Some(ref rotated) = tokens.refresh_token {
        ctx.store
            .set_secret(&settings.profile, SecretKind::RefreshToken, rotated)?;
    }

    let out = json!({
        "refreshed": true,
        "profile": settings.profile,
        "url": settings.url,
        "expires_in": tokens.expires_in,
        "access_token": mask_secret(&tokens.access_token),
        "has_refresh_token": tokens.refresh_token.is_some(),
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_id<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let vid = matches
        .get_one::<String>("village_id")
        .ok_or_else(|| CliError::Usage("missing required <VILLAGE_ID>".into()))?;
    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let result = client.resolve_village_id(vid).await?;
    write_output(ctx.stdout, &result, settings.output)
}

async fn handle_search<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    if let Some((op_name, op_matches)) = matches.subcommand() {
        return invoke_catalog_operation("search", op_name, op_matches, settings, ctx).await;
    }

    let term = matches
        .get_one::<String>("search_term")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            CliError::Usage(
                "provide a search query (elder search <QUERY>) or a search subcommand".into(),
            )
        })?;

    let mut query = vec![("q".to_string(), term.to_string())];
    if let Some(extra) = matches.get_many::<String>("query") {
        query.extend(parse_query_pairs(extra.map(String::as_str))?);
    }

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let value = if matches.get_flag("all-pages") {
        let per_page = matches.get_one::<u32>("per-page").copied();
        let max_pages = matches.get_one::<u32>("max-pages").map(|n| *n as usize);
        let items = client
            .paginate("/api/v1/search", Some(&query), per_page, max_pages)
            .await?;
        Value::Array(items)
    } else {
        client
            .request(HttpMethod::Get, "/api/v1/search", Some(&query), None)
            .await?
    };

    write_output(ctx.stdout, &value, settings.output)
}

async fn handle_ai_research<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let query = matches
        .get_one::<String>("query")
        .ok_or_else(|| CliError::Usage("missing required <QUERY>".into()))?;
    let model = matches.get_one::<String>("model").map(String::as_str);
    let max_steps = matches.get_one::<u32>("max-steps").copied();

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let result = client.ai_research(query, model, None, max_steps).await?;
    write_output(ctx.stdout, &result, settings.output)
}

async fn handle_runs<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (sub, sub_matches) = matches
        .subcommand()
        .ok_or_else(|| CliError::Usage("runs requires a subcommand".into()))?;

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;

    match sub {
        "list" => {
            let params = ListRunFunctionsParams {
                page: sub_matches.get_one::<u32>("page").copied(),
                per_page: sub_matches.get_one::<u32>("per-page").copied(),
                runtime: sub_matches.get_one::<String>("runtime").cloned(),
                search: sub_matches.get_one::<String>("search").cloned(),
                worker_cluster: sub_matches.get_one::<String>("worker-cluster").cloned(),
                is_enabled: sub_matches.get_one::<bool>("is-enabled").copied(),
            };
            let page = client.runs().list(&params).await?;
            let val = serde_json::to_value(&page).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "get" => {
            let func_id = *sub_matches
                .get_one::<i64>("func_id")
                .ok_or_else(|| CliError::Usage("missing required <FUNC_ID>".into()))?;
            let func = client.runs().get(func_id).await?;
            let val = serde_json::to_value(&func).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "create" => {
            let name = sub_matches
                .get_one::<String>("name")
                .ok_or_else(|| CliError::Usage("missing required --name".into()))?
                .clone();
            let runtime = sub_matches
                .get_one::<String>("runtime")
                .ok_or_else(|| CliError::Usage("missing required --runtime".into()))?
                .clone();
            let code = read_code_source(
                sub_matches.get_one::<String>("code").map(String::as_str),
                sub_matches
                    .get_one::<String>("code-file")
                    .map(String::as_str),
                ctx.stdin,
            )?
            .ok_or_else(|| CliError::Usage("provide either --code or --code-file".into()))?;

            let env_vars = sub_matches
                .get_many::<String>("env")
                .map(|vals| parse_env_pairs(vals.map(String::as_str)))
                .transpose()?;

            let mut req = CreateRunFunctionRequest::new(name, runtime, code);
            req.description = sub_matches.get_one::<String>("description").cloned();
            req.entrypoint = sub_matches.get_one::<String>("entrypoint").cloned();
            req.timeout_seconds = sub_matches.get_one::<u32>("timeout-seconds").copied();
            req.memory_mb = sub_matches.get_one::<u32>("memory-mb").copied();
            req.env_vars = env_vars;
            req.schedule_cron = sub_matches.get_one::<String>("schedule-cron").cloned();
            req.execution_namespace = sub_matches
                .get_one::<String>("execution-namespace")
                .cloned();
            req.worker_cluster = sub_matches.get_one::<String>("worker-cluster").cloned();
            req.is_enabled = sub_matches.get_one::<bool>("is-enabled").copied();

            let created = client.runs().create(&req).await?;
            let val =
                serde_json::to_value(&created).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "update" => {
            let func_id = *sub_matches
                .get_one::<i64>("func_id")
                .ok_or_else(|| CliError::Usage("missing required <FUNC_ID>".into()))?;
            let code = read_code_source(
                sub_matches.get_one::<String>("code").map(String::as_str),
                sub_matches
                    .get_one::<String>("code-file")
                    .map(String::as_str),
                ctx.stdin,
            )?;
            let env_vars = sub_matches
                .get_many::<String>("env")
                .map(|vals| parse_env_pairs(vals.map(String::as_str)))
                .transpose()?;

            let req = UpdateRunFunctionRequest {
                name: sub_matches.get_one::<String>("name").cloned(),
                description: sub_matches.get_one::<String>("description").cloned(),
                runtime: sub_matches.get_one::<String>("runtime").cloned(),
                code,
                entrypoint: sub_matches.get_one::<String>("entrypoint").cloned(),
                timeout_seconds: sub_matches.get_one::<u32>("timeout-seconds").copied(),
                memory_mb: sub_matches.get_one::<u32>("memory-mb").copied(),
                env_vars,
                schedule_cron: sub_matches.get_one::<String>("schedule-cron").cloned(),
                execution_namespace: sub_matches
                    .get_one::<String>("execution-namespace")
                    .cloned(),
                worker_cluster: sub_matches.get_one::<String>("worker-cluster").cloned(),
                is_enabled: sub_matches.get_one::<bool>("is-enabled").copied(),
            };

            let updated = client.runs().update(func_id, &req).await?;
            let val =
                serde_json::to_value(&updated).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "delete" => {
            let func_id = *sub_matches
                .get_one::<i64>("func_id")
                .ok_or_else(|| CliError::Usage("missing required <FUNC_ID>".into()))?;
            client.runs().delete(func_id).await?;
            let out = json!({
                "deleted": true,
                "id": func_id,
            });
            write_output(ctx.stdout, &out, settings.output)
        }
        "invoke" => {
            let func_id = *sub_matches
                .get_one::<i64>("func_id")
                .ok_or_else(|| CliError::Usage("missing required <FUNC_ID>".into()))?;
            let input_data = sub_matches
                .get_one::<String>("input")
                .map(|raw| parse_json_input(raw, ctx.stdin))
                .transpose()?;
            let req = InvokeRunRequest {
                input_data,
                trigger_type: sub_matches.get_one::<String>("trigger-type").cloned(),
            };
            let resp = client.runs().invoke(func_id, &req).await?;
            let val = serde_json::to_value(&resp).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "executions" => {
            let function_id = sub_matches
                .get_one::<i64>("func_id")
                .or_else(|| sub_matches.get_one::<i64>("function-id"))
                .copied();
            let params = ListExecutionsParams {
                page: sub_matches.get_one::<u32>("page").copied(),
                per_page: sub_matches.get_one::<u32>("per-page").copied(),
                function_id,
                status: sub_matches.get_one::<String>("status").cloned(),
                trigger_type: sub_matches.get_one::<String>("trigger-type").cloned(),
            };
            let page = client.runs().list_executions(&params).await?;
            let val = serde_json::to_value(&page).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "execution" => {
            let execution_id = sub_matches
                .get_one::<String>("execution_id")
                .ok_or_else(|| CliError::Usage("missing required <EXECUTION_ID>".into()))?;
            let exec = client.runs().get_execution(execution_id).await?;
            let val = serde_json::to_value(&exec).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        other => Err(CliError::Usage(format!(
            "unknown runs subcommand {other:?}"
        ))),
    }
}

async fn handle_workers<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (sub, sub_matches) = matches
        .subcommand()
        .ok_or_else(|| CliError::Usage("workers requires a subcommand".into()))?;

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;

    match sub {
        "list" => {
            let params = ListRemoteWorkersParams {
                worker_type: sub_matches.get_one::<String>("type").cloned(),
                cluster: sub_matches.get_one::<String>("cluster").cloned(),
                status: sub_matches.get_one::<String>("status").cloned(),
                page: sub_matches.get_one::<u32>("page").copied(),
                per_page: sub_matches.get_one::<u32>("per-page").copied(),
            };
            let resp = client.workers().list(&params).await?;
            let val = serde_json::to_value(&resp).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "get" => {
            let id = sub_matches
                .get_one::<String>("id")
                .ok_or_else(|| CliError::Usage("missing required <ID>".into()))?;
            let worker = client.workers().get(id).await?;
            let val = serde_json::to_value(&worker).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "onboard" => {
            let name = sub_matches
                .get_one::<String>("name")
                .ok_or_else(|| CliError::Usage("missing required --name".into()))?
                .clone();
            let worker_type = sub_matches
                .get_one::<String>("type")
                .ok_or_else(|| CliError::Usage("missing required --type".into()))?
                .clone();
            let mut req = OnboardRemoteWorkerRequest::new(name, worker_type);
            req.cluster = sub_matches.get_one::<String>("cluster").cloned();
            req.poll_interval_seconds = sub_matches.get_one::<u32>("poll-interval").copied();
            req.progress_interval_seconds =
                sub_matches.get_one::<u32>("progress-interval").copied();
            req.max_concurrent = sub_matches.get_one::<u32>("max-concurrent").copied();

            let resp = client.workers().onboard(&req).await?;
            let val = serde_json::to_value(&resp).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "update" => {
            let id = sub_matches
                .get_one::<String>("id")
                .ok_or_else(|| CliError::Usage("missing required <ID>".into()))?;
            let req = UpdateRemoteWorkerRequest {
                name: sub_matches.get_one::<String>("name").cloned(),
                cluster: sub_matches.get_one::<String>("cluster").cloned(),
                is_enabled: sub_matches.get_one::<bool>("enabled").copied(),
                poll_interval_seconds: sub_matches.get_one::<u32>("poll-interval").copied(),
                progress_interval_seconds: sub_matches.get_one::<u32>("progress-interval").copied(),
                max_concurrent: sub_matches.get_one::<u32>("max-concurrent").copied(),
                config: None,
                capabilities: None,
            };
            let updated = client.workers().update(id, &req).await?;
            let val =
                serde_json::to_value(&updated).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "rotate-token" => {
            let id = sub_matches
                .get_one::<String>("id")
                .ok_or_else(|| CliError::Usage("missing required <ID>".into()))?;
            let rotated = client.workers().rotate_token(id).await?;
            let val =
                serde_json::to_value(&rotated).map_err(|e| CliError::Format(e.to_string()))?;
            write_output(ctx.stdout, &val, settings.output)
        }
        "offboard" => {
            let id = sub_matches
                .get_one::<String>("id")
                .ok_or_else(|| CliError::Usage("missing required <ID>".into()))?;
            client.workers().offboard(id).await?;
            let out = json!({
                "offboarded": true,
                "id": id,
            });
            write_output(ctx.stdout, &out, settings.output)
        }
        other => Err(CliError::Usage(format!(
            "unknown workers subcommand {other:?}"
        ))),
    }
}

fn read_code_source<R: Read>(
    inline_code: Option<&str>,
    code_file: Option<&str>,
    stdin: &mut R,
) -> Result<Option<String>> {
    if let Some(code) = inline_code {
        return Ok(Some(code.to_string()));
    }
    if let Some(path) = code_file {
        let trimmed = path.trim();
        if trimmed == "-" {
            let mut buf = String::new();
            stdin.read_to_string(&mut buf)?;
            return Ok(Some(buf));
        }
        let content = fs::read_to_string(trimmed)
            .map_err(|e| CliError::Usage(format!("failed to read --code-file '{trimmed}': {e}")))?;
        return Ok(Some(content));
    }
    Ok(None)
}

fn parse_env_pairs<'a>(
    pairs: impl IntoIterator<Item = &'a str>,
) -> Result<BTreeMap<String, Value>> {
    let mut map = BTreeMap::new();
    for raw in pairs {
        let Some((key, val)) = raw.split_once('=') else {
            return Err(CliError::Usage(format!(
                "invalid --env entry {raw:?}; expected KEY=VAL"
            )));
        };
        let key = key.trim();
        if key.is_empty() {
            return Err(CliError::Usage(format!(
                "invalid --env entry {raw:?}; key cannot be empty"
            )));
        }
        map.insert(key.to_string(), Value::String(val.to_string()));
    }
    Ok(map)
}

fn parse_json_input<R: Read>(raw: &str, stdin: &mut R) -> Result<Value> {
    let trimmed = raw.trim();
    let json_text = if trimmed == "-" {
        let mut buf = String::new();
        stdin.read_to_string(&mut buf)?;
        buf
    } else if let Some(path) = trimmed.strip_prefix('@') {
        fs::read_to_string(path)
            .map_err(|e| CliError::Usage(format!("failed to read --input file '{path}': {e}")))?
    } else {
        trimmed.to_string()
    };

    serde_json::from_str::<Value>(&json_text)
        .map_err(|e| CliError::Usage(format!("invalid JSON for --input: {e}")))
}

async fn handle_api<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let method_raw = matches
        .get_one::<String>("method")
        .ok_or_else(|| CliError::Usage("missing required <METHOD>".into()))?;
    let method: HttpMethod = method_raw.parse()?;
    let path = matches
        .get_one::<String>("path")
        .ok_or_else(|| CliError::Usage("missing required <PATH>".into()))?;

    let query = matches
        .get_many::<String>("query")
        .map(|vals| parse_query_pairs(vals.map(String::as_str)))
        .transpose()?;
    let body_spec = matches.get_one::<String>("body").map(String::as_str);
    let fields: Vec<&str> = matches
        .get_many::<String>("field")
        .map(|vals| vals.map(String::as_str).collect())
        .unwrap_or_default();
    let body = parse_request_body(body_spec, fields, ctx.stdin)?;

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;
    let value = if matches.get_flag("all-pages") {
        if method != HttpMethod::Get {
            return Err(CliError::Usage(
                "--all-pages is only supported with GET requests".into(),
            ));
        }
        let per_page = matches.get_one::<u32>("per-page").copied();
        let max_pages = matches.get_one::<u32>("max-pages").map(|n| *n as usize);
        let items = client
            .paginate(path, query.as_deref(), per_page, max_pages)
            .await?;
        Value::Array(items)
    } else {
        client
            .request(method, path, query.as_deref(), body.as_ref())
            .await?
    };

    write_output(ctx.stdout, &value, settings.output)
}

async fn handle_version<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let cat = Catalog::embedded();
    let cli_version = env!("CARGO_PKG_VERSION");

    let mut payload = Map::new();
    payload.insert("cli_version".into(), Value::String(cli_version.to_string()));
    payload.insert(
        "catalog_services".into(),
        Value::Number((cat.services().len() as u64).into()),
    );
    payload.insert(
        "catalog_operations".into(),
        Value::Number((cat.len() as u64).into()),
    );

    if matches.get_flag("check-updates") {
        let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;
        match client.status().await {
            Ok(status_val) => {
                let server_version = status_val
                    .get("latest_cli_version")
                    .or_else(|| status_val.get("version"))
                    .or_else(|| status_val.get("app_version"))
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);

                let update_available = server_version
                    .as_deref()
                    .map(|sv| sv.trim_start_matches('v') != cli_version.trim_start_matches('v'));

                payload.insert("server_url".into(), Value::String(settings.url.clone()));
                if let Some(sv) = server_version {
                    payload.insert("server_version".into(), Value::String(sv));
                }
                if let Some(avail) = update_available {
                    payload.insert("update_available".into(), Value::Bool(avail));
                }
                payload.insert("server_status".into(), status_val);
            }
            Err(err) => {
                payload.insert("server_url".into(), Value::String(settings.url.clone()));
                payload.insert("update_check_error".into(), Value::String(err.to_string()));
            }
        }
    }

    write_output(ctx.stdout, &Value::Object(payload), settings.output)
}

fn handle_completions<R, W, E>(
    matches: &ArgMatches,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let shell_name = matches
        .get_one::<String>("shell")
        .ok_or_else(|| CliError::Usage("missing required <SHELL>".into()))?;
    let shell = match shell_name.as_str() {
        "bash" => Shell::Bash,
        "zsh" => Shell::Zsh,
        "fish" => Shell::Fish,
        "powershell" => Shell::PowerShell,
        other => {
            return Err(CliError::Usage(format!("unsupported shell {other:?}")));
        }
    };
    let mut cmd = build_cli();
    generate(shell, &mut cmd, "elder", ctx.stdout);
    Ok(())
}

async fn handle_services<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    if let Some((op_name, op_matches)) = matches.subcommand() {
        return invoke_catalog_operation("services", op_name, op_matches, settings, ctx).await;
    }

    let cat = Catalog::embedded();
    let rows: Vec<Value> = cat
        .services()
        .into_iter()
        .map(|svc| {
            json!({
                "service": svc,
                "operations": cat.operations(svc).len(),
            })
        })
        .collect();

    let out = json!({
        "total_services": rows.len(),
        "total_operations": cat.len(),
        "services": rows,
    });
    write_output(ctx.stdout, &out, settings.output)
}

fn handle_describe<R, W, E>(
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let cat = Catalog::embedded();
    let service = matches
        .get_one::<String>("service")
        .ok_or_else(|| CliError::Usage("missing required <SERVICE>".into()))?;

    if let Some(operation) = matches.get_one::<String>("operation") {
        let op = cat
            .find(service, operation)
            .ok_or_else(|| CliError::Usage(format!("unknown operation '{service}.{operation}'")))?;
        let cli_flags: Vec<String> = op
            .path_params
            .iter()
            .map(|p| format!("--{}", path_param_flag_name(p)))
            .collect();
        let example_flags = op
            .path_params
            .iter()
            .map(|p| format!("--{} <{}>", path_param_flag_name(p), p))
            .collect::<Vec<_>>()
            .join(" ");
        let example = if example_flags.is_empty() {
            format!("elder {} {}", op.service, op.operation)
        } else {
            format!("elder {} {} {}", op.service, op.operation, example_flags)
        };

        let out = json!({
            "service": op.service,
            "operation": op.operation,
            "method": op.method.as_str(),
            "path": op.path,
            "path_params": op.path_params,
            "cli_flags": cli_flags,
            "has_body": op.has_body,
            "summary": op.summary,
            "example": example,
        });
        return write_output(ctx.stdout, &out, settings.output);
    }

    let ops = cat.operations(service);
    if ops.is_empty() {
        return Err(CliError::Usage(format!(
            "unknown service '{service}'; run 'elder services' to list available services"
        )));
    }

    let rows: Vec<Value> = ops
        .iter()
        .map(|op| {
            json!({
                "operation": op.operation,
                "method": op.method.as_str(),
                "path": op.path,
                "path_params": op.path_params,
                "has_body": op.has_body,
                "summary": op.summary,
            })
        })
        .collect();

    let out = json!({
        "service": ops[0].service,
        "count": rows.len(),
        "operations": rows,
    });
    write_output(ctx.stdout, &out, settings.output)
}

async fn handle_catalog_service<R, W, E>(
    service: &str,
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let (op_name, op_matches) = matches
        .subcommand()
        .ok_or_else(|| CliError::Usage(format!("service '{service}' requires an operation")))?;
    invoke_catalog_operation(service, op_name, op_matches, settings, ctx).await
}

async fn invoke_catalog_operation<R, W, E>(
    service: &str,
    operation: &str,
    matches: &ArgMatches,
    settings: &ResolvedSettings,
    ctx: &mut RunContext<'_, R, W, E>,
) -> Result<()>
where
    R: Read,
    W: Write,
    E: Write,
{
    let cat = Catalog::embedded();
    let op = cat
        .find(service, operation)
        .ok_or_else(|| CliError::Usage(format!("unknown operation '{service}.{operation}'")))?;

    let mut path_params = HashMap::with_capacity(op.path_params.len());
    for param in &op.path_params {
        let arg_id = format!("path:{param}");
        let val = matches.get_one::<String>(&arg_id).ok_or_else(|| {
            CliError::Usage(format!(
                "missing required path parameter '--{}'",
                path_param_flag_name(param)
            ))
        })?;
        path_params.insert(param.clone(), val.clone());
    }

    let query = matches
        .get_many::<String>("query")
        .map(|vals| parse_query_pairs(vals.map(String::as_str)))
        .transpose()?;

    let has_body_args = op.has_body || op.method != HttpMethod::Get;
    let body = if has_body_args {
        let body_spec = matches.get_one::<String>("body").map(String::as_str);
        let fields: Vec<&str> = matches
            .get_many::<String>("field")
            .map(|vals| vals.map(String::as_str).collect())
            .unwrap_or_default();
        parse_request_body(body_spec, fields, ctx.stdin)?
    } else {
        None
    };

    let (client, _) = build_client(settings, ctx.store, ctx.env_lookup)?;

    let value = if op.method == HttpMethod::Get && matches.get_flag("all-pages") {
        let per_page = matches.get_one::<u32>("per-page").copied();
        let max_pages = matches.get_one::<u32>("max-pages").map(|n| *n as usize);
        let items = client
            .invoke_paginate(
                &op.service,
                &op.operation,
                &path_params,
                query.as_deref(),
                per_page,
                max_pages,
            )
            .await?;
        Value::Array(items)
    } else {
        client
            .invoke(
                &op.service,
                &op.operation,
                &path_params,
                query.as_deref(),
                body.as_ref(),
            )
            .await?
    };

    write_output(ctx.stdout, &value, settings.output)
}
