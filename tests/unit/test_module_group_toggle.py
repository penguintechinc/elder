"""Phase 1: ELDER_GROUP_* toggles; per-module override beats group beats base."""

from apps.api.modules.registry import resolve_enabled


def _names(env):
    return {m.name for m in resolve_enabled(env)}


def test_group_false_disables_all_modules_in_group():
    env = {"ELDER_MODULES_ENABLED": "all", "ELDER_GROUP_WORKFLOW": "false"}
    assert "helpdesk" not in _names(env)


def test_group_true_enables_group_modules():
    env = {"ELDER_MODULES_ENABLED": "issues", "ELDER_GROUP_KB": "true"}
    got = _names(env)
    assert {"documents", "pages", "diagrams"}.issubset(got)


def test_per_module_override_beats_group():
    # group off, but the module is explicitly on -> module wins
    env = {
        "ELDER_MODULES_ENABLED": "all",
        "ELDER_GROUP_WORKFLOW": "false",
        "ELDER_MODULE_HELPDESK": "true",
    }
    assert "helpdesk" in _names(env)
