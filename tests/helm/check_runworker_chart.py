#!/usr/bin/env python3
"""Render-time security assertions for the svc-runworker Helm templates.

Runs ``helm template`` against the chart with several value sets and checks
the isolation guarantees that must hold for the pod that schedules tenant
code and for the pods that run it. Standalone on purpose (``make test-helm``):
it needs only ``helm`` and PyYAML, not the application test environment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml

CHART = Path(__file__).resolve().parents[2] / "k8s" / "helm" / "elder"
BASE = (
    "svc-runworker.enabled=true",
    "svc-runworker.internalTokenSecret.name=rw-token",
)
EXEC_NS = "elder-runs"


def helm_template(*sets: str) -> subprocess.CompletedProcess[str]:
    """Run ``helm template`` on the chart with the given ``--set`` values."""
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError("helm binary not found; chart assertions cannot run")
    cmd = [helm, "template", "elder", str(CHART)]
    for item in sets:
        cmd += ["--set", item]
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, check=False, timeout=120
    )


def render(*sets: str) -> list[dict[str, Any]]:
    """Render the chart and return every non-empty manifest document."""
    proc = helm_template(*sets)
    if proc.returncode != 0:
        raise AssertionError(f"helm template failed: {proc.stderr.strip()[-400:]}")
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    """Return the single rendered document of ``kind`` named ``name``."""
    hits = [
        d for d in docs if d.get("kind") == kind and d["metadata"].get("name") == name
    ]
    if len(hits) != 1:
        raise AssertionError(f"expected exactly one {kind}/{name}, found {len(hits)}")
    return hits[0]


def has_kind(docs: list[dict[str, Any]], kind: str, name: str) -> bool:
    """Return True when a document of ``kind`` named ``name`` was rendered."""
    return any(
        d.get("kind") == kind and d["metadata"].get("name") == name for d in docs
    )


class DefaultBackendTests(unittest.TestCase):
    """Default values: Job backend, controller runs no tenant code."""

    docs: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Render the chart once with the default (k8s_job) backend."""
        cls.docs = render(*BASE)

    def _container(self) -> dict[str, Any]:
        """Return the svc-runworker container spec."""
        deploy = find(self.docs, "Deployment", "svc-runworker")
        return deploy["spec"]["template"]["spec"]["containers"][0]

    def _env(self) -> dict[str, dict[str, Any]]:
        """Return the worker container env entries keyed by name."""
        return {e["name"]: e for e in self._container()["env"]}

    def test_no_signing_key_and_no_literal_secret(self) -> None:
        """The worker gets no signing key and no secret rendered as a literal."""
        env = self._env()
        self.assertNotIn("JWT_SECRET_KEY", env)
        self.assertNotIn("SECRET_KEY", env)
        deploy_text = yaml.safe_dump(find(self.docs, "Deployment", "svc-runworker"))
        self.assertNotIn("CHANGE-IN-PRODUCTION", deploy_text)
        token = env["ELDER_INTERNAL_TOKEN"]
        self.assertNotIn("value", token)
        ref = token["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref["name"], "rw-token")
        self.assertNotIn("optional", ref)

    def test_default_backend_is_job_with_kata_execution_pods(self) -> None:
        """Default backend is k8s_job and execution pods default to Kata."""
        env = self._env()
        self.assertEqual(env["RUNS_RUNNER_BACKEND"]["value"], "k8s_job")
        self.assertEqual(env["RUNS_RUNTIME_CLASS_NAME"]["value"], "kata")
        self.assertEqual(
            env["RUNS_EXECUTION_SERVICE_ACCOUNT"]["value"], "elder-run-exec"
        )
        self.assertNotIn("RUNS_KATA_EXCEPTION_SANDBOX", env)
        self.assertNotIn("RUNS_KATA_EXCEPTION_JOBS", env)

    def test_controller_pod_hardening(self) -> None:
        """Controller is non-root, read-only rootfs, drops all capabilities."""
        ctx = self._container()["securityContext"]
        self.assertTrue(ctx["runAsNonRoot"])
        self.assertTrue(ctx["readOnlyRootFilesystem"])
        self.assertFalse(ctx["allowPrivilegeEscalation"])
        self.assertEqual(ctx["capabilities"]["drop"], ["ALL"])

    def test_execution_service_account_has_no_token_and_no_rbac(self) -> None:
        """Execution pods use a ServiceAccount with no token and no binding."""
        sa = next(
            d
            for d in self.docs
            if d.get("kind") == "ServiceAccount"
            and d["metadata"]["name"] == "elder-run-exec"
        )
        self.assertEqual(sa["metadata"]["namespace"], EXEC_NS)
        self.assertIs(sa["automountServiceAccountToken"], False)
        for doc in self.docs:
            if doc.get("kind") in ("RoleBinding", "ClusterRoleBinding"):
                for subject in doc.get("subjects", []):
                    self.assertNotEqual(subject.get("name"), "elder-run-exec")

    def test_controller_role_is_minimal(self) -> None:
        """Controller Role grants only the verbs the Job backend uses."""
        role = find(self.docs, "Role", "svc-runworker-executor")
        self.assertEqual(role["metadata"]["namespace"], EXEC_NS)
        granted = {
            (res, verb)
            for rule in role["rules"]
            for res in rule["resources"]
            for verb in rule["verbs"]
        }
        self.assertEqual(
            granted,
            {
                ("jobs", "create"),
                ("jobs/status", "get"),
                ("pods", "list"),
                ("pods/log", "get"),
                ("configmaps", "create"),
                ("configmaps", "delete"),
                ("secrets", "create"),
                ("secrets", "delete"),
            },
        )
        # Secrets are write-only for the controller: it can never read one.
        for verb in ("get", "list", "watch", "patch", "update", "*"):
            self.assertNotIn(("secrets", verb), granted)
        self.assertTrue(all(rule["apiGroups"] != ["*"] for rule in role["rules"]))
        self.assertFalse(
            any(
                d.get("kind") == "ClusterRole" and "runworker" in d["metadata"]["name"]
                for d in self.docs
            )
        )

    def test_controller_egress_has_no_open_internet(self) -> None:
        """Controller egress has no CIDR rule at all, so no 0.0.0.0/0."""
        policy = find(self.docs, "CiliumNetworkPolicy", "svc-runworker-policy")
        egress = policy["spec"]["egress"]
        self.assertNotIn("0.0.0.0/0", yaml.safe_dump(policy))
        self.assertFalse(any("toCIDRSet" in r or "toCIDR" in r for r in egress))
        self.assertFalse(any("toFQDNs" in r for r in egress))
        self.assertTrue(any(r.get("toEntities") == ["kube-apiserver"] for r in egress))

    def test_execution_pods_default_deny_and_gateway_only(self) -> None:
        """Execution namespace is default-deny; labelled pods reach only the gateway."""
        deny = find(self.docs, "CiliumNetworkPolicy", "elder-runs-default-deny")
        self.assertEqual(deny["metadata"]["namespace"], EXEC_NS)
        self.assertEqual(deny["spec"]["egress"], [])
        self.assertEqual(deny["spec"]["ingress"], [])
        self.assertFalse(
            has_kind(self.docs, "CiliumNetworkPolicy", "elder-runs-api-access")
        )
        gateway = find(self.docs, "CiliumNetworkPolicy", "elder-runs-gateway-access")
        egress = gateway["spec"]["egress"]
        self.assertEqual(len(egress), 1)
        target = egress[0]["toEndpoints"][0]["matchLabels"]
        self.assertEqual(target["app.kubernetes.io/component"], "svc-runworker")
        ports = [p["port"] for p in egress[0]["toPorts"][0]["ports"]]
        self.assertEqual(ports, [str(18080 + i) for i in range(5)])
        controller = find(self.docs, "CiliumNetworkPolicy", "svc-runworker-policy")
        ingress = controller["spec"]["ingress"]
        self.assertEqual([p["port"] for p in ingress[0]["toPorts"][0]["ports"]], ports)
        self.assertEqual(
            ingress[0]["fromEndpoints"][0]["matchLabels"][
                "elder.penguintech.io/gateway-access"
            ],
            "true",
        )

    def test_execution_namespace_enforces_restricted_psa(self) -> None:
        """Execution namespace carries the restricted Pod Security labels."""
        ns = find(self.docs, "Namespace", EXEC_NS)
        self.assertEqual(
            ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"],
            "restricted",
        )

    def test_controller_has_exec_allowlist_policy(self) -> None:
        """A pure controller pod is covered by a Tetragon exec allowlist."""
        self.assertTrue(
            has_kind(self.docs, "TracingPolicy", "elder-exec-allowlist-svc-runworker")
        )


class GuardTests(unittest.TestCase):
    """Value combinations that must fail rendering, and the opt-in paths."""

    def assert_fails(self, expected: str, *sets: str) -> None:
        """Assert ``helm template`` fails and the error mentions ``expected``."""
        proc = helm_template(*sets)
        self.assertNotEqual(proc.returncode, 0, "render unexpectedly succeeded")
        self.assertIn(expected, proc.stderr)

    def test_disabled_by_default_renders_no_runworker(self) -> None:
        """With stock values the worker is not rendered at all."""
        docs = render()
        self.assertFalse(has_kind(docs, "Deployment", "svc-runworker"))

    def test_fails_without_worker_token_secret(self) -> None:
        """Rendering fails when the worker token Secret reference is unset."""
        self.assert_fails(
            "internalTokenSecret.name is required", "svc-runworker.enabled=true"
        )

    def test_fails_sandbox_without_kata(self) -> None:
        """In-pod sandbox without a Kata pod runtime class is refused."""
        self.assert_fails(
            "KATA EXCEPTION", *BASE, "svc-runworker.runnerBackend=sandbox"
        )
        self.assert_fails(
            "KATA EXCEPTION",
            *BASE,
            "svc-runworker.runnerBackend=sandbox",
            "svc-runworker.podRuntimeClassName=runc",
        )

    def test_fails_auto_without_exception(self) -> None:
        """``auto`` (tenant code next to an API token) needs the exception flag."""
        self.assert_fails(
            "KATA EXCEPTION",
            *BASE,
            "svc-runworker.runnerBackend=auto",
            "svc-runworker.podRuntimeClassName=kata",
        )

    def test_fails_execution_pods_without_runtime_class(self) -> None:
        """Execution pods without a runtime class are refused."""
        self.assert_fails(
            "is not one of svc-runworker.sandboxedRuntimeClasses",
            *BASE,
            "svc-runworker.runtimeClassName=",
        )

    def test_fails_execution_pods_with_runc(self) -> None:
        """A non-empty but unsandboxed runtime class (runc) is refused."""
        self.assert_fails(
            'runtimeClassName "runc" is not one of',
            *BASE,
            "svc-runworker.runtimeClassName=runc",
        )

    def test_fails_execution_pods_with_misspelt_class(self) -> None:
        """A misspelt Kata class is refused instead of silently accepted."""
        self.assert_fails(
            'runtimeClassName "kata-qmeu" is not one of',
            *BASE,
            "svc-runworker.runtimeClassName=kata-qmeu",
        )
        self.assert_fails(
            "is not one of",
            *BASE,
            "svc-runworker.runnerBackend=auto",
            "svc-runworker.kataException.inPodSandbox=true",
            "svc-runworker.runtimeClassName=runc",
        )

    def test_listed_kata_class_is_accepted_and_shared_with_worker(self) -> None:
        """A listed class renders, and the worker receives the same list."""
        docs = render(*BASE, "svc-runworker.runtimeClassName=kata-qemu")
        pod = find(docs, "Deployment", "svc-runworker")["spec"]["template"]["spec"]
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["RUNS_RUNTIME_CLASS_NAME"]["value"], "kata-qemu")
        self.assertEqual(
            env["RUNS_SANDBOXED_RUNTIME_CLASSES"]["value"],
            "kata,kata-qemu,kata-clh,kata-fc",
        )
        self.assertNotIn("RUNS_KATA_EXCEPTION_JOBS", env)

    def test_runc_needs_the_execution_pod_exception(self) -> None:
        """runc renders only with the documented executionPods exception."""
        docs = render(
            *BASE,
            "svc-runworker.runtimeClassName=runc",
            "svc-runworker.kataException.executionPods=true",
        )
        pod = find(docs, "Deployment", "svc-runworker")["spec"]["template"]["spec"]
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["RUNS_KATA_EXCEPTION_JOBS"]["value"], "approved")
        self.assertEqual(env["RUNS_RUNTIME_CLASS_NAME"]["value"], "runc")

    def test_fails_unlabelled_external_execution_namespace(self) -> None:
        """A namespace the chart does not create must be verified as labelled."""
        self.assert_fails(
            "executionNamespacePodSecurityVerified",
            *BASE,
            "svc-runworker.createExecutionNamespace=false",
        )
        docs = render(
            *BASE,
            "svc-runworker.createExecutionNamespace=false",
            "svc-runworker.executionNamespacePodSecurityVerified=true",
        )
        self.assertFalse(has_kind(docs, "Namespace", EXEC_NS))

    def test_fails_execution_namespace_equal_to_release_namespace(self) -> None:
        """Execution pods may not share the platform namespace."""
        self.assert_fails(
            "must differ from the release namespace",
            *BASE,
            "svc-runworker.executionNamespace=elder",
        )

    def test_fails_unknown_backend(self) -> None:
        """An unknown backend name is refused instead of falling back."""
        self.assert_fails("not supported", *BASE, "svc-runworker.runnerBackend=warm")

    def test_sandbox_under_kata_mounts_no_api_token(self) -> None:
        """Kata in-pod sandbox: no API token, no RBAC, no exec allowlist policy."""
        docs = render(
            *BASE,
            "svc-runworker.runnerBackend=sandbox",
            "svc-runworker.podRuntimeClassName=kata",
        )
        pod = find(docs, "Deployment", "svc-runworker")["spec"]["template"]["spec"]
        self.assertIs(pod["automountServiceAccountToken"], False)
        self.assertEqual(pod["runtimeClassName"], "kata")
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["RUNS_POD_RUNTIME_CLASS"]["value"], "kata")
        self.assertNotIn("RUNS_KATA_EXCEPTION_SANDBOX", env)
        self.assertFalse(has_kind(docs, "Role", "svc-runworker-executor"))
        self.assertFalse(
            has_kind(docs, "TracingPolicy", "elder-exec-allowlist-svc-runworker")
        )
        policy = find(docs, "CiliumNetworkPolicy", "svc-runworker-policy")
        self.assertEqual(policy["spec"]["ingress"], [])
        self.assertNotIn("kube-apiserver", yaml.safe_dump(policy))
        self.assertNotIn("0.0.0.0/0", yaml.safe_dump(policy))

    def test_exception_flags_are_explicit(self) -> None:
        """Exception flags render the override env only when set."""
        docs = render(
            *BASE,
            "svc-runworker.runnerBackend=sandbox",
            "svc-runworker.kataException.inPodSandbox=true",
        )
        pod = find(docs, "Deployment", "svc-runworker")["spec"]["template"]["spec"]
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["RUNS_KATA_EXCEPTION_SANDBOX"]["value"], "approved")
        self.assertIs(pod["automountServiceAccountToken"], False)
        docs = render(
            *BASE,
            "svc-runworker.runtimeClassName=",
            "svc-runworker.kataException.executionPods=true",
        )
        pod = find(docs, "Deployment", "svc-runworker")["spec"]["template"]["spec"]
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["RUNS_KATA_EXCEPTION_JOBS"]["value"], "approved")

    def test_declared_fqdn_egress_is_the_only_internet_path(self) -> None:
        """Operator-declared FQDNs render as an explicit HTTPS-only rule."""
        docs = render(
            *BASE, "svc-runworker.networkPolicy.egressFQDNs={api.example.com}"
        )
        policy = find(docs, "CiliumNetworkPolicy", "svc-runworker-policy")
        fqdn_rules = [r for r in policy["spec"]["egress"] if "toFQDNs" in r]
        self.assertEqual(len(fqdn_rules), 1)
        self.assertEqual(fqdn_rules[0]["toFQDNs"], [{"matchName": "api.example.com"}])
        self.assertEqual(fqdn_rules[0]["toPorts"][0]["ports"][0]["port"], "443")
        self.assertNotIn("0.0.0.0/0", yaml.safe_dump(policy))


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    if result.testsRun == 0:
        print("FAIL: zero chart assertions ran")
        sys.exit(1)
    sys.exit(0 if result.wasSuccessful() else 1)
