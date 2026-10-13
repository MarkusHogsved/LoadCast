"""LoadCast forecasting model.

Pure Python, no Home Assistant imports, so it can be unit-tested and
backtested in isolation.

The model predicts the energy (kWh) a single load component draws in a given
hour. One model is fitted per component (base load, heat pump, EV charger, …)
and the forecasts are summed by the caller.

Design (see SPEC.md §6):

* **Slots** – an hour's expected level depends on the local hour of day and
  the day type (weekday / weekend). Instead of hard slot buckets we use a
  kernel: samples from the same hour count fully, neighbouring hours and the
  other day type count partially. This keeps sparse slots (weekend hours with
  only a handful of samples) from becoming noisy.
* **Recency** – every sample is weighted ``0.5 ** (age_days / half_life)``.
  The model therefore follows regime changes (a heat pump that stops) in both
  directions at the same speed. There is deliberately no asymmetric damping:
  conservatism belongs in the consumer's decision margins.
* **Temperature** – one slope per component, estimated with slot fixed
  effects (each sample is compared to its own slot's mean), shrunk towards
  zero when the temperature spread in the window is small. The hourly level
  is ``slot_intercept + slope * temperature``.
* **Uncertainty** – weighted residual quantiles (P10 / P90) from the same
  kernel neighbourhood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
import math

# --- Tunables (defaults chosen from backtests on real data, see tests) ------

DEFAULT_HALF_LIFE_DAYS = 7.0
DEFAULT_WINDOW_DAYS = 28
NEIGHBOUR_HOUR_WEIGHT = 0.4  # weight of samples one hour away
OTHER_DAYTYPE_WEIGHT = 0.3  # weight of weekday samples for a weekend slot etc.
SLOPE_SHRINK_C2 = 1.0  # shrink slope by var / (var + this), in °C²
MIN_SAMPLES = 24  # fewer usable samples than this → model not ready


@dataclass(frozen=True)
class Sample:
    """One hour of measured energy for a component."""

    start: datetime  # timezone-aware hour start
    kwh: float
    temp_c: float | None = None


@dataclass(frozen=True)
class Prediction:
    """Forecast for one hour."""

    kwh: float
    p10: float
    p90: float


@dataclass
class _Point:
    """A fitted sample with its slot coordinates and recency weight."""

    hour: int
    weekend: bool
    kwh: float
    temp_c: float | None
    weight: float


@dataclass
class ComponentModel:
    """Learned load profile for one component."""

    tz: tzinfo
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    window_days: int = DEFAULT_WINDOW_DAYS
    use_temperature: bool = True

    # Fitted state
    slope_kwh_per_c: float = 0.0
    fitted_at: datetime | None = None
    sample_count: int = 0
    _points: list[_Point] = field(default_factory=list, repr=False)
    _slot_cache: dict[tuple[int, bool], tuple[float, float, float, float]] = field(
        default_factory=dict, repr=False
    )

    @property
    def ready(self) -> bool:
        """True when enough history has been seen to forecast."""
        return self.sample_count >= MIN_SAMPLES

    # ------------------------------------------------------------------ fit

    def fit(self, samples: list[Sample], now: datetime) -> None:
        """Fit the model on samples that lie inside the history window."""
        cutoff = now - timedelta(days=self.window_days)
        points: list[_Point] = []
        for s in samples:
            if s.start < cutoff or s.start >= now or not math.isfinite(s.kwh):
                continue
            local = s.start.astimezone(self.tz)
            age_days = (now - s.start).total_seconds() / 86400
            points.append(
                _Point(
                    hour=local.hour,
                    weekend=local.weekday() >= 5,
                    kwh=max(s.kwh, 0.0),
                    temp_c=s.temp_c
                    if (s.temp_c is not None and math.isfinite(s.temp_c))
                    else None,
                    weight=0.5 ** (age_days / self.half_life_days),
                )
            )

        self._points = points
        self.sample_count = len(points)
        self.fitted_at = now
        self._slot_cache = {}
        self.slope_kwh_per_c = self._fit_slope() if self.use_temperature else 0.0

    def _kernel(self, p: _Point, hour: int, weekend: bool) -> float:
        """Similarity weight of a sample for a target slot."""
        dh = min((p.hour - hour) % 24, (hour - p.hour) % 24)
        if dh == 0:
            k = 1.0
        elif dh == 1:
            k = NEIGHBOUR_HOUR_WEIGHT
        else:
            return 0.0
        if p.weekend != weekend:
            k *= OTHER_DAYTYPE_WEIGHT
        return k

    def _fit_slope(self) -> float:
        """Temperature slope with slot fixed effects, shrunk towards zero.

        Each sample is centred on the weighted mean of its own exact slot
        (hour, day type), so the slope only explains how a given hour differs
        on warmer vs colder days – not the daily shape.
        """
        groups: dict[tuple[int, bool], list[_Point]] = {}
        for p in self._points:
            if p.temp_c is not None:
                groups.setdefault((p.hour, p.weekend), []).append(p)

        sxy = sxx = sw = 0.0
        for pts in groups.values():
            w = sum(p.weight for p in pts)
            if w <= 0 or len(pts) < 2:
                continue
            mx = sum(p.weight * p.temp_c for p in pts) / w  # type: ignore[operator]
            my = sum(p.weight * p.kwh for p in pts) / w
            for p in pts:
                dx = p.temp_c - mx  # type: ignore[operator]
                sxy += p.weight * dx * (p.kwh - my)
                sxx += p.weight * dx * dx
                sw += p.weight

        if sw <= 0 or sxx <= 0:
            return 0.0
        raw = sxy / sxx
        var = sxx / sw  # weighted within-slot temperature variance
        return raw * var / (var + SLOPE_SHRINK_C2)

    def _slot_stats(
        self, hour: int, weekend: bool
    ) -> tuple[float, float, float, float]:
        """Return (intercept, mean_temp, p10_residual, p90_residual) for a slot."""
        key = (hour, weekend)
        if key in self._slot_cache:
            return self._slot_cache[key]

        weighted: list[tuple[float, _Point]] = []
        for p in self._points:
            k = self._kernel(p, hour, weekend)
            if k > 0:
                weighted.append((k * p.weight, p))

        total_w = sum(w for w, _ in weighted)
        if total_w <= 0:
            stats = (0.0, 0.0, 0.0, 0.0)
            self._slot_cache[key] = stats
            return stats

        b = self.slope_kwh_per_c
        # Temperature-adjust each sample to a common reference so the
        # intercept is the level at 0 °C: kwh - b*T.
        adj = [
            (w, p.kwh - (b * p.temp_c if p.temp_c is not None else 0.0), p)
            for w, p in weighted
        ]
        intercept = sum(w * a for w, a, _ in adj) / total_w

        temps = [(w, p.temp_c) for w, p in weighted if p.temp_c is not None]
        tw = sum(w for w, _ in temps)
        mean_temp = sum(w * t for w, t in temps) / tw if tw > 0 else 0.0

        residuals = [(a - intercept, w) for w, a, _ in adj]
        p10 = _weighted_quantile(residuals, 0.10)
        p90 = _weighted_quantile(residuals, 0.90)

        stats = (intercept, mean_temp, p10, p90)
        self._slot_cache[key] = stats
        return stats

    # -------------------------------------------------------------- predict

    def predict(self, start: datetime, temp_c: float | None = None) -> Prediction:
        """Forecast the energy for the hour beginning at ``start``.

        ``temp_c`` is the expected outdoor temperature for that hour. When it
        is missing the slot's own (recency-weighted) mean temperature is used,
        which reduces to a pure time-of-day profile.
        """
        local = start.astimezone(self.tz)
        intercept, mean_temp, r10, r90 = self._slot_stats(
            local.hour, local.weekday() >= 5
        )
        t = temp_c if temp_c is not None else mean_temp
        mean = intercept + self.slope_kwh_per_c * t
        return Prediction(
            kwh=max(mean, 0.0),
            p10=max(mean + r10, 0.0),
            p90=max(mean + r90, 0.0),
        )


@dataclass
class AdaptiveComponentModel:
    """Component model that picks its own memory length from outcomes.

    A fixed recency half-life is a compromise: short enough to follow a
    heat pump that stops, long enough not to chase noise. Instead, every fit
    replays the last ``eval_days`` days: for each candidate half-life the
    model is fitted on data before that day and its forecast is compared with
    what actually happened. The half-life with the lowest error wins and is
    used for the real forecast.

    When the house is stable the long half-lives win (less noise). When a
    load changes level, the short half-lives start winning within a day or
    two and the forecast snaps to the new level.
    """

    tz: tzinfo
    # Chosen from the rolling backtest on real data (tests/backtest.py):
    # best total MAPE and fastest recovery after a sub-load stopped.
    candidates: tuple[float, ...] = (0.75, 1.5, 3.0, 7.0, 14.0)
    eval_days: int = 3
    window_days: int = DEFAULT_WINDOW_DAYS
    use_temperature: bool = True

    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    # Whether the chosen model uses temperature. Decided per fit from
    # outcomes, like the half-life (only possible when use_temperature=True).
    uses_temperature: bool = True
    errors: dict[tuple[float, bool], float] = field(default_factory=dict)
    _model: ComponentModel | None = field(default=None, repr=False)

    # A fully fitted model is not in transition after a known change.
    uncertain = False
    new_samples: int | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None and self._model.ready

    @property
    def slope_kwh_per_c(self) -> float:
        return self._model.slope_kwh_per_c if self._model else 0.0

    @property
    def fitted_at(self) -> datetime | None:
        return self._model.fitted_at if self._model else None

    def _new(self, half_life: float, use_temperature: bool) -> ComponentModel:
        return ComponentModel(
            tz=self.tz,
            half_life_days=half_life,
            window_days=self.window_days,
            use_temperature=use_temperature,
        )

    def fit(self, samples: list[Sample], now: datetime) -> None:
        """Select the best half-life and temperature use on recent days.

        Candidates are every half-life, with and without temperature. A
        temperature slope is only kept if it does not make the replayed
        forecasts noticeably worse – so a load that merely *happened* to
        correlate with the weather (an EV charger) loses its spurious slope,
        while heating keeps its real one.
        """
        local_now = now.astimezone(self.tz)
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Group the evaluation days' samples once.
        eval_starts = [today - timedelta(days=d) for d in range(self.eval_days, 0, -1)]
        by_day: dict[datetime, list[Sample]] = {d: [] for d in eval_starts}
        for s in samples:
            local = s.start.astimezone(self.tz)
            day = local.replace(hour=0, minute=0, second=0, microsecond=0)
            if day in by_day:
                by_day[day].append(s)

        has_temps = any(s.temp_c is not None for s in samples)
        temp_options = (
            (True, False) if (self.use_temperature and has_temps) else (False,)
        )

        errors: dict[tuple[float, bool], float] = {}
        day_totals = [sum(s.kwh for s in ds) for ds in by_day.values() if len(ds) >= 20]
        mean_day = sum(day_totals) / len(day_totals) if day_totals else 0.0
        for use_temp in temp_options:
            for hl in self.candidates:
                abs_err = 0.0
                used = 0
                for day_start, day_samples in by_day.items():
                    if len(day_samples) < 20:
                        continue  # incomplete day, not a fair test
                    m = self._new(hl, use_temp)
                    m.fit(samples, now=day_start)
                    if not m.ready:
                        continue
                    f = sum(m.predict(s.start, s.temp_c).kwh for s in day_samples)
                    a = sum(s.kwh for s in day_samples)
                    abs_err += abs(f - a)
                    used += 1
                if used:
                    errors[(hl, use_temp)] = abs_err / used

        self.errors = errors
        if errors:
            # Among candidates that are practically as good as the best –
            # within 5 % of its error, or within 2 % of a typical day's energy
            # (differences that small are noise, not signal) – prefer:
            #   1. keeping temperature (a real heating slope matters most on
            #      the cold day that hasn't happened yet), then
            #   2. the longest memory (steadier forecast).
            best = min(errors.values())
            tolerance = max(best * 0.05, mean_day * 0.02)
            good = [k for k, e in errors.items() if e <= best + tolerance + 1e-9]
            self.half_life_days, self.uses_temperature = max(
                good, key=lambda k: (k[1], k[0])
            )
        else:
            self.half_life_days = DEFAULT_HALF_LIFE_DAYS
            self.uses_temperature = self.use_temperature and has_temps

        self._model = self._new(self.half_life_days, self.uses_temperature)
        self._model.fit(samples, now=now)

    def predict(self, start: datetime, temp_c: float | None = None) -> Prediction:
        if self._model is None:
            return Prediction(0.0, 0.0, 0.0)
        return self._model.predict(start, temp_c)


@dataclass
class TransitionModel:
    """Forecast for a component during the first day after a known change.

    Until a marked component has a day of new data (``MIN_SAMPLES`` hours) its
    own model is not ready. Publishing nothing would hide the whole household
    forecast, so instead:

    * the **shape** (hour of day, weekday/weekend, temperature) comes from a
      model fitted on the history *before* the change, as of the change;
    * the **level** is moved by the mean residual of the new hours against
      that model (a heat pump that starts adds its new draw at once; a false
      marker leaves the forecast almost unchanged);
    * P10/P90 are widened by the residual spread plus the size of the shift,
      so the band covers both the old and the new level.

    With no history before the change the new hours' mean is used as a flat
    level. The forecast is flagged ``uncertain``.
    """

    tz: tzinfo
    window_days: int = DEFAULT_WINDOW_DAYS
    base: AdaptiveComponentModel | None = field(default=None, repr=False)
    shift_kwh: float = 0.0
    extra_kwh: float = 0.0
    new_samples: int = 0
    _flat: tuple[float, float, float] | None = field(default=None, repr=False)

    uncertain = True

    def fit(self, samples: list[Sample], since: datetime, now: datetime) -> None:
        old = [s for s in samples if s.start < since]
        new = [s for s in samples if since <= s.start <= now]
        self.new_samples = len(new)
        base = AdaptiveComponentModel(tz=self.tz, window_days=self.window_days)
        base.fit(old, now=since)
        self.base = base if base.ready else None
        if self.base is not None:
            residuals = [s.kwh - self.base.predict(s.start, s.temp_c).kwh for s in new]
            if residuals:
                self.shift_kwh = sum(residuals) / len(residuals)
                spread = math.sqrt(
                    sum((r - self.shift_kwh) ** 2 for r in residuals) / len(residuals)
                )
                self.extra_kwh = spread + abs(self.shift_kwh)
        elif new:
            values = [s.kwh for s in new]
            self._flat = (sum(values) / len(values), min(values), max(values))

    @property
    def ready(self) -> bool:
        return self.base is not None or self._flat is not None

    @property
    def half_life_days(self) -> float:
        return self.base.half_life_days if self.base else DEFAULT_HALF_LIFE_DAYS

    @property
    def uses_temperature(self) -> bool:
        return self.base.uses_temperature if self.base else False

    @property
    def slope_kwh_per_c(self) -> float:
        return self.base.slope_kwh_per_c if self.base else 0.0

    @property
    def fitted_at(self) -> datetime | None:
        return self.base.fitted_at if self.base else None

    def predict(self, start: datetime, temp_c: float | None = None) -> Prediction:
        if self.base is not None:
            p = self.base.predict(start, temp_c)
            kwh = max(p.kwh + self.shift_kwh, 0.0)
            return Prediction(
                kwh=kwh,
                p10=max(min(p.p10 + self.shift_kwh, kwh) - self.extra_kwh, 0.0),
                p90=max(p.p90 + self.shift_kwh, kwh) + self.extra_kwh,
            )
        if self._flat is not None:
            mean, lo, hi = self._flat
            return Prediction(kwh=mean, p10=lo, p90=hi)
        return Prediction(0.0, 0.0, 0.0)


def _weighted_quantile(values: list[tuple[float, float]], q: float) -> float:
    """Weighted quantile of (value, weight) pairs."""
    pairs = sorted((v, w) for v, w in values if w > 0)
    if not pairs:
        return 0.0
    total = sum(w for _, w in pairs)
    target = q * total
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= target:
            return v
    return pairs[-1][0]
