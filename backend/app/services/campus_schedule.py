import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.schemas.access import ScheduleStatus

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True)
class _AccessWindow:
    opening: time
    closing: time


@dataclass(frozen=True)
class ScheduleEvaluation:
    status: ScheduleStatus
    timezone: str | None = None
    local_datetime: datetime | None = None


class CampusScheduleEvaluator:
    def __init__(self, schedule_path: str | Path) -> None:
        self.schedule_path = Path(schedule_path)

    def evaluate(self, now: datetime | None = None) -> ScheduleEvaluation:
        try:
            config = json.loads(self.schedule_path.read_text(encoding="utf-8"))
            zone, weekly, exceptions = self._parse_config(config)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None or current.utcoffset() is None:
                raise ValueError("Schedule evaluation requires an aware timestamp.")
            local = current.astimezone(zone)
            entry = exceptions.get(
                local.date(),
                weekly[WEEKDAYS[local.weekday()]],
            )
        except (
            OSError,
            UnicodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ):
            return ScheduleEvaluation(ScheduleStatus.UNAVAILABLE)

        if entry is None:
            return ScheduleEvaluation(
                ScheduleStatus.OUTSIDE_SCHEDULE,
                zone.key,
                local,
            )

        status = (
            ScheduleStatus.WITHIN_SCHEDULE
            if entry.opening <= local.timetz().replace(tzinfo=None) < entry.closing
            else ScheduleStatus.OUTSIDE_SCHEDULE
        )
        return ScheduleEvaluation(status, zone.key, local)

    @classmethod
    def _parse_config(
        cls, config: object
    ) -> tuple[ZoneInfo, dict[str, _AccessWindow | None], dict[date, _AccessWindow | None]]:
        if not isinstance(config, dict) or set(config) != {
            "timezone",
            "weekly",
            "exceptions",
        }:
            raise ValueError("Invalid campus schedule structure.")

        timezone_name = config["timezone"]
        if not isinstance(timezone_name, str) or not timezone_name.strip():
            raise ValueError("Invalid schedule timezone.")
        zone = ZoneInfo(timezone_name)

        weekly_config = config["weekly"]
        if not isinstance(weekly_config, dict) or set(weekly_config) != set(WEEKDAYS):
            raise ValueError("Every weekday must have a schedule entry.")
        weekly = {
            weekday: cls._parse_window(weekly_config[weekday])
            for weekday in WEEKDAYS
        }

        exceptions_config = config["exceptions"]
        if not isinstance(exceptions_config, dict):
            raise ValueError("Schedule exceptions must be an object.")
        exceptions: dict[date, _AccessWindow | None] = {}
        for day_text, entry in exceptions_config.items():
            if not isinstance(day_text, str):
                raise ValueError("Schedule exception dates must be strings.")
            exception_day = date.fromisoformat(day_text)
            if exception_day.isoformat() != day_text:
                raise ValueError("Schedule exception dates must use YYYY-MM-DD.")
            exceptions[exception_day] = cls._parse_window(entry)
        return zone, weekly, exceptions

    @staticmethod
    def _parse_window(value: object) -> _AccessWindow | None:
        if not isinstance(value, dict):
            raise ValueError("Schedule entries must be objects.")
        if value == {"closed": True}:
            return None
        if set(value) != {"open", "close"}:
            raise ValueError("Schedule entries require open/close or closed=true.")

        opening = CampusScheduleEvaluator._parse_time(value["open"])
        closing = CampusScheduleEvaluator._parse_time(value["close"])
        if closing <= opening:
            raise ValueError("Schedule closing time must follow opening time.")
        return _AccessWindow(opening, closing)

    @staticmethod
    def _parse_time(value: object) -> time:
        if not isinstance(value, str):
            raise ValueError("Schedule times must use HH:MM strings.")
        parsed = time.fromisoformat(value)
        if parsed.strftime("%H:%M") != value:
            raise ValueError("Schedule times must use HH:MM strings.")
        return parsed


campus_schedule = CampusScheduleEvaluator(settings.CAMPUS_SCHEDULE_PATH)
