"""Coverage tests for flows-invoker sandbox, gitops and review modules."""

import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.flows_invoker import gitops, review, sandbox
from apps.flows_invoker.sandbox import SandboxError


class _FakeProc:
    """Popen double returning canned output and optionally timing out once."""

    def __init__(self, out="ok", rc=0, timeout_first=False):
        """Store the canned output, return code and timeout behaviour."""
        self.out, self.returncode, self.pid = out, rc, 4242
        self.timeout_first = timeout_first
        self.killed = False

    def communicate(self, timeout=None):
        """Raise TimeoutExpired once if configured, else return output."""
        if self.timeout_first and timeout is not None:
            raise subprocess.TimeoutExpired("x", timeout)
        return self.out, None

    def kill(self):
        """Record that the process was killed directly."""
        self.killed = True


class TestResourceLimits:
    """Resource-limit env parsing and the preexec closure."""

    def test_from_env_overrides_and_bad_values(self, monkeypatch):
        """Valid ints override defaults; garbage falls back to the default."""
        monkeypatch.setenv("FLOWS_LIMIT_CPU_SECONDS", "5")
        monkeypatch.setenv("FLOWS_LIMIT_NPROC", "notint")
        lim = sandbox.ResourceLimits.from_env()
        assert lim.cpu_seconds == 5
        assert lim.max_processes == 4096

    def test_preexec_applies_limits(self, monkeypatch):
        """The child hook sets every rlimit, skipping AS when it is zero."""
        calls = []
        monkeypatch.setattr(
            sandbox.resource, "setrlimit", lambda k, v: calls.append((k, v))
        )
        sandbox._limit_preexec(sandbox.ResourceLimits())()
        assert len(calls) == 6
        calls.clear()
        sandbox._limit_preexec(sandbox.ResourceLimits(address_space_bytes=0))()
        assert len(calls) == 5


class TestRunCommand:
    """run_command validation, timeout, and truncation paths."""

    def test_rejects_bad_argv_and_workspace(self, tmp_path):
        """Empty argv, non-string argv and missing workspace are rejected."""
        with pytest.raises(SandboxError):
            sandbox.run_command([], str(tmp_path))
        with pytest.raises(SandboxError):
            sandbox.run_command(["python3", 1], str(tmp_path))
        with pytest.raises(SandboxError):
            sandbox.run_command(["python3"], str(tmp_path / "missing"))

    def test_success(self, tmp_path, monkeypatch):
        """A normal exit returns captured output and success."""
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc("hi"))
        res = sandbox.run_command(["python3", "-V"], str(tmp_path))
        assert res.success and res.stdout == "hi" and not res.truncated

    def test_none_output_and_none_returncode(self, tmp_path, monkeypatch):
        """None output becomes empty and a None return code becomes -1."""
        proc = _FakeProc(None, None)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
        res = sandbox.run_command(["python3"], str(tmp_path))
        assert res.stdout == "" and res.returncode == -1

    def test_timeout_killpg(self, tmp_path, monkeypatch):
        """A timeout kills the process group."""
        killed = []
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: _FakeProc("x", 0, True)
        )
        monkeypatch.setattr(sandbox.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(sandbox.os, "killpg", lambda p, s: killed.append(s))
        res = sandbox.run_command(["python3"], str(tmp_path), timeout=1)
        assert res.timed_out and not res.success
        assert killed == [signal.SIGKILL]

    def test_timeout_killpg_fails_falls_back_to_kill(self, tmp_path, monkeypatch):
        """If the group is already gone, the direct kill is used."""
        proc = _FakeProc("x", 0, True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)

        def boom(*_a):
            """Simulate a vanished process."""
            raise ProcessLookupError

        monkeypatch.setattr(sandbox.os, "getpgid", boom)
        sandbox.run_command(["python3"], str(tmp_path), timeout=1)
        assert proc.killed

    def test_truncation(self, tmp_path, monkeypatch):
        """Output beyond the cap is truncated and flagged."""
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc("a" * 100))
        res = sandbox.run_command(
            ["python3"], str(tmp_path), max_output_bytes=10, env={"A": "b"}
        )
        assert res.truncated and "truncated" in res.stdout


class TestSandboxParsing:
    """parse_command edge cases not covered elsewhere."""

    def test_blank_and_whitespace(self):
        """Whitespace-only commands are rejected."""
        with pytest.raises(SandboxError):
            sandbox.parse_command("   ")

    def test_empty_argv_after_split(self, monkeypatch):
        """A split that yields nothing is rejected too."""
        monkeypatch.setattr(sandbox.shlex, "split", lambda c: [])
        with pytest.raises(SandboxError):
            sandbox.parse_command("x")

    def test_allowed_binaries_env_and_path_ok(self, tmp_path, monkeypatch):
        """Env override is honoured and in-workspace paths pass."""
        monkeypatch.setenv("FLOWS_ALLOWED_BINARIES", "foo, bar")
        assert sandbox.allowed_binaries() == frozenset({"foo", "bar"})
        sandbox.resolve_executable("./s.sh", str(tmp_path))
        with pytest.raises(SandboxError):
            sandbox.resolve_executable("-x", str(tmp_path))
        with pytest.raises(SandboxError):
            sandbox.resolve_executable("rm", str(tmp_path))
        sandbox.resolve_executable("git", str(tmp_path), frozenset({"git"}))

    def test_build_env_blocks(self, tmp_path):
        """Blocked and non-string env entries are dropped; others kept."""
        env = sandbox.build_env(
            str(tmp_path), {"PATH": "/x", "LD_X": "1", "OK": "1", 3: "x"}
        )
        assert env["OK"] == "1" and env["PATH"] != "/x" and "LD_X" not in env


class TestGitops:
    """Validation, workspace lifecycle and git command helpers."""

    def test_url_validation_branches(self, monkeypatch):
        """Empty, bad scheme, userinfo and host-less URLs are rejected."""
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_repo_url(" ")
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_repo_url("http://example.invalid/r.git")
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_repo_url("https://u:p@example.invalid/r.git")
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_repo_url("https:///r.git")
        assert (
            gitops.validate_repo_url(" https://e.invalid/r ") == "https://e.invalid/r"
        )
        monkeypatch.setenv("FLOWS_GIT_ALLOWED_SCHEMES", "ssh, ,")
        assert gitops.allowed_url_schemes() == frozenset({"ssh"})
        assert gitops.validate_repo_url("ssh://h/r") == "ssh://h/r"

    def test_ref_and_sha_validation(self):
        """Refs and SHAs cover empty, dash, bad-pattern and good inputs."""
        for bad in ("", "-x", "a..b", "a//b", "a@{1", "x.lock", "x/", "x."):
            with pytest.raises(gitops.GitOpsError):
                gitops.validate_ref(bad)
        assert gitops.validate_ref(" main ") == "main"
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_commit_sha("")
        with pytest.raises(gitops.GitOpsError):
            gitops.validate_commit_sha("zz")
        assert gitops.validate_commit_sha(" abcdef1 ") == "abcdef1"

    def test_scrub(self):
        """Scrub handles empty text/token and replaces the token."""
        assert gitops.scrub("", "t") == ""
        assert gitops.scrub("abc", None) == "abc"
        assert gitops.scrub("a tok b", "tok") == "a *** b"

    def test_workspace_create_and_cleanup(self, tmp_path, monkeypatch):
        """A workspace is created under the configured root and removed."""
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path))
        ws = gitops.create_workspace("t")
        assert ws.root.startswith(str(tmp_path)) and ws.token == "t"
        assert "FLOWS_GIT_TOKEN" in open(ws.askpass_path).read()
        ws.cleanup()
        assert not tmp_path.joinpath(ws.root).exists()

    def test_git_env_token_gating(self):
        """The token only appears in env when requested and present."""
        ws = gitops.GitWorkspace("/r", "/r/repo", "/r/a", token="tok")
        assert gitops._git_env(ws, with_token=True)["FLOWS_GIT_TOKEN"] == "tok"
        assert "FLOWS_GIT_TOKEN" not in gitops._git_env(ws, with_token=False)
        ws2 = gitops.GitWorkspace("/r", "/r/repo", "/r/a")
        assert "FLOWS_GIT_TOKEN" not in gitops._git_env(ws2, with_token=True)

    def test_run_git_failure_scrubbed(self, monkeypatch):
        """A failed git command raises with scrubbed output and timeout note."""
        ws = gitops.GitWorkspace("/r", "/r/repo", "/r/a", token="tok")
        res = sandbox.CommandResult(1, "bad tok", True, 0.1, False)
        monkeypatch.setattr(gitops, "run_command", lambda *a, **k: res)
        with pytest.raises(gitops.GitOpsError, match="timed out") as ei:
            gitops._run_git(ws, ["clone"])
        assert "tok" not in ei.value.output

    def test_clone_and_merge_flow(self, monkeypatch):
        """Clone, checkout, merge run in order and return the merge sha."""
        ws = gitops.GitWorkspace("/r", "/r/repo", "/r/a")
        seen = []
        shas = iter(["pre\n", "post\n"])

        def fake(ws_, args, **kw):
            """Record the git args and fake rev-parse output."""
            seen.append(args)
            out = next(shas) if args[0] == "rev-parse" else ""
            return SimpleNamespace(stdout=out)

        monkeypatch.setattr(gitops, "_run_git", fake)
        sha = gitops.clone_and_merge(
            ws, "https://e.invalid/r", "dev", "main", source_commit="abcdef1"
        )
        assert sha == "post" and ws.pre_merge_sha == "pre"
        assert ["merge", "--no-edit", "--", "abcdef1"] in seen
        shas = iter(["a", "b"])
        gitops.clone_and_merge(ws, "https://e.invalid/r", "dev", "main")
        assert ["merge", "--no-edit", "--", "origin/dev"] in seen

    def test_merge_diff_and_push(self, monkeypatch):
        """merge_diff needs a prior merge; push validates the branch."""
        ws = gitops.GitWorkspace("/r", "/r/repo", "/r/a")
        with pytest.raises(gitops.GitOpsError):
            gitops.merge_diff(ws)
        ws.pre_merge_sha = "abc"
        calls = []

        def fake(ws_, args, **kw):
            """Record args and return a fake diff."""
            calls.append(args)
            return SimpleNamespace(stdout="DIFF")

        monkeypatch.setattr(gitops, "_run_git", fake)
        assert gitops.merge_diff(ws) == "DIFF"
        gitops.push_target(ws, "main")
        assert calls[-1][0] == "push"
        with pytest.raises(gitops.GitOpsError):
            gitops.push_target(ws, "-bad")


def _resp(body):
    """Build an httpx response double."""
    r = MagicMock()
    r.json.return_value = body
    return r


class TestReview:
    """Darwin review client paths."""

    def test_configured_flag(self, monkeypatch):
        """darwin_configured reflects the env var."""
        monkeypatch.delenv("DARWIN_API_URL", raising=False)
        assert not review.darwin_configured()
        monkeypatch.setenv("DARWIN_API_URL", "https://d.invalid")
        assert review.darwin_configured()

    def test_unconfigured(self, monkeypatch):
        """Without a URL the reviewer is unavailable."""
        monkeypatch.delenv("DARWIN_API_URL", raising=False)
        out = review.run_darwin_review("d")
        assert not out.available and "not configured" in out.error

    def test_http_error(self, monkeypatch):
        """Transport failures resolve to unavailable."""
        import httpx

        monkeypatch.setenv("DARWIN_API_URL", "https://d.invalid/")
        monkeypatch.setattr(httpx, "post", MagicMock(side_effect=RuntimeError("down")))
        out = review.run_darwin_review("d")
        assert not out.available and out.error == "down"

    @pytest.mark.parametrize(
        "body,passed",
        [
            ({"score": 90, "issues": []}, True),
            ({"score": 10, "issues": []}, False),
            ({"score": 90, "issues": [{"severity": "Critical"}, "junk"]}, False),
            ({"score": "x", "issues": "notlist"}, True),
            ({}, True),
        ],
    )
    def test_gate_policy(self, monkeypatch, body, passed):
        """Score threshold and critical severities drive the pass flag."""
        import httpx

        monkeypatch.setenv("DARWIN_API_URL", "https://d.invalid")
        monkeypatch.setenv("DARWIN_API_TOKEN", "tkn")
        post = MagicMock(return_value=_resp(body))
        monkeypatch.setattr(httpx, "post", post)
        out = review.run_darwin_review("d")
        assert out.available and out.passed is passed
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tkn"

    def test_no_block_on_critical(self, monkeypatch):
        """Critical issues are ignored when block_on_critical is off."""
        import httpx

        monkeypatch.setenv("DARWIN_API_URL", "https://d.invalid")
        monkeypatch.delenv("DARWIN_API_TOKEN", raising=False)
        body = {"score": 95, "issues": [{"severity": "critical"}]}
        monkeypatch.setattr(httpx, "post", MagicMock(return_value=_resp(body)))
        out = review.run_darwin_review("d", block_on_critical=False)
        assert out.passed and out.score == 95


class TestResidual:
    """Remaining branches: successful git run, unparseable and escaping paths."""

    def test_run_git_success(self, monkeypatch):
        """A successful git command returns the sandbox result."""
        ws = gitops.GitWorkspace("/r", "/r/repo", "/r/a")
        res = sandbox.CommandResult(0, "ok", False, 0.1, False)
        monkeypatch.setattr(gitops, "run_command", lambda *a, **k: res)
        assert gitops._run_git(ws, ["status"], cwd="/x") is res

    def test_unparseable_and_escape(self, tmp_path):
        """Unbalanced quotes and path escapes are policy violations."""
        with pytest.raises(SandboxError, match="unparseable"):
            sandbox.parse_command("echo 'unterminated")
        with pytest.raises(SandboxError, match="escapes"):
            sandbox.resolve_executable("../../bin/x", str(tmp_path))

    def test_parse_command_ok(self):
        """A valid command splits into an argv list."""
        assert sandbox.parse_command("python3 -V") == ["python3", "-V"]
