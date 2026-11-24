"""Unit tests for the marketing screenshot capture tool (scripts/capture-screenshots.cjs)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "capture-screenshots.cjs"
APP_TSX = REPO_ROOT / "web" / "src" / "App.tsx"
MODULES_DIR = REPO_ROOT / "web" / "src" / "modules"


def _run_node(expr: str) -> object:
    """Evaluate a small Node snippet against capture-screenshots.cjs and parse JSON stdout."""
    proc = subprocess.run(
        ["node", "-e", expr],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _load_tool_metadata() -> dict:
    return _run_node(
        f"""
        const tool = require({json.dumps(str(SCRIPT_PATH))});
        console.log(JSON.stringify({{
            pages: tool.PAGES,
            defaultOutputDir: tool.DEFAULT_OUTPUT_DIR,
            artifactDir: tool.ARTIFACT_DIR,
        }}));
        """
    )


def _extract_nav_hrefs_from_modules() -> set[str]:
    """Collect every sidebar navigation href declared in web/src/modules/*/index.tsx."""
    hrefs: set[str] = set()
    for module_file in sorted(MODULES_DIR.glob("*/index.tsx")):
        text = module_file.read_text(encoding="utf-8")
        for match in re.finditer(r"""href\s*:\s*['"]([^'"]+)['"]""", text):
            hrefs.add(match.group(1))
    return hrefs


def test_screenshot_tool_paths_and_artifact_dir() -> None:
    meta = _load_tool_metadata()
    assert meta["artifactDir"] == "/tmp/playwright-elder"
    assert (
        Path(meta["defaultOutputDir"]).resolve()
        == (REPO_ROOT / "docs" / "screenshots").resolve()
    )


def test_screenshot_tool_covers_all_v4_routes_and_excludes_stale_ones() -> None:
    meta = _load_tool_metadata()
    pages = meta["pages"]
    names = [p["name"] for p in pages]
    paths = {p["path"] for p in pages}
    base_paths = {p["path"].split("?")[0] for p in pages}

    # Unique filenames
    assert len(names) == len(set(names)), "Duplicate screenshot names in PAGES"

    # Both unauthenticated and authenticated pages are covered
    unauth = [p for p in pages if p.get("requiresAuth") is False]
    auth = [p for p in pages if p.get("requiresAuth") is not False]
    assert {p["path"] for p in unauth} >= {"/login", "/register"}
    assert len(auth) >= 40

    # Key v4.0.x routes must all be present
    required_v4_paths = {
        "/",
        "/runs",
        "/admin/workers",
        "/admin/integrations",
        "/admin/modules",
        "/streams",
        "/executions",
        "/approvals",
        "/csv-templates",
        "/on-call-rotations",
        "/diagrams",
        "/ai",
        "/data-inventory",
    }
    missing = required_v4_paths - base_paths
    assert not missing, f"Missing required v4.0.x routes: {sorted(missing)}"

    # Every module navigation href in web/src/modules/*/index.tsx must be covered
    nav_hrefs = _extract_nav_hrefs_from_modules()
    assert len(nav_hrefs) > 0
    uncovered_nav = nav_hrefs - base_paths
    assert (
        not uncovered_nav
    ), f"Module nav routes missing from screenshot tool: {sorted(uncovered_nav)}"

    # Stale / removed routes must not be present
    stale_paths = {"/diagram", "/helpdesk/tickets", "/admin/sync-config"}
    present_stale = stale_paths & paths
    assert (
        not present_stale
    ), f"Stale routes still in screenshot tool: {sorted(present_stale)}"


def test_detect_page_errors_flags_error_states() -> None:
    result = _run_node(
        f"""
        const {{ detectPageErrors }} = require({json.dumps(str(SCRIPT_PATH))});
        console.log(JSON.stringify({{
            clean: detectPageErrors({{
                pageInfo: {{ name: 'runs', path: '/runs' }},
                finalUrl: 'http://localhost:3005/runs',
                bodyText: 'CodeRuns\\n3 active jobs',
                alertTexts: [],
                brokenImages: 0,
                pageErrors: [],
            }}),
            redirectLogin: detectPageErrors({{
                pageInfo: {{ name: 'runs', path: '/runs' }},
                finalUrl: 'http://localhost:3005/login?next=%2Fruns',
                bodyText: 'Sign in',
            }}),
            redirectRoot: detectPageErrors({{
                pageInfo: {{ name: 'runs', path: '/runs' }},
                finalUrl: 'http://localhost:3005/',
                bodyText: 'Dashboard',
            }}),
            errorBoundary: detectPageErrors({{
                pageInfo: {{ name: 'runs', path: '/runs' }},
                finalUrl: 'http://localhost:3005/runs',
                bodyText: 'Something went wrong while rendering this page',
            }}),
            brokenImgAndAlert: detectPageErrors({{
                pageInfo: {{ name: 'runs', path: '/runs' }},
                finalUrl: 'http://localhost:3005/runs',
                bodyText: 'CodeRuns',
                alertTexts: ['Failed to load executions (500)'],
                brokenImages: 2,
                pageErrors: ['TypeError: Cannot read properties of undefined'],
            }}),
        }}));
        """
    )

    assert result["clean"] == []
    assert any("Redirected to /login" in msg for msg in result["redirectLogin"])
    assert any("Unexpected redirect to /" in msg for msg in result["redirectRoot"])
    assert any("Something went wrong" in msg for msg in result["errorBoundary"])
    assert len(result["brokenImgAndAlert"]) == 3


def test_prune_stale_screenshots(tmp_path: Path) -> None:
    (tmp_path / "login.png").write_bytes(b"fake")
    (tmp_path / "diagram.png").write_bytes(b"fake")
    (tmp_path / "helpdesk.png").write_bytes(b"fake")

    removed = _run_node(
        f"""
        const {{ pruneStaleScreenshots }} = require({json.dumps(str(SCRIPT_PATH))});
        const removed = pruneStaleScreenshots({json.dumps(str(tmp_path))});
        console.log(JSON.stringify(removed.sort()));
        """
    )

    assert removed == ["diagram.png", "helpdesk.png"]
    assert (tmp_path / "login.png").exists()
    assert not (tmp_path / "diagram.png").exists()
    assert not (tmp_path / "helpdesk.png").exists()
