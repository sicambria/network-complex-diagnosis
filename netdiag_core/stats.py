"""Statistics helpers — pure functions over lists of numbers (no I/O, no deps)."""

import statistics


def percentile(values, pct):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def clean_float(value):
    if value is None:
        return None
    return round(float(value), 2)


def series_stats(values):
    if not values:
        return {"count": 0, "min_ms": None, "avg_ms": None, "max_ms": None,
                "stdev_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {
        "count": len(values),
        "min_ms": clean_float(min(values)),
        "avg_ms": clean_float(statistics.mean(values)),
        "max_ms": clean_float(max(values)),
        "stdev_ms": clean_float(statistics.pstdev(values)) if len(values) > 1 else 0,
        "p50_ms": clean_float(percentile(values, 50)),
        "p95_ms": clean_float(percentile(values, 95)),
        "p99_ms": clean_float(percentile(values, 99)),
    }


def jitter_ms(values):
    if len(values) < 2:
        return None
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return clean_float(statistics.mean(diffs))
