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


def run() -> list[dict]:
    data = load_house()
    days = sorted(by_local_day(data["base"]))
    results = []
    for day in days[WARMUP_DAYS:]:
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        actual = 0.0
        forecast = 0.0
        per_comp = {}
        for comp in COMPONENTS:
            model = AdaptiveComponentModel(tz=TZ) if ADAPTIVE else ComponentModel(tz=TZ)
            model.fit(data[comp], now=midnight)
            day_samples = [
                s for s in data[comp] if s.start.astimezone(TZ).date() == day
            ]
            if len(day_samples) < 24:
                break
            f = sum(model.predict(s.start, s.temp_c).kwh for s in day_samples)
            a = sum(s.kwh for s in day_samples)
            per_comp[comp] = (f, a, getattr(model, "half_life_days", None))
            forecast += f
            actual += a
        else:
            temps = [
                s.temp_c for s in data["base"] if s.start.astimezone(TZ).date() == day
            ]
            t_mean = sum(temps) / len(temps)
            results.append(
                {
                    "day": day,
                    "actual": actual,
                    "loadcast": forecast,
                    "legacy": legacy_daily(t_mean),
                    "per_comp": per_comp,
                }
            )
    return results


def mape(rows: list[dict], key: str) -> float:
    return 100 * sum(abs(r[key] - r["actual"]) / r["actual"] for r in rows) / len(rows)


if __name__ == "__main__":
    rows = run()
    print("day          actual  loadcast  legacy   hp(f/a)")
    for r in rows:
        hf, ha, hl = r["per_comp"]["heatpump"]
        bhl = r["per_comp"]["base"][2]
        print(
            f"{r['day']}  {r['actual']:6.1f}  {r['loadcast']:8.1f}  {r['legacy']:6.1f}"
            f"   {hf:4.1f}/{ha:4.1f}   hl hp={hl} base={bhl}"
        )
    print(
        f"\nMAPE loadcast {mape(rows, 'loadcast'):.1f} %   legacy {mape(rows, 'legacy'):.1f} %"
    )
