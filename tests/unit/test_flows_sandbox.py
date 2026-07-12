"""Flows invoker sandbox + gitops unit tests (no DB required).

regression: flows-invoker-slice2 — the sandbox is the security boundary for
user-defined stage commands; every policy layer gets a direct test.
"""

import os
import subprocess

import pytest

from apps.flows_invoker import gitops
from apps.flows_invoker.sandbox import (
    DEFAULT_ALLOWED_BINARIES,
    SandboxError,
    build_env,
    parse_command,
    resolve_executable,
    run_command,
)


class TestParseCommand:
    def test_shell_metacharacters_are_literal(self):
        """`;`, `|`, `$()` must be plain argv tokens, not shell syntax."""
        argv = parse_command("pytest -x; rm -rf / | echo $(whoami)")
        assert argv[0] == "pytest"
        assert ";" in argv[1] or argv[2] == "rm" or ";" in " ".join(argv)
        # No shell ever runs — the metacharacters survive as literal args.
        assert "$(whoami)" in argv

    def test_empty_command_rejected(self):
        with pytest.raises(SandboxError):
            parse_command("   ")

    def test_unparseable_command_rejected(self):
        with pytest.raises(SandboxError):
            parse_command('pytest "unterminated')


class TestResolveExecutable:
    def test_allowlisted_binary_ok(self, tmp_path):
        resolve_executable("pytest", str(tmp_path))

    def test_unlisted_binary_rejected(self, tmp_path):
        with pytest.raises(SandboxError, match="not allowlisted"):
            resolve_executable("curl", str(tmp_path))

    def test_absolute_path_outside_workspace_rejected(self, tmp_path):
        with pytest.raises(SandboxError, match="escapes workspace"):
            resolve_executable("/bin/rm", str(tmp_path))

    def test_relative_escape_rejected(self, tmp_path):
        with pytest.raises(SandboxError, match="escapes workspace"):
            resolve_executable("../../../bin/rm", str(tmp_path))

    def test_symlink_escape_rejected(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "link").symlink_to(outside)
        with pytest.raises(SandboxError, match="escapes workspace"):
            resolve_executable("./link/evil.sh", str(ws))

    def test_script_inside_workspace_ok(self, tmp_path):
        (tmp_path / "run.sh").write_text("#!/bin/sh\n")
        resolve_executable("./run.sh", str(tmp_path))

    def test_dash_argv0_rejected(self, tmp_path):
        with pytest.raises(SandboxError, match="argument-injection"):
            resolve_executable("--version", str(tmp_path))

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLOWS_ALLOWED_BINARIES", "onlythis")
        with pytest.raises(SandboxError):
            resolve_executable("pytest", str(tmp_path))
        resolve_executable("onlythis", str(tmp_path))

    def test_extra_allowed_permits_git(self, tmp_path):
        assert "git" not in DEFAULT_ALLOWED_BINARIES
        with pytest.raises(SandboxError):
            resolve_executable("git", str(tmp_path))
        resolve_executable("git", str(tmp_path), extra_allowed=frozenset({"git"}))


class TestBuildEnv:
    def test_parent_env_not_inherited(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
        env = build_env(str(tmp_path))
        assert "DATABASE_URL" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert env["HOME"] == str(tmp_path)

    def test_loader_hijack_keys_blocked(self, tmp_path):
        env = build_env(
            str(tmp_path),
            {
                "LD_PRELOAD": "/tmp/evil.so",
                "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
                "BASH_ENV": "/tmp/evil.sh",
                "PATH": "/tmp/evil",
                "IFS": ";",
                "SAFE_VAR": "ok",
            },
        )
        assert "LD_PRELOAD" not in env
        assert "DYLD_INSERT_LIBRARIES" not in env
        assert "BASH_ENV" not in env
        assert "IFS" not in env
        assert env["PATH"] != "/tmp/evil"  # caller cannot override PATH
        assert env["SAFE_VAR"] == "ok"

    def test_non_string_values_dropped(self, tmp_path):
        env = build_env(str(tmp_path), {"N": 5, "OK": "yes"})
        assert "N" not in env and env["OK"] == "yes"


class TestRunCommand:
    def test_success_and_output(self, tmp_path):
        result = run_command(["python3", "-c", "print('hello-sandbox')"], str(tmp_path))
        assert result.success
        assert "hello-sandbox" in result.stdout

    def test_nonzero_exit(self, tmp_path):
        result = run_command(
            ["python3", "-c", "import sys; sys.exit(3)"], str(tmp_path)
        )
        assert not result.success
        assert result.returncode == 3

    def test_timeout_kills_process_group(self, tmp_path):
        result = run_command(
            ["python3", "-c", "import time; time.sleep(30)"],
            str(tmp_path),
            timeout=1,
        )
        assert result.timed_out
        assert not result.success
        assert result.duration_seconds < 10

    def test_output_truncation(self, tmp_path):
        result = run_command(
            ["python3", "-c", "print('x' * 10000)"],
            str(tmp_path),
            max_output_bytes=100,
        )
        assert result.truncated
        assert "[output truncated]" in result.stdout

    def test_rejected_binary_never_starts(self, tmp_path):
        with pytest.raises(SandboxError):
            run_command(["nc", "-l", "4444"], str(tmp_path))

    def test_missing_workspace_rejected(self):
        with pytest.raises(SandboxError, match="workspace does not exist"):
            run_command(["pytest"], "/nonexistent-workspace-xyz")

    def test_secrets_not_visible_to_child(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
        result = run_command(
            ["python3", "-c", "import os; print(sorted(os.environ))"],
            str(tmp_path),
        )
        assert result.success
        assert "DATABASE_URL" not in result.stdout


class TestGitOpsValidation:
    def test_https_url_ok(self):
        assert gitops.validate_repo_url("https://github.com/org/repo.git")

    def test_http_rejected(self):
        with pytest.raises(gitops.GitOpsError, match="scheme"):
            gitops.validate_repo_url("http://github.com/org/repo.git")

    def test_file_rejected_by_default(self):
        with pytest.raises(gitops.GitOpsError, match="scheme"):
            gitops.validate_repo_url("file:///tmp/repo")

    def test_scheme_env_override(self, monkeypatch):
        monkeypatch.setenv("FLOWS_GIT_ALLOWED_SCHEMES", "file,https")
        assert gitops.validate_repo_url("file:///tmp/repo")

    def test_embedded_credentials_rejected(self):
        with pytest.raises(gitops.GitOpsError, match="embed"):
            gitops.validate_repo_url("https://user:tok@github.com/org/repo.git")

    def test_ext_transport_rejected(self):
        with pytest.raises(gitops.GitOpsError, match="scheme"):
            gitops.validate_repo_url("ext::sh -c whoami")

    def test_ref_validation(self):
        assert gitops.validate_ref("release/v4.0.X") == "release/v4.0.X"
        for bad in ("-upload-pack=/bin/sh", "a..b", "a b", "a@{1}", "x.lock", ""):
            with pytest.raises(gitops.GitOpsError):
                gitops.validate_ref(bad)

    def test_sha_validation(self):
        assert gitops.validate_commit_sha("deadbeef") == "deadbeef"
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_commit_sha("$(reboot)")

    def test_scrub(self):
        assert gitops.scrub("fatal: token tok123 rejected", "tok123") == (
            "fatal: token *** rejected"
        )
        assert gitops.scrub("no token here", None) == "no token here"


def _git(cwd, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def make_git_origin(tmp_path):
    """Create a bare origin with `dev` (ahead) and `prod` branches.

    Returns (repo_url, seed_dir).
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main", ".")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=prod", ".")
    (seed / "README.md").write_text("v1\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "checkout", "-b", "dev")
    (seed / "feature.txt").write_text("new feature\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "feature")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "--all", "origin")
    return f"file://{origin}", seed


class TestGitOpsFlow:
    @pytest.fixture(autouse=True)
    def _allow_file_scheme(self, monkeypatch):
        monkeypatch.setenv("FLOWS_GIT_ALLOWED_SCHEMES", "file,https")

    def test_clone_merge_and_push(self, tmp_path, monkeypatch):
        repo_url, _ = make_git_origin(tmp_path)
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path))
        ws = gitops.create_workspace()
        try:
            merge_sha = gitops.clone_and_merge(ws, repo_url, "dev", "prod")
            assert merge_sha and ws.pre_merge_sha != merge_sha
            assert "feature" in gitops.merge_diff(ws)
            gitops.push_target(ws, "prod")
        finally:
            ws.cleanup()
        # Origin's prod ref now points at the merge result.
        out = subprocess.run(
            ["git", "rev-parse", "prod"],
            cwd=repo_url.removeprefix("file://"),
            check=True,
            capture_output=True,
            text=True,
        )
        assert out.stdout.strip() == merge_sha
        assert not os.path.exists(ws.root)

    def test_merge_conflict_fails_cleanly(self, tmp_path, monkeypatch):
        repo_url, seed = make_git_origin(tmp_path)
        # Create a conflicting change on prod.
        _git(seed, "checkout", "prod")
        (seed / "feature.txt").write_text("conflicting\n")
        _git(seed, "add", ".")
        _git(seed, "commit", "-m", "conflict")
        _git(seed, "push", "origin", "prod")
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path))
        ws = gitops.create_workspace()
        try:
            with pytest.raises(gitops.GitOpsError, match="merge"):
                gitops.clone_and_merge(ws, repo_url, "dev", "prod")
        finally:
            ws.cleanup()

    def test_unknown_source_commit_fails(self, tmp_path, monkeypatch):
        repo_url, _ = make_git_origin(tmp_path)
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path))
        ws = gitops.create_workspace()
        try:
            with pytest.raises(gitops.GitOpsError):
                gitops.clone_and_merge(
                    ws, repo_url, "dev", "prod", source_commit="deadbeefcafe"
                )
        finally:
            ws.cleanup()
