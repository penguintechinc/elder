"""On-call rotation calculation service.

Provides business logic for determining who is currently on-call,
calculating shifts based on rotation schedules, and managing escalations.

Supports multiple schedule types:
- weekly: Fixed day-of-week rotation
- cron: Cron expression-based rotation
- manual: Manually assigned shifts
- follow_the_sun: Timezone-aware follow-the-sun rotation
"""

# flake8: noqa: E501


import datetime
from dataclasses import dataclass
from typing import Optional

import pytz
from croniter import croniter

from apps.api.models.dataclasses import CurrentOnCallDTO
from apps.api.utils.async_utils import run_in_threadpool


@dataclass(slots=True)
class OnCallShiftInfo:
    """Information about an on-call shift."""

    identity_id: int
    shift_start: datetime.datetime
    shift_end: datetime.datetime
    is_override: bool = False


class OnCallCalculator:
    """Business logic for on-call rotation calculations."""

    # Schedule type constants
    SCHEDULE_WEEKLY = "weekly"
    SCHEDULE_CRON = "cron"
    SCHEDULE_MANUAL = "manual"
    SCHEDULE_FOLLOW_THE_SUN = "follow_the_sun"

    @staticmethod
    async def get_current_oncall(
        db, rotation_id: int, target_datetime: Optional[datetime.datetime] = None
    ) -> Optional[CurrentOnCallDTO]:
        """
        Get the current on-call person for a rotation.

        Algorithm:
        1. Check for active overrides in the time range first
        2. If override exists, return override identity
        3. Otherwise, calculate based on schedule_type
        4. Return CurrentOnCallDTO or None

        Args:
            db: PyDAL database instance
            rotation_id: ID of the rotation to check
            target_datetime: Datetime to calculate for (defaults to now)

        Returns:
            CurrentOnCallDTO if someone is on-call, None otherwise
        """
        if target_datetime is None:
            target_datetime = datetime.datetime.now(datetime.timezone.utc)

        def get_rotation_and_current():
            rotation = db.on_call_rotations[rotation_id]
            if not rotation:
                return None, None

            # Check for active overrides first
            override = db(
                (db.on_call_overrides.rotation_id == rotation_id)
                & (db.on_call_overrides.start_datetime <= target_datetime)
                & (db.on_call_overrides.end_datetime > target_datetime)
            ).select(limitby=(0, 1))

            if override:
                override_row = override[0]
                # Get override identity details
                override_identity = db.identities[override_row.override_identity_id]
                return rotation, override_row, override_identity

            return rotation, None, None

        rotation, override, override_identity = await run_in_threadpool(
            get_rotation_and_current
        )

        if not rotation:
            return None

        # If override exists, return it
        if override:
            shift_end = override.end_datetime
            return CurrentOnCallDTO(
                identity_id=override.override_identity_id,
                identity_name=override_identity.name or override_identity.email,
                identity_email=override_identity.email,
                shift_start=override.start_datetime,
                shift_end=shift_end,
                is_override=True,
                override_reason=override.reason,
            )

        # Otherwise, calculate based on schedule type
        shift_info = None

        if rotation.schedule_type == OnCallCalculator.SCHEDULE_WEEKLY:
            shift_info = await OnCallCalculator.calculate_weekly_rotation(
                db, rotation, target_datetime
            )
        elif rotation.schedule_type == OnCallCalculator.SCHEDULE_CRON:
            shift_info = await OnCallCalculator.calculate_cron_rotation(
                db, rotation, target_datetime
            )
        elif rotation.schedule_type == OnCallCalculator.SCHEDULE_FOLLOW_THE_SUN:
            shift_info = await OnCallCalculator.calculate_followthesun_rotation(
                db, rotation, target_datetime
            )
        # SCHEDULE_MANUAL handled separately - no automatic calculation

        if not shift_info:
            return None

        # Get identity details
        def get_identity():
            return db.identities[shift_info.identity_id]

        identity = await run_in_threadpool(get_identity)
        if not identity:
            return None

        return CurrentOnCallDTO(
            identity_id=shift_info.identity_id,
            identity_name=identity.name or identity.email,
            identity_email=identity.email,
            shift_start=shift_info.shift_start,
            shift_end=shift_info.shift_end,
            is_override=False,
            override_reason=None,
        )

    @staticmethod
    async def calculate_weekly_rotation(
        db, rotation, target_datetime: datetime.datetime
    ) -> Optional[OnCallShiftInfo]:
        """
        Calculate on-call shift for weekly rotation.

        Weekly rotations cycle through participants based on day of week and rotation_length_days.
        Algorithm:
        - rotation_start_date defines when rotation began
        - rotation_length_days defines how many days each person is on-call
        - Participants ordered by order_index
        - Calculate elapsed days since start, mod by (num_participants * rotation_length_days)
        - Determine which participant is on-call

        Args:
            db: PyDAL database instance
            rotation: Rotation record
            target_datetime: Datetime to calculate for

        Returns:
            OnCallShiftInfo if valid weekly rotation, None otherwise
        """
        if not rotation.rotation_start_date or not rotation.rotation_length_days:
            return None

        def get_participants():
            participants = db(
                (db.on_call_rotation_participants.rotation_id == rotation.id)
                & (db.on_call_rotation_participants.is_active is True)
            ).select(orderby=db.on_call_rotation_participants.order_index)
            return list(participants)

        participants = await run_in_threadpool(get_participants)
        if not participants:
            return None

        # Convert start_date to UTC datetime at start of day
        start_date = rotation.rotation_start_date
        if isinstance(start_date, str):
            start_date = datetime.datetime.fromisoformat(start_date).date()

        rotation_start_dt = datetime.datetime.combine(
            start_date, datetime.time.min, tzinfo=datetime.timezone.utc
        )

        # Calculate elapsed days since rotation start
        elapsed_days = (
            target_datetime.replace(tzinfo=datetime.timezone.utc) - rotation_start_dt
        ).days

        # Calculate cycle length: num_participants * rotation_length_days
        cycle_length = len(participants) * rotation.rotation_length_days
        position_in_cycle = elapsed_days % cycle_length

        # Determine which participant based on position
        participant_index = position_in_cycle // rotation.rotation_length_days
        if participant_index >= len(participants):
            participant_index = 0

        participant = participants[participant_index]

        # Calculate shift start and end
        shift_start = rotation_start_dt + datetime.timedelta(days=position_in_cycle)
        shift_end = shift_start + datetime.timedelta(days=rotation.rotation_length_days)

        return OnCallShiftInfo(
            identity_id=participant.identity_id,
            shift_start=shift_start,
            shift_end=shift_end,
            is_override=False,
        )

    @staticmethod
    async def calculate_cron_rotation(
        db, rotation, target_datetime: datetime.datetime
    ) -> Optional[OnCallShiftInfo]:
        """
        Calculate on-call shift for cron-based rotation.

        Cron rotations use a cron expression to determine handoff times.
        Algorithm:
        - schedule_cron defines cron pattern for handoffs
        - Find previous and next handoff times
        - Determine which participant is on-call based on number of handoffs since start
        - Use rotation_length_days for shift end (or next handoff time if defined)

        Args:
            db: PyDAL database instance
            rotation: Rotation record
            target_datetime: Datetime to calculate for

        Returns:
            OnCallShiftInfo if valid cron rotation, None otherwise
        """
        if not rotation.schedule_cron or not rotation.rotation_start_date:
            return None

        def get_participants():
            participants = db(
                (db.on_call_rotation_participants.rotation_id == rotation.id)
                & (db.on_call_rotation_participants.is_active is True)
            ).select(orderby=db.on_call_rotation_participants.order_index)
            return list(participants)

        participants = await run_in_threadpool(get_participants)
        if not participants:
            return None

        try:
            # Convert start_date to UTC datetime
            start_date = rotation.rotation_start_date
            if isinstance(start_date, str):
                start_date = datetime.datetime.fromisoformat(start_date).date()

            rotation_start_dt = datetime.datetime.combine(
                start_date, datetime.time.min, tzinfo=datetime.timezone.utc
            )

            # Create cron iterator from rotation start
            cron = croniter(rotation.schedule_cron, rotation_start_dt)

            # Find next handoff before target_datetime
            handoff_times = []
            current_iter = rotation_start_dt

            while current_iter < target_datetime:
                next_handoff = cron.get_next(datetime.datetime)
                if next_handoff >= target_datetime:
                    break
                handoff_times.append(next_handoff)
                current_iter = next_handoff

            # Determine participant based on number of handoffs
            num_handoffs = len(handoff_times)
            participant_index = num_handoffs % len(participants)
            participant = participants[participant_index]

            # Shift start is the last handoff time
            shift_start = handoff_times[-1] if handoff_times else rotation_start_dt

            # Shift end is next handoff time
            cron_end = croniter(rotation.schedule_cron, shift_start)
            shift_end = cron_end.get_next(datetime.datetime)

            return OnCallShiftInfo(
                identity_id=participant.identity_id,
                shift_start=shift_start,
                shift_end=shift_end,
                is_override=False,
            )

        except Exception:
            # Invalid cron expression
            return None

    @staticmethod
    async def calculate_followthesun_rotation(
        db, rotation, target_datetime: datetime.datetime
    ) -> Optional[OnCallShiftInfo]:
        """
        Calculate on-call shift for follow-the-sun rotation with timezones.

        Follow-the-sun rotations track coverage across timezones.
        Algorithm:
        - shift_config contains timezone definitions and shift times
        - Each participant has an associated timezone
        - Based on target_datetime and timezone, determine current shift
        - Participants ordered by order_index/timezone

        shift_config format: {
            "timezones": [
                {
                    "timezone": "US/Eastern",
                    "shift_start_hour": 9,
                    "shift_end_hour": 17,
                    "participant_ids": [1, 2, 3]
                }
            ]
        }

        Args:
            db: PyDAL database instance
            rotation: Rotation record
            target_datetime: Datetime to calculate for

        Returns:
            OnCallShiftInfo if valid follow-the-sun rotation, None otherwise
        """
        if not rotation.shift_config or not isinstance(rotation.shift_config, dict):
            return None

        timezones = rotation.shift_config.get("timezones", [])
        if not timezones:
            return None

        try:
            # Check each timezone to find who's currently on-call
            for tz_config in timezones:
                tz_name = tz_config.get("timezone")
                if not tz_name:
                    continue

                # Validate timezone
                try:
                    tz = pytz.timezone(tz_name)
                except pytz.exceptions.UnknownTimeZoneError:
                    continue

                # Convert target datetime to this timezone
                local_dt = target_datetime.astimezone(tz)
                current_hour = local_dt.hour

                shift_start_hour = tz_config.get("shift_start_hour", 9)
                shift_end_hour = tz_config.get("shift_end_hour", 17)

                # Check if current hour is within shift window
                if shift_start_hour <= current_hour < shift_end_hour:
                    participant_ids = tz_config.get("participant_ids", [])
                    if not participant_ids:
                        continue

                    # Get first available participant for this timezone
                    def get_participant():
                        for pid in participant_ids:
                            participant = db.on_call_rotation_participants[
                                (
                                    db.on_call_rotation_participants.rotation_id
                                    == rotation.id
                                )
                                & (db.on_call_rotation_participants.identity_id == pid)
                                & (db.on_call_rotation_participants.is_active is True)
                            ]
                            if participant:
                                return participant
                        return None

                    participant = await run_in_threadpool(get_participant)
                    if not participant:
                        continue

                    # Calculate shift boundaries in UTC
                    shift_start_local = local_dt.replace(
                        hour=shift_start_hour, minute=0, second=0, microsecond=0
                    )
                    shift_end_local = local_dt.replace(
                        hour=shift_end_hour, minute=0, second=0, microsecond=0
                    )

                    shift_start_utc = shift_start_local.astimezone(
                        datetime.timezone.utc
                    )
                    shift_end_utc = shift_end_local.astimezone(datetime.timezone.utc)

                    return OnCallShiftInfo(
                        identity_id=participant.identity_id,
                        shift_start=shift_start_utc,
                        shift_end=shift_end_utc,
                        is_override=False,
                    )

            return None

        except Exception:
            return None

    @staticmethod
    async def get_escalation_chain(db, rotation_id: int) -> list:
        """
        Get ordered escalation policies for a rotation.

        Returns escalation policies sorted by level, with identity/group details.

        Args:
            db: PyDAL database instance
            rotation_id: ID of the rotation

        Returns:
            List of escalation policy dicts with identity/group details
        """

        def get_policies():
            policies = db(
                db.on_call_escalation_policies.rotation_id == rotation_id
            ).select(orderby=db.on_call_escalation_policies.level)

            result = []
            for policy in policies:
                policy_dict = {
                    "id": policy.id,
                    "level": policy.level,
                    "escalation_type": policy.escalation_type,
                    "escalation_delay_minutes": policy.escalation_delay_minutes,
                    "notification_channels": policy.notification_channels or [],
                }

                # Add identity or group details
                if policy.identity_id:
                    identity = db.identities[policy.identity_id]
                    if identity:
                        policy_dict["identity"] = {
                            "id": identity.id,
                            "name": identity.name,
                            "email": identity.email,
                        }

                if policy.group_id:
                    group = db.identity_groups[policy.group_id]
                    if group:
                        policy_dict["group"] = {
                            "id": group.id,
                            "name": group.name,
                        }

                result.append(policy_dict)

            return result

        return await run_in_threadpool(get_policies)
