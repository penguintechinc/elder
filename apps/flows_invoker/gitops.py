"""Git operations for the Flows invoker (slice 2).

Clones the flow's repository into an ephemeral workspace, merges the
promotion's source ref into the target stage branch, and (after tests pass)
pushes the merge. Every git invocation goes through the sandbox
(:mod:`apps.flows_invoker.sandbox`) with ``git`` explicitly permitted via
``extra_allowed`` — git is deliberately NOT in the user-facing binary
allowlist, so stage test commands can never invoke it with our credentials.

Credential handling — the access token:

- is passed to git via the ``FLOWS_GIT_TOKEN`` environment variable and a
  **static** ``GIT_ASKPASS`` helper (the script contains no secret), so it is
  never written to disk, never appears in argv (``/proc/*/cmdline``), and
  never lands in ``.git/config``;
- is only present in the environment of *git* subprocesses — test commands
  get a fresh minimal env (:func:`sandbox.build_env`) without it;
- is scrubbed from any captured output before logging, defensively.

Input hardening: repository URLs are restricted to https (no embedded
userinfo), and branch names / commit SHAs are validated against git ref
syntax with a leading ``-`` ban (argument injection) before ever reaching an
argv list.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from apps.flows_invoker.sandbox import CommandResult, build_env, run_command

# git must be allowed for our own invocations without exposing it to
# user-defined stage commands.
_GIT_ALLOWED = frozenset({"git"})

# Static askpass helper — echoes the token for both the username and password
# prompts (works for GitHub PATs and GitLab tokens). Contains NO secret.
_ASKPASS_SCRIPT = '#!/bin/sh\necho "$FLOWS_GIT_TOKEN"\n'

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
# Conservative subset of valid git ref characters.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

DEFAULT_GIT_TIMEOUT = 300


class GitOpsError(Exception):
    """A git operation was rejected or failed. ``output`` is scrubbed."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def allowed_url_schemes() -> frozenset:
    """URL schemes permitted for repository clones (https unless overridden)."""
    raw = os.environ.get("FLOWS_GIT_ALLOWED_SCHEMES")
    if raw:
        return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
    return frozenset({"https"})


def validate_repo_url(url: str) -> str:
    """Validate a repository URL (scheme allowlist, no embedded credentials)."""
    if not url or not url.strip():
        raise GitOpsError("repository URL is empty")
    url = url.strip()
    parts = urlsplit(url)
    schemes = allowed_url_schemes()
    if parts.scheme.lower() not in schemes:
        raise GitOpsError(
            f"repository URL scheme {parts.scheme!r} not permitted "
            f"(allowed: {sorted(schemes)})"
        )
    if parts.username or parts.password:
        # Credentials belong in iceflows_credentials, never in the URL —
        # embedded userinfo would persist into .git/config and logs.
        raise GitOpsError("repository URL must not embed credentials")
    if parts.scheme.lower() in ("http", "https") and not parts.hostname:
        raise GitOpsError("repository URL has no host")
    return url


def validate_ref(name: str, *, what: str = "ref") -> str:
    """Validate a branch name / ref against git syntax + argv injection."""
    if not name or not name.strip():
        raise GitOpsError(f"{what} is empty")
    name = name.strip()
    if name.startswith("-"):
        raise GitOpsError(f"{what} {name!r} starts with '-'")
    if (
        not _REF_RE.match(name)
        or ".." in name
        or "//" in name
        or "@{" in name
        or name.endswith((".lock", "/", "."))
    ):
        raise GitOpsError(f"{what} {name!r} is not a valid git ref")
    return name


def validate_commit_sha(sha: str) -> str:
    """Validate a commit SHA (hex, 7-64 chars)."""
    if not sha or not _SHA_RE.match(sha.strip()):
        raise GitOpsError(f"invalid commit SHA {sha!r}")
    return sha.strip()


def scrub(text: str, token: str | None) -> str:
    """Remove the credential token from captured output, defensively."""
    if not text or not token:
        return text or ""
    return text.replace(token, "***")


@dataclass(slots=True)
class GitWorkspace:
    """Ephemeral checkout for one execution. ``repo_dir`` is where tests run."""

    root: str
    repo_dir: str
    askpass_path: str
    token: str | None = None
    pre_merge_sha: str | None = None
    merge_sha: str | None = None

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def create_workspace(token: str | None = None) -> GitWorkspace:
    """Create an ephemeral workspace under ``FLOWS_WORKSPACE_ROOT``."""
    ws_root = os.environ.get("FLOWS_WORKSPACE_ROOT") or None
    root = tempfile.mkdtemp(prefix="flows-", dir=ws_root)
    # 0o700 is owner-only rwx — a DIRECTORY needs the exec bit to be
    # traversable, and group/other get nothing. (The rule's 0o644 suggestion
    # applies to plain files.)
    os.chmod(root, 0o700)  # nosemgrep
    repo_dir = os.path.join(root, "repo")
    askpass_path = os.path.join(root, "askpass.sh")
    with open(askpass_path, "w", encoding="utf-8") as f:
        f.write(_ASKPASS_SCRIPT)
    os.chmod(askpass_path, stat.S_IRWXU)  # 0o700 — owner only
    return GitWorkspace(
        root=root, repo_dir=repo_dir, askpass_path=askpass_path, token=token
    )


def _git_env(ws: GitWorkspace, *, with_token: bool) -> dict[str, str]:
    """Minimal env for git subprocesses (token only when needed)."""
    extra: dict[str, str] = {
        "GIT_ASKPASS": ws.askpass_path,
        "GIT_CONFIG_NOSYSTEM": "1",
        # Deterministic merge-commit identity.
        "GIT_AUTHOR_NAME": "Elder Flows",
        "GIT_AUTHOR_EMAIL": "flows@elder.invalid",
        "GIT_COMMITTER_NAME": "Elder Flows",
        "GIT_COMMITTER_EMAIL": "flows@elder.invalid",
    }
    if with_token and ws.token:
        extra["FLOWS_GIT_TOKEN"] = ws.token
    return build_env(ws.root, extra)


def _run_git(
    ws: GitWorkspace,
    args: list[str],
    *,
    cwd: str | None = None,
    with_token: bool = False,
    timeout: int = DEFAULT_GIT_TIMEOUT,
) -> CommandResult:
    """Run one git command through the sandbox; raise GitOpsError on failure."""
    result = run_command(
        ["git", *args],
        cwd or ws.root,
        timeout=timeout,
        env=_git_env(ws, with_token=with_token),
        extra_allowed=_GIT_ALLOWED,
    )
    if not result.success:
        raise GitOpsError(
            f"git {args[0]} failed (rc={result.returncode}"
            f"{', timed out' if result.timed_out else ''})",
            output=scrub(result.stdout, ws.token),
        )
    return result


def clone_and_merge(
    ws: GitWorkspace,
    repo_url: str,
    source_branch: str,
    target_branch: str,
    source_commit: str | None = None,
    *,
    timeout: int = DEFAULT_GIT_TIMEOUT,
) -> str:
    """Clone the repo, check out the target branch, merge the source ref.

    Returns the merge commit SHA. The merge is local only — nothing is pushed
    until :func:`push_target` is called after tests/review pass.
    """
    repo_url = validate_repo_url(repo_url)
    source_branch = validate_ref(source_branch, what="source branch")
    target_branch = validate_ref(target_branch, what="target branch")
    if source_commit:
        source_commit = validate_commit_sha(source_commit)

    _run_git(
        ws,
        ["clone", "--quiet", "--no-tags", "--", repo_url, ws.repo_dir],
        with_token=True,
        timeout=timeout,
    )
    _run_git(
        ws,
        ["checkout", "--quiet", "-B", target_branch, f"origin/{target_branch}"],
        cwd=ws.repo_dir,
        timeout=timeout,
    )
    head = _run_git(ws, ["rev-parse", "HEAD"], cwd=ws.repo_dir, timeout=timeout)
    ws.pre_merge_sha = head.stdout.strip()

    merge_ref = source_commit or f"origin/{source_branch}"
    _run_git(
        ws,
        ["merge", "--no-edit", "--", merge_ref],
        cwd=ws.repo_dir,
        timeout=timeout,
    )
    merged = _run_git(ws, ["rev-parse", "HEAD"], cwd=ws.repo_dir, timeout=timeout)
    ws.merge_sha = merged.stdout.strip()
    return ws.merge_sha


def merge_diff(ws: GitWorkspace, *, timeout: int = DEFAULT_GIT_TIMEOUT) -> str:
    """Diff introduced by the merge (pre-merge target HEAD → merged HEAD)."""
    if not ws.pre_merge_sha:
        raise GitOpsError("no merge has been performed in this workspace")
    result = _run_git(
        ws,
        ["diff", ws.pre_merge_sha, "HEAD"],
        cwd=ws.repo_dir,
        timeout=timeout,
    )
    return result.stdout


def push_target(
    ws: GitWorkspace, target_branch: str, *, timeout: int = DEFAULT_GIT_TIMEOUT
) -> None:
    """Push the merged target branch back to origin (fails on non-fast-forward)."""
    target_branch = validate_ref(target_branch, what="target branch")
    _run_git(
        ws,
        ["push", "--quiet", "origin", f"HEAD:refs/heads/{target_branch}"],
        cwd=ws.repo_dir,
        with_token=True,
        timeout=timeout,
    )
