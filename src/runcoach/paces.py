"""Pace conversions: min/km to min/mi, km/h, and mph."""

from __future__ import annotations

KM_PER_MI = 1.609344


def parse_pace(text: str) -> float:
    """'7:00' -> 7.0 minutes per km."""
    m, s = text.strip().split(":")
    return int(m) + int(s) / 60


def fmt_pace(minutes: float) -> str:
    total = round(minutes * 60)
    return f"{total // 60}:{total % 60:02d}"


def per_mile(min_per_km: float) -> float:
    return min_per_km * KM_PER_MI


def kmh(min_per_km: float) -> float:
    return 60 / min_per_km


def mph(min_per_km: float) -> float:
    return kmh(min_per_km) / KM_PER_MI


def describe(paces: list[str]) -> dict[str, str]:
    """Return a pace (or pace range) in all four units.

    >>> describe(["7:00", "7:30"])["km"]
    '7:00–7:30/km'
    """
    vals = [parse_pace(p) for p in paces]
    def rng(fn, fmt):
        out = [fmt(fn(v)) for v in vals]
        return out[0] if len(out) == 1 else f"{out[0]}–{out[-1]}"

    speed = [kmh(v) for v in vals]
    speed_mph = [mph(v) for v in vals]
    # A slower pace is a lower speed, so show speeds low-high.
    kmh_txt = "–".join(f"{s:.1f}" for s in sorted(speed)) if len(vals) > 1 else f"{speed[0]:.1f}"
    mph_txt = "–".join(f"{s:.1f}" for s in sorted(speed_mph)) if len(vals) > 1 else f"{speed_mph[0]:.1f}"
    return {
        "km": rng(lambda v: v, fmt_pace) + "/km",
        "mi": rng(per_mile, fmt_pace) + "/mi",
        "kmh": kmh_txt + " km/h",
        "mph": mph_txt + " mph",
    }


def km_to_mi(km: float) -> float:
    return km / KM_PER_MI
