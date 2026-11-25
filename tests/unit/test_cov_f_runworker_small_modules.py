"""Tests for the runworker runtimes table, SDK bundle writer and native healthcheck."""

import sys
import time
from pathlib import Path

import pytest

from apps.runworker import elder_sdk_bundle as sdk
from apps.runworker import healthcheck as hc
from apps.runworker import runtimes


class TestRuntimes:
    """Runtime name/alias resolution and pinning invariants."""

    @pytest.mark.parametrize(
        ("name", "canonical"),
        [
            ("python", "python"),
            (" PY ", "python"),
            ("python3", "python"),
            ("node", "nodejs"),
            ("js", "nodejs"),
            ("javascript", "nodejs"),
            ("go", "golang"),
            ("rb", "ruby"),
            ("rs", "rust"),
        ],
    )
    def test_get_runtime_resolves_aliases(self, name: str, canonical: str) -> None:
        """Aliases and mixed case resolve to the canonical spec."""
        assert runtimes.get_runtime(name).name == canonical

    @pytest.mark.parametrize("name", ["", None, "cobol"])
    def test_get_runtime_rejects_unknown(self, name: str | None) -> None:
        """Empty and unsupported runtimes raise ValueError listing options."""
        with pytest.raises(ValueError, match="supported runtimes"):
            runtimes.get_runtime(name)  # type: ignore[arg-type]

    def test_every_image_is_digest_pinned(self) -> None:
        """Every runtime image is pinned to an immutable sha256 digest."""
        for spec in runtimes.RUNTIMES.values():
            assert "@sha256:" in spec.image
            assert spec.command and spec.filename


class TestSdkBundle:
    """Built-in helper SDK lookup and workspace writing."""

    @pytest.mark.parametrize(
        ("runtime", "filename"),
        [
            ("python", "elder.py"),
            ("py", "elder.py"),
            ("node", "elder.mjs"),
            ("rb", "elder.rb"),
        ],
    )
    def test_sdk_file_for_runtime(self, runtime: str, filename: str) -> None:
        """Runtimes with a bundled SDK return its filename and source."""
        entry = sdk.sdk_file_for_runtime(runtime)
        assert entry is not None and entry[0] == filename and entry[1]

    @pytest.mark.parametrize("runtime", ["golang", "", None, "nope"])
    def test_sdk_file_missing(self, runtime: str | None) -> None:
        """Runtimes without an SDK return None."""
        assert sdk.sdk_file_for_runtime(runtime) is None  # type: ignore[arg-type]

    def test_write_sdk_files_writes_source(self, tmp_path: Path) -> None:
        """The SDK source is written into the workspace and path returned."""
        target = sdk.write_sdk_files(tmp_path, "python")
        assert target == tmp_path / "elder.py"
        assert target.read_text(encoding="utf-8") == sdk.ELDER_PY_SDK

    def test_write_sdk_files_noop_for_unbundled_runtime(self, tmp_path: Path) -> None:
        """No file is written when the runtime has no SDK."""
        assert sdk.write_sdk_files(tmp_path, "rust") is None
        assert list(tmp_path.iterdir()) == []

    def test_python_sdk_is_valid_python(self) -> None:
        """The bundled Python SDK compiles."""
        compile(sdk.ELDER_PY_SDK, "elder.py", "exec")


class TestHealthcheck:
    """Native healthcheck probes."""

    def test_check_imports_ok(self) -> None:
        """Runtime module imports cleanly."""
        assert hc.check_imports() is True

    def test_check_imports_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An import error is reported on stderr and fails the check."""
        monkeypatch.setitem(sys.modules, "apps.runworker.runtimes", None)
        monkeypatch.delattr("apps.runworker.runtimes", raising=False)
        import apps.runworker as pkg

        monkeypatch.delattr(pkg, "runtimes", raising=False)
        assert hc.check_imports() is False
        assert "Import error" in capsys.readouterr().err

    def test_check_config(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The API URL must be configured."""
        monkeypatch.delenv("ELDER_API_URL", raising=False)
        assert hc.check_config() is False
        assert "ELDER_API_URL" in capsys.readouterr().err
        monkeypatch.setenv("ELDER_API_URL", "http://hub.invalid")
        assert hc.check_config() is True

    def test_heartbeat_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A missing heartbeat file fails."""
        monkeypatch.setenv("RUNS_HEALTH_FILE", str(tmp_path / "nope"))
        assert hc.check_heartbeat() is False
        assert "missing" in capsys.readouterr().err

    def test_heartbeat_default_path_used(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without an override the file lives in the temp dir."""
        monkeypatch.delenv("RUNS_HEALTH_FILE", raising=False)
        monkeypatch.setattr(hc.tempfile, "gettempdir", lambda: str(tmp_path))
        (tmp_path / "runworker.healthy").write_text(f"{time.time()}\n")
        assert hc.check_heartbeat() is True

    def test_heartbeat_unreadable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A non-numeric heartbeat fails as unreadable."""
        f = tmp_path / "hb"
        f.write_text("garbage")
        monkeypatch.setenv("RUNS_HEALTH_FILE", str(f))
        assert hc.check_heartbeat() is False
        assert "unreadable" in capsys.readouterr().err

    def test_heartbeat_stale_and_fresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Old timestamps fail, recent ones pass."""
        f = tmp_path / "hb"
        monkeypatch.setenv("RUNS_HEALTH_FILE", str(f))
        f.write_text(str(time.time() - 10_000))
        assert hc.check_heartbeat() is False
        assert "stale" in capsys.readouterr().err
        f.write_text(str(time.time()))
        assert hc.check_heartbeat() is True

    def test_main_ok(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """All checks passing prints OK and returns 0."""
        for name in ("check_imports", "check_config", "check_heartbeat"):
            monkeypatch.setattr(hc, name, lambda: True)
        assert hc.main() == 0
        assert capsys.readouterr().out == "OK\n"

    def test_dunder_main_exits_with_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Running the module as a script exits non-zero when unhealthy."""
        import runpy

        monkeypatch.delenv("ELDER_API_URL", raising=False)
        monkeypatch.setenv("RUNS_HEALTH_FILE", str(tmp_path / "missing"))
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("apps.runworker.healthcheck", run_name="__main__")
        assert exc.value.code == 1

    def test_main_failure_and_exception(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A failing check and a raising check both yield exit code 1."""

        def boom() -> bool:
            """Raise to simulate a crashing probe."""
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(hc, "check_imports", lambda: False)
        monkeypatch.setattr(hc, "check_config", boom)
        monkeypatch.setattr(hc, "check_heartbeat", lambda: True)
        assert hc.main() == 1
        err = capsys.readouterr().err
        assert "Check failed: imports" in err
        assert "Check error (config)" in err
