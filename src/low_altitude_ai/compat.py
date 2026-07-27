"""Narrow compatibility helpers for host-side validation tools."""

import datetime as dt

UTC: dt.tzinfo = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017
