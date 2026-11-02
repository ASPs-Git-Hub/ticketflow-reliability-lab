"""Shared constants and helpers. Synthetic lab; fictional company 'Northwind Cloud'."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
PRIORITIES = ("urgent", "high", "normal", "low")
STATUSES = ("open", "waiting_on_customer", "resolved")
DEFAULT_TZ = "UTC"

# (first response minutes, resolution minutes), in BUSINESS minutes
DEFAULT_TARGETS = {
    "urgent": (60, 240),
    "high": (120, 480),
    "normal": (240, 960),
    "low": (480, 2400),
}
AT_RISK_RATIO = 0.8

# queue name -> (keywords, skills)
DEFAULT_QUEUES = {
    "billing": (("invoice", "refund", "charge", "billing", "payment"), ("billing",)),
    "technical": (("error", "api", "crash", "bug", "429", "timeout", "integration"), ("api",)),
    "accounts": (("password", "login", "2fa", "locked", "sign in", "access"), ("identity",)),
    "security": (("phishing", "breach", "suspicious", "hacked"), ("security",)),
}
FALLBACK_QUEUE = "general"
ESCALATION_COOLDOWN_MINUTES = 120


class MalformedEmail(ValueError):
    pass


def get_tz(name):
    """Return a ZoneInfo, falling back to UTC for unknown or non-string names."""
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return ZoneInfo(DEFAULT_TZ)


def parse_dt(value):
    """Aware datetime in UTC from a datetime, an ISO 8601 string or an RFC 2822 Date header.

    Naive input is taken as UTC (documented lab behaviour for inbound mail only; the public
    event APIs agent_reply and set_status reject naive datetimes)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                raise MalformedEmail("bad date: %r" % (value,))
    else:
        raise MalformedEmail("bad date type: %s" % type(value).__name__)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
