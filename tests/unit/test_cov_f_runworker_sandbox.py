"""Tests for the warm sandbox runner: runtime resolution, env/command building, rlimits, execution."""

import base64
import io
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.runworker import sandbox_runner as mod
from apps.runworker.k8s_runner import RunJobRequest
from apps.runworker.sandbox_runner import (
    SandboxRunner,
    normalize_sandbox_runtime,
    resolve_local_runtime_binary,
)
from tests.unit._cov_f_helpers import fake_secret

WASM = b"\x00asm\x01\x00\x00\x00"
BIN = "/opt/fake/bin/runtime"


def _req(**kw: Any) -> RunJobRequest:
    """Build a RunJobRequest with defaults for sandbox tests."""
    base: dict[str, Any] = {
        "execution_id": "exec-1",
        "runtime": "python",
        "code": "print(1)",
        "input_data": {"x": 1},
    }
    base.update(kw)
    return RunJobRequest(**base)


class FakeStdout:
    """Pipe stand-in yielding preset chunks via ``read1``."""

    def __init__(
        self, chunks: list[bytes], *, fail_read: bool = False, fail_close: bool = False
    ) -> None:
        """Store the chunks and failure switches."""
        self.chunks = list(chunks)
        self.fail_read = fail_read
        self.fail_close = fail_close

    def read1(self, _n: int) -> bytes:
        """Return the next chunk, or empty bytes at EOF."""
        if self.fail_read:
            raise OSError("pipe broke")
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        """Close the stream, optionally failing."""
        if self.fail_close:
            raise OSError("close failed")


class FakeProc:
    """Popen stand-in with controllable completion."""

    def __init__(
        self,
        stdout: FakeStdout,
        returncode: int | None = 0,
        *,
        run_until: Any = None,
        wait_raises: bool = False,
    ) -> None:
        """Configure the exit code and when ``poll`` reports completion."""
        self.pid = 4242
        self.stdout = stdout
        self._rc = returncode
        self.returncode: int | None = None
        self.run_until = run_until
        self.wait_raises = wait_raises
        self.killed = False

    def poll(self) -> int | None:
        """Report completion once ``run_until`` (if any) has been set."""
        if self.run_until is not None and not self.run_until.is_set():
            return None
        if self._rc is None:
            return None
        self.returncode = self._rc
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        """Pretend to wait for the process, optionally timing out."""
        if self.wait_raises:
            raise subprocess.TimeoutExpired("cmd", timeout or 0)
        return 0

    def kill(self) -> None:
        """Record a direct kill."""
        self.killed = True


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch subprocess.Popen/killpg/binary lookup and expose what was launched."""
    state = SimpleNamespace(proc=None, calls=[], killpg=[])

    def fake_popen(cmd: list[str], **kwargs: Any) -> Any:
        """Record the launch and return the configured fake process."""
        state.calls.append((cmd, kwargs))
        return state.proc

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        mod.os, "killpg", lambda pid, sig: state.killpg.append((pid, sig))
    )
    monkeypatch.setattr(mod, "resolve_local_runtime_binary", lambda rt: BIN)
    return state


class TestRuntimeHelpers:
    """Runtime normalisation, wasm detection and binary lookup."""

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            ("py", "python"),
            (" Node ", "nodejs"),
            ("wasi", "wasm"),
            ("wasmtime", "wasm"),
            ("", ""),
            (None, ""),
            ("zig", "zig"),
        ],
    )
    def test_normalize(self, raw: Any, canonical: str) -> None:
        """Aliases map to canonical names; unknown values pass through."""
        assert normalize_sandbox_runtime(raw) == canonical

    @pytest.mark.parametrize(
        ("runtime", "url", "code", "expected"),
        [
            ("wasm", None, "", True),
            ("python", "https://x/a.WASM?sig=1", "", True),
            ("python", "https://x/a.py", WASM, True),
            ("python", None, bytearray(WASM), True),
            ("python", "https://x/a.py", b"plain", False),
            ("python", None, "str", False),
        ],
    )
    def test_is_wasm_request(
        self, runtime: str, url: Any, code: Any, expected: bool
    ) -> None:
        """Wasm is detected by runtime, URL suffix, or magic bytes."""
        assert mod._is_wasm_request(runtime, code_url=url, code=code) is expected

    @pytest.mark.parametrize(
        ("runtime", "tool"),
        [
            ("nodejs", "node"),
            ("ruby", "ruby"),
            ("golang", "go"),
            ("rust", "rustc"),
            ("wasm", "wasmtime"),
        ],
    )
    def test_resolve_binary_uses_which(
        self, monkeypatch: pytest.MonkeyPatch, runtime: str, tool: str
    ) -> None:
        """Non-Python runtimes are looked up on PATH."""
        monkeypatch.setattr(mod.shutil, "which", lambda name: f"/x/{name}")
        assert resolve_local_runtime_binary(runtime) == f"/x/{tool}"

    def test_resolve_binary_python_and_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Python uses the interpreter running the worker; unknown runtimes are None."""
        monkeypatch.setattr(mod.sys, "executable", "/py/bin/python")
        assert resolve_local_runtime_binary("py") == "/py/bin/python"
        monkeypatch.setattr(mod.sys, "executable", "")
        monkeypatch.setattr(mod.shutil, "which", lambda name: "/fallback/python3")
        assert resolve_local_runtime_binary("python") == "/fallback/python3"
        assert resolve_local_runtime_binary("zig") is None

    def test_can_execute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Wasm requests need wasmtime; others need their own binary."""
        seen: list[str] = []

        def fake(runtime: str) -> str | None:
            """Record the lookup and report only wasmtime as installed."""
            seen.append(runtime)
            return "/x" if runtime == "wasm" else None

        monkeypatch.setattr(mod, "resolve_local_runtime_binary", fake)
        runner = SandboxRunner()
        assert runner.can_execute("wasi") is True
        assert runner.can_execute("python", code=WASM) is True
        assert runner.can_execute("python") is False
        assert seen == ["wasm", "wasm", "python"]

    def test_decode_wasm_bytes(self) -> None:
        """Raw, prefixed-base64, bare-base64 and plain text inputs are decoded."""
        assert mod._decode_wasm_bytes(WASM) == WASM
        assert mod._decode_wasm_bytes(bytearray(WASM)) == WASM
        b64 = base64.b64encode(WASM).decode()
        assert mod._decode_wasm_bytes(f"base64:{b64}") == WASM
        assert mod._decode_wasm_bytes(f"  {b64}  ") == WASM
        assert mod._decode_wasm_bytes("hello world") == b"hello world"
        assert mod._decode_wasm_bytes(
            base64.b64encode(b"notwasm").decode()
        ) == base64.b64encode(b"notwasm")
        assert mod._decode_wasm_bytes("") == b""
        assert mod._decode_wasm_bytes(None) == b""  # type: ignore[arg-type]


class FakeResource:
    """Fake ``resource`` module recording setrlimit calls."""

    RLIM_INFINITY = -1
    RLIMIT_CPU = 0
    RLIMIT_AS = 9
    RLIMIT_NOFILE = 7
    RLIMIT_NPROC = 6

    def __init__(
        self,
        limits: dict[int, tuple[int, int]] | None = None,
        *,
        raises: type[Exception] | None = None,
    ) -> None:
        """Configure current limits and failure behaviour."""
        self.limits = limits or {}
        self.raises = raises
        self.calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, res_id: int) -> tuple[int, int]:
        """Return the configured limit (default unlimited)."""
        return self.limits.get(res_id, (-1, -1))

    def setrlimit(self, res_id: int, pair: tuple[int, int]) -> None:
        """Record the call or raise the configured error."""
        if self.raises:
            raise self.raises("nope")
        self.calls.append((res_id, pair))


class TestRlimits:
    """POSIX resource limit application (against a fake resource module)."""

    def _run(
        self, monkeypatch: pytest.MonkeyPatch, fake: Any, **kw: Any
    ) -> list[tuple[int, tuple[int, int]]]:
        """Build and invoke the preexec hook with the fake resource module."""
        monkeypatch.setattr(mod, "resource", fake)
        mod._build_rlimit_preexec(
            timeout_seconds=kw.pop("t", 10), memory_mb=kw.pop("m", 128), **kw
        )()
        return fake.calls

    def test_unlimited_host_applies_all_caps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no host limits every cap is applied as requested."""
        calls = dict(self._run(monkeypatch, FakeResource()))
        assert calls[FakeResource.RLIMIT_CPU] == (11, 12)
        assert calls[FakeResource.RLIMIT_AS] == (128 * 1024 * 1024,) * 2
        assert calls[FakeResource.RLIMIT_NOFILE] == (mod.DEFAULT_NOFILE_LIMIT,) * 2
        assert calls[FakeResource.RLIMIT_NPROC] == (64, 64)

    def test_tight_host_limits_are_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caps never exceed the host's existing hard limits."""
        fake = FakeResource(
            {
                FakeResource.RLIMIT_CPU: (5, 6),
                FakeResource.RLIMIT_AS: (10, 20),
                FakeResource.RLIMIT_NOFILE: (8, 16),
                FakeResource.RLIMIT_NPROC: (100, 200),
            }
        )
        calls = dict(self._run(monkeypatch, fake, nproc_limit=100))
        assert calls[FakeResource.RLIMIT_CPU][1] == 6
        assert calls[FakeResource.RLIMIT_AS] == (20, 20)
        assert calls[FakeResource.RLIMIT_NOFILE] == (8, 16)
        assert calls[FakeResource.RLIMIT_NPROC] == (100, 100)

    def test_nproc_skipped_when_it_would_raise_limits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NPROC is left alone if the host limit is already below the target."""
        hard_low = FakeResource({FakeResource.RLIMIT_NPROC: (-1, 10)})
        assert FakeResource.RLIMIT_NPROC not in dict(self._run(monkeypatch, hard_low))
        soft_low = FakeResource({FakeResource.RLIMIT_NPROC: (10, 500)})
        assert FakeResource.RLIMIT_NPROC not in dict(self._run(monkeypatch, soft_low))

    def test_errors_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OS and value errors when setting limits never abort the child."""
        for exc in (OSError, ValueError):
            assert self._run(monkeypatch, FakeResource(raises=exc)) == []

    def test_missing_nproc_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Platforms without RLIMIT_NPROC skip that cap."""
        monkeypatch.delattr(FakeResource, "RLIMIT_NPROC")
        calls = dict(self._run(monkeypatch, FakeResource()))
        assert 6 not in calls

    def test_getrlimit_error_in_nproc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing getrlimit for NPROC is tolerated."""
        fake = FakeResource()

        def boom(res_id: int) -> tuple[int, int]:
            """Fail only for NPROC."""
            if res_id == FakeResource.RLIMIT_NPROC:
                raise OSError("x")
            return (-1, -1)

        fake.getrlimit = boom  # type: ignore[method-assign]
        calls = dict(self._run(monkeypatch, fake))
        assert FakeResource.RLIMIT_NPROC not in calls

    def test_kill_process_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Killing falls back to proc.kill when killpg fails, and tolerates both failing."""
        killed: list[Any] = []
        monkeypatch.setattr(
            mod.os, "killpg", lambda pid, sig: killed.append((pid, sig))
        )
        proc = SimpleNamespace(pid=7, kill=lambda: killed.append("kill"))
        mod._kill_process_group(proc)  # type: ignore[arg-type]
        assert killed == [(7, mod.signal.SIGKILL)]

        def deny(pid: int, sig: int) -> None:
            """Fail like a vanished process group."""
            raise ProcessLookupError()

        monkeypatch.setattr(mod.os, "killpg", deny)
        mod._kill_process_group(proc)  # type: ignore[arg-type]
        assert killed[-1] == "kill"

        def bad_kill() -> None:
            """Fail like an already-reaped process."""
            raise OSError("gone")

        mod._kill_process_group(SimpleNamespace(pid=7, kill=bad_kill))  # type: ignore[arg-type]


class TestEnvAndCommand:
    """Environment sanitisation and command construction."""

    def _env(self, tmp_path: Path, runtime: str, **kw: Any) -> dict[str, str]:
        """Build the sandbox env for a runtime."""
        gateway = kw.pop("gateway", None)
        return SandboxRunner()._build_env(
            _req(**kw),
            canonical_runtime=runtime,
            binary_path="/opt/rt/bin/tool",
            workspace=tmp_path,
            tmp_dir=tmp_path / "tmp",
            input_json='{"x":1}',
            gateway=gateway,
        )

    @pytest.mark.parametrize(
        ("runtime", "key"),
        [
            ("python", "PYTHONPATH"),
            ("nodejs", "NODE_PATH"),
            ("ruby", "RUBYLIB"),
            ("golang", "GOPATH"),
            ("rust", "CARGO_HOME"),
        ],
    )
    def test_runtime_specific_env(self, tmp_path: Path, runtime: str, key: str) -> None:
        """Each runtime gets its own isolated variables and a stripped PATH."""
        env = self._env(tmp_path, runtime)
        assert key in env
        assert env["PATH"].startswith("/opt/rt/bin:")
        assert env["HOME"] == str(tmp_path / "tmp")
        assert env["ELDER_EXECUTION_ID"] == "exec-1"

    def test_wasm_env_is_minimal(self, tmp_path: Path) -> None:
        """Wasm gets only the base variables."""
        assert set(self._env(tmp_path, "wasm")) == {
            "PATH",
            "HOME",
            "TMPDIR",
            "ELDER_RUN_INPUT",
            "ELDER_EXECUTION_ID",
        }

    def test_user_env_filtering_and_gateway(self, tmp_path: Path) -> None:
        """Protected, duplicate and malformed keys are dropped; gateway vars are injected."""
        token = fake_secret("gw")
        gw = SimpleNamespace(url="http://127.0.0.1:1", gateway_token=token)
        env = self._env(
            tmp_path,
            "python",
            gateway=gw,
            env={
                "OK": 1,  # type: ignore[dict-item]
                "ELDER_API_TOKEN": "leak",
                "LD_PRELOAD": "x.so",
                "PYTHONUNBUFFERED": "0",
                "bad-key": "v",
                "ELDER_GATEWAY_TOKEN": "spoof",
            },
        )
        assert env["OK"] == "1"
        assert "ELDER_API_TOKEN" not in env and "LD_PRELOAD" not in env
        assert "bad-key" not in env
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["ELDER_GATEWAY_TOKEN"] == token
        assert env["ELDER_GATEWAY_URL"] == "http://127.0.0.1:1"

    @pytest.mark.parametrize(
        ("runtime", "boot", "first"),
        [
            ("python", "_elder_bootstrap.py", None),
            ("nodejs", "_elder_bootstrap.mjs", None),
            ("ruby", "_elder_bootstrap.rb", None),
        ],
    )
    def test_command_with_bootstrap(
        self, tmp_path: Path, runtime: str, boot: str, first: Any
    ) -> None:
        """Interpreted runtimes write a bootstrap file and pass the script path."""
        cmd = SandboxRunner()._build_command(
            canonical_runtime=runtime,
            binary_path=BIN,
            script_path=tmp_path / "main",
            workspace=tmp_path,
            tmp_dir=tmp_path,
            env={},
        )
        assert cmd[0] == BIN and cmd[-1] == str(tmp_path / "main")
        assert (tmp_path / boot).read_text(encoding="utf-8")

    def test_command_go_rust(self, tmp_path: Path) -> None:
        """Compiled runtimes use go run and a compile-then-exec shell line."""
        runner = SandboxRunner()
        kw: dict[str, Any] = {
            "binary_path": BIN,
            "script_path": tmp_path / "m",
            "workspace": tmp_path,
            "tmp_dir": tmp_path,
            "env": {},
        }
        assert runner._build_command(canonical_runtime="golang", **kw)[1] == "run"
        rust = runner._build_command(canonical_runtime="rust", **kw)
        assert rust[:2] == ["sh", "-c"] and "exec" in rust[2]

    def test_command_wasm_forwards_gateway_env(self, tmp_path: Path) -> None:
        """Wasm runs forward only the whitelisted Elder variables."""
        runner = SandboxRunner()
        kw: dict[str, Any] = {
            "binary_path": BIN,
            "script_path": tmp_path / "m.wasm",
            "workspace": tmp_path,
            "tmp_dir": tmp_path,
        }
        base = {"ELDER_RUN_INPUT": "{}", "ELDER_EXECUTION_ID": "e"}
        plain = runner._build_command(canonical_runtime="wasm", env=base, **kw)
        assert "ELDER_GATEWAY_URL" not in " ".join(plain)
        full = runner._build_command(
            canonical_runtime="wasm",
            env={**base, "ELDER_GATEWAY_URL": "u", "ELDER_GATEWAY_TOKEN": "t"},
            **kw,
        )
        assert "ELDER_GATEWAY_URL=u" in full and "ELDER_GATEWAY_TOKEN=t" in full
        assert full[-1] == str(tmp_path / "m.wasm")

    def test_command_unknown_runtime(self, tmp_path: Path) -> None:
        """Unsupported runtimes raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported"):
            SandboxRunner()._build_command(
                canonical_runtime="zig",
                binary_path=BIN,
                script_path=tmp_path,
                workspace=tmp_path,
                tmp_dir=tmp_path,
                env={},
            )


class FakeGateway:
    """RestrictionGateway stand-in recording lifecycle and construction."""

    instances: list["FakeGateway"] = []
    stop_error: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        """Capture the constructor arguments."""
        self.kwargs = kwargs
        self.url = "http://127.0.0.1:9"
        self.gateway_token = fake_secret("gwtoken")
        self.started = False
        self.stopped = False
        FakeGateway.instances.append(self)

    def start(self) -> "FakeGateway":
        """Mark started and return self."""
        self.started = True
        return self

    def stop(self) -> None:
        """Mark stopped, optionally failing."""
        self.stopped = True
        if FakeGateway.stop_error:
            raise FakeGateway.stop_error


class TestExecute:
    """End-to-end execute() with a fake subprocess."""

    def _runner(self, tmp_path: Path, **kw: Any) -> SandboxRunner:
        """Build a runner rooted in the test's tmp dir with a fast poll."""
        return SandboxRunner(workspace_root=str(tmp_path), poll_interval=0.01, **kw)

    def test_success_with_structured_output(
        self, tmp_path: Path, popen: SimpleNamespace
    ) -> None:
        """A zero exit yields succeeded, merged logs and parsed JSON output."""
        popen.proc = FakeProc(FakeStdout([b"hello\n", b'{"v": 3}\n']), 0)
        res = self._runner(tmp_path).execute(_req())
        assert (res.status, res.exit_code, res.output, res.error) == (
            "succeeded",
            0,
            {"v": 3},
            None,
        )
        assert res.logs.startswith("hello")
        cmd, kwargs = popen.calls[0]
        assert kwargs["start_new_session"] is True and callable(kwargs["preexec_fn"])
        assert kwargs["env"]["ELDER_EXECUTION_ID"] == "exec-1"
        assert not any(p.name.startswith("elder-run-") for p in tmp_path.iterdir())

    def test_workspace_contents_during_run(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The workspace holds script, input.json and the SDK while running."""
        seen: dict[str, Any] = {}

        def capture(cmd: list[str], **kwargs: Any) -> Any:
            """Snapshot the workspace at launch time."""
            ws = Path(kwargs["cwd"])
            seen["files"] = sorted(p.name for p in ws.iterdir())
            seen["input"] = (ws / "input.json").read_text()
            seen["mode"] = oct(ws.stat().st_mode & 0o777)
            return FakeProc(FakeStdout([]), 0)

        monkeypatch.setattr(mod.subprocess, "Popen", capture)
        self._runner(tmp_path).execute(_req(input_data=None, code=b"print(2)"))
        assert {"main.py", "input.json", "elder.py", "tmp"} <= set(seen["files"])
        assert seen["input"] == "{}" and seen["mode"] == "0o700"

    def test_failed_exit_code(self, tmp_path: Path, popen: SimpleNamespace) -> None:
        """A non-zero exit is reported as failed."""
        popen.proc = FakeProc(FakeStdout([b"boom"]), 3)
        res = self._runner(tmp_path).execute(_req(execution_id=""))
        assert (res.status, res.exit_code, res.output) == ("failed", 3, None)

    def test_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unavailable runtime binary raises before any workspace is created."""
        monkeypatch.setattr(mod, "resolve_local_runtime_binary", lambda rt: None)
        with pytest.raises(RuntimeError, match="not installed"):
            self._runner(tmp_path).execute(_req())
        assert list(tmp_path.iterdir()) == []

    def test_wasm_execution(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wasm code is decoded to main.wasm and no SDK is written."""
        seen: dict[str, Any] = {}

        def capture(cmd: list[str], **kwargs: Any) -> Any:
            """Snapshot the wasm workspace."""
            ws = Path(kwargs["cwd"])
            seen["wasm"] = (ws / "main.wasm").read_bytes()
            seen["files"] = {p.name for p in ws.iterdir()}
            seen["cmd"] = cmd
            return FakeProc(FakeStdout([]), 0)

        monkeypatch.setattr(mod.subprocess, "Popen", capture)
        code = "base64:" + base64.b64encode(WASM).decode()
        self._runner(tmp_path).execute(_req(runtime="wasm", code=code))
        assert seen["wasm"] == WASM and "elder.py" not in seen["files"]
        assert seen["cmd"][1] == "run"

    def test_timeout_kills_process_group(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exceeding the deadline kills the process group and reports timeout."""
        monkeypatch.setattr(mod, "clamp_timeout_seconds", lambda t: 0)
        popen.proc = FakeProc(FakeStdout([]), None, wait_raises=True)
        res = self._runner(tmp_path).execute(_req())
        assert res.status == "timeout" and "timeout" in (res.error or "")
        assert popen.killpg and popen.killpg[0][0] == 4242

    def test_live_log_flush_and_callback_errors(
        self, tmp_path: Path, popen: SimpleNamespace
    ) -> None:
        """Changed logs are pushed via on_logs; a failing callback is tolerated."""
        done = threading.Event()
        popen.proc = FakeProc(FakeStdout([b"line one\n"]), 0, run_until=done)
        got: list[str] = []

        def on_logs(text: str) -> None:
            """Record the logs, then release the fake process."""
            got.append(text)
            done.set()

        self._runner(tmp_path).execute(_req(), on_logs=on_logs, log_flush_interval=0.01)
        assert got and got[0].startswith("line one")

        done2 = threading.Event()
        popen.proc = FakeProc(FakeStdout([b"x\n"]), 0, run_until=done2)

        def bad(_text: str) -> None:
            """Release the process then fail."""
            done2.set()
            raise RuntimeError("sink down")

        res = self._runner(tmp_path).execute(
            _req(), on_logs=bad, log_flush_interval=0.01
        )
        assert res.status == "succeeded"

    def test_log_cap_and_reader_errors(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output beyond the cap is dropped; pipe errors do not crash the run."""
        monkeypatch.setattr(mod, "MAX_LOG_BYTES", 4)
        popen.proc = FakeProc(FakeStdout([b"abcdef", b"ghi"], fail_close=True), 0)
        res = self._runner(tmp_path).execute(_req())
        assert res.logs == "abcd"
        popen.proc = FakeProc(FakeStdout([], fail_read=True, fail_close=True), 0)
        assert self._runner(tmp_path).execute(_req()).status == "succeeded"

    def test_gateway_started_and_stopped(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scopes or egress domains start a gateway that is always stopped and keeps the real token out."""
        FakeGateway.instances = []
        FakeGateway.stop_error = None
        monkeypatch.setattr(mod, "RestrictionGateway", FakeGateway)
        popen.proc = FakeProc(FakeStdout([]), 0)
        api_token = fake_secret("real", "api")
        self._runner(tmp_path).execute(
            _req(granted_scopes=("a:read",), elder_api_token=api_token)
        )
        gw = FakeGateway.instances[0]
        assert gw.started and gw.stopped
        env = popen.calls[0][1]["env"]
        assert env["ELDER_GATEWAY_URL"] == gw.url
        assert api_token not in env.values()
        assert gw.kwargs["elder_api_token"] == api_token

    def test_no_gateway_without_scopes(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requests with neither scopes nor egress domains get no gateway."""
        FakeGateway.instances = []
        monkeypatch.setattr(mod, "RestrictionGateway", FakeGateway)
        popen.proc = FakeProc(FakeStdout([]), 0)
        self._runner(tmp_path).execute(_req())
        assert FakeGateway.instances == []
        FakeGateway.instances = []
        self._runner(tmp_path).execute(_req(allowed_egress_domains=("a.com",)))
        assert len(FakeGateway.instances) == 1

    def test_gateway_stop_failure_is_logged(
        self, tmp_path: Path, popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing gateway shutdown does not mask the result and cleanup still runs."""
        FakeGateway.instances = []
        FakeGateway.stop_error = RuntimeError("stop failed")
        monkeypatch.setattr(mod, "RestrictionGateway", FakeGateway)
        popen.proc = FakeProc(FakeStdout([]), 0)
        try:
            res = self._runner(tmp_path).execute(_req(granted_scopes=("a:read",)))
        finally:
            FakeGateway.stop_error = None
        assert res.status == "succeeded"
        assert not any(p.name.startswith("elder-run-") for p in tmp_path.iterdir())

    def test_workspace_cleaned_when_launch_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the subprocess cannot start the workspace is still removed."""
        monkeypatch.setattr(mod, "resolve_local_runtime_binary", lambda rt: BIN)

        def boom(*_a: Any, **_kw: Any) -> Any:
            """Simulate an exec failure."""
            raise OSError("exec failed")

        monkeypatch.setattr(mod.subprocess, "Popen", boom)
        with pytest.raises(OSError):
            self._runner(tmp_path).execute(_req())
        assert list(tmp_path.iterdir()) == []

    def test_constructor_normalisation(self) -> None:
        """Constructor clamps its numeric options and trims the URL."""
        runner = SandboxRunner(
            elder_api_url="", max_gateway_requests=0, poll_interval=0
        )
        assert runner.elder_api_url == "http://hub-api:5000"
        assert runner.max_gateway_requests == 1 and runner.poll_interval == 0.01

    def test_real_python_run_end_to_end(self, tmp_path: Path) -> None:
        """A real local interpreter run executes the handler bootstrap and parses output."""
        runner = SandboxRunner(workspace_root=str(tmp_path), poll_interval=0.01)
        code = "def handler(event):\n    return {'doubled': event['x'] * 2}\n"
        res = runner.execute(_req(code=code, input_data={"x": 4}, memory_mb=1024))
        assert res.status == "succeeded", res.logs
        assert res.output == {"doubled": 8}
