"""Model tests – synthetic invariants plus the real-house regression case."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

import pytest

from custom_components.loadcast.model import (
    AdaptiveComponentModel,
    ComponentModel,
    Sample,
)

from . import backtest
from .fixtures.loader import load_house

TZ = ZoneInfo("Europe/Stockholm")
START = datetime(2026, 9, 1, tzinfo=TZ)


def hours(days: int, start: datetime = START):
    for i in range(days * 24):
        yield (start + timedelta(hours=i)).astimezone(ZoneInfo("UTC"))


def day_total(model, day: datetime, temp: float | None = None) -> float:
    return sum(model.predict(day + timedelta(hours=h), temp).kwh for h in range(24))


# --- Synthetic invariants -------------------------------------------------------


def test_not_ready_without_history():
    m = ComponentModel(tz=TZ)
    m.fit([Sample(t, 1.0) for t in hours(0)], now=START)
    assert not m.ready


def test_learns_daily_shape():
    """Evening peak is learned without being told."""
    samples = [
        Sample(t, 3.0 if t.astimezone(TZ).hour in (18, 19, 20) else 1.0)
        for t in hours(14)
    ]
    now = START + timedelta(days=14)
    m = ComponentModel(tz=TZ, use_temperature=False)
    m.fit(samples, now=now)
    assert m.predict(now + timedelta(hours=19)).kwh > 2.5
    assert m.predict(now + timedelta(hours=3)).kwh == pytest.approx(1.0, abs=0.05)


def test_recovers_temperature_slope():
    """kWh = 2 - 0.1*T is recovered from noisy-free data with varying temps."""
    samples = []
    for i, t in enumerate(hours(21)):
        temp = 5 + 10 * math.sin(i / 37)  # temps vary independently of hour
        samples.append(Sample(t, 2 - 0.1 * temp, temp))
    now = START + timedelta(days=21)
    m = ComponentModel(tz=TZ, half_life_days=30)
    m.fit(samples, now=now)
    assert m.slope_kwh_per_c == pytest.approx(-0.1, abs=0.02)
    assert m.predict(now, 0.0).kwh == pytest.approx(2.0, abs=0.15)


def test_no_slope_without_temperature_spread():
    samples = [Sample(t, 1.0, 10.0) for t in hours(14)]
    m = ComponentModel(tz=TZ)
    m.fit(samples, now=START + timedelta(days=14))
    assert m.slope_kwh_per_c == 0.0


def test_never_negative():
    samples = [Sample(t, 0.0) for t in hours(14)]
    m = ComponentModel(tz=TZ)
    m.fit(samples, now=START + timedelta(days=14))
    p = m.predict(START + timedelta(days=14, hours=5), -30.0)
    assert p.kwh >= 0 and p.p10 >= 0 and p.p90 >= 0


@pytest.mark.parametrize(("before", "after"), [(0.5, 0.1), (0.1, 0.5)])
def test_adaptive_follows_step_change_both_ways(before: float, after: float):
    """A level change is learned within 3 days, equally fast up and down."""
    change = START + timedelta(days=14)
    samples = [Sample(t, before if t < change else after) for t in hours(17)]
    now = START + timedelta(days=17)
    m = AdaptiveComponentModel(tz=TZ, use_temperature=False)
    m.fit(samples, now=now)
    forecast = day_total(m, now)
    target = after * 24
    assert abs(forecast - target) / target < 0.2
    assert m.half_life_days <= 3  # it chose a short memory on its own


def test_adaptive_keeps_long_memory_when_stable():
    samples = [
        Sample(t, 1.0 + 0.3 * math.sin(i * 1.7)) for i, t in enumerate(hours(21))
    ]
    m = AdaptiveComponentModel(tz=TZ, use_temperature=False)
    m.fit(samples, now=START + timedelta(days=21))
    assert m.half_life_days >= 7


def test_dst_transition_does_not_break():
    """Europe/Stockholm leaves DST on 2026-10-25; a fit across it works."""
    start = datetime(2026, 10, 15, tzinfo=TZ)
    samples = [Sample(t, 1.0) for t in hours(14, start)]
    now = start + timedelta(days=14)
    m = AdaptiveComponentModel(tz=TZ)
    m.fit(samples, now=now)
    assert m.predict(now).kwh == pytest.approx(1.0, abs=0.05)


# --- Real data -----------------------------------------------------------------


def test_real_heat_pump_outage_is_learned():
    """The heat pump stopped on 2026-09-23 (~12 → ~3 kWh/day).

    Four days later the forecast must be within 2.5 kWh of the new level. The
    regression this replaces still planned ~30 kWh/day two weeks later.
    """
    data = load_house()
    day = datetime(2026, 9, 27, tzinfo=TZ)
    m = AdaptiveComponentModel(tz=TZ)
    m.fit(data["heatpump"], now=day)
    actual = sum(
        s.kwh for s in data["heatpump"] if s.start.astimezone(TZ).date() == day.date()
    )
    temps = {
        s.start: s.temp_c
        for s in data["heatpump"]
        if s.start.astimezone(TZ).date() == day.date()
    }
    forecast = sum(m.predict(t, temp).kwh for t, temp in temps.items())
    assert abs(forecast - actual) < 2.5


def test_backtest_beats_legacy_regression():
    rows = backtest.run()
    assert len(rows) >= 15
    loadcast = backtest.mape(rows, "loadcast")
    legacy = backtest.mape(rows, "legacy")
    assert loadcast < 13.0
    assert loadcast < legacy - 5


def test_adaptive_drops_spurious_temperature_slope():
    """An EV-like load that only *happened* to coincide with warm days in the
    history must not keep a temperature slope when that slope hurts."""
    samples = []
    for i, t in enumerate(hours(17)):
        day = i // 24
        if day < 14:
            # History: charging (2 kWh/h) on exactly the warm days
            warm = day % 2 == 0
            temp = 14.0 if warm else 6.0
            kwh = 2.0 if warm else 0.0
        else:
            # Recent days: warm, but no charging – the "slope" was a fluke
            temp, kwh = 14.0, 0.0 if day != 15 else 2.0
        samples.append(Sample(t, kwh, temp))
    m = AdaptiveComponentModel(tz=TZ)
    m.fit(samples, now=START + timedelta(days=17))
    assert m.uses_temperature is False
    assert m.slope_kwh_per_c == 0.0


def test_adaptive_keeps_real_heating_slope():
    """Heating that truly follows temperature keeps its slope."""
    samples = []
    for i, t in enumerate(hours(21)):
        temp = 5 + 8 * math.sin(i / 29)
        samples.append(Sample(t, max(1.5 - 0.1 * temp, 0), temp))
    m = AdaptiveComponentModel(tz=TZ)
    m.fit(samples, now=START + timedelta(days=21))
    assert m.uses_temperature is True
    assert m.slope_kwh_per_c < -0.05
