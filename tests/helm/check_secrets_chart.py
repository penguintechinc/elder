#!/usr/bin/env python3
"""Render-time assertions for chart-wide secret handling and pod hardening.

Covers issue #385: no credential is ever rendered as a literal env value,
the chart fails closed on missing/unsafe secret configuration, every pod and
container carries the restricted security baseline, no ``latest`` image tag
is rendered, and the DB connection budget guard trips. Standalone
(``make test-helm``): needs only ``helm`` and PyYAML.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml

CHART = Path(__file__).resolve().parents[2] / "k8s" / "helm" / "elder"
VALUES_FILES = ("", "alpha.yml", "values-beta.yaml", "do-beta.yml")
SENSITIVE = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|LICENSE_KEY|ACCESS_KEY)")
NOT_SENSITIVE = re.compile(r"_(HOURS|DAYS|SECONDS|PATH|FILE|URL)$")
CRED_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/@]*:[^/@]+@")


def helm_template(
    values_file: str = "", *sets: str
) -> subprocess.CompletedProcess[str]:
    """Run ``helm template`` with an optional values file and ``--set`` items."""
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError("helm binary not found; chart assertions cannot run")
    cmd = [helm, "template", "elder", str(CHART)]
    if values_file:
        cmd += ["--values", str(CHART / values_file)]
    for item in sets:
        cmd += ["--set", item]
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, check=False, timeout=120
    )


def render(values_file: str = "", *sets: str) -> list[dict[str, Any]]:
    """Render the chart and return every non-empty manifest document."""
    proc = helm_template(values_file, *sets)
    if proc.returncode != 0:
        raise AssertionError(f"helm template failed: {proc.stderr.strip()[-400:]}")
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def pod_specs(docs: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Yield (name, podSpec) for every workload document."""
    out = []
    for d in docs:
        spec = d.get("spec") or {}
        tmpl = (spec.get("jobTemplate") or {}).get("spec", {}).get(
            "template"
        ) or spec.get("template")
        if tmpl and "containers" in tmpl.get("spec", {}):
            out.append((d["metadata"]["name"], tmpl["spec"]))
    return out


class RenderMatrixTest(unittest.TestCase):
    """Every shipped values file renders hardened, literal-free manifests."""

    def test_every_values_file(self) -> None:
        """Run the full assertion set against each values file."""
        for vf in VALUES_FILES:
            with self.subTest(values=vf or "default"):
                docs = render(vf)
                specs = pod_specs(docs)
                self.assertTrue(specs)
                for name, ps in specs:
                    psc = ps.get("securityContext", {})
                    self.assertEqual(
                        psc.get("seccompProfile", {}).get("type"),
                        "RuntimeDefault",
                        name,
                    )
                    self.assertTrue(psc.get("runAsNonRoot"), name)
                    for c in ps["containers"] + ps.get("initContainers", []):
                        sc = c.get("securityContext", {})
                        self.assertTrue(sc.get("runAsNonRoot"), name)
                        self.assertIs(sc.get("allowPrivilegeEscalation"), False, name)
                        self.assertTrue(sc.get("readOnlyRootFilesystem"), name)
                        self.assertEqual(
                            sc.get("capabilities"), {"drop": ["ALL"]}, name
                        )
                        self.assertEqual(
                            sc.get("seccompProfile", {}).get("type"),
                            "RuntimeDefault",
                            name,
                        )
                        tag = c["image"].rsplit(":", 1)[-1]
                        self.assertNotEqual(tag, "latest", name)
                        for e in c.get("env", []):
                            if "value" not in e or not e["value"]:
                                continue
                            sensitive = SENSITIVE.search(
                                e["name"]
                            ) and not NOT_SENSITIVE.search(e["name"])
                            self.assertFalse(
                                sensitive, f"{name}: {e['name']} is a literal"
                            )
                            self.assertFalse(
                                CRED_URL.match(str(e["value"])), f"{name}: {e['name']}"
                            )

    def test_jwt_secret_from_secret_ref_in_cronjobs(self) -> None:
        """svc-scanner/svc-integrations get JWT_SECRET_KEY via secretKeyRef."""
        docs = render("", "svc-scanner.mode=cronjob")
        for name, ps in pod_specs(docs):
            for c in ps["containers"]:
                for e in c.get("env", []):
                    if e["name"] in ("JWT_SECRET_KEY", "SECRET_KEY"):
                        self.assertIn("secretKeyRef", e.get("valueFrom", {}), name)


class FailClosedTest(unittest.TestCase):
    """Unsafe configurations must refuse to render."""

    def assert_fails(self, needle: str, values_file: str = "", *sets: str) -> None:
        """Assert rendering fails with ``needle`` in the error."""
        proc = helm_template(values_file, *sets)
        self.assertNotEqual(proc.returncode, 0, sets)
        self.assertIn(needle, proc.stderr)

    def test_missing_secret_name(self) -> None:
        """Empty secrets.existingSecret outside dev fails."""
        self.assert_fails(
            "secrets.existingSecret is required", "", "secrets.existingSecret="
        )

    def test_dev_secret_refused_outside_dev(self) -> None:
        """Chart-created Secret is refused in non-dev environments."""
        self.assert_fails(
            "only allowed when environment is dev",
            "values-beta.yaml",
            "secrets.create=true",
        )

    def test_env_literal_refused(self) -> None:
        """A credential literal in a component env map fails."""
        self.assert_fails("must not be a literal", "", "hub-mcp.env.SMTP_PASSWORD=x")
        self.assert_fails(
            "must not be a literal", "", "hub-mcp.env.SOME_DSN_URL=redis://:pw@h:6379/0"
        )

    def test_latest_tag_refused(self) -> None:
        """``latest`` image tags fail."""
        self.assert_fails("mutable 'latest' tag", "", "hub-api.image.tag=latest")

    def test_db_budget(self) -> None:
        """Summed pools above postgres.maxConnections fail."""
        self.assert_fails(
            "DB connection budget exceeded",
            "",
            "hub-api.autoscaling.enabled=true",
            "hub-api.autoscaling.maxReplicas=50",
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
