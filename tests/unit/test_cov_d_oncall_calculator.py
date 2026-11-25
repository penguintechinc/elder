"""Coverage tests for the on-call calculator service.

The calculator takes the database as an argument.  Paths that work against
the real Postgres schema use it directly (known source bugs are pinned with
strict xfails).  The shift arithmetic sits behind a participants query that
is broken against a real database (``is True`` builds a bare bool), so that
arithmetic is exercised through a small in-memory stand-in for the DAL.
"""

# flake8: noqa: E501

import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.modules.services_oncall.services.on_call_calculator import (
    OnCallCalculator,
    OnCallShiftInfo,
)
from apps.api.utils.async_utils import run_in_threadpool
from tests.unit._cov_d_helpers import now, vid, world  # noqa: F401

UTC = datetime.UTC


class _Expr:
    """Query-expression stand-in that absorbs any comparison or conjunction."""

    def __eq__(self, other: Any) -> "_Expr":  # type: ignore[override]
        """Return another expression for ``==``."""
        return _Expr()

    __le__ = __lt__ = __gt__ = __ge__ = __and__ = __rand__ = __eq__

    def __hash__(self) -> int:
        """Keep the expression hashable despite the custom ``__eq__``."""
        return id(self)


class _Table:
    """Table stand-in: attributes are columns, items come from a queue or rows."""

    def __init__(self) -> None:
        """Start with no rows and an empty lookup queue."""
        self.rows: dict[int, Any] = {}
        self.lookups: list[Any] = []

    def __getattr__(self, name: str) -> _Expr:
        """Return a column expression for any attribute."""
        return _Expr()

    def __getitem__(self, key: Any) -> Any:
        """Resolve an id from ``rows``, or pop the next queued lookup for a query."""
        if isinstance(key, int):
            return self.rows.get(key)
        return self.lookups.pop(0) if self.lookups else None


class _Set:
    """Result-set stand-in returning the next queued select result."""

    def __init__(self, db: "_FakeDB") -> None:
        """Remember the owning database."""
        self.db = db

    def select(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Pop and return the next queued select result."""
        return self.db.selects.pop(0)


class _FakeDB:
    """Minimal DAL stand-in: tables by name and a queue of select results."""

    def __init__(self) -> None:
        """Create the table registry and select queue."""
        self.tables: dict[str, _Table] = {}
        self.selects: list[list[Any]] = []

    def __getattr__(self, name: str) -> _Table:
        """Return (creating if needed) the named table."""
        return self.tables.setdefault(name, _Table())

    def __call__(self, query: Any) -> _Set:
        """Return a result set for any query."""
        return _Set(self)


def _rotation(**kw: Any) -> SimpleNamespace:
    """Build a rotation row stand-in with sensible defaults."""
    base = {
        "id": 1,
        "schedule_type": "weekly",
        "rotation_length_days": 7,
        "rotation_start_date": datetime.date(2024, 1, 1),
        "schedule_cron": None,
        "shift_config": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _participants(*identity_ids: int) -> list[SimpleNamespace]:
    """Build ordered participant row stand-ins."""
    return [SimpleNamespace(identity_id=i) for i in identity_ids]


def _ident(identity_id: int, name: str | None = "Pat", email: str = "p@example.com"):
    """Build an identity row stand-in carrying a ``name`` attribute."""
    return SimpleNamespace(id=identity_id, name=name, email=email)


def test_shift_info_defaults() -> None:
    """OnCallShiftInfo defaults is_override to False."""
    t = now()
    info = OnCallShiftInfo(identity_id=1, shift_start=t, shift_end=t)
    assert info.is_override is False


# --- weekly -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_missing_config_returns_none() -> None:
    """A rotation without start date or length yields no shift."""
    db = _FakeDB()
    t = now()
    assert (
        await OnCallCalculator.calculate_weekly_rotation(
            db, _rotation(rotation_start_date=None), t
        )
        is None
    )
    assert (
        await OnCallCalculator.calculate_weekly_rotation(
            db, _rotation(rotation_length_days=None), t
        )
        is None
    )


@pytest.mark.asyncio
async def test_weekly_no_participants_returns_none() -> None:
    """A weekly rotation with nobody enrolled yields no shift."""
    db = _FakeDB()
    db.selects.append([])
    assert (
        await OnCallCalculator.calculate_weekly_rotation(
            db, _rotation(), datetime.datetime(2024, 1, 2, tzinfo=UTC)
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start", [datetime.date(2024, 1, 1), "2024-01-01"], ids=["date", "iso-string"]
)
async def test_weekly_picks_second_participant(start: Any) -> None:
    """Day 8 of a 7-day, two-person cycle lands on the second participant."""
    db = _FakeDB()
    db.selects.append(_participants(11, 22))
    target = datetime.datetime(2024, 1, 9, 12, tzinfo=UTC)
    info = await OnCallCalculator.calculate_weekly_rotation(
        db, _rotation(rotation_start_date=start), target
    )
    assert info is not None
    assert info.identity_id == 22
    assert info.shift_start == datetime.datetime(2024, 1, 9, tzinfo=UTC)
    assert info.shift_end - info.shift_start == datetime.timedelta(days=7)
    assert info.is_override is False


@pytest.mark.asyncio
async def test_weekly_wraps_around_cycle() -> None:
    """After a full cycle the first participant is on call again."""
    db = _FakeDB()
    db.selects.append(_participants(11, 22))
    target = datetime.datetime(2024, 1, 16, 1, tzinfo=UTC)
    info = await OnCallCalculator.calculate_weekly_rotation(db, _rotation(), target)
    assert info is not None and info.identity_id == 11


# --- cron -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_missing_config_returns_none() -> None:
    """Cron rotations need both an expression and a start date."""
    db = _FakeDB()
    t = now()
    assert (
        await OnCallCalculator.calculate_cron_rotation(
            db, _rotation(schedule_cron=None), t
        )
        is None
    )
    assert (
        await OnCallCalculator.calculate_cron_rotation(
            db, _rotation(schedule_cron="0 0 * * *", rotation_start_date=None), t
        )
        is None
    )


@pytest.mark.asyncio
async def test_cron_no_participants_returns_none() -> None:
    """A cron rotation with nobody enrolled yields no shift."""
    db = _FakeDB()
    db.selects.append([])
    assert (
        await OnCallCalculator.calculate_cron_rotation(
            db, _rotation(schedule_cron="0 0 * * *"), now()
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start", [datetime.date(2024, 1, 1), "2024-01-01"], ids=["date", "iso-string"]
)
async def test_cron_counts_handoffs(start: Any) -> None:
    """Two daily handoffs before the target select the first of two participants."""
    db = _FakeDB()
    db.selects.append(_participants(11, 22))
    target = datetime.datetime(2024, 1, 3, 12, tzinfo=UTC)
    info = await OnCallCalculator.calculate_cron_rotation(
        db, _rotation(schedule_cron="0 0 * * *", rotation_start_date=start), target
    )
    assert info is not None
    assert info.identity_id == 11
    assert info.shift_start == datetime.datetime(2024, 1, 3, tzinfo=UTC)
    assert info.shift_end == datetime.datetime(2024, 1, 4, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cron_before_first_handoff_uses_rotation_start() -> None:
    """Before any handoff occurs the shift starts at the rotation start."""
    db = _FakeDB()
    db.selects.append(_participants(11, 22))
    target = datetime.datetime(2024, 1, 1, 1, tzinfo=UTC)
    info = await OnCallCalculator.calculate_cron_rotation(
        db, _rotation(schedule_cron="0 0 * * *"), target
    )
    assert info is not None
    assert info.identity_id == 11
    assert info.shift_start == datetime.datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cron_invalid_expression_returns_none() -> None:
    """An unparseable cron expression is swallowed and yields no shift."""
    db = _FakeDB()
    db.selects.append(_participants(11))
    assert (
        await OnCallCalculator.calculate_cron_rotation(
            db, _rotation(schedule_cron="definitely not cron"), now()
        )
        is None
    )


# --- follow the sun -----------------------------------------------------------


def _fts(tz_configs: list[Any]) -> SimpleNamespace:
    """Build a follow-the-sun rotation stand-in."""
    return _rotation(
        schedule_type="follow_the_sun", shift_config={"timezones": tz_configs}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, "text", [], {}, {"timezones": []}], ids=repr)
async def test_fts_invalid_config_returns_none(config: Any) -> None:
    """Missing, non-dict or empty shift config yields no shift."""
    db = _FakeDB()
    rot = _rotation(schedule_type="follow_the_sun", shift_config=config)
    assert (
        await OnCallCalculator.calculate_followthesun_rotation(db, rot, now()) is None
    )


@pytest.mark.asyncio
async def test_fts_finds_in_window_participant() -> None:
    """The first timezone whose window contains the target supplies the shift."""
    db = _FakeDB()
    db.on_call_rotation_participants.lookups.extend([None, _participants(33)[0]])
    cfg = [
        {"shift_start_hour": 1, "shift_end_hour": 2, "participant_ids": [1]},
        {"timezone": "Not/AZone", "participant_ids": [1]},
        {"timezone": "UTC", "shift_start_hour": 0, "shift_end_hour": 1},
        {"timezone": "UTC", "shift_start_hour": 5, "shift_end_hour": 6},
        {"timezone": "UTC", "participant_ids": []},
        {"timezone": "UTC", "participant_ids": [8]},
        {"timezone": "UTC", "participant_ids": [8, 9]},
    ]
    target = datetime.datetime(2024, 1, 3, 10, 30, tzinfo=UTC)
    info = await OnCallCalculator.calculate_followthesun_rotation(db, _fts(cfg), target)
    assert info is not None
    assert info.identity_id == 33
    assert info.shift_start == datetime.datetime(2024, 1, 3, 9, tzinfo=UTC)
    assert info.shift_end == datetime.datetime(2024, 1, 3, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fts_no_matching_window_returns_none() -> None:
    """When no timezone window contains the target nobody is on call."""
    db = _FakeDB()
    cfg = [{"timezone": "UTC", "participant_ids": [1]}]
    target = datetime.datetime(2024, 1, 3, 20, tzinfo=UTC)
    assert (
        await OnCallCalculator.calculate_followthesun_rotation(db, _fts(cfg), target)
        is None
    )


@pytest.mark.asyncio
async def test_fts_malformed_entry_returns_none() -> None:
    """A non-dict timezone entry is caught and yields no shift."""
    db = _FakeDB()
    assert (
        await OnCallCalculator.calculate_followthesun_rotation(db, _fts(["bad"]), now())
        is None
    )


# --- get_current_oncall (fake db: arithmetic dispatch) -------------------------


@pytest.mark.asyncio
async def test_current_oncall_override_wins() -> None:
    """An active override short-circuits the schedule calculation."""
    db = _FakeDB()
    t = now()
    override = SimpleNamespace(
        override_identity_id=7,
        start_datetime=t,
        end_datetime=t + datetime.timedelta(hours=1),
        reason="vacation",
    )
    db.on_call_rotations.rows[1] = _rotation()
    db.identities.rows[7] = _ident(7, name=None, email="o@example.com")
    db.selects.append([override])
    dto = await OnCallCalculator.get_current_oncall(db, 1, t)
    assert dto is not None
    assert dto.is_override is True
    assert dto.identity_name == "o@example.com"
    assert dto.override_reason == "vacation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schedule,selects,lookups",
    [
        ("weekly", [[], _participants(5)], []),
        ("cron", [[], _participants(5)], []),
        ("follow_the_sun", [[]], [_participants(5)[0]]),
    ],
)
async def test_current_oncall_dispatches_by_schedule(
    schedule: str, selects: list[Any], lookups: list[Any]
) -> None:
    """Each schedule type is routed to its calculator and the identity resolved."""
    db = _FakeDB()
    target = datetime.datetime(2024, 1, 3, 10, tzinfo=UTC)
    db.on_call_rotations.rows[1] = _rotation(
        schedule_type=schedule,
        schedule_cron="0 0 * * *",
        shift_config={
            "timezones": [
                {
                    "timezone": "UTC",
                    "shift_start_hour": 0,
                    "shift_end_hour": 23,
                    "participant_ids": [5],
                }
            ]
        },
    )
    db.identities.rows[5] = _ident(5, name="Pat", email="pat@example.com")
    db.selects.extend(selects)
    db.on_call_rotation_participants.lookups.extend(lookups)
    dto = await OnCallCalculator.get_current_oncall(db, 1, target)
    assert dto is not None
    assert dto.identity_id == 5
    assert dto.identity_name == "Pat"
    assert dto.is_override is False


@pytest.mark.asyncio
async def test_current_oncall_defaults_target_time() -> None:
    """Without a target the current time is used (manual yields no one)."""
    db = _FakeDB()
    db.on_call_rotations.rows[1] = _rotation(schedule_type="manual")
    db.selects.append([])
    assert await OnCallCalculator.get_current_oncall(db, 1) is None


@pytest.mark.asyncio
async def test_current_oncall_identity_missing_returns_none() -> None:
    """A shift whose identity no longer exists yields no one."""
    db = _FakeDB()
    db.on_call_rotations.rows[1] = _rotation()
    db.selects.extend([[], _participants(99)])
    target = datetime.datetime(2024, 1, 3, tzinfo=UTC)
    assert await OnCallCalculator.get_current_oncall(db, 1, target) is None


@pytest.mark.asyncio
async def test_escalation_chain_with_identity_and_group() -> None:
    """Escalation entries carry identity and group details when they resolve."""
    db = _FakeDB()

    def pol(identity_id: int, group_id: int) -> SimpleNamespace:
        """Build an escalation policy row stand-in."""
        return SimpleNamespace(
            id=1,
            level=1,
            escalation_type="identity",
            escalation_delay_minutes=5,
            notification_channels=None,
            identity_id=identity_id,
            group_id=group_id,
        )

    db.selects.append(
        [
            pol(3, 4),
            pol(98, 99),
        ]
    )
    db.identities.rows[3] = _ident(3, name="Pat")
    db.identity_groups.rows[4] = SimpleNamespace(id=4, name="Ops")
    chain = await OnCallCalculator.get_escalation_chain(db, 1)
    assert chain[0]["identity"] == {"id": 3, "name": "Pat", "email": "p@example.com"}
    assert chain[0]["group"] == {"id": 4, "name": "Ops"}
    assert chain[0]["notification_channels"] == []
    assert "identity" not in chain[1] and "group" not in chain[1]


# --- real database -------------------------------------------------------------


async def _seed_rotation(w: dict[str, Any], **kw: Any) -> int:
    """Insert a rotation for tenant A and return its id."""
    a = w["a"]

    def _do() -> int:
        ts = now()
        values = dict(
            name="rot",
            is_active=True,
            scope_type="organization",
            organization_id=a["org"],
            schedule_type="manual",
            tenant_id=a["tenant"],
            village_id=vid(),
            created_at=ts,
            updated_at=ts,
        )
        values.update(kw)
        rid = w["db"].on_call_rotations.insert(**values)
        w["db"].commit()
        return rid

    return await run_in_threadpool(_do)


@pytest.mark.asyncio
async def test_real_manual_rotation_has_no_automatic_oncall(world) -> None:  # noqa: F811
    """A manual rotation without overrides resolves to nobody on call."""
    rid = await _seed_rotation(world, schedule_type="manual")
    assert await OnCallCalculator.get_current_oncall(world["db"], rid) is None


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: get_current_oncall returns a 2-tuple for a missing rotation but unpacks 3",
)
async def test_real_missing_rotation_returns_none(world) -> None:  # noqa: F811
    """An unknown rotation id should yield None rather than raising."""
    assert await OnCallCalculator.get_current_oncall(world["db"], 987654321) is None


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: calculator reads identity.name but identities have no name column",
)
async def test_real_override_resolves_identity(world) -> None:  # noqa: F811
    """An active override on a real rotation should resolve its identity."""
    a = world["a"]
    rid = await _seed_rotation(world)
    t = now()

    def _ov() -> None:
        world["db"].on_call_overrides.insert(
            rotation_id=rid,
            original_identity_id=a["user"],
            override_identity_id=a["user"],
            start_datetime=t - datetime.timedelta(hours=1),
            end_datetime=t + datetime.timedelta(hours=1),
            reason="cover",
            created_at=t,
        )
        world["db"].commit()

    await run_in_threadpool(_ov)
    dto = await OnCallCalculator.get_current_oncall(world["db"], rid, t)
    assert dto is not None and dto.is_override is True


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: participants filter uses `is True`, producing a bare bool query",
)
async def test_real_weekly_rotation_resolves_participant(world) -> None:  # noqa: F811
    """A real weekly rotation should resolve its enrolled participant."""
    a = world["a"]
    rid = await _seed_rotation(
        world,
        schedule_type="weekly",
        rotation_length_days=7,
        rotation_start_date=datetime.date(2024, 1, 1),
    )
    t = now()

    def _p() -> None:
        world["db"].on_call_rotation_participants.insert(
            rotation_id=rid,
            identity_id=a["user"],
            order_index=0,
            is_active=True,
            created_at=t,
            updated_at=t,
        )
        world["db"].commit()

    await run_in_threadpool(_p)
    rot = await run_in_threadpool(lambda: world["db"].on_call_rotations[rid])
    info = await OnCallCalculator.calculate_weekly_rotation(world["db"], rot, t)
    assert info is not None and info.identity_id == a["user"]


@pytest.mark.asyncio
async def test_real_escalation_chain_with_group(world) -> None:  # noqa: F811
    """The escalation chain is ordered by level and resolves group details."""
    a = world["a"]
    rid = await _seed_rotation(world)
    t = now()

    def _seed() -> int:
        db = world["db"]
        gid = db.identity_groups.insert(
            name=f"grp-{world['sfx']}",
            village_id=vid(),
            is_active=True,
            review_enabled=False,
            review_auto_apply=True,
            created_at=t,
            updated_at=t,
        )
        db.on_call_escalation_policies.insert(
            rotation_id=rid,
            level=2,
            escalation_type="group",
            group_id=gid,
            escalation_delay_minutes=10,
            created_at=t,
            updated_at=t,
        )
        db.on_call_escalation_policies.insert(
            rotation_id=rid,
            level=1,
            escalation_type="rotation_participant",
            escalation_delay_minutes=5,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return gid

    gid = await run_in_threadpool(_seed)
    chain = await OnCallCalculator.get_escalation_chain(world["db"], rid)
    assert [c["level"] for c in chain] == [1, 2]
    assert chain[1]["group"]["id"] == gid
    assert "identity" not in chain[0]
