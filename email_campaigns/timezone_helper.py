"""Timezone resolution from company location strings.

Flow:
    location string
        → geopy Nominatim geocode  (lat, lon)
        → timezonefinder           (IANA timezone, offline, fast)
        → ZoneInfo                 (DST-aware Python tz object)
        → local send time → UTC

Fallback: any failure returns settings.outreach_default_timezone (no crash).
Results are LRU-cached in-process so the same location string is geocoded at
most once per process lifetime.  DB-level caching (resolved_timezone column)
prevents repeated geocoding across restarts.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import settings

logger = logging.getLogger(__name__)


def default_timezone_name() -> str:
    """Configured fallback IANA timezone for remote/unknown locations."""
    return settings.outreach_default_timezone


# ─────────────────────────────────────────────────────────────
# Geocoding + timezone lookup
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def _geocode_cached(location: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for a location string, or None on failure. LRU-cached."""
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError

    try:
        geolocator = Nominatim(user_agent="hirepilot-tz-resolver", timeout=5)
        result = geolocator.geocode(location)
        if result:
            return result.latitude, result.longitude
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        logger.warning("Geocoder error for %r: %s", location, exc)
    except Exception as exc:
        logger.warning("Unexpected geocoder error for %r: %s", location, exc)
    return None


@lru_cache(maxsize=512)
def _coords_to_tz(lat: float, lon: float) -> Optional[str]:
    """Return IANA timezone name for coordinates, or None. LRU-cached."""
    from timezonefinder import TimezoneFinder
    tf = TimezoneFinder()
    return tf.timezone_at(lat=lat, lng=lon)


def resolve_timezone(location: Optional[str]) -> str:
    """Resolve a free-text location to an IANA timezone string.

    Steps:
      1. Normalize / skip obvious non-locations (Remote, etc.)
      2. Geocode via Nominatim → (lat, lon)
      3. timezonefinder → IANA name
      4. Any failure → settings.outreach_default_timezone

    Results are LRU-cached in process; callers should also persist the result
    in the DB (resolved_timezone) to survive restarts.
    """
    default_tz = default_timezone_name()
    loc = (location or "").strip()

    if not loc or loc.lower() in ("remote", "worldwide (remote)", "worldwide", "any", ""):
        return default_tz

    logger.debug("Resolving timezone for location: %r", loc)

    coords = _geocode_cached(loc)
    if not coords:
        logger.warning("Unable to geocode %r — using default timezone: %s", loc, default_tz)
        return default_tz

    lat, lon = coords
    logger.debug("Geocoded %r → lat=%.4f lon=%.4f", loc, lat, lon)

    tz_name = _coords_to_tz(lat, lon)
    if not tz_name:
        logger.warning("No timezone found for coords (%.4f, %.4f) [%r] — using default: %s",
                       lat, lon, loc, default_tz)
        return default_tz

    logger.debug("Resolved timezone for %r → %s", loc, tz_name)
    return tz_name


# Keep old name as alias — outreach_automation.py imports resolve_timezone_name
resolve_timezone_name = resolve_timezone


def resolve_timezone_from_location(location: Optional[str]) -> ZoneInfo:
    """Resolve location to a Python ZoneInfo instance."""
    name = resolve_timezone(location)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(default_timezone_name())


# ─────────────────────────────────────────────────────────────
# Stage send-time helpers
# ─────────────────────────────────────────────────────────────

def parse_time_str(time_str: Optional[str], default_hour: int) -> Tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute)."""
    raw = (time_str or "").strip()
    if ":" in raw:
        parts = raw.split(":", 1)
        try:
            h, m = int(parts[0]), int(parts[1][:2])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except Exception:
            pass
    return default_hour, 0


def get_stage_fire_time(stage: int) -> Tuple[int, int]:
    """Return (hour, minute) target time for stage in company local time.

    Stage 1: 10:00 AM (default)
    Stage 2:  7:00 PM (default)
    Stage 3: 10:00 AM (default)
    """
    stg = int(stage or 1)
    if stg == 1:
        return parse_time_str(getattr(settings, "outreach_stage1_time", "10:00"), 10)
    elif stg == 2:
        return parse_time_str(getattr(settings, "outreach_stage2_time", "19:00"), 19)
    else:
        return parse_time_str(getattr(settings, "outreach_stage3_time", "10:00"), 10)


def calculate_stage_send_datetime(
    stage: int,
    location: Optional[str] = None,
    from_date: Optional[date] = None,
    delay_days: int = 0,
    resolved_tz: Optional[str] = None,
) -> str:
    """Return ISO string YYYY-MM-DDTHH:MM:SS (UTC-naive) for when to fire a stage.

    Uses the company's local timezone so "10:00 AM" means 10:00 AM *their* time.
    Pass resolved_tz to skip geocoding when you already have the IANA name cached.

    Note: no rounding — times are exact (e.g. 10:30, 19:15).
    """
    target_date = (from_date or date.today()) + timedelta(days=max(0, delay_days))
    hour, minute = get_stage_fire_time(stage)

    tz_name = resolved_tz or resolve_timezone(location)
    try:
        comp_tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = default_timezone_name()
        comp_tz = ZoneInfo(tz_name)

    # Build local time in company's timezone
    local_dt = datetime(target_date.year, target_date.month, target_date.day,
                        hour, minute, tzinfo=comp_tz)

    logger.debug(
        "Stage %d scheduled: %s local (%s) → UTC %s",
        stage,
        local_dt.strftime("%Y-%m-%d %H:%M"),
        tz_name,
        local_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M"),
    )

    # Convert to UTC naive (what the DB/scheduler expects)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S")


def calculate_next_stage1_send_datetime(
    location: Optional[str] = None,
    resolved_tz: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Next Stage 1 slot in company-local time → UTC-naive ISO.

    If today's Stage 1 local time has not passed, schedule today.
    If it has already passed (or is exactly now), schedule tomorrow.
    Returns (next_send_at_utc_naive, tz_name).
    """
    tz_name = resolved_tz or resolve_timezone(location)
    try:
        zi = ZoneInfo(tz_name)
    except Exception:
        tz_name = default_timezone_name()
        zi = ZoneInfo(tz_name)

    if now is None:
        now_local = datetime.now(zi)
    elif now.tzinfo is None:
        now_local = now.replace(tzinfo=timezone.utc).astimezone(zi)
    else:
        now_local = now.astimezone(zi)

    hour, minute = get_stage_fire_time(1)
    stage1_today = datetime(
        now_local.year, now_local.month, now_local.day, hour, minute, tzinfo=zi
    )
    target_date = now_local.date() if now_local < stage1_today else (now_local.date() + timedelta(days=1))

    return (
        calculate_stage_send_datetime(
            stage=1,
            location=location,
            from_date=target_date,
            delay_days=0,
            resolved_tz=tz_name,
        ),
        tz_name,
    )


# ─────────────────────────────────────────────────────────────
# UI helper
# ─────────────────────────────────────────────────────────────

def get_location_timezone_info(location: Optional[str]) -> Dict[str, str]:
    """Return timezone details for UI serialization."""
    tz_name = resolve_timezone(location)
    try:
        zi = ZoneInfo(tz_name)
    except Exception:
        tz_name = default_timezone_name()
        zi = ZoneInfo(tz_name)
    now_in_tz = datetime.now(zi)
    abbr = now_in_tz.strftime("%Z") or tz_name
    offset = now_in_tz.strftime("%z")
    if offset and len(offset) == 5:
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}"
    else:
        offset_fmt = "UTC"
    return {"tz_name": tz_name, "abbr": abbr, "offset": offset_fmt}


# ─────────────────────────────────────────────────────────────
# Self-check (run directly: venv/bin/python email_campaigns/timezone_helper.py)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Stage times unchanged
    assert get_stage_fire_time(1) == (10, 0)
    assert get_stage_fire_time(2) == (19, 0)
    assert get_stage_fire_time(3) == (10, 0)

    # Geocoding-based resolution
    assert resolve_timezone("San Francisco, CA") == "America/Los_Angeles", resolve_timezone("San Francisco, CA")
    assert resolve_timezone("London, UK") == "Europe/London", resolve_timezone("London, UK")
    assert resolve_timezone("Berlin, Germany") == "Europe/Berlin", resolve_timezone("Berlin, Germany")
    assert resolve_timezone("Bengaluru, India") == "Asia/Kolkata", resolve_timezone("Bengaluru, India")
    assert resolve_timezone("Manali, Himachal Pradesh") == "Asia/Kolkata", resolve_timezone("Manali, Himachal Pradesh")
    assert resolve_timezone("Remote") == default_timezone_name()
    assert resolve_timezone(None) == default_timezone_name()

    # Exact UTC conversion: 10:00 AM IST = 04:30 UTC
    utc_str = calculate_stage_send_datetime(1, resolved_tz="Asia/Kolkata", from_date=date(2026, 9, 23))
    assert utc_str.endswith("T04:30:00"), f"Expected 04:30:00 UTC, got {utc_str}"

    # Enable-auto: before Stage 1 → today; after → tomorrow (LA)
    before = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)  # 08:00 LA
    after = datetime(2026, 9, 23, 21, 0, tzinfo=timezone.utc)   # 14:00 LA
    today_utc, tz_a = calculate_next_stage1_send_datetime(resolved_tz="America/Los_Angeles", now=before)
    tomorrow_utc, tz_b = calculate_next_stage1_send_datetime(resolved_tz="America/Los_Angeles", now=after)
    assert tz_a == tz_b == "America/Los_Angeles"
    assert today_utc == "2026-09-23T17:00:00", today_utc  # 10:00 LA PDT = 17:00 UTC
    assert tomorrow_utc == "2026-09-24T17:00:00", tomorrow_utc

    s1 = calculate_stage_send_datetime(1, "San Francisco, CA", from_date=date(2026, 9, 23))
    s2 = calculate_stage_send_datetime(2, "San Francisco, CA", from_date=date(2026, 9, 23))
    s3 = calculate_stage_send_datetime(3, "San Francisco, CA", from_date=date(2026, 9, 23))
    print(f"SF Stage 1 (UTC): {s1}")
    print(f"SF Stage 2 (UTC): {s2}")
    print(f"SF Stage 3 (UTC): {s3}")
    print("timezone_helper self-check passed!")
