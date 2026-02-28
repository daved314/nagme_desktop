#!/usr/bin/env python3
"""Desktop companion for Nagme using Supabase event storage.

Features:
- Email/password Supabase sign-in
- Download nag events and rebuild current nag state
- Bucket filtering, smart sorting, recurring window options
- Color/progress visualization similar to mobile bars
- Add/edit/delete/push and recurring-complete actions
- Writes all changes back to Supabase as new event rows
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import platform
import tkinter as tk
import uuid
from io import BytesIO
from dataclasses import dataclass, field
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageTk

SUPABASE_URL = "https://gaehvakpfvcuzurbqkvv.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_lwqNwhtsIC73MVWFqL35XA_w72wCi1X"
TABLE_CANDIDATES = ("nag", "events")
VIEW_ONLY_MODE = True
AUTO_RELOAD_INTERVAL_MS = 60 * 60 * 1000
CREDENTIALS_FILE = os.path.join(os.path.expanduser("~"), ".nagme_desktop_credentials.json")
CORE_EVENT_SELECT_COLUMNS = "id,created_at,payload,user_id"
EXTENDED_EVENT_SELECT_COLUMNS = "id,created_at,payload,user_id,icon_png_base64,payload_version,client_synced_at,event_id"

ALL_BUCKET = "All"
PROJECT_BUCKET = "Project"
DEFAULT_PROJECT_NAME = "General"
DEFAULT_BUCKETS = ["Work", "Personal", "Weekend", "Holiday", PROJECT_BUCKET]

MONTHLY_VIEW_30_DAYS = 30
MONTHLY_VIEW_1_YEAR_DAYS = 365
PRE_DUE_COLOR_WINDOW_DAYS = 14

SORT_ENTERED = "Entered"
SORT_WEIGHT = "Weight"
SORT_DUE = "Due"
SORT_SMART = "Smart"
SORT_OPTIONS = [SORT_ENTERED, SORT_WEIGHT, SORT_DUE, SORT_SMART]

RECUR_NEXT_ONLY = "Next only"
RECUR_ALL_WINDOW = "All in window"

NAG_MODE_ONE_TIME = "ONE_TIME"
NAG_MODE_MONTHLY = "MONTHLY"

PATTERN_DAY_OF_MONTH = "DAY_OF_MONTH"
PATTERN_DAY_OF_WEEK = "DAY_OF_WEEK"
PATTERN_NTH_WEEKDAY = "NTH_WEEKDAY_OF_MONTH"
PATTERN_END_OF_MONTH = "END_OF_MONTH"
PATTERN_QUARTERLY = "QUARTERLY"
PATTERN_ANNUAL = "ANNUAL"
PATTERN_OPTIONS = [
    PATTERN_DAY_OF_MONTH,
    PATTERN_DAY_OF_WEEK,
    PATTERN_NTH_WEEKDAY,
    PATTERN_END_OF_MONTH,
    PATTERN_QUARTERLY,
    PATTERN_ANNUAL,
]

JAVA_MONDAY = 2
WEEKDAY_OPTIONS = [1, 2, 3, 4, 5, 6, 7]
NTH_WEEK_OPTIONS = [1, 2, 3, 4, 5]

COLOR_THEME = {
    "pre_due_base": "#FFF9C4",
    "pre_due_progress": "#FBC02D",
    "overdue_base": "#FFCDD2",
    "overdue_progress": "#FF0000",
    "far_future_base": "#FFFFFF",
    "far_future_progress": "#D5D5D5",
}

INVALID_ICON_TOKENS = {"", "none", "null", "non", "img", "undefined", "nan", "na", "n/a"}
SORT_ICON_MAP = {
    SORT_ENTERED: "🕒",
    SORT_WEIGHT: "⚖️",
    SORT_DUE: "⏰",
    SORT_SMART: "🧠",
}
VIEW_WINDOW_ICON_MAP = {
    "30 days": "📅",
    "1 year": "🗓️",
}
RECURRING_ICON_MAP = {
    RECUR_NEXT_ONLY: "➡️",
    RECUR_ALL_WINDOW: "🔁",
}


def now_ms() -> int:
    return int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1000)


def local_tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def ms_to_local(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone(local_tz())


def local_to_ms(value: dt.datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=local_tz())
    return int(value.timestamp() * 1000)


def parse_local_datetime(text: str) -> Optional[dt.datetime]:
    clean = text.strip()
    if not clean:
        return None
    formats = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%m/%d/%Y %H:%M"]
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(clean, fmt)
            return parsed.replace(tzinfo=local_tz())
        except ValueError:
            continue
    return None


def looks_like_icon_text(text: str) -> bool:
    # Favor emoji/symbol glyphs; suppress verbose placeholders.
    return any(ord(ch) > 127 for ch in text)


def normalize_icon_glyph(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        for key in ("glyph", "emoji", "icon", "text", "value", "label", "name"):
            if key in raw_value:
                value = normalize_icon_glyph(raw_value.get(key))
                if value:
                    return value
        return None
    if isinstance(raw_value, (list, tuple)):
        for item in raw_value:
            value = normalize_icon_glyph(item)
            if value:
                return value
        return None

    text = str(raw_value).strip()
    if not text:
        return None
    if text.lower() in INVALID_ICON_TOKENS:
        return None
    if text.lower().startswith("<img") or text.lower().startswith("http"):
        return None
    if text.lower().startswith("icon:"):
        text = text.split(":", 1)[1].strip()
        if not text:
            return None
    if not looks_like_icon_text(text):
        return None
    return text


def normalize_image_url(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        for key in ("url", "src", "imageUrl", "image", "iconUrl", "icon"):
            if key in raw_value:
                value = normalize_image_url(raw_value.get(key))
                if value:
                    return value
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    lower = text.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return text
    return None


def normalize_icon_png_base64(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        for key in ("icon_png_base64", "iconPngBase64", "base64", "data", "value"):
            if key in raw_value:
                value = normalize_icon_png_base64(raw_value.get(key))
                if value:
                    return value
        return None
    if isinstance(raw_value, (list, tuple)):
        for item in raw_value:
            value = normalize_icon_png_base64(item)
            if value:
                return value
        return None

    text = str(raw_value).strip()
    if not text:
        return None
    lower = text.lower()
    if lower.startswith("data:") and "base64," in lower:
        text = text.split(",", 1)[1].strip()
    text = "".join(text.split())
    if len(text) < 16:
        return None
    try:
        base64.b64decode(text, validate=True)
    except Exception:
        return None
    return text


def normalize_project_name(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    text = str(raw_value).replace("\n", " ").replace("\r", " ").strip()
    return text if text else None


def format_local_datetime(ms: int) -> str:
    return ms_to_local(ms).strftime("%Y-%m-%d %H:%M")


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return 255, 255, 255
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(start: Tuple[int, int, int], end: Tuple[int, int, int], amount: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, amount))
    return (
        int(round(start[0] + (end[0] - start[0]) * t)),
        int(round(start[1] + (end[1] - start[1]) * t)),
        int(round(start[2] + (end[2] - start[2]) * t)),
    )


def alpha_over_white(rgb: Tuple[int, int, int], alpha: float) -> Tuple[int, int, int]:
    a = max(0.0, min(1.0, alpha))
    return (
        int(round(255 * (1 - a) + rgb[0] * a)),
        int(round(255 * (1 - a) + rgb[1] * a)),
        int(round(255 * (1 - a) + rgb[2] * a)),
    )


def java_day_of_week(date_value: dt.date) -> int:
    # Python: Monday=0..Sunday=6, Java Calendar: Sunday=1..Saturday=7
    return ((date_value.weekday() + 1) % 7) + 1


def month_max_day(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.timedelta(days=1)).day


def nth_weekday_day_of_month(year: int, month: int, target_java_dow: int, nth_week: int) -> int:
    max_day = month_max_day(year, month)
    matches: List[int] = []
    for day in range(1, max_day + 1):
        if java_day_of_week(dt.date(year, month, day)) == target_java_dow:
            matches.append(day)
    if not matches:
        return 1
    if nth_week >= 5:
        return matches[-1]
    index = max(0, nth_week - 1)
    return matches[index] if index < len(matches) else matches[-1]


def format_duration_compact(duration_ms: int) -> str:
    millis = max(0, int(duration_ms))
    week = 7 * 24 * 60 * 60 * 1000
    day = 24 * 60 * 60 * 1000
    hour = 60 * 60 * 1000
    minute = 60 * 1000
    second = 1000
    if millis >= week:
        return f"{millis // week}w"
    if millis >= day:
        return f"{millis // day}d"
    if millis >= hour:
        return f"{millis // hour}h"
    if millis >= minute:
        return f"{millis // minute}m"
    if millis >= second:
        return f"{millis // second}s"
    return f"{millis}ms"


@dataclass
class DueWindow:
    start_ms: int
    due_ms: int
    source_due_ms: int


@dataclass
class NagLineVisual:
    base_color: Tuple[int, int, int]
    progress_color: Tuple[int, int, int]
    progress_fraction: float
    text_color: str
    time_label: str
    percent_label: str


@dataclass
class Nag:
    work_name: str
    nag_text: str
    bucket: str
    project_name: Optional[str]
    lateness_days: int
    mode: str
    repeat_minutes: int
    continue_minutes: Optional[int]
    notifications_enabled: bool
    weight: int
    one_time_epoch_ms: Optional[int]
    monthly_day: Optional[int]
    monthly_hour: Optional[int]
    monthly_minute: Optional[int]
    created_at_epoch_ms: int
    skipped_monthly_due_epoch_ms: List[int] = field(default_factory=list)
    icon_glyph: Optional[str] = None
    icon_png_base64: Optional[str] = None
    image_url: Optional[str] = None
    recurring_pattern_type: str = PATTERN_DAY_OF_MONTH
    recurring_day_of_week: Optional[int] = None
    recurring_nth_week: Optional[int] = None
    recurring_month_of_year: Optional[int] = None
    recurring_quarter_anchor_month: Optional[int] = None
    recurring_visible_days_before_due: Optional[int] = None
    pushed_offset_ms: int = 0
    push_count: int = 0
    pushed_total_ms: int = 0

    @staticmethod
    def from_payload(payload: Dict[str, Any]) -> Optional["Nag"]:
        try:
            work_name = str(payload.get("workName", "")).strip()
            nag_text = str(payload.get("nagText", "")).strip()
            if not work_name or not nag_text:
                return None

            def opt_int(name: str, default: Optional[int] = None) -> Optional[int]:
                raw = payload.get(name, default)
                if raw is None:
                    return None
                if raw == "":
                    return None
                return int(raw)

            skipped_raw = payload.get("skippedMonthlyDueEpochMillis", [])
            skipped: List[int] = []
            if isinstance(skipped_raw, list):
                for item in skipped_raw:
                    try:
                        skipped.append(int(item))
                    except Exception:
                        continue

            continue_minutes_raw = payload.get("continueMinutes")
            continue_minutes = None if continue_minutes_raw is None else int(continue_minutes_raw)

            icon_glyph = None
            for candidate_key in ("iconGlyph", "icon", "iconEmoji", "nagIcon", "iconText"):
                icon_glyph = normalize_icon_glyph(payload.get(candidate_key))
                if icon_glyph:
                    break
            image_url = None
            for candidate_key in ("imageUrl", "iconImageUrl", "nagImageUrl", "nagImage", "iconUrl", "image", "icon"):
                image_url = normalize_image_url(payload.get(candidate_key))
                if image_url:
                    break
            project_name = None
            for candidate_key in ("projectName", "project", "project_name"):
                project_name = normalize_project_name(payload.get(candidate_key))
                if project_name:
                    break
            icon_png_base64 = None
            for candidate_key in ("iconPngBase64", "icon_png_base64", "iconImageBase64", "nagIconBase64"):
                icon_png_base64 = normalize_icon_png_base64(payload.get(candidate_key))
                if icon_png_base64:
                    break
            recurring_visible_days_before_due = opt_int("recurringVisibleDaysBeforeDue")
            if recurring_visible_days_before_due is not None:
                recurring_visible_days_before_due = max(1, recurring_visible_days_before_due)
            bucket = str(payload.get("bucket", DEFAULT_BUCKETS[0])) or DEFAULT_BUCKETS[0]
            if bucket.lower() == PROJECT_BUCKET.lower():
                project_name = project_name or DEFAULT_PROJECT_NAME
            else:
                project_name = None

            return Nag(
                work_name=work_name,
                nag_text=nag_text,
                bucket=bucket,
                project_name=project_name,
                lateness_days=max(1, int(payload.get("latenessDays", 7))),
                mode=str(payload.get("mode", NAG_MODE_ONE_TIME)) or NAG_MODE_ONE_TIME,
                repeat_minutes=max(1, int(payload.get("repeatMinutes", 60))),
                continue_minutes=continue_minutes,
                notifications_enabled=bool(payload.get("notificationsEnabled", True)),
                weight=max(0, min(100, int(payload.get("weight", 50)))),
                one_time_epoch_ms=opt_int("oneTimeEpochMillis"),
                monthly_day=opt_int("monthlyDay"),
                monthly_hour=opt_int("monthlyHour"),
                monthly_minute=opt_int("monthlyMinute"),
                created_at_epoch_ms=int(payload.get("createdAtEpochMillis", now_ms())),
                skipped_monthly_due_epoch_ms=sorted(set(skipped)),
                icon_glyph=icon_glyph,
                icon_png_base64=icon_png_base64,
                image_url=image_url,
                recurring_pattern_type=str(payload.get("recurringPatternType", PATTERN_DAY_OF_MONTH)),
                recurring_day_of_week=opt_int("recurringDayOfWeek"),
                recurring_nth_week=opt_int("recurringNthWeek"),
                recurring_month_of_year=opt_int("recurringMonthOfYear"),
                recurring_quarter_anchor_month=opt_int("recurringQuarterAnchorMonth"),
                recurring_visible_days_before_due=recurring_visible_days_before_due,
                pushed_offset_ms=max(0, int(payload.get("pushedOffsetMillis", 0))),
                push_count=max(0, int(payload.get("pushCount", 0))),
                pushed_total_ms=max(0, int(payload.get("pushedTotalMillis", 0))),
            )
        except Exception:
            return None

    def to_payload(self, action: str) -> Dict[str, Any]:
        return {
            "action": action,
            "syncedAtEpochMillis": now_ms(),
            "workName": self.work_name,
            "nagText": self.nag_text,
            "bucket": self.bucket,
            "projectName": self.project_name,
            "latenessDays": self.lateness_days,
            "mode": self.mode,
            "repeatMinutes": self.repeat_minutes,
            "continueMinutes": self.continue_minutes,
            "notificationsEnabled": self.notifications_enabled,
            "weight": self.weight,
            "oneTimeEpochMillis": self.one_time_epoch_ms,
            "monthlyDay": self.monthly_day,
            "monthlyHour": self.monthly_hour,
            "monthlyMinute": self.monthly_minute,
            "createdAtEpochMillis": self.created_at_epoch_ms,
            "skippedMonthlyDueEpochMillis": self.skipped_monthly_due_epoch_ms,
            "iconGlyph": self.icon_glyph,
            "iconPngBase64": self.icon_png_base64,
            "imageUrl": self.image_url,
            "recurringPatternType": self.recurring_pattern_type,
            "recurringDayOfWeek": self.recurring_day_of_week,
            "recurringNthWeek": self.recurring_nth_week,
            "recurringMonthOfYear": self.recurring_month_of_year,
            "recurringQuarterAnchorMonth": self.recurring_quarter_anchor_month,
            "recurringVisibleDaysBeforeDue": self.recurring_visible_days_before_due,
            "pushedOffsetMillis": self.pushed_offset_ms,
            "pushCount": self.push_count,
            "pushedTotalMillis": self.pushed_total_ms,
        }

    def is_monthly_due_skipped(self, due_ms: int) -> bool:
        return due_ms in set(self.skipped_monthly_due_epoch_ms)


@dataclass
class NagListEntry:
    nag: Nag
    due_window: Optional[DueWindow]
    key: str

def apply_push_offset(nag: Nag, base_due_ms: int) -> int:
    return base_due_ms + max(0, nag.pushed_offset_ms)


def is_recurring_date_match(nag: Nag, day_value: dt.date) -> bool:
    if nag.mode != NAG_MODE_MONTHLY:
        return False

    year = day_value.year
    month = day_value.month
    day_of_month = day_value.day
    java_dow = java_day_of_week(day_value)
    max_day = month_max_day(year, month)
    pattern = nag.recurring_pattern_type or PATTERN_DAY_OF_MONTH

    if pattern == PATTERN_DAY_OF_MONTH:
        target_day = min(nag.monthly_day or day_of_month, max_day)
        return day_of_month == target_day

    if pattern == PATTERN_DAY_OF_WEEK:
        return java_dow == (nag.recurring_day_of_week or JAVA_MONDAY)

    if pattern == PATTERN_NTH_WEEKDAY:
        target_day = nth_weekday_day_of_month(
            year=year,
            month=month,
            target_java_dow=(nag.recurring_day_of_week or JAVA_MONDAY),
            nth_week=(nag.recurring_nth_week or 1),
        )
        return day_of_month == target_day

    if pattern == PATTERN_END_OF_MONTH:
        return day_of_month == max_day

    if pattern == PATTERN_QUARTERLY:
        created_month = ms_to_local(nag.created_at_epoch_ms).month
        anchor_month = max(1, min(12, nag.recurring_quarter_anchor_month or created_month))
        month_delta = ((month - anchor_month) % 12 + 12) % 12
        in_cycle = month_delta % 3 == 0
        target_day = min(nag.monthly_day or day_of_month, max_day)
        return in_cycle and day_of_month == target_day

    if pattern == PATTERN_ANNUAL:
        target_month = max(1, min(12, nag.recurring_month_of_year or month))
        target_day = min(nag.monthly_day or day_of_month, max_day)
        return month == target_month and day_of_month == target_day

    return False


def resolve_next_recurring_base_due_ms(nag: Nag, reference_ms: int) -> Optional[int]:
    if nag.mode != NAG_MODE_MONTHLY:
        return None
    if nag.monthly_hour is None or nag.monthly_minute is None:
        return None

    start = ms_to_local(reference_ms)
    probe_date = start.date()
    for day_offset in range(0, 366 * 6 + 1):
        current_date = probe_date + dt.timedelta(days=day_offset)
        if not is_recurring_date_match(nag, current_date):
            continue
        due_dt = dt.datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            nag.monthly_hour,
            nag.monthly_minute,
            tzinfo=local_tz(),
        )
        due_ms = local_to_ms(due_dt)
        if due_ms < reference_ms:
            continue
        if nag.is_monthly_due_skipped(due_ms):
            continue
        return due_ms
    return None


def resolve_previous_recurring_base_due_ms(nag: Nag, reference_ms: int) -> Optional[int]:
    if nag.mode != NAG_MODE_MONTHLY:
        return None
    if nag.monthly_hour is None or nag.monthly_minute is None:
        return None

    start = ms_to_local(reference_ms)
    probe_date = start.date()
    for day_offset in range(0, 366 * 6 + 1):
        current_date = probe_date - dt.timedelta(days=day_offset)
        if not is_recurring_date_match(nag, current_date):
            continue
        due_dt = dt.datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            nag.monthly_hour,
            nag.monthly_minute,
            tzinfo=local_tz(),
        )
        due_ms = local_to_ms(due_dt)
        if due_ms > reference_ms:
            continue
        return due_ms
    return None


def resolve_current_display_monthly_due_window(nag: Nag, reference_ms: int) -> Optional[DueWindow]:
    base_due = resolve_next_recurring_base_due_ms(nag, reference_ms)
    if base_due is None:
        return None
    previous_base = resolve_previous_recurring_base_due_ms(nag, base_due - 1) or nag.created_at_epoch_ms
    return DueWindow(
        start_ms=max(previous_base, nag.created_at_epoch_ms),
        due_ms=apply_push_offset(nag, base_due),
        source_due_ms=base_due,
    )


def resolve_monthly_due_windows_in_range(nag: Nag, range_start_ms: int, range_end_ms: int) -> List[DueWindow]:
    if range_end_ms < range_start_ms:
        return []
    windows: List[DueWindow] = []
    search_cursor = range_start_ms
    guard = 0
    while guard < 600:
        next_base_due = resolve_next_recurring_base_due_ms(nag, search_cursor)
        if next_base_due is None:
            break
        due_ms = apply_push_offset(nag, next_base_due)
        if due_ms > range_end_ms:
            break
        previous_base_due = resolve_previous_recurring_base_due_ms(nag, next_base_due - 1) or nag.created_at_epoch_ms
        windows.append(
            DueWindow(
                start_ms=max(previous_base_due, nag.created_at_epoch_ms),
                due_ms=due_ms,
                source_due_ms=next_base_due,
            )
        )
        search_cursor = next_base_due + 60_000
        guard += 1
    return windows


def resolve_due_window(nag: Nag, now_ms_value: int) -> Optional[DueWindow]:
    if nag.mode == NAG_MODE_ONE_TIME:
        if nag.one_time_epoch_ms is None:
            return None
        base_due = nag.one_time_epoch_ms
        return DueWindow(
            start_ms=min(nag.created_at_epoch_ms, base_due),
            due_ms=apply_push_offset(nag, base_due),
            source_due_ms=base_due,
        )
    return resolve_current_display_monthly_due_window(nag, now_ms_value)


def resolve_next_due_ms(nag: Nag, reference_ms: int) -> Optional[int]:
    if nag.mode == NAG_MODE_ONE_TIME:
        if nag.one_time_epoch_ms is None:
            return None
        return apply_push_offset(nag, nag.one_time_epoch_ms)
    base_due = resolve_next_recurring_base_due_ms(nag, reference_ms)
    if base_due is None:
        return None
    return apply_push_offset(nag, base_due)


def effective_project_name(nag: Nag) -> Optional[str]:
    if (nag.bucket or "").strip().lower() != PROJECT_BUCKET.lower():
        return None
    return normalize_project_name(nag.project_name) or DEFAULT_PROJECT_NAME


def should_show_recurring_due_window(nag: Nag, due_ms: int, now_ms_value: int) -> bool:
    if nag.mode != NAG_MODE_MONTHLY:
        return True
    visible_days = nag.recurring_visible_days_before_due
    if visible_days is None:
        return True
    threshold_ms = now_ms_value + max(1, int(visible_days)) * 24 * 60 * 60 * 1000
    return due_ms <= threshold_ms


def build_visible_entries(
    nags: List[Nag],
    now_ms_value: int,
    monthly_view_days: int,
    recurring_view_mode: str,
) -> List[NagListEntry]:
    horizon_days = MONTHLY_VIEW_1_YEAR_DAYS if monthly_view_days >= MONTHLY_VIEW_1_YEAR_DAYS else MONTHLY_VIEW_30_DAYS
    horizon_ms = horizon_days * 24 * 60 * 60 * 1000
    horizon_end_ms = now_ms_value + horizon_ms

    entries: List[NagListEntry] = []
    for nag in nags:
        if nag.mode == NAG_MODE_ONE_TIME:
            entries.append(NagListEntry(nag=nag, due_window=resolve_due_window(nag, now_ms_value), key=f"{nag.work_name}_single"))
            continue

        if recurring_view_mode == RECUR_ALL_WINDOW:
            windows = resolve_monthly_due_windows_in_range(nag, now_ms_value, horizon_end_ms)
            for window in windows:
                if not should_show_recurring_due_window(nag, window.due_ms, now_ms_value):
                    continue
                entries.append(NagListEntry(nag=nag, due_window=window, key=f"{nag.work_name}_{window.due_ms}"))
        else:
            display_window = resolve_current_display_monthly_due_window(nag, now_ms_value)
            next_upcoming = resolve_next_due_ms(nag, now_ms_value)
            if display_window is None or next_upcoming is None:
                continue
            if (next_upcoming - now_ms_value) > horizon_ms:
                continue
            if not should_show_recurring_due_window(nag, next_upcoming, now_ms_value):
                continue
            entries.append(NagListEntry(nag=nag, due_window=display_window, key=f"{nag.work_name}_{display_window.due_ms}"))
    return entries


def build_project_overview_entries(nags: List[Nag], now_ms_value: int) -> List[NagListEntry]:
    if not nags:
        return []

    grouped: Dict[str, List[Nag]] = {}
    for nag in nags:
        project = effective_project_name(nag) or DEFAULT_PROJECT_NAME
        grouped.setdefault(project, []).append(nag)

    entries: List[NagListEntry] = []
    for project_name, project_nags in grouped.items():
        representative: Optional[Tuple[Nag, Optional[DueWindow], int]] = None
        for nag in project_nags:
            due_window = resolve_due_window(nag, now_ms_value)
            due_ms = due_window.due_ms if due_window is not None else (resolve_next_due_ms(nag, now_ms_value) or 2**63 - 1)
            candidate = (nag, due_window, due_ms)
            if representative is None:
                representative = candidate
                continue
            rep_nag, _, rep_due_ms = representative
            rep_key = (0 if rep_due_ms <= now_ms_value else 1, rep_due_ms, -rep_nag.weight, rep_nag.created_at_epoch_ms)
            cand_key = (0 if due_ms <= now_ms_value else 1, due_ms, -nag.weight, nag.created_at_epoch_ms)
            if cand_key < rep_key:
                representative = candidate
        if representative is None:
            continue
        rep_nag, rep_window, _ = representative
        entries.append(
            NagListEntry(
                nag=rep_nag,
                due_window=None,
                key=f"project_overview_{project_name.lower()}",
            )
        )
    return entries


def smart_status_rank(due_ms: int, now_ms_value: int) -> int:
    if due_ms == 2**63 - 1:
        return 3
    if now_ms_value > due_ms:
        return 0
    if (due_ms - now_ms_value) <= PRE_DUE_COLOR_WINDOW_DAYS * 24 * 60 * 60 * 1000:
        return 1
    return 2


def sort_entries(entries: List[NagListEntry], sort_mode: str, now_ms_value: int) -> List[NagListEntry]:
    max_long = 2**63 - 1

    def entry_due_ms(entry: NagListEntry) -> int:
        if entry.due_window is not None:
            return entry.due_window.due_ms
        due = resolve_next_due_ms(entry.nag, now_ms_value)
        return due if due is not None else max_long

    if sort_mode == SORT_WEIGHT:
        return sorted(entries, key=lambda e: (-e.nag.weight, entry_due_ms(e), e.nag.created_at_epoch_ms))
    if sort_mode == SORT_DUE:
        return sorted(entries, key=lambda e: (entry_due_ms(e), -e.nag.weight, e.nag.created_at_epoch_ms))
    if sort_mode == SORT_SMART:
        return sorted(
            entries,
            key=lambda e: (
                smart_status_rank(entry_due_ms(e), now_ms_value),
                -e.nag.weight,
                entry_due_ms(e),
                e.nag.created_at_epoch_ms,
            ),
        )
    return sorted(entries, key=lambda e: (e.nag.created_at_epoch_ms, entry_due_ms(e)))


def overdue_window_ms(lateness_days: int) -> int:
    return max(1, lateness_days) * 24 * 60 * 60 * 1000


def progress_fraction(now_ms_value: int, start_ms: int, end_ms: int) -> float:
    if end_ms <= start_ms:
        return 1.0 if now_ms_value >= end_ms else 0.0
    ratio = (now_ms_value - start_ms) / (end_ms - start_ms)
    return max(0.0, min(1.0, ratio))


def due_status_label(now_ms_value: int, due_ms: int) -> str:
    if now_ms_value > due_ms:
        return f"+{format_duration_compact(now_ms_value - due_ms)}"
    return format_duration_compact(due_ms - now_ms_value)


def progress_percent_label(now_ms_value: int, start_ms: int, due_ms: int, lateness_days: int) -> str:
    if now_ms_value <= due_ms:
        denominator = max(1, due_ms - start_ms)
        elapsed = max(0, min(denominator, now_ms_value - start_ms))
        percent = int(round((elapsed / denominator) * 100.0))
    else:
        window = max(1, overdue_window_ms(lateness_days))
        past_due = max(0, now_ms_value - due_ms)
        percent = int(round((past_due / window) * 100.0))
    return f"{percent}%"


def recurring_indicator_label(nag: Nag) -> str:
    if nag.mode != NAG_MODE_MONTHLY:
        return ""
    pattern = nag.recurring_pattern_type or PATTERN_DAY_OF_MONTH
    if pattern == PATTERN_DAY_OF_MONTH:
        return "R:M"
    if pattern == PATTERN_DAY_OF_WEEK:
        return "R:W"
    if pattern == PATTERN_NTH_WEEKDAY:
        return "R:N"
    if pattern == PATTERN_END_OF_MONTH:
        return "R:EOM"
    if pattern == PATTERN_QUARTERLY:
        return "R:Q"
    if pattern == PATTERN_ANNUAL:
        return "R:Y"
    return "R"


def push_summary_label(nag: Nag) -> str:
    if nag.push_count <= 0 or nag.pushed_total_ms <= 0:
        return ""
    return f"P{nag.push_count}+{format_duration_compact(nag.pushed_total_ms)}"


def nag_line_visual(nag: Nag, now_ms_value: int, due_window_override: Optional[DueWindow]) -> NagLineVisual:
    due_window = due_window_override or resolve_due_window(nag, now_ms_value)
    if due_window is None:
        return NagLineVisual(
            base_color=hex_to_rgb("#ffffff"),
            progress_color=hex_to_rgb("#ededed"),
            progress_fraction=0.0,
            text_color="#000000",
            time_label="",
            percent_label="",
        )

    due_ms = due_window.due_ms
    time_label = due_status_label(now_ms_value, due_ms)
    percent_label = progress_percent_label(now_ms_value, due_window.start_ms, due_ms, nag.lateness_days)
    text_color = "#ffffff" if (now_ms_value > due_ms and nag.weight >= 100) else "#000000"

    pre_base = hex_to_rgb(COLOR_THEME["pre_due_base"])
    pre_progress = hex_to_rgb(COLOR_THEME["pre_due_progress"])
    overdue_base = hex_to_rgb(COLOR_THEME["overdue_base"])
    overdue_progress = hex_to_rgb(COLOR_THEME["overdue_progress"])
    far_base = hex_to_rgb(COLOR_THEME["far_future_base"])
    far_progress = hex_to_rgb(COLOR_THEME["far_future_progress"])

    if now_ms_value <= due_ms:
        pre_due_progress = progress_fraction(now_ms_value, due_window.start_ms, due_ms)
        millis_until_due = due_ms - now_ms_value
        pre_due_window_ms = PRE_DUE_COLOR_WINDOW_DAYS * 24 * 60 * 60 * 1000

        if millis_until_due > pre_due_window_ms:
            return NagLineVisual(
                base_color=far_base,
                progress_color=alpha_over_white(far_progress, 0.18),
                progress_fraction=pre_due_progress,
                text_color=text_color,
                time_label=time_label,
                percent_label=percent_label,
            )

        yellow_strength = max(0.0, min(1.0, nag.weight / 50.0))
        base_yellow = lerp_color((255, 255, 255), pre_base, yellow_strength)
        base_yellow = alpha_over_white(base_yellow, 0.18 + 0.60 * yellow_strength)
        progress_yellow = lerp_color(pre_base, pre_progress, yellow_strength)
        progress_yellow = alpha_over_white(progress_yellow, 0.30 + 0.55 * yellow_strength)
        return NagLineVisual(
            base_color=base_yellow,
            progress_color=progress_yellow,
            progress_fraction=pre_due_progress,
            text_color=text_color,
            time_label=time_label,
            percent_label=percent_label,
        )

    red_strength = max(0.0, min(1.0, (max(50, min(100, nag.weight)) - 50) / 50.0))
    base_red = lerp_color((255, 255, 255), overdue_base, red_strength)
    base_red = alpha_over_white(base_red, 0.28 + 0.45 * red_strength)
    progress_red = lerp_color(overdue_base, overdue_progress, red_strength)
    progress_red = alpha_over_white(progress_red, 0.60 + 0.35 * red_strength)
    post_progress = progress_fraction(now_ms_value, due_ms, due_ms + overdue_window_ms(nag.lateness_days))

    return NagLineVisual(
        base_color=base_red,
        progress_color=progress_red,
        progress_fraction=post_progress,
        text_color=text_color,
        time_label=time_label,
        percent_label=percent_label,
    )


class SupabaseSession:
    def __init__(self, supabase_url: str, supabase_key: str):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        self.access_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.table_name: Optional[str] = None
        self.table_row_counts: Dict[str, int] = {}
        self.table_fetch_errors: Dict[str, str] = {}

    @property
    def signed_in(self) -> bool:
        return bool(self.access_token and self.user_id)

    def sign_out(self) -> None:
        self.access_token = None
        self.user_id = None
        self.table_name = None
        self.table_row_counts = {}
        self.table_fetch_errors = {}

    def sign_in(self, email: str, password: str) -> str:
        endpoint = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        headers = {
            "apikey": self.supabase_key,
            "Content-Type": "application/json",
        }
        payload = {
            "email": email.strip(),
            "password": password,
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=25)
        if response.status_code >= 300:
            raise RuntimeError(self._extract_error(response))
        body = response.json()
        self.access_token = body.get("access_token")
        user = body.get("user") or {}
        self.user_id = user.get("id")
        self.table_name = None
        if not self.access_token or not self.user_id:
            raise RuntimeError("Sign-in returned no access token or user id.")
        self.detect_table()
        return self.user_id

    def change_password(self, new_password: str) -> None:
        endpoint = f"{self.supabase_url}/auth/v1/user"
        response = requests.put(
            endpoint,
            headers=self._auth_headers(include_json=True),
            json={"password": new_password},
            timeout=25,
        )
        if response.status_code >= 300:
            raise RuntimeError(self._extract_error(response))

    def _auth_headers(self, include_json: bool = True) -> Dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Not signed in.")
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.access_token}",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _extract_error(response: requests.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                if isinstance(body.get("error"), dict):
                    msg = body["error"].get("message")
                    if msg:
                        return f"HTTP {response.status_code}: {msg}"
                msg = body.get("message") or body.get("error_description") or body.get("error")
                if msg:
                    return f"HTTP {response.status_code}: {msg}"
        except Exception:
            pass
        text = (response.text or "").strip()
        if text:
            return f"HTTP {response.status_code}: {text[:300]}"
        return f"HTTP {response.status_code}: request failed"

    @staticmethod
    def _is_missing_optional_column_error(response: requests.Response) -> bool:
        text = (response.text or "").lower()
        references_optional = any(
            col in text for col in ("icon_png_base64", "payload_version", "client_synced_at", "event_id")
        )
        if not references_optional:
            return False
        return ("column" in text and "does not exist" in text) or ("schema cache" in text)

    def detect_table(self) -> str:
        if self.table_name:
            return self.table_name
        headers = self._auth_headers(include_json=False)
        last_error: Optional[str] = None
        for table in TABLE_CANDIDATES:
            url = f"{self.supabase_url}/rest/v1/{table}"
            params = {
                "select": "id",
                "limit": "1",
            }
            response = requests.get(url, headers=headers, params=params, timeout=20)
            if response.status_code in (200, 206):
                self.table_name = table
                return table

            body_text = (response.text or "").lower()
            if "does not exist" in body_text and "relation" in body_text:
                continue
            if response.status_code == 404:
                continue
            if response.status_code in (401, 403) or "permission denied" in body_text:
                self.table_name = table
                return table
            last_error = self._extract_error(response)

        raise RuntimeError(last_error or "Unable to find a usable table (nag/events).")

    def fetch_events(self) -> List[Dict[str, Any]]:
        if not self.user_id:
            raise RuntimeError("Not signed in.")
        self.detect_table()
        self.table_row_counts = {}
        self.table_fetch_errors = {}
        headers = self._auth_headers(include_json=False)
        merged_events: List[Dict[str, Any]] = []
        errors: List[str] = []
        table_order = [self.table_name] + [t for t in TABLE_CANDIDATES if t != self.table_name]

        for table in table_order:
            if not table:
                continue
            events: List[Dict[str, Any]] = []
            step = 1000
            offset = 0
            try:
                while True:
                    params = {
                        "select": EXTENDED_EVENT_SELECT_COLUMNS,
                        "order": "created_at.asc",
                        "limit": str(step),
                        "offset": str(offset),
                    }
                    response = requests.get(
                        f"{self.supabase_url}/rest/v1/{table}",
                        headers=headers,
                        params=params,
                        timeout=30,
                    )
                    if response.status_code >= 300 and self._is_missing_optional_column_error(response):
                        params["select"] = CORE_EVENT_SELECT_COLUMNS
                        response = requests.get(
                            f"{self.supabase_url}/rest/v1/{table}",
                            headers=headers,
                            params=params,
                            timeout=30,
                        )
                    if response.status_code >= 300:
                        raise RuntimeError(self._extract_error(response))
                    batch = response.json()
                    if not isinstance(batch, list):
                        raise RuntimeError("Unexpected response format while loading rows.")
                    events.extend(batch)
                    if len(batch) < step:
                        break
                    offset += step
                self.table_row_counts[table] = len(events)
                for event in events:
                    if isinstance(event, dict):
                        event["_source_table"] = table
                    merged_events.append(event)
            except Exception as exc:
                error_text = str(exc)
                normalized = error_text.lower()
                is_missing_fallback_table = (
                    table == "events"
                    and "http 404" in normalized
                    and ("could not find the table" in normalized or "public.events" in normalized)
                )
                if is_missing_fallback_table:
                    # Optional fallback table is absent in this project; skip quietly.
                    continue
                self.table_fetch_errors[table] = error_text
                errors.append(f"{table}: {error_text}")
                continue

        if not self.table_row_counts:
            detail = "; ".join(errors) if errors else "No accessible tables found."
            raise RuntimeError(f"Unable to load from nag/events. {detail}")

        self.table_name = max(self.table_row_counts.items(), key=lambda item: item[1])[0]

        dedup: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for event in merged_events:
            payload_value = event.get("payload")
            if isinstance(payload_value, (dict, list)):
                payload_key = json.dumps(payload_value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            else:
                payload_key = str(payload_value)
            dedup_key = (
                str(event.get("created_at", "")),
                str(event.get("user_id", "")),
                payload_key,
            )
            existing = dedup.get(dedup_key)
            if existing is None:
                dedup[dedup_key] = event
                continue

            incoming_has_icon = normalize_icon_png_base64(event.get("icon_png_base64")) is not None
            existing_has_icon = normalize_icon_png_base64(existing.get("icon_png_base64")) is not None
            if incoming_has_icon and not existing_has_icon:
                dedup[dedup_key] = event
                continue
            if incoming_has_icon == existing_has_icon:
                incoming_source = str(event.get("_source_table", ""))
                existing_source = str(existing.get("_source_table", ""))
                if incoming_source == self.table_name and existing_source != self.table_name:
                    dedup[dedup_key] = event

        rows = list(dedup.values())
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    def insert_event(self, payload: Dict[str, Any]) -> None:
        if not self.user_id:
            raise RuntimeError("Not signed in.")

        row = {
            "payload": payload,
            "user_id": self.user_id,
        }

        table_order = [self.detect_table()] + [t for t in TABLE_CANDIDATES if t != self.table_name]
        last_error: Optional[str] = None

        for table in table_order:
            response = requests.post(
                f"{self.supabase_url}/rest/v1/{table}",
                headers={**self._auth_headers(include_json=True), "Prefer": "return=minimal"},
                json=row,
                timeout=30,
            )
            if response.status_code < 300:
                self.table_name = table
                return

            body_text = (response.text or "").lower()
            last_error = self._extract_error(response)
            if "does not exist" in body_text and "relation" in body_text:
                continue

        raise RuntimeError(last_error or "Insert failed")

class NagDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, nag: Optional[Nag], buckets: List[str]):
        super().__init__(parent)
        self.title("Edit nag" if nag else "Add nag")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self.result: Optional[Nag] = None
        now_value = now_ms()
        base = nag or Nag(
            work_name=f"desktop-{uuid.uuid4().hex[:12]}",
            nag_text="",
            bucket=(buckets[0] if buckets else DEFAULT_BUCKETS[0]),
            project_name=None,
            lateness_days=7,
            mode=NAG_MODE_ONE_TIME,
            repeat_minutes=60,
            continue_minutes=24 * 60,
            notifications_enabled=True,
            weight=50,
            one_time_epoch_ms=now_value + 24 * 60 * 60 * 1000,
            monthly_day=1,
            monthly_hour=9,
            monthly_minute=0,
            created_at_epoch_ms=now_value,
            recurring_pattern_type=PATTERN_DAY_OF_MONTH,
            recurring_day_of_week=JAVA_MONDAY,
            recurring_nth_week=1,
            recurring_month_of_year=ms_to_local(now_value).month,
            recurring_quarter_anchor_month=ms_to_local(now_value).month,
            recurring_visible_days_before_due=None,
        )

        self._base = base
        pad = {"padx": 6, "pady": 4}

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        self.nag_text_var = tk.StringVar(value=base.nag_text)
        self.bucket_var = tk.StringVar(value=base.bucket)
        self.project_name_var = tk.StringVar(value=base.project_name or "")
        self.weight_var = tk.StringVar(value=str(base.weight))
        self.lateness_var = tk.StringVar(value=str(base.lateness_days))
        self.entered_var = tk.StringVar(value=format_local_datetime(base.created_at_epoch_ms))
        self.icon_var = tk.StringVar(value=base.icon_glyph or "")
        self.mode_var = tk.StringVar(value=base.mode)
        self.one_time_var = tk.StringVar(value=format_local_datetime(base.one_time_epoch_ms or now_value))
        self.monthly_day_var = tk.StringVar(value=str(base.monthly_day or 1))
        self.monthly_hour_var = tk.StringVar(value=str(base.monthly_hour or 9))
        self.monthly_minute_var = tk.StringVar(value=str(base.monthly_minute or 0))
        self.pattern_var = tk.StringVar(value=base.recurring_pattern_type or PATTERN_DAY_OF_MONTH)
        self.day_of_week_var = tk.StringVar(value=str(base.recurring_day_of_week or JAVA_MONDAY))
        self.nth_week_var = tk.StringVar(value=str(base.recurring_nth_week or 1))
        self.recurring_month_var = tk.StringVar(value=str(base.recurring_month_of_year or ms_to_local(now_value).month))
        self.quarter_anchor_var = tk.StringVar(value=str(base.recurring_quarter_anchor_month or ms_to_local(now_value).month))
        self.recurring_visible_days_var = tk.StringVar(
            value="" if base.recurring_visible_days_before_due is None else str(base.recurring_visible_days_before_due)
        )

        row = 0
        ttk.Label(container, text="Text").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.nag_text_var, width=48).grid(row=row, column=1, columnspan=3, sticky="ew", **pad)

        row += 1
        ttk.Label(container, text="Bucket").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(container, textvariable=self.bucket_var, values=buckets or DEFAULT_BUCKETS, width=18).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Icon").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.icon_var, width=8).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Project name").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.project_name_var, width=22).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Recur visible days").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.recurring_visible_days_var, width=8).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Weight 0-100").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.weight_var, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Lateness days").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.lateness_var, width=8).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Entered (YYYY-MM-DD HH:MM)").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.entered_var, width=22).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Mode").grid(row=row, column=2, sticky="w", **pad)
        ttk.Combobox(container, textvariable=self.mode_var, values=[NAG_MODE_ONE_TIME, NAG_MODE_MONTHLY], width=14).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="One-time due").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.one_time_var, width=22).grid(row=row, column=1, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Monthly day").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.monthly_day_var, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Hour").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.monthly_hour_var, width=5).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Minute").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.monthly_minute_var, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Pattern").grid(row=row, column=2, sticky="w", **pad)
        ttk.Combobox(container, textvariable=self.pattern_var, values=PATTERN_OPTIONS, width=22).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Day of week (1-7)").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.day_of_week_var, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Nth week (1-5)").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.nth_week_var, width=8).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        ttk.Label(container, text="Annual month (1-12)").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.recurring_month_var, width=8).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(container, text="Quarter anchor (1-12)").grid(row=row, column=2, sticky="w", **pad)
        ttk.Entry(container, textvariable=self.quarter_anchor_var, width=8).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        button_row = ttk.Frame(container)
        button_row.grid(row=row, column=0, columnspan=4, sticky="e", **pad)
        ttk.Button(button_row, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(button_row, text="Save", command=self._on_save).pack(side=tk.RIGHT)

        for c in range(4):
            container.grid_columnconfigure(c, weight=1)

        self.bind("<Return>", lambda _: self._on_save())
        self.bind("<Escape>", lambda _: self._on_cancel())

    def _to_int(self, value: str, field_name: str) -> int:
        try:
            return int(value.strip())
        except Exception:
            raise ValueError(f"{field_name} must be a whole number.")

    def _on_save(self) -> None:
        nag_text = self.nag_text_var.get().strip()
        if not nag_text:
            messagebox.showerror("Validation", "Nag text is required.", parent=self)
            return

        bucket = self.bucket_var.get().strip() or DEFAULT_BUCKETS[0]
        project_name = normalize_project_name(self.project_name_var.get())
        if bucket.lower() == PROJECT_BUCKET.lower():
            if not project_name:
                messagebox.showerror("Validation", "Project name is required for Project bucket nags.", parent=self)
                return
        else:
            project_name = None
        weight = self._to_int(self.weight_var.get(), "Weight")
        lateness_days = self._to_int(self.lateness_var.get(), "Lateness days")
        if weight < 0 or weight > 100:
            messagebox.showerror("Validation", "Weight must be 0..100.", parent=self)
            return
        if lateness_days < 1:
            messagebox.showerror("Validation", "Lateness days must be at least 1.", parent=self)
            return

        entered = parse_local_datetime(self.entered_var.get())
        if entered is None:
            messagebox.showerror("Validation", "Entered date must be YYYY-MM-DD HH:MM.", parent=self)
            return

        mode = self.mode_var.get().strip().upper()
        if mode not in (NAG_MODE_ONE_TIME, NAG_MODE_MONTHLY):
            messagebox.showerror("Validation", "Mode must be ONE_TIME or MONTHLY.", parent=self)
            return

        one_time_ms: Optional[int] = None
        monthly_day: Optional[int] = None
        monthly_hour: Optional[int] = None
        monthly_minute: Optional[int] = None

        if mode == NAG_MODE_ONE_TIME:
            one_time = parse_local_datetime(self.one_time_var.get())
            if one_time is None:
                messagebox.showerror("Validation", "One-time due must be YYYY-MM-DD HH:MM.", parent=self)
                return
            one_time_ms = local_to_ms(one_time)
        else:
            monthly_day = self._to_int(self.monthly_day_var.get(), "Monthly day")
            monthly_hour = self._to_int(self.monthly_hour_var.get(), "Monthly hour")
            monthly_minute = self._to_int(self.monthly_minute_var.get(), "Monthly minute")
            if monthly_day < 1 or monthly_day > 31:
                messagebox.showerror("Validation", "Monthly day must be 1..31.", parent=self)
                return
            if monthly_hour < 0 or monthly_hour > 23:
                messagebox.showerror("Validation", "Monthly hour must be 0..23.", parent=self)
                return
            if monthly_minute < 0 or monthly_minute > 59:
                messagebox.showerror("Validation", "Monthly minute must be 0..59.", parent=self)
                return

        pattern = self.pattern_var.get().strip().upper() or PATTERN_DAY_OF_MONTH
        if pattern not in PATTERN_OPTIONS:
            pattern = PATTERN_DAY_OF_MONTH

        recurring_day_of_week = self._to_int(self.day_of_week_var.get(), "Day of week")
        recurring_nth_week = self._to_int(self.nth_week_var.get(), "Nth week")
        recurring_month = self._to_int(self.recurring_month_var.get(), "Annual month")
        recurring_quarter_anchor = self._to_int(self.quarter_anchor_var.get(), "Quarter anchor")

        if recurring_day_of_week not in WEEKDAY_OPTIONS:
            messagebox.showerror("Validation", "Day of week must be 1..7 (Java Calendar style).", parent=self)
            return
        if recurring_nth_week not in NTH_WEEK_OPTIONS:
            messagebox.showerror("Validation", "Nth week must be 1..5.", parent=self)
            return
        if recurring_month < 1 or recurring_month > 12:
            messagebox.showerror("Validation", "Annual month must be 1..12.", parent=self)
            return
        if recurring_quarter_anchor < 1 or recurring_quarter_anchor > 12:
            messagebox.showerror("Validation", "Quarter anchor must be 1..12.", parent=self)
            return

        recurring_visible_days: Optional[int]
        recurring_visible_raw = self.recurring_visible_days_var.get().strip()
        if not recurring_visible_raw:
            recurring_visible_days = None
        else:
            recurring_visible_days = self._to_int(recurring_visible_raw, "Recur visible days")
            if recurring_visible_days < 1:
                messagebox.showerror("Validation", "Recur visible days must be at least 1.", parent=self)
                return
        if mode != NAG_MODE_MONTHLY:
            recurring_visible_days = None

        icon = self.icon_var.get().strip() or None

        self.result = Nag(
            work_name=self._base.work_name,
            nag_text=nag_text,
            bucket=bucket,
            project_name=project_name or (DEFAULT_PROJECT_NAME if bucket.lower() == PROJECT_BUCKET.lower() else None),
            lateness_days=lateness_days,
            mode=mode,
            repeat_minutes=max(1, self._base.repeat_minutes),
            continue_minutes=self._base.continue_minutes,
            notifications_enabled=self._base.notifications_enabled,
            weight=weight,
            one_time_epoch_ms=one_time_ms,
            monthly_day=monthly_day,
            monthly_hour=monthly_hour,
            monthly_minute=monthly_minute,
            created_at_epoch_ms=local_to_ms(entered),
            skipped_monthly_due_epoch_ms=list(self._base.skipped_monthly_due_epoch_ms),
            icon_glyph=icon,
            recurring_pattern_type=pattern,
            recurring_day_of_week=recurring_day_of_week,
            recurring_nth_week=recurring_nth_week,
            recurring_month_of_year=recurring_month,
            recurring_quarter_anchor_month=recurring_quarter_anchor,
            recurring_visible_days_before_due=recurring_visible_days,
            pushed_offset_ms=max(0, self._base.pushed_offset_ms),
            push_count=max(0, self._base.push_count),
            pushed_total_ms=max(0, self._base.pushed_total_ms),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

class NagDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Nagme Desktop")

        self._configure_platform_ui()

        self.session = SupabaseSession(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
        self.events: List[Dict[str, Any]] = []
        self.nags_by_work: Dict[str, Nag] = {}
        self.visible_entries: List[NagListEntry] = []
        self.row_bounds: List[Tuple[int, int, NagListEntry]] = []
        self.selected_key: Optional[str] = None
        self.write_buttons: List[ttk.Button] = []
        self.auto_reload_job: Optional[str] = None
        self.bucket_options: List[str] = [ALL_BUCKET] + DEFAULT_BUCKETS[:]
        self.bucket_buttons: Dict[str, tk.Button] = {}
        self.sort_buttons: Dict[str, tk.Button] = {}
        self.window_buttons: Dict[str, tk.Button] = {}
        self.recurring_buttons: Dict[str, tk.Button] = {}
        self.row_image_cache: Dict[str, ImageTk.PhotoImage] = {}
        self.row_image_failures: set[str] = set()
        self._touch_scroll_press_y = 0
        self._touch_scroll_press_x = 0
        self._touch_scroll_dragging = False
        self._touch_scroll_last_y = 0
        self._touch_scroll_threshold_px = 18
        self._touch_scroll_start_fraction = 0.0
        self._long_press_job: Optional[str] = None
        self._long_press_triggered = False
        self.last_parseable_payload_rows = 0
        self.last_valid_nag_rows = 0
        self.active_project_name: Optional[str] = None

        self.email_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.user_id_var = tk.StringVar(value="User ID: (not signed in)")
        self.status_var = tk.StringVar(
            value="View-only mode. Sign in to load nags from Supabase."
            if VIEW_ONLY_MODE
            else "Sign in to load nags from Supabase."
        )

        self.bucket_var = tk.StringVar(value=ALL_BUCKET)
        self.sort_var = tk.StringVar(value=SORT_SMART)
        self.view_days_var = tk.StringVar(value="30 days")
        self.recurring_mode_var = tk.StringVar(value=RECUR_NEXT_ONLY)
        self.project_mode_var = tk.StringVar(value="")

        self._build_ui()
        self._load_saved_credentials()
        self._update_auth_indicator()
        self._redraw_canvas()
        self._schedule_auto_reload()
        self.root.after(400, self.auto_sign_in_if_possible)

    def _configure_platform_ui(self) -> None:
        system = platform.system().lower()
        style = ttk.Style(self.root)
        if "windows" in system:
            self.root.geometry("1250x820")
            self.root.option_add("*Font", "{Segoe UI} 10")
            if "vista" in style.theme_names():
                style.theme_use("vista")
        else:
            self.root.geometry("1280x840")
            self.root.option_add("*Font", "{DejaVu Sans} 10")
            if "clam" in style.theme_names():
                style.theme_use("clam")

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        login = ttk.LabelFrame(main, text="Supabase Login")
        login.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(login, text="Email").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        ttk.Entry(login, textvariable=self.email_var, width=34).grid(row=0, column=1, padx=6, pady=4, sticky="w")
        ttk.Label(login, text="Password").grid(row=0, column=2, padx=6, pady=4, sticky="w")
        ttk.Entry(login, textvariable=self.password_var, width=24, show="*").grid(row=0, column=3, padx=6, pady=4, sticky="w")

        self.sign_in_button = ttk.Button(login, text="Sign in", command=self.sign_in)
        self.sign_in_button.grid(row=0, column=4, padx=6, pady=4)
        self.sign_out_button = ttk.Button(login, text="Sign out", command=self.sign_out)
        self.sign_out_button.grid(row=0, column=5, padx=6, pady=4)
        self.change_password_button = ttk.Button(login, text="Change password", command=self.change_password)
        self.change_password_button.grid(row=0, column=6, padx=6, pady=4)
        self.reload_button = ttk.Button(login, text="Reload", command=self.reload_from_supabase)
        self.reload_button.grid(row=0, column=7, padx=6, pady=4)
        self.reload_button.configure(text="Reload now")

        login_bg = self.root.cget("bg")
        self.auth_indicator_canvas = tk.Canvas(
            login,
            width=16,
            height=16,
            bg=login_bg,
            highlightthickness=0,
            borderwidth=0,
        )
        self.auth_indicator_canvas.grid(row=0, column=8, padx=(10, 4), pady=4, sticky="w")
        self.auth_indicator_circle = self.auth_indicator_canvas.create_oval(
            2, 2, 14, 14, fill="#d32f2f", outline="#6d0000"
        )
        ttk.Label(login, text="Supabase").grid(row=0, column=9, padx=(0, 6), pady=4, sticky="w")

        controls = ttk.LabelFrame(main, text="View")
        controls.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(controls, text="Bucket").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.bucket_selector_frame = ttk.Frame(controls)
        self.bucket_selector_frame.grid(row=0, column=1, padx=4, pady=2, sticky="w")

        ttk.Label(controls, text="Sort").grid(row=0, column=2, padx=6, pady=4, sticky="w")
        sort_selector_frame = ttk.Frame(controls)
        sort_selector_frame.grid(row=0, column=3, padx=4, pady=2, sticky="w")
        self._render_icon_button_group(
            container=sort_selector_frame,
            options=SORT_OPTIONS,
            selected_value=self.sort_var,
            icon_for=lambda value: SORT_ICON_MAP.get(value, "•"),
            button_store=self.sort_buttons,
            on_select=self.refresh_visible_entries,
        )

        ttk.Label(controls, text="Monthly window").grid(row=0, column=4, padx=6, pady=4, sticky="w")
        window_selector_frame = ttk.Frame(controls)
        window_selector_frame.grid(row=0, column=5, padx=4, pady=2, sticky="w")
        self._render_icon_button_group(
            container=window_selector_frame,
            options=["30 days", "1 year"],
            selected_value=self.view_days_var,
            icon_for=lambda value: VIEW_WINDOW_ICON_MAP.get(value, "•"),
            button_store=self.window_buttons,
            on_select=self.refresh_visible_entries,
        )

        ttk.Label(controls, text="Recurring").grid(row=0, column=6, padx=6, pady=4, sticky="w")
        recurring_selector_frame = ttk.Frame(controls)
        recurring_selector_frame.grid(row=0, column=7, padx=4, pady=2, sticky="w")
        self._render_icon_button_group(
            container=recurring_selector_frame,
            options=[RECUR_NEXT_ONLY, RECUR_ALL_WINDOW],
            selected_value=self.recurring_mode_var,
            icon_for=lambda value: RECURRING_ICON_MAP.get(value, "•"),
            button_store=self.recurring_buttons,
            on_select=self.refresh_visible_entries,
        )
        self.project_nav_frame = ttk.Frame(controls)
        self.project_nav_frame.grid(row=1, column=0, columnspan=8, padx=6, pady=(0, 4), sticky="w")
        self.project_nav_label = ttk.Label(self.project_nav_frame, textvariable=self.project_mode_var)
        self.project_nav_label.grid(row=0, column=0, padx=(0, 8), pady=2, sticky="w")
        self.project_back_button = ttk.Button(
            self.project_nav_frame,
            text="Projects",
            command=self._go_to_project_overview,
        )
        self.project_back_button.grid(row=0, column=1, padx=(0, 4), pady=2, sticky="w")
        self.project_exit_button = ttk.Button(
            self.project_nav_frame,
            text="Normal view",
            command=self._exit_project_mode,
        )
        self.project_exit_button.grid(row=0, column=2, padx=(0, 4), pady=2, sticky="w")
        self.write_buttons = []

        list_frame = ttk.Frame(main)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(list_frame, bg="#fafafa", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", lambda _: self._redraw_canvas())
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Double-1>", self.on_canvas_double_click)
        self.canvas.bind("<Button-3>", self.on_canvas_right_click)

        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_mousewheel_up_linux)
        self.canvas.bind("<Button-5>", self.on_mousewheel_down_linux)

        status = ttk.Label(main, textvariable=self.status_var, anchor="w")
        status.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(main, textvariable=self.user_id_var, anchor="w").pack(fill=tk.X)

        self.row_menu = tk.Menu(self.root, tearoff=0)
        self.row_menu.add_command(label="Enter project", command=self.enter_selected_project)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Edit", command=self.edit_selected)
        self.row_menu.add_command(label="Push due...", command=self.push_selected)
        self.row_menu.add_command(label="Complete recurring occurrence", command=self.complete_selected_occurrence)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Delete", command=self.delete_selected)

        self._apply_view_only_ui_state()
        self.update_bucket_options()
        self._update_project_navigation_ui()

    def _render_icon_button_group(
        self,
        container: tk.Misc,
        options: List[str],
        selected_value: tk.StringVar,
        icon_for: Any,
        button_store: Dict[str, tk.Button],
        on_select: Any,
    ) -> None:
        for child in container.winfo_children():
            child.destroy()
        button_store.clear()
        for col, option in enumerate(options):
            button = tk.Button(
                container,
                text=icon_for(option),
                font=("Segoe UI Emoji", 16),
                width=3,
                height=1,
                padx=2,
                pady=2,
                command=lambda value=option: self._set_filter_value(selected_value, value, on_select),
            )
            button.grid(row=0, column=col, padx=2, pady=2, sticky="w")
            button_store[option] = button
        self._update_icon_button_state(button_store, selected_value.get())

    def _set_filter_value(self, var: tk.StringVar, value: str, on_select: Any) -> None:
        var.set(value)
        self._update_icon_button_state(self.bucket_buttons, self.bucket_var.get())
        self._update_icon_button_state(self.sort_buttons, self.sort_var.get())
        self._update_icon_button_state(self.window_buttons, self.view_days_var.get())
        self._update_icon_button_state(self.recurring_buttons, self.recurring_mode_var.get())
        on_select()

    def _update_icon_button_state(self, button_map: Dict[str, tk.Button], selected_value: str) -> None:
        for value, button in button_map.items():
            if value == selected_value:
                button.configure(relief=tk.SUNKEN, bg="#d8ecff")
            else:
                button.configure(relief=tk.RAISED, bg="#f1f1f1")

    @staticmethod
    def _bucket_icon(bucket_name: str) -> str:
        text = bucket_name.strip().lower()
        if text == ALL_BUCKET.lower():
            return "🧺"
        if text == "work":
            return "💼"
        if text == "personal":
            return "👤"
        if text == "weekend":
            return "🌴"
        if text == "holiday":
            return "🎉"
        if text == PROJECT_BUCKET.lower():
            return "🗂️"
        return "🏷️"

    def _load_saved_credentials(self) -> None:
        try:
            if not os.path.exists(CREDENTIALS_FILE):
                return
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            email = str(data.get("email", "")).strip()
            password = str(data.get("password", ""))
            if email:
                self.email_var.set(email)
            if password:
                self.password_var.set(password)
        except Exception:
            # Credentials are optional; ignore local file parse issues.
            return

    def _save_credentials(self, email: str, password: str) -> None:
        data = {"email": email.strip(), "password": password}
        try:
            with open(CREDENTIALS_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
        except Exception:
            return

    def _clear_saved_credentials(self) -> None:
        try:
            if os.path.exists(CREDENTIALS_FILE):
                os.remove(CREDENTIALS_FILE)
        except Exception:
            return

    def auto_sign_in_if_possible(self) -> None:
        if self.session.signed_in:
            return
        if not self.email_var.get().strip() or not self.password_var.get():
            return
        self.sign_in(interactive=False)

    def _schedule_auto_reload(self) -> None:
        self.auto_reload_job = self.root.after(AUTO_RELOAD_INTERVAL_MS, self._auto_reload_tick)

    def _auto_reload_tick(self) -> None:
        try:
            if self.session.signed_in:
                self.reload_from_supabase(interactive=False, source_label="hourly auto-reload")
        finally:
            self._schedule_auto_reload()

    def on_mousewheel(self, event: tk.Event) -> str:
        delta = event.delta
        if delta > 0:
            self.canvas.yview_scroll(-1, "units")
        elif delta < 0:
            self.canvas.yview_scroll(1, "units")
        return "break"

    def on_mousewheel_up_linux(self, event: tk.Event) -> str:
        self.canvas.yview_scroll(-1, "units")
        return "break"

    def on_mousewheel_down_linux(self, event: tk.Event) -> str:
        self.canvas.yview_scroll(1, "units")
        return "break"

    def on_canvas_press(self, event: tk.Event) -> str:
        self._cancel_long_press()
        self._touch_scroll_press_x = event.x
        self._touch_scroll_press_y = event.y
        self._touch_scroll_last_y = event.y
        current_view = self.canvas.yview()
        self._touch_scroll_start_fraction = current_view[0] if current_view else 0.0
        self._touch_scroll_dragging = False
        self._long_press_triggered = False
        self._long_press_job = self.root.after(550, lambda: self._trigger_long_press(event.x, event.y))
        return "break"

    def on_canvas_drag(self, event: tk.Event) -> str:
        if self._long_press_triggered:
            return "break"

        # Touch scrolling should start only from vertical movement.
        if not self._touch_scroll_dragging:
            if abs(event.y - self._touch_scroll_press_y) >= self._touch_scroll_threshold_px:
                # Arm drag anchor on threshold crossing; avoid first-frame jump.
                self._touch_scroll_dragging = True
                self._touch_scroll_press_y = event.y
                current_view = self.canvas.yview()
                self._touch_scroll_start_fraction = current_view[0] if current_view else 0.0
            else:
                return "break"

        if self._touch_scroll_dragging:
            self._cancel_long_press()

        if self._touch_scroll_dragging:
            scroll_region = self.canvas.cget("scrollregion")
            if not scroll_region:
                return
            try:
                x0, y0, x1, y1 = [float(v) for v in str(scroll_region).split()]
            except Exception:
                return

            content_height = max(0.0, y1 - y0)
            viewport_height = float(max(1, self.canvas.winfo_height()))
            max_offset = max(0.0, content_height - viewport_height)
            if max_offset <= 0:
                return

            start_offset = self._touch_scroll_start_fraction * max_offset
            total_drag_dy = float(event.y - self._touch_scroll_press_y)
            target_offset = max(0.0, min(max_offset, start_offset - total_drag_dy))
            self.canvas.yview_moveto(target_offset / max_offset)
        return "break"

    def on_canvas_release(self, event: tk.Event) -> str:
        self._cancel_long_press()
        if self._long_press_triggered:
            self._long_press_triggered = False
            return "break"
        if self._touch_scroll_dragging:
            self._touch_scroll_dragging = False
            return "break"
        self.on_canvas_click(event)
        return "break"

    def _cancel_long_press(self) -> None:
        if self._long_press_job:
            self.root.after_cancel(self._long_press_job)
            self._long_press_job = None

    def _trigger_long_press(self, x: int, y: int) -> None:
        self._long_press_job = None
        if self._touch_scroll_dragging:
            return
        pointer_x = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        pointer_y = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        if (
            abs(pointer_x - self._touch_scroll_press_x) > self._touch_scroll_threshold_px
            or abs(pointer_y - self._touch_scroll_press_y) > self._touch_scroll_threshold_px
        ):
            return
        self._long_press_triggered = True
        entry = self._find_entry_by_y(pointer_y)
        if not entry:
            return
        self.selected_key = entry.key
        can_enter_project = self._is_project_overview_mode() and bool(effective_project_name(entry.nag))
        self.row_menu.entryconfigure("Enter project", state="normal" if can_enter_project else "disabled")
        self._redraw_canvas()
        x_root = self.canvas.winfo_rootx() + pointer_x
        y_root = self.canvas.winfo_rooty() + pointer_y
        try:
            self.row_menu.tk_popup(x_root, y_root)
        finally:
            self.row_menu.grab_release()

    def _apply_view_only_ui_state(self) -> None:
        if not VIEW_ONLY_MODE:
            return
        for button in self.write_buttons:
            button.state(["disabled"])
        self.row_menu.entryconfigure("Edit", state="disabled")
        self.row_menu.entryconfigure("Push due...", state="disabled")
        self.row_menu.entryconfigure("Complete recurring occurrence", state="disabled")
        self.row_menu.entryconfigure("Delete", state="disabled")

    def _reject_write_when_view_only(self) -> bool:
        if not VIEW_ONLY_MODE:
            return False
        self.set_status("View-only mode is enabled. Write actions are disabled.")
        return True

    def _update_auth_indicator(self) -> None:
        if not hasattr(self, "auth_indicator_canvas"):
            return
        signed_in = self.session.signed_in
        fill = "#2e7d32" if signed_in else "#c62828"
        outline = "#1b5e20" if signed_in else "#8e0000"
        self.auth_indicator_canvas.itemconfigure(
            self.auth_indicator_circle,
            fill=fill,
            outline=outline,
        )

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def change_password(self) -> None:
        if not self.session.signed_in:
            messagebox.showwarning("Sign in required", "Sign in first.", parent=self.root)
            return
        new_password = simpledialog.askstring(
            "Change password",
            "Enter new password:",
            parent=self.root,
            show="*",
        )
        if new_password is None:
            return
        new_password = new_password.strip()
        if len(new_password) < 8:
            messagebox.showerror("Validation", "Password must be at least 8 characters.", parent=self.root)
            return
        confirm_password = simpledialog.askstring(
            "Change password",
            "Confirm new password:",
            parent=self.root,
            show="*",
        )
        if confirm_password is None:
            return
        if new_password != confirm_password.strip():
            messagebox.showerror("Validation", "Passwords do not match.", parent=self.root)
            return
        try:
            self.session.change_password(new_password)
            email = self.email_var.get().strip()
            if email:
                self._save_credentials(email, new_password)
            self.password_var.set(new_password)
            self.set_status("Password changed successfully.")
            messagebox.showinfo("Password updated", "Your password has been changed.", parent=self.root)
        except Exception as exc:
            self.set_status(f"Password change failed: {exc}")
            messagebox.showerror("Password change failed", str(exc), parent=self.root)

    def sign_in(self, interactive: bool = True) -> None:
        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not email or not password:
            if interactive:
                messagebox.showwarning("Missing", "Enter email and password first.", parent=self.root)
            return
        try:
            user_id = self.session.sign_in(email, password)
            self._save_credentials(email, password)
            self._update_auth_indicator()
            self.user_id_var.set(f"User ID: {user_id}")
            self.set_status(f"Signed in as {user_id}. Loading nags...")
            self.reload_from_supabase(interactive=interactive, source_label="sign-in")
        except Exception as exc:
            self._update_auth_indicator()
            self.user_id_var.set("User ID: (sign-in failed)")
            self.set_status(f"Sign-in failed: {exc}")
            if interactive:
                messagebox.showerror("Supabase sign-in failed", str(exc), parent=self.root)

    def sign_out(self) -> None:
        self.session.sign_out()
        self._clear_saved_credentials()
        self.events = []
        self.nags_by_work = {}
        self.visible_entries = []
        self.selected_key = None
        self.active_project_name = None
        self.bucket_var.set(ALL_BUCKET)
        self.last_parseable_payload_rows = 0
        self.last_valid_nag_rows = 0
        self.update_bucket_options()
        self._redraw_canvas()
        self._update_auth_indicator()
        self.user_id_var.set("User ID: (not signed in)")
        self.set_status("Signed out.")

    def _parse_payload(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if "payload" in value and isinstance(value.get("payload"), (dict, str)):
                nested = self._parse_payload(value.get("payload"))
                if nested:
                    return nested
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return None

    def _rebuild_current_nags_from_events(self) -> None:
        current: Dict[str, Nag] = {}
        parseable_count = 0
        valid_nag_count = 0
        sorted_events = sorted(self.events, key=lambda e: str(e.get("created_at", "")))
        for row in sorted_events:
            payload = self._parse_payload(row.get("payload"))
            if not payload:
                continue
            parseable_count += 1
            nag = Nag.from_payload(payload)
            if not nag:
                continue
            row_icon_base64 = normalize_icon_png_base64(row.get("icon_png_base64"))
            if row_icon_base64:
                nag.icon_png_base64 = row_icon_base64
            valid_nag_count += 1
            action = str(payload.get("action", "")).strip().lower()
            if action == "delete":
                current.pop(nag.work_name, None)
            else:
                current[nag.work_name] = nag
        self.last_parseable_payload_rows = parseable_count
        self.last_valid_nag_rows = valid_nag_count
        self.nags_by_work = current

    def reload_from_supabase(self, interactive: bool = True, source_label: str = "manual reload") -> None:
        if not self.session.signed_in:
            if interactive:
                messagebox.showinfo("Sign in required", "Sign in first.", parent=self.root)
            return
        try:
            self.events = self.session.fetch_events()
            self._rebuild_current_nags_from_events()
            self.selected_key = None
            self.update_bucket_options()
            self.refresh_visible_entries()
            table_counts = ", ".join(
                f"{table}:{count}"
                for table, count in self.session.table_row_counts.items()
            )
            if not table_counts:
                table_counts = "no rows"
            table_errors = ", ".join(
                f"{table}:{err}"
                for table, err in self.session.table_fetch_errors.items()
            )
            errors_suffix = f"; errors {table_errors}" if table_errors else ""
            no_rows_suffix = ""
            if len(self.events) == 0:
                no_rows_suffix = " No readable rows were returned for this signed-in user."
            self.set_status(
                f"{source_label}: loaded {len(self.events)} merged row(s), active nags: {len(self.nags_by_work)} "
                f"(payload rows {self.last_parseable_payload_rows}, valid nag rows {self.last_valid_nag_rows}; "
                f"tables {table_counts}; active table: {self.session.table_name}; "
                f"user {self.session.user_id}{errors_suffix}).{no_rows_suffix}"
            )
        except Exception as exc:
            self.set_status(f"Load failed: {exc}")
            if interactive:
                messagebox.showerror("Supabase load failed", str(exc), parent=self.root)

    def update_bucket_options(self) -> None:
        buckets = sorted({nag.bucket for nag in self.nags_by_work.values()}, key=lambda s: s.lower())
        merged = [ALL_BUCKET] + DEFAULT_BUCKETS + [b for b in buckets if b not in DEFAULT_BUCKETS]
        seen: set[str] = set()
        final: List[str] = []
        for bucket in merged:
            if bucket not in seen:
                seen.add(bucket)
                final.append(bucket)
        self.bucket_options = final
        if self.bucket_var.get() not in final:
            self.bucket_var.set(ALL_BUCKET)
        self._render_icon_button_group(
            container=self.bucket_selector_frame,
            options=final,
            selected_value=self.bucket_var,
            icon_for=self._bucket_icon,
            button_store=self.bucket_buttons,
            on_select=self._on_bucket_selected,
        )
        self._update_project_navigation_ui()

    def _on_bucket_selected(self) -> None:
        selected_bucket = (self.bucket_var.get() or ALL_BUCKET).strip()
        if selected_bucket.lower() != PROJECT_BUCKET.lower():
            self.active_project_name = None
        self.refresh_visible_entries()

    def refresh_visible_entries(self) -> None:
        now_value = now_ms()
        selected_bucket = self.bucket_var.get() or ALL_BUCKET
        monthly_days = MONTHLY_VIEW_1_YEAR_DAYS if self.view_days_var.get() == "1 year" else MONTHLY_VIEW_30_DAYS

        if selected_bucket.lower() != PROJECT_BUCKET.lower():
            self.active_project_name = None
        nags = list(self.nags_by_work.values())
        project_overview_mode = False
        if selected_bucket == ALL_BUCKET:
            nags = [n for n in nags if (n.bucket or "").strip().lower() != PROJECT_BUCKET.lower()]
        else:
            if selected_bucket.lower() == PROJECT_BUCKET.lower():
                nags = [n for n in nags if (n.bucket or "").strip().lower() == PROJECT_BUCKET.lower()]
                active_project = normalize_project_name(self.active_project_name)
                if active_project:
                    nags = [
                        n for n in nags
                        if (effective_project_name(n) or DEFAULT_PROJECT_NAME).lower() == active_project.lower()
                    ]
                else:
                    project_overview_mode = True
            else:
                nags = [n for n in nags if n.bucket == selected_bucket]

        if project_overview_mode:
            entries = build_project_overview_entries(nags=nags, now_ms_value=now_value)
        else:
            entries = build_visible_entries(
                nags=nags,
                now_ms_value=now_value,
                monthly_view_days=monthly_days,
                recurring_view_mode=self.recurring_mode_var.get() or RECUR_NEXT_ONLY,
            )
        self.visible_entries = sort_entries(entries, self.sort_var.get() or SORT_SMART, now_value)
        self._update_project_navigation_ui()
        self._redraw_canvas()

    def _is_project_overview_mode(self) -> bool:
        selected_bucket = (self.bucket_var.get() or ALL_BUCKET).strip().lower()
        return selected_bucket == PROJECT_BUCKET.lower() and normalize_project_name(self.active_project_name) is None

    def _update_project_navigation_ui(self) -> None:
        selected_bucket = (self.bucket_var.get() or ALL_BUCKET).strip().lower()
        if selected_bucket != PROJECT_BUCKET.lower():
            self.project_mode_var.set("")
            self.project_nav_frame.grid_remove()
            return

        self.project_nav_frame.grid()
        active_project = normalize_project_name(self.active_project_name)
        if active_project:
            self.project_mode_var.set(f"Project: {active_project}")
            self.project_back_button.grid()
        else:
            self.project_mode_var.set("Project list: tap a row to enter")
            self.project_back_button.grid_remove()
        self.project_exit_button.grid()

    def _go_to_project_overview(self) -> None:
        self.active_project_name = None
        self.refresh_visible_entries()

    def _exit_project_mode(self) -> None:
        self.active_project_name = None
        self.bucket_var.set(ALL_BUCKET)
        self.refresh_visible_entries()

    def _try_enter_project_from_entry(self, entry: Optional[NagListEntry]) -> bool:
        if not entry or not self._is_project_overview_mode():
            return False
        project_name = effective_project_name(entry.nag)
        if not project_name:
            return False
        if not messagebox.askyesno("Enter project", f"Open project \"{project_name}\"?", parent=self.root):
            return False
        self.active_project_name = project_name
        self.refresh_visible_entries()
        return True

    def _find_entry_by_y(self, y: int) -> Optional[NagListEntry]:
        canvas_y = int(self.canvas.canvasy(y))
        for y0, y1, entry in self.row_bounds:
            if y0 <= canvas_y <= y1:
                return entry
        return None

    def on_canvas_click(self, event: tk.Event) -> None:
        entry = self._find_entry_by_y(event.y)
        if self._try_enter_project_from_entry(entry):
            return
        self.selected_key = entry.key if entry else None
        self._redraw_canvas()

    def on_canvas_double_click(self, event: tk.Event) -> None:
        entry = self._find_entry_by_y(event.y)
        if self._try_enter_project_from_entry(entry):
            return
        if entry:
            self.selected_key = entry.key
            self.edit_selected()

    def on_canvas_right_click(self, event: tk.Event) -> None:
        entry = self._find_entry_by_y(event.y)
        if entry:
            self.selected_key = entry.key
            can_enter_project = self._is_project_overview_mode() and bool(effective_project_name(entry.nag))
            self.row_menu.entryconfigure("Enter project", state="normal" if can_enter_project else "disabled")
            self._redraw_canvas()
            try:
                self.row_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.row_menu.grab_release()

    def _selected_entry(self) -> Optional[NagListEntry]:
        if not self.selected_key:
            return None
        for entry in self.visible_entries:
            if entry.key == self.selected_key:
                return entry
        return None

    def enter_selected_project(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            messagebox.showinfo("Select", "Select a project row first.", parent=self.root)
            return
        if not self._try_enter_project_from_entry(entry):
            messagebox.showinfo(
                "Project view",
                "This option is available in Project bucket overview rows.",
                parent=self.root,
            )

    def _insert_event(self, action: str, nag: Nag) -> bool:
        if self._reject_write_when_view_only():
            return False
        if not self.session.signed_in:
            messagebox.showwarning("Sign in required", "Sign in first.", parent=self.root)
            return False
        try:
            self.session.insert_event(nag.to_payload(action))
            self.set_status(f"Synced action '{action}' for {nag.work_name}.")
            return True
        except Exception as exc:
            self.set_status(f"Sync failed: {exc}")
            messagebox.showerror("Supabase sync failed", str(exc), parent=self.root)
            return False

    def sync_all(self) -> None:
        if self._reject_write_when_view_only():
            return
        if not self.session.signed_in:
            messagebox.showwarning("Sign in required", "Sign in first.", parent=self.root)
            return
        count = 0
        for nag in list(self.nags_by_work.values()):
            if self._insert_event("manual_sync", nag):
                count += 1
        self.set_status(f"Synced {count} nag(s).")

    def add_nag(self) -> None:
        if self._reject_write_when_view_only():
            return
        buckets = [b for b in self.bucket_options if b != ALL_BUCKET]
        dialog = NagDialog(self.root, None, buckets)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        nag = dialog.result
        if self._insert_event("create", nag):
            self.nags_by_work[nag.work_name] = nag
            self.update_bucket_options()
            self.refresh_visible_entries()

    def edit_selected(self) -> None:
        if self._reject_write_when_view_only():
            return
        entry = self._selected_entry()
        if not entry:
            messagebox.showinfo("Select", "Select a nag first.", parent=self.root)
            return

        nag = self.nags_by_work.get(entry.nag.work_name)
        if not nag:
            return

        buckets = [b for b in self.bucket_options if b != ALL_BUCKET]
        dialog = NagDialog(self.root, nag, buckets)
        self.root.wait_window(dialog)
        if not dialog.result:
            return

        updated = dialog.result
        if self._insert_event("update", updated):
            self.nags_by_work[updated.work_name] = updated
            self.update_bucket_options()
            self.refresh_visible_entries()

    def delete_selected(self) -> None:
        if self._reject_write_when_view_only():
            return
        entry = self._selected_entry()
        if not entry:
            messagebox.showinfo("Select", "Select a nag first.", parent=self.root)
            return

        nag = self.nags_by_work.get(entry.nag.work_name)
        if not nag:
            return

        if not messagebox.askyesno("Delete nag", f"Delete this nag?\n\n{nag.nag_text}", parent=self.root):
            return

        if self._insert_event("delete", nag):
            self.nags_by_work.pop(nag.work_name, None)
            self.selected_key = None
            self.update_bucket_options()
            self.refresh_visible_entries()

    def push_selected(self) -> None:
        if self._reject_write_when_view_only():
            return
        entry = self._selected_entry()
        if not entry:
            messagebox.showinfo("Select", "Select a nag first.", parent=self.root)
            return
        nag = self.nags_by_work.get(entry.nag.work_name)
        if not nag:
            return

        value = simpledialog.askstring(
            "Push due",
            "Enter push duration. Examples: 7d, 12h, 90m, 1y",
            parent=self.root,
        )
        if value is None:
            return

        push_ms = self._parse_duration_to_ms(value)
        if push_ms is None or push_ms <= 0:
            messagebox.showerror("Invalid", "Could not parse duration.", parent=self.root)
            return

        updated = Nag(**{**nag.__dict__})
        updated.pushed_offset_ms = max(0, updated.pushed_offset_ms + push_ms)
        updated.push_count = max(0, updated.push_count + 1)
        updated.pushed_total_ms = max(0, updated.pushed_total_ms + push_ms)

        if self._insert_event("push_due", updated):
            self.nags_by_work[updated.work_name] = updated
            self.refresh_visible_entries()

    def complete_selected_occurrence(self) -> None:
        if self._reject_write_when_view_only():
            return
        entry = self._selected_entry()
        if not entry:
            messagebox.showinfo("Select", "Select a recurring nag row first.", parent=self.root)
            return

        nag = self.nags_by_work.get(entry.nag.work_name)
        if not nag or nag.mode != NAG_MODE_MONTHLY or not entry.due_window:
            messagebox.showinfo("Not recurring", "This action is for recurring nags.", parent=self.root)
            return

        source_due = entry.due_window.source_due_ms
        if source_due in nag.skipped_monthly_due_epoch_ms:
            messagebox.showinfo("Already completed", "That occurrence is already completed.", parent=self.root)
            return

        updated = Nag(**{**nag.__dict__})
        updated.skipped_monthly_due_epoch_ms = sorted(set(updated.skipped_monthly_due_epoch_ms + [source_due]))[-200:]

        if self._insert_event("complete_occurrence", updated):
            self.nags_by_work[updated.work_name] = updated
            self.refresh_visible_entries()

    def _parse_duration_to_ms(self, text: str) -> Optional[int]:
        clean = text.strip().lower()
        if not clean:
            return None

        unit_map = {
            "ms": 1,
            "s": 1000,
            "m": 60 * 1000,
            "h": 60 * 60 * 1000,
            "d": 24 * 60 * 60 * 1000,
            "w": 7 * 24 * 60 * 60 * 1000,
            "y": 365 * 24 * 60 * 60 * 1000,
        }

        for unit in ("ms", "s", "m", "h", "d", "w", "y"):
            if clean.endswith(unit):
                number_part = clean[: -len(unit)].strip()
                try:
                    amount = float(number_part)
                except Exception:
                    return None
                return int(amount * unit_map[unit])

        try:
            days = float(clean)
            return int(days * unit_map["d"])
        except Exception:
            return None

    def _redraw_canvas(self, preserve_scroll: bool = True) -> None:
        previous_view = self.canvas.yview() if preserve_scroll else (0.0, 1.0)
        previous_start = previous_view[0] if previous_view else 0.0
        self.canvas.delete("all")
        width = max(900, self.canvas.winfo_width())
        row_height = 64
        x0 = 8
        x1 = width - 12
        selected_bucket = self.bucket_var.get() or ALL_BUCKET
        project_overview_mode = self._is_project_overview_mode()
        project_counts: Dict[str, int] = {}
        if project_overview_mode:
            for candidate in self.nags_by_work.values():
                project_name = effective_project_name(candidate)
                if not project_name:
                    continue
                project_counts[project_name] = project_counts.get(project_name, 0) + 1

        self.row_bounds = []
        now_value = now_ms()

        if not self.visible_entries:
            empty_message = "No nags to show."
            if selected_bucket.lower() == PROJECT_BUCKET.lower() and project_overview_mode:
                empty_message = "No projects in Project bucket yet."
            self.canvas.create_text(
                20,
                24,
                anchor="w",
                text=empty_message,
                fill="#444444",
                font=("TkDefaultFont", 11),
            )
            self.canvas.configure(scrollregion=(0, 0, width, 120))
            if preserve_scroll:
                self.canvas.yview_moveto(max(0.0, min(1.0, previous_start)))
            return

        for index, entry in enumerate(self.visible_entries):
            y0 = 6 + index * row_height
            y1 = y0 + row_height - 8
            nag = entry.nag
            if project_overview_mode:
                visual = NagLineVisual(
                    base_color=(255, 255, 255),
                    progress_color=(230, 230, 230),
                    progress_fraction=0.0,
                    text_color="#000000",
                    time_label="",
                    percent_label="",
                )
            else:
                visual = nag_line_visual(nag, now_value, entry.due_window)

            self.canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                fill=rgb_to_hex(visual.base_color),
                outline="#d0d0d0",
                width=1,
            )

            progress_x = x0 + int((x1 - x0) * max(0.0, min(1.0, visual.progress_fraction)))
            if progress_x > x0:
                self.canvas.create_rectangle(
                    x0,
                    y0,
                    progress_x,
                    y1,
                    fill=rgb_to_hex(visual.progress_color),
                    outline="",
                )

            if self.selected_key == entry.key:
                self.canvas.create_rectangle(x0, y0, x1, y1, outline="#1565c0", width=2)

            left_text_x = x0 + 10
            image = self._resolve_row_image(nag)
            if image is not None:
                self.canvas.create_image(x0 + 10, y0 + int((row_height - 8) / 2), image=image, anchor="w")
                left_text_x = x0 + 54
            icon = (normalize_icon_glyph(nag.icon_glyph) or "")[:3]
            if image is not None:
                icon = ""
            project_name = effective_project_name(nag)
            if project_overview_mode:
                title_prefix = f"[{project_name or DEFAULT_PROJECT_NAME}] "
            elif selected_bucket == ALL_BUCKET:
                if project_name:
                    title_prefix = f"[{nag.bucket}:{project_name}] "
                else:
                    title_prefix = f"[{nag.bucket}] "
            else:
                title_prefix = ""
            title = f"{icon + ' ' if icon else ''}{title_prefix}{nag.nag_text}"
            if project_overview_mode:
                project_task_count = project_counts.get(project_name or DEFAULT_PROJECT_NAME, 0)
                subtitle = f"{project_task_count} task(s) in project"
            else:
                subtitle_parts: List[str] = [f"w{nag.weight}", f"late:{nag.lateness_days}d"]
                if nag.mode == NAG_MODE_MONTHLY and nag.recurring_visible_days_before_due is not None:
                    subtitle_parts.append(f"vis<= {max(1, nag.recurring_visible_days_before_due)}d")
                recurring_badge = recurring_indicator_label(nag)
                if recurring_badge:
                    subtitle_parts.append(recurring_badge)
                push_badge = push_summary_label(nag)
                if push_badge:
                    subtitle_parts.append(push_badge)
                subtitle = "  ".join(subtitle_parts)

            self.canvas.create_text(
                left_text_x,
                y0 + 18,
                anchor="w",
                text=title,
                fill=visual.text_color,
                width=(x1 - x0) - 250,
                font=("TkDefaultFont", 10, "bold"),
            )

            self.canvas.create_text(
                left_text_x,
                y0 + 39,
                anchor="w",
                text=subtitle,
                fill=visual.text_color,
                width=(x1 - x0) - 250,
                font=("TkDefaultFont", 8),
            )

            if not project_overview_mode:
                right_label = visual.time_label
                if visual.percent_label:
                    right_label = f"{right_label} /{visual.percent_label}".strip()
                self.canvas.create_text(
                    x1 - 8,
                    y0 + (row_height / 2) - 6,
                    anchor="e",
                    text=right_label,
                    fill=visual.text_color,
                    font=("TkDefaultFont", 9),
                )

            self.row_bounds.append((y0, y1, entry))

        total_height = 12 + len(self.visible_entries) * row_height
        self.canvas.configure(scrollregion=(0, 0, width, total_height))
        if preserve_scroll:
            self.canvas.yview_moveto(max(0.0, min(1.0, previous_start)))

    def _resolve_row_image(self, nag: Nag) -> Optional[ImageTk.PhotoImage]:
        inline_icon_base64 = normalize_icon_png_base64(nag.icon_png_base64)
        if inline_icon_base64:
            inline_key = f"inline:{hashlib.sha1(inline_icon_base64.encode('utf-8')).hexdigest()}"
            if inline_key not in self.row_image_failures:
                cached_inline = self.row_image_cache.get(inline_key)
                if cached_inline is not None:
                    return cached_inline
                try:
                    image = Image.open(BytesIO(base64.b64decode(inline_icon_base64, validate=True)))
                    image = image.convert("RGBA")
                    image.thumbnail((36, 36), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                    self.row_image_cache[inline_key] = photo
                    return photo
                except Exception:
                    self.row_image_failures.add(inline_key)

        url = normalize_image_url(nag.image_url)
        if not url or url in self.row_image_failures:
            return None
        cached = self.row_image_cache.get(url)
        if cached is not None:
            return cached

        try:
            response = requests.get(url, timeout=12)
            if response.status_code >= 300:
                self.row_image_failures.add(url)
                return None
            image = Image.open(BytesIO(response.content))
            image = image.convert("RGBA")
            image.thumbnail((36, 36), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.row_image_cache[url] = photo
            return photo
        except Exception:
            self.row_image_failures.add(url)
            return None


def main() -> None:
    root = tk.Tk()
    app = NagDesktopApp(root)
    root.after(300, app.refresh_visible_entries)
    root.mainloop()


if __name__ == "__main__":
    main()
