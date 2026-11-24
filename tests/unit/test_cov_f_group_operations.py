"""Behavioural tests for the provider-agnostic group write-back mixin."""

from datetime import datetime

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.connectors.group_operations import (
    GroupMembershipResult,
    GroupOperationsMixin,
    GroupOperationType,
    GroupSyncResult,
)


class _Provider(GroupOperationsMixin):
    """In-memory identity provider implementing the mixin's abstract surface."""

    def __init__(self, members: set[str], rejects: set[str] | None = None):
        """Start with the given members; ``rejects`` are users the provider refuses."""
        self.members = set(members)
        self.rejects = rejects or set()
        self.exists = True

    async def add_group_member(
        self, group_id: str, user_id: str
    ) -> GroupMembershipResult:
        """Add a member unless the provider rejects that user."""
        ok = user_id not in self.rejects
        if ok:
            self.members.add(user_id)
        return GroupMembershipResult(ok, group_id, user_id, "add", None if ok else "no")

    async def remove_group_member(
        self, group_id: str, user_id: str
    ) -> GroupMembershipResult:
        """Remove a member unless the provider rejects that user."""
        ok = user_id not in self.rejects
        if ok:
            self.members.discard(user_id)
        return GroupMembershipResult(
            ok, group_id, user_id, "remove", None if ok else "no"
        )

    async def get_group_members(self, group_id: str) -> list[str]:
        """List members, or fail like a provider that cannot find the group."""
        if not self.exists:
            raise LookupError(f"group {group_id} not found")
        return sorted(self.members)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "desired", "expected_members", "expected_ops"),
    [
        ("replace", ["b", "c"], {"b", "c"}, {("remove", "a"), ("add", "c")}),
        ("replace", ["a", "b"], {"a", "b"}, set()),
        ("replace", [], set(), {("remove", "a"), ("remove", "b")}),
        ("add", ["c"], {"a", "b", "c"}, {("add", "c")}),
        ("remove", ["a"], {"b"}, {("remove", "a")}),
        ("add", [], {"a", "b"}, set()),
    ],
)
async def test_sync_modes_converge_membership(
    mode: str, desired: list[str], expected_members: set[str], expected_ops: set
) -> None:
    """Each mode issues exactly the operations needed and reaches the target state."""
    provider = _Provider({"a", "b"})
    result = await provider.sync_group_members("grp", desired, mode=mode)

    assert provider.members == expected_members
    assert {(r.operation, r.user_id) for r in result.results} == expected_ops
    assert result.total_operations == len(expected_ops) == result.successful
    assert result.failed == 0 and result.has_errors is False
    assert {r.group_id for r in result.results} <= {"grp"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "desired"), [("replace", ["c"]), ("add", ["a", "c"]), ("remove", ["a"])]
)
async def test_provider_rejections_are_counted_not_raised(
    mode: str, desired: list[str]
) -> None:
    """Rejected operations are tallied as failures and flagged in the summary."""
    provider = _Provider({"a", "b"}, rejects={"a", "c"})
    result = await provider.sync_group_members("grp", desired, mode=mode)

    failed = [r for r in result.results if not r.success]
    assert result.failed == len(failed) >= 1
    assert result.successful + result.failed == result.total_operations
    assert result.has_errors is True
    summary = result.to_dict()
    assert summary["has_errors"] is True
    assert summary["failed"] == result.failed
    assert all(r["error"] == "no" for r in summary["results"] if not r["success"])


@pytest.mark.xfail(
    strict=True,
    reason="bug: group_operations.py:208 an unknown sync mode silently does nothing "
    "and reports success instead of failing loudly",
)
@pytest.mark.asyncio
async def test_unknown_mode_is_rejected() -> None:
    """A typo in the sync mode must not look like a successful no-op."""
    provider = _Provider({"a"})
    with pytest.raises(ValueError, match="mode"):
        await provider.sync_group_members("grp", ["b"], mode="replce")


@pytest.mark.asyncio
async def test_validate_group_exists() -> None:
    """Group existence is derived from whether members can be listed."""
    provider = _Provider({"a"})
    assert await provider.validate_group_exists("grp") is True
    provider.exists = False
    assert await provider.validate_group_exists("grp") is False


def test_result_serialisation() -> None:
    """Results serialise with ISO timestamps and aggregate error state."""
    stamp = datetime(2026, 5, 6, 7, 8, 9)
    single = GroupMembershipResult(True, "g", "u", "add", timestamp=stamp)
    assert single.to_dict() == {
        "success": True,
        "group_id": "g",
        "user_id": "u",
        "operation": "add",
        "error": None,
        "timestamp": "2026-05-06T07:08:09",
    }
    assert (
        GroupMembershipResult(True, "g", "u", "add", timestamp=None).to_dict()[
            "timestamp"
        ]
        is None
    )
    bulk = GroupSyncResult(errors=["provider unreachable"])
    assert bulk.has_errors is True
    assert bulk.to_dict() == {
        "total_operations": 0,
        "successful": 0,
        "failed": 0,
        "has_errors": True,
        "results": [],
        "errors": ["provider unreachable"],
    }
    assert {t.value for t in GroupOperationType} == {"add_member", "remove_member"}
