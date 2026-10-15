"""Rolling-origin backtest of the LoadCast model on the house fixture.

For every day D the model is fitted only on data before D 00:00 local time
and then forecasts D's 24 hours (using the day's measured temperatures, i.e.
a perfect weather forecast). The forecast is compared with what actually
happened, and with the hand-tuned regression LoadCast replaces.

Run:  python -m tests.backtest
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.loadcast.model import (
    AdaptiveComponentModel,
    ComponentModel,
    Sample,
)

from .fixtures.loader import load_house

TZ = ZoneInfo("Europe/Stockholm")
COMPONENTS = ("base", "heatpump")  # EV is planned separately; excluded here
WARMUP_DAYS = 7
ADAPTIVE = True


def legacy_daily(temp_c: float) -> float:
    """The regression in the author's battery planner (factor stuck at 1.0)."""
    base = max(35.7 + 6.7 - 0.51 * temp_c, 20)
    heatpump = max(48.7 - 2.03 * temp_c, 0) * 1.0
    return base + heatpump


def by_local_day(samples: list[Sample]) -> dict:
    days: dict = defaultdict(list)
    for s in samples:
        days[s.start.astimezone(TZ).date()].append(s)
    return days


def run(aligned: bool = True) -> list[dict]:
    """Fit on ``load_house(aligned)``; always score against the aligned data,
    whose base load is energy-conserving (the honest actual)."""
    fit_data = load_house(aligned=aligned)
    truth = load_house(aligned=True)
    days = sorted(by_local_day(truth["base"]))
    results = []
    for day in days[WARMUP_DAYS:]:
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        actual = 0.0
        forecast = 0.0
        per_comp = {}
        hourly = []  # (forecast, p10, p90, actual) per hour, summed over components
        for comp in COMPONENTS:
            model = AdaptiveComponentModel(tz=TZ) if ADAPTIVE else ComponentModel(tz=TZ)
            model.fit(fit_data[comp], now=midnight)
            day_samples = [
                s for s in truth[comp] if s.start.astimezone(TZ).date() == day
            ]
            if len(day_samples) < 24:
                break
            preds = [model.predict(s.start, s.temp_c) for s in day_samples]
            f = sum(p.kwh for p in preds)
            a = sum(s.kwh for s in day_samples)
            per_comp[comp] = (f, a, getattr(model, "half_life_days", None))
            forecast += f
            actual += a
            if not hourly:
                hourly = [[0.0, 0.0, 0.0, 0.0] for _ in day_samples]
            for h, (p, s) in zip(
                hourly, zip(preds, day_samples, strict=False), strict=False
            ):
                h[0] += p.kwh
                h[1] += p.p10
                h[2] += p.p90
                h[3] += s.kwh
        else:
            temps = [
                s.temp_c for s in truth["base"] if s.start.astimezone(TZ).date() == day
            ]
            t_mean = sum(temps) / len(temps)
            results.append(
                {
                    "day": day,
                    "actual": actual,
                    "loadcast": forecast,
                    "legacy": legacy_daily(t_mean),
                    "per_comp": per_comp,
                    "hourly": hourly,
                }
            )
    return results


def hourly_mae(rows: list[dict]) -> float:
    hs = [h for r in rows for h in r["hourly"]]
    return sum(abs(h[0] - h[3]) for h in hs) / len(hs)


def p10_p90_coverage(rows: list[dict]) -> float:
    """Share of hours whose actual lies inside the summed P10-P90 band
    (summing quantiles is conservative; an ideal per-component band gives 80 %)."""
    hs = [h for r in rows for h in r["hourly"]]
    return 100 * sum(1 for h in hs if h[1] <= h[3] <= h[2]) / len(hs)


def bias(rows: list[dict], key: str = "loadcast") -> float:
    return sum(r[key] - r["actual"] for r in rows) / len(rows)


def mape(rows: list[dict], key: str) -> float:
    return 100 * sum(abs(r[key] - r["actual"]) / r["actual"] for r in rows) / len(rows)


if __name__ == "__main__":
    for aligned in (False, True):
        rows = run(aligned=aligned)
        label = "aligned meters (0.1.6)" if aligned else "clipped, no meter lag (0.1.5)"
        print(f"\n=== {label} ===")
        print("day          actual  loadcast  legacy   hp(f/a)")
        for r in rows:
            hf, ha, hl = r["per_comp"]["heatpump"]
            bhl = r["per_comp"]["base"][2]
            print(
                f"{r['day']}  {r['actual']:6.1f}  {r['loadcast']:8.1f}  {r['legacy']:6.1f}"
                f"   {hf:4.1f}/{ha:4.1f}   hl hp={hl} base={bhl}"
            )
        print(
            f"MAPE loadcast {mape(rows, 'loadcast'):.1f} %   legacy {mape(rows, 'legacy'):.1f} %"
            f"   bias {bias(rows):+.2f} kWh/day   hourly MAE {hourly_mae(rows):.3f} kWh"
            f"   P10-P90 coverage {p10_p90_coverage(rows):.0f} %"
        )
