import re
from datetime import datetime, timedelta, timezone


RELATIVE_PATTERN = re.compile(r"(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks)\s+ago", re.I)


def utc_now():
    return datetime.now(timezone.utc)


def ensure_datetime(value):
    if value is None or isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def parse_posted_at(value, scraped_at=None):
    if scraped_at is None:
        scraped_at = utc_now()

    if not value:
        return None, None

    posted_at_raw = str(value).strip()
    relative = RELATIVE_PATTERN.search(posted_at_raw)

    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()

        if unit.startswith("minute"):
            return scraped_at - timedelta(minutes=amount), posted_at_raw
        if unit.startswith("hour"):
            return scraped_at - timedelta(hours=amount), posted_at_raw
        if unit.startswith("day"):
            return scraped_at - timedelta(days=amount), posted_at_raw
        if unit.startswith("week"):
            return scraped_at - timedelta(days=amount * 7), posted_at_raw

    if re.search(r"today", posted_at_raw, re.I):
        return scraped_at, posted_at_raw

    if re.search(r"yesterday", posted_at_raw, re.I):
        return scraped_at - timedelta(days=1), posted_at_raw

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(posted_at_raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed, posted_at_raw
        except ValueError:
            pass

    return None, posted_at_raw


def is_within_hours(date_value, hours, now=None):
    date_value = ensure_datetime(date_value)
    if date_value is None:
        return True
    if now is None:
        now = utc_now()
    age = now - date_value
    return timedelta(0) <= age <= timedelta(hours=hours)


def hours_old(date_value, now=None):
    date_value = ensure_datetime(date_value)
    if date_value is None:
        return None
    if now is None:
        now = utc_now()
    return max(0, (now - date_value).total_seconds() / 3600)
