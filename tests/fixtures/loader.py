"""Load the hourly CSV fixtures into per-component sample lists."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from custom_components.loadcast.model import (
    Sample,
    detect_meter_lag,
    reconcile_residual,
    shift_meter,
)

FIXTURES = Path(__file__).parent
HOUSE_CSV = FIXTURES / "house_2026-09-14_2026-10-14.csv"


def load_house(path: Path = HOUSE_CSV, aligned: bool = True) -> dict[str, list[Sample]]:
    """Return samples for 'base', 'heatpump', 'ev' and 'total'.

    The CSV holds total household load plus two metered sub-loads per hour.
    Base load = total − heat pump − EV, built the same way as the coordinator
    does it: late sub-load meters are shifted back (``detect_meter_lag``) and
    negative hours borrow from their neighbours (``reconcile_residual``).
    ``aligned=False`` gives the pre-0.1.6 behaviour (no shift, clip at 0) for
    comparison. Hours with any missing value are skipped for every component.
    """
    total: dict[datetime, float] = {}
    subs: dict[str, dict[datetime, float]] = {"heatpump": {}, "ev": {}}
    temps: dict[datetime, float | None] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not all(row[k] for k in ("total_kwh", "heatpump_kwh", "ev_kwh")):
                continue
            start = datetime.fromisoformat(row["hour_start_utc"].replace("Z", "+00:00"))
            temps[start] = float(row["temp_c"]) if row["temp_c"] else None
            total[start] = float(row["total_kwh"])
            subs["heatpump"][start] = float(row["heatpump_kwh"])
            subs["ev"][start] = float(row["ev_kwh"])
    if aligned:
        subs = {n: shift_meter(v, detect_meter_lag(total, v)) for n, v in subs.items()}
    out: dict[str, list[Sample]] = {"base": [], "heatpump": [], "ev": [], "total": []}
    hours, residual = [], []
    for t in sorted(total):
        if any(t not in v for v in subs.values()):
            continue
        hp, ev = max(subs["heatpump"][t], 0.0), max(subs["ev"][t], 0.0)
        out["total"].append(Sample(t, total[t], temps[t]))
        out["heatpump"].append(Sample(t, hp, temps[t]))
        out["ev"].append(Sample(t, ev, temps[t]))
        hours.append(t)
        residual.append(total[t] - hp - ev)
    base = (
        reconcile_residual(hours, residual)
        if aligned
        else [max(r, 0.0) for r in residual]
    )
    out["base"] = [Sample(t, b, temps[t]) for t, b in zip(hours, base, strict=False)]
    return out
