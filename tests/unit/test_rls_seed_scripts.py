"""The direct-DAL operator seed scripts must run in an explicit MAINTENANCE scope.

They write across tenants out-of-band; under enforced RLS an un-scoped script
would silently insert nothing / fail. Static check: each script's ``__main__``
entry point is wrapped in ``with system_scope(SystemScopeReason.MAINTENANCE, ...)``.
"""

import ast
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
NAMES = (
    "seed_access_reviews",
    "seed_cloud_discovery",
    "seed_demo_unified",
    "seed_k8s_geo_demo",
)


def _main_block(tree: ast.Module) -> ast.If:
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and getattr(node.test.left, "id", "") == "__name__"
        ):
            return node
    raise AssertionError("no __main__ block")


@pytest.mark.parametrize("name", NAMES)
def test_entrypoint_runs_inside_a_maintenance_system_scope(name):
    tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
    withs = [n for n in ast.walk(_main_block(tree)) if isinstance(n, ast.With)]
    assert len(withs) == 1, f"{name}: expected exactly one with-block in __main__"
    call = withs[0].items[0].context_expr
    assert isinstance(call, ast.Call) and call.func.id == "system_scope"
    reason, purpose = call.args[0], call.args[1]
    assert (reason.value.id, reason.attr) == ("SystemScopeReason", "MAINTENANCE")
    assert purpose.value == f"scripts.{name}"
    inner = [
        n.func.id
        for n in ast.walk(withs[0])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert name in inner, f"{name}() must be called inside the scope"
