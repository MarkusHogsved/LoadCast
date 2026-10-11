"""Load the hourly CSV fixtures into per-component sample lists."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from custom_components.loadcast.model import Sample

FIXTURES = Path(__file__).parent
HOUSE_CSV = FIXTURES / "house_2026-09-14_2026-10-11.csv"


def load_house(path: Path = HOUSE_CSV) -> dict[str, list[Sample]]:
    """Return samples for 'base', 'heatpump', 'ev' and 'total'.

    The CSV holds total household load plus two metered sub-loads per hour.
    Base load = total − heat pump − EV. Hours with any missing value are
    skipped for every component so they stay aligned.
    """
    out: dict[str, list[Sample]] = {"base": [], "heatpump": [], "ev": [], "total": []}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not all(row[k] for k in ("total_kwh", "heatpump_kwh", "ev_kwh")):
                continue
            start = datetime.fromisoformat(row["hour_start_utc"].replace("Z", "+00:00"))
            temp = float(row["temp_c"]) if row["temp_c"] else None
            total = float(row["total_kwh"])
            hp = float(row["heatpump_kwh"])
            ev = float(row["ev_kwh"])
            out["total"].append(Sample(start, total, temp))
            out["heatpump"].append(Sample(start, hp, temp))
            out["ev"].append(Sample(start, ev, temp))
            out["base"].append(Sample(start, max(total - hp - ev, 0.0), temp))
    return out
