"""Argv hardening for the warm sandbox runner's subprocess spawn."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from apps.runworker.sandbox_runner import SandboxRunner, _validate_argv


class TestValidateArgv:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python3 main.py",
            [],
            ["/usr/bin/python3", 1],
            ["/usr/bin/python3", "a\x00b"],
            ["python3", "main.py"],
        ],
    )
    def test_rejects_unsafe_commands(self, cmd: object) -> None:
        with pytest.raises(ValueError):
            _validate_argv(cmd)

    @pytest.mark.parametrize(
        "cmd",
        [
            ["/usr/bin/python3", "-u", "main.py"],
            ["sh", "-c", '"$1"', "sh", "/usr/bin/rustc"],
        ],
    )
    def test_accepts_absolute_argv(self, cmd: list[str]) -> None:
        _validate_argv(cmd)


class TestRustCommand:
    def _cmd(self, tmp_path: Path, binary: str, script: Path) -> list[str]:
        runner = SandboxRunner(workspace_root=str(tmp_path))
        return runner._build_command(
            canonical_runtime="rust",
            binary_path=binary,
            script_path=script,
            workspace=tmp_path,
            tmp_dir=tmp_path,
            env={},
        )

    def test_paths_are_positional_not_interpolated(self, tmp_path: Path) -> None:
        script = tmp_path / 'm"; touch pwned; ".rs'
        cmd = self._cmd(tmp_path, "/usr/bin/rustc", script)
        assert cmd[:2] == ["sh", "-c"]
        assert str(script) not in cmd[2]
        assert "/usr/bin/rustc" not in cmd[2]
        assert cmd[4:] == [
            "/usr/bin/rustc",
            str(script),
            str(tmp_path / "elder_run_bin"),
        ]
        _validate_argv(cmd)

    def test_shell_metacharacters_in_paths_are_inert(self, tmp_path: Path) -> None:
        # Fake "compiler" that copies a tiny program to the -o target.
        fake = tmp_path / "fakerustc"
        fake.write_text(
            '#!/bin/sh\nprintf \'#!/bin/sh\\necho ran\\n\' > "$3"\nchmod +x "$3"\n'
        )
        fake.chmod(0o755)
        script = tmp_path / "x$(touch pwned);`touch pwned2`.rs"
        script.write_text("fn main(){}")
        cmd = self._cmd(tmp_path, str(fake), script)
        out = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "ran"
        assert not (tmp_path / "pwned").exists()
        assert not (tmp_path / "pwned2").exists()
