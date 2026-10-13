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
    TransitionModel,
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


# --- Transition after a known change (mark_change) -------------------------------


def _pump(kwh_before: float, kwh_after: float, change: datetime, new_hours: int):
    """14 days at one level, then ``new_hours`` hours at another."""
    before = [Sample(t, kwh_before, 10.0) for t in hours(14) if t < change]
    after = [
        Sample(change + timedelta(hours=i), kwh_after, 10.0) for i in range(new_hours)
    ]
    return before + after


CHANGE = (START + timedelta(days=14)).astimezone(ZoneInfo("UTC"))


def test_transition_false_marker_keeps_forecast():
    """A marker with no real change (12/10: a battery on the same circuit)
    leaves the forecast where it was, flagged uncertain."""
    samples = _pump(0.15, 0.15, CHANGE, 6)
    now = CHANGE + timedelta(hours=6)
    t = TransitionModel(tz=TZ)
    t.fit(samples, since=CHANGE, now=now)
    full = AdaptiveComponentModel(tz=TZ)
    full.fit(samples, now=now)
    assert t.ready and t.uncertain and t.new_samples == 6
    assert abs(t.shift_kwh) < 0.02
    day = START + timedelta(days=15)
    assert day_total(t, day, 10.0) == pytest.approx(day_total(full, day, 10.0), abs=0.5)


def test_transition_pump_start_moves_level_at_once():
    """A heat pump that starts shows up after its first hours, with a band
    that covers both the old and the new level."""
    samples = _pump(0.1, 1.5, CHANGE, 3)
    t = TransitionModel(tz=TZ)
    t.fit(samples, since=CHANGE, now=CHANGE + timedelta(hours=3))
    p = t.predict(CHANGE + timedelta(hours=10), 10.0)
    assert p.kwh == pytest.approx(1.5, abs=0.1)
    assert p.p10 <= 0.2  # old level still inside the band
    assert p.p90 >= 1.5
    assert p.p10 <= p.kwh <= p.p90


def test_transition_without_new_hours_is_the_old_model():
    samples = _pump(0.4, 0.4, CHANGE, 0)
    t = TransitionModel(tz=TZ)
    t.fit(samples, since=CHANGE, now=CHANGE)
    base = AdaptiveComponentModel(tz=TZ)
    base.fit(samples, now=CHANGE)
    assert t.ready and t.new_samples == 0 and t.shift_kwh == 0
    when = CHANGE + timedelta(hours=5)
    assert t.predict(when, 10.0) == base.predict(when, 10.0)


def test_transition_without_old_history_uses_new_mean():
    """A brand-new load: the hours seen so far as a flat level."""
    new = [
        Sample(CHANGE + timedelta(hours=i), v) for i, v in enumerate([1.0, 2.0, 3.0])
    ]
    t = TransitionModel(tz=TZ)
    t.fit(new, since=CHANGE, now=CHANGE + timedelta(hours=3))
    p = t.predict(CHANGE + timedelta(hours=8))
    assert t.ready and p.kwh == pytest.approx(2.0) and p.p10 == 1.0 and p.p90 == 3.0
    empty = TransitionModel(tz=TZ)
    empty.fit([], since=CHANGE, now=CHANGE)
    assert not empty.ready
