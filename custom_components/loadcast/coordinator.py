"""Data coordinator: reads recorder statistics, fits models, builds forecasts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BASE_COMPONENT,
    COMPUTED_KEYS,
    COMPUTED_SIGNS,
    CONF_CHANGE_MARKERS,
    CONF_HORIZON_HOURS,
    CONF_LOAD_MODE,
    CONF_SUBLOAD_INCLUDE,
    CONF_SUBLOAD_NAME,
    CONF_SUBLOAD_PREVIOUS,
    CONF_SUBLOAD_SENSOR,
    CONF_SUBLOADS,
    CONF_TEMPERATURE,
    CONF_TOTAL,
    CONF_WEATHER,
    CONF_WINDOW_DAYS,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    LOAD_MODE_COMPUTED,
    UPDATE_INTERVAL,
)
from .model import (
    AdaptiveComponentModel,
    Prediction,
    Sample,
    TransitionModel,
    detect_meter_lag,
    hand_over,
    reconcile_residual,
    shift_meter,
)

_LOGGER = logging.getLogger(__name__)

# Extra history beyond the model window: the adaptive model replays the last
# few days, and accuracy reporting needs a week.
HISTORY_MARGIN_DAYS = 8
# Hours of issued forecasts kept for forecast-versus-outcome charts.
PAST_HOURS = 48
PAST_STORE_VERSION = 1
POWER_UNITS = {u.value for u in UnitOfPower}

type LoadCastConfigEntry = ConfigEntry["LoadCastCoordinator"]


@dataclass
class HourForecast:
    """Forecast for one hour, summed over the included components."""

    start: datetime
    kwh: float
    p10: float
    p90: float
    components: dict[str, Prediction] = field(default_factory=dict)


@dataclass
class ComponentInfo:
    """What the sensors expose about each fitted component."""

    name: str
    included: bool
    ready: bool
    half_life_days: float
    uses_temperature: bool
    slope_kwh_per_c: float
    samples: int
    changed_since: datetime | None = None
    uncertain: bool = False
    new_samples: int | None = None
    meter_lag_hours: int = 0


@dataclass
class LoadCastData:
    """Snapshot published to entities."""

    hours: list[HourForecast]
    components: dict[str, ComponentInfo]
    fitted_at: datetime | None
    ready: bool
    uncertain_components: list[str] = field(default_factory=list)
    accuracy_yesterday_pct: float | None = None
    bias_7d_kwh: float | None = None
    error_days: list[dict[str, Any]] = field(default_factory=list)
    past: list[HourForecast] = field(default_factory=list)


def _meters(subload: dict[str, Any]) -> list[str]:
    """A sub-load's meters, oldest first, ending with the current one."""
    return [*subload.get(CONF_SUBLOAD_PREVIOUS, []), subload[CONF_SUBLOAD_SENSOR]]


class LoadCastCoordinator(DataUpdateCoordinator[LoadCastData]):
    """Fit once per new hour of statistics, forecast every update."""

    config_entry: LoadCastConfigEntry

    def __init__(self, hass: HomeAssistant, entry: LoadCastConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._models: dict[str, AdaptiveComponentModel | TransitionModel] = {}
        self._series: dict[str, list[Sample]] = {}
        self.meter_lags: dict[str, int] = {}
        self._last_fit_hour: datetime | None = None
        self._accuracy: tuple[float | None, float | None, list[dict[str, Any]]] = (
            None,
            None,
            [],
        )
        # Each hour's forecast as it stood when the hour began (the first
        # update in the hour), kept across restarts.
        self._past: dict[datetime, HourForecast] = {}
        self._past_store: Store[list[dict[str, Any]]] = Store(
            hass, PAST_STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.past"
        )
        self._past_loaded = False

    # ----------------------------------------------------------- config

    @property
    def _conf(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def subloads(self) -> list[dict[str, Any]]:
        return list(self._conf.get(CONF_SUBLOADS, []))

    @property
    def window_days(self) -> int:
        return int(self._conf.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS))

    @property
    def horizon_hours(self) -> int:
        return int(self._conf.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS))

    @property
    def change_markers(self) -> dict[str, datetime]:
        """Per-component change markers (UTC); history before them is ignored."""
        out: dict[str, datetime] = {}
        for name, value in (self._conf.get(CONF_CHANGE_MARKERS) or {}).items():
            parsed = dt_util.parse_datetime(str(value))
            if parsed is not None:
                out[name] = dt_util.as_utc(parsed)
        return out

    def _total_ids(self) -> list[str]:
        conf = self._conf
        if conf.get(CONF_LOAD_MODE) == LOAD_MODE_COMPUTED:
            return [conf[k] for k in COMPUTED_KEYS if conf.get(k)]
        return [conf[CONF_TOTAL]]

    # ----------------------------------------------------------- update

    async def _async_update_data(self) -> LoadCastData:
        now = dt_util.now()
        hour = now.replace(minute=0, second=0, microsecond=0)

        if self._last_fit_hour != hour:
            await self._async_refit(now)
            self._last_fit_hour = hour

        temps = await self._async_forecast_temperatures(hour)
        data = self._build(hour, temps)
        await self._async_record_past(hour, data)
        return data

    async def _async_record_past(self, hour: datetime, data: LoadCastData) -> None:
        """Keep this hour's forecast as issued at its start, publish the last 48 h."""
        if not self._past_loaded:
            self._past_loaded = True
            for row in await self._past_store.async_load() or []:
                start = dt_util.parse_datetime(str(row.get("start", "")))
                if start is not None:
                    self._past[dt_util.as_utc(start)] = HourForecast(
                        start=dt_util.as_utc(start),
                        kwh=float(row["kwh"]),
                        p10=float(row["p10"]),
                        p90=float(row["p90"]),
                    )
        hour_u = dt_util.as_utc(hour)
        if data.hours and hour_u not in self._past:
            first = data.hours[0]
            self._past[hour_u] = HourForecast(
                start=first.start, kwh=first.kwh, p10=first.p10, p90=first.p90
            )
            cutoff = hour_u - timedelta(hours=PAST_HOURS)
            self._past = {t: h for t, h in self._past.items() if t >= cutoff}
            self._past_store.async_delay_save(self._past_rows, 60)
        data.past = [self._past[t] for t in sorted(self._past)]

    async def async_flush_past(self) -> None:
        """Write the past forecasts now, cancelling the delayed save."""
        if self._past_loaded:
            await self._past_store.async_save(self._past_rows())

    def _past_rows(self) -> list[dict[str, Any]]:
        return [
            {"start": h.start.isoformat(), "kwh": h.kwh, "p10": h.p10, "p90": h.p90}
            for h in self._past.values()
        ]

    async def _async_refit(self, now: datetime) -> None:
        """Load statistics and fit one model per component."""
        full = await self._async_load_series(now)
        markers = self.change_markers
        # A marked component forgets everything before its change: the model
        # starts over on the new level instead of averaging it with the old
        # one. Until it has a day of new data it is forecast by a
        # TransitionModel (old shape, new level, wider band, flagged
        # uncertain) so the household forecast stays available.
        self._series = {
            name: [s for s in samples if s.start >= markers[name]]
            if name in markers
            else samples
            for name, samples in full.items()
        }
        tz = dt_util.get_default_time_zone()

        def _fit() -> tuple[dict[str, AdaptiveComponentModel | TransitionModel], tuple]:
            models: dict[str, AdaptiveComponentModel | TransitionModel] = {}
            for name, samples in self._series.items():
                m = AdaptiveComponentModel(tz=tz, window_days=self.window_days)
                m.fit(samples, now=now)
                if not m.ready and name in markers:
                    t = TransitionModel(tz=tz, window_days=self.window_days)
                    t.fit(full[name], since=markers[name], now=now)
                    if t.ready:
                        models[name] = t
                        continue
                if name in markers:
                    m.new_samples = len(samples)
                models[name] = m
            return models, self._compute_accuracy(now, tz)

        self._models, self._accuracy = await self.hass.async_add_executor_job(_fit)

    async def _async_load_series(self, now: datetime) -> dict[str, list[Sample]]:
        """Read hourly statistics and turn them into per-component samples."""
        start = dt_util.start_of_local_day(now) - timedelta(
            days=self.window_days + HISTORY_MARGIN_DAYS
        )
        conf = self._conf
        total_ids = self._total_ids()
        sub_ids = [m for s in self.subloads for m in _meters(s)]
        temp_id = conf.get(CONF_TEMPERATURE)
        ids = set(total_ids) | set(sub_ids) | ({temp_id} if temp_id else set())

        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            now,
            ids,
            "hour",
            {
                "energy": UnitOfEnergy.KILO_WATT_HOUR,
                "power": UnitOfPower.WATT,
                "temperature": UnitOfTemperature.CELSIUS,
            },
            {"change", "mean"},
        )

        def hourly(stat_id: str) -> dict[datetime, float]:
            """kWh per hour for an energy (change) or power (mean W) statistic."""
            out: dict[datetime, float] = {}
            for row in stats.get(stat_id, []):
                t = dt_util.utc_from_timestamp(row["start"])
                if row.get("change") is not None:
                    out[t] = float(row["change"])
                elif row.get("mean") is not None:
                    # Power statistic: hourly mean W equals Wh in that hour.
                    out[t] = float(row["mean"]) / 1000
            return out

        temps: dict[datetime, float] = {}
        if temp_id:
            for row in stats.get(temp_id, []):
                if row.get("mean") is not None:
                    temps[dt_util.utc_from_timestamp(row["start"])] = float(row["mean"])

        # Total load per hour
        if conf.get(CONF_LOAD_MODE) == LOAD_MODE_COMPUTED:
            parts = {k: hourly(conf[k]) for k in COMPUTED_KEYS if conf.get(k)}
            hours_with_all = set.intersection(*(set(p) for p in parts.values()))
            total = {
                t: sum(COMPUTED_SIGNS[k] * parts[k][t] for k in parts)
                for t in hours_with_all
            }
        else:
            total = hourly(conf[CONF_TOTAL])

        # A meter that reports late (Zaptec: ~1 h) shows more than the total in
        # the hour after the load; it is moved back to when the load ran, otherwise the base load gets a false peak in the
        # charging hour and negative hours after it. Each meter of a replaced
        # sub-load is aligned on its own before the hand-over: an old and a new
        # meter can be late by different amounts.
        self.meter_lags = {}
        subs: dict[str, dict[datetime, float]] = {}
        for s in self.subloads:
            merged: dict[datetime, float] | None = None
            lag = 0
            for meter in _meters(s):
                readings = hourly(meter)
                lag = detect_meter_lag(total, readings)
                readings = shift_meter(readings, lag)
                merged = (
                    readings if merged is None else hand_over(merged, readings)
                )
            subs[s[CONF_SUBLOAD_NAME]] = merged or {}
            self.meter_lags[s[CONF_SUBLOAD_NAME]] = lag  # the current meter's

        series: dict[str, list[Sample]] = {BASE_COMPONENT: []}
        for name in subs:
            series[name] = []
        hours: list[datetime] = []
        residual: list[float] = []
        for t in sorted(total):
            if any(t not in sub for sub in subs.values()):
                continue  # keep components aligned hour by hour
            temp = temps.get(t)
            sub_sum = 0.0
            for name, sub in subs.items():
                v = max(sub[t], 0.0)
                sub_sum += v
                series[name].append(Sample(t, v, temp))
            hours.append(t)
            residual.append(total[t] - sub_sum)
        # Base load = what is left; negative hours borrow from their
        # neighbours instead of being clipped, so no energy is invented.
        for t, v in zip(hours, reconcile_residual(hours, residual), strict=False):
            series[BASE_COMPONENT].append(Sample(t, v, temps.get(t)))
        return series

    async def _async_forecast_temperatures(
        self, hour: datetime
    ) -> dict[datetime, float]:
        """Hourly temperatures from the weather entity, keyed by UTC hour."""
        weather = self._conf.get(CONF_WEATHER)
        if not weather or self.hass.states.get(weather) is None:
            return {}
        try:
            resp = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - forecast is optional
            _LOGGER.debug("Weather forecast unavailable: %s", err)
            return {}
        out: dict[datetime, float] = {}
        for item in (resp or {}).get(weather, {}).get("forecast", []):
            t = dt_util.parse_datetime(item.get("datetime", ""))
            temp = item.get("temperature")
            if t is not None and temp is not None:
                out[dt_util.as_utc(t).replace(minute=0, second=0, microsecond=0)] = (
                    float(temp)
                )
        return out

    # ----------------------------------------------------------- results

    def _included(self) -> dict[str, bool]:
        inc = {BASE_COMPONENT: True}
        for s in self.subloads:
            inc[s[CONF_SUBLOAD_NAME]] = bool(s.get(CONF_SUBLOAD_INCLUDE, True))
        return inc

    def _fallback_temp(self) -> float | None:
        """Latest measured temperature, used where the weather forecast ends."""
        samples = self._series.get(BASE_COMPONENT) or []
        for s in reversed(samples):
            if s.temp_c is not None:
                return s.temp_c
        return None

    def _build(self, hour: datetime, temps: dict[datetime, float]) -> LoadCastData:
        included = self._included()
        fallback = self._fallback_temp()
        hours: list[HourForecast] = []
        for i in range(self.horizon_hours):
            start = dt_util.as_utc(hour) + timedelta(hours=i)
            temp = temps.get(start, fallback)
            preds = {n: m.predict(start, temp) for n, m in self._models.items()}
            inc = [p for n, p in preds.items() if included.get(n, True)]
            hours.append(
                HourForecast(
                    start=start,
                    kwh=sum(p.kwh for p in inc),
                    # Summing per-component quantiles overstates the spread a
                    # little (components are not perfectly correlated). That
                    # errs on the honest side for a planner.
                    p10=sum(p.p10 for p in inc),
                    p90=sum(p.p90 for p in inc),
                    components=preds,
                )
            )

        components = {
            n: ComponentInfo(
                name=n,
                included=included.get(n, True),
                ready=m.ready,
                half_life_days=m.half_life_days,
                uses_temperature=m.uses_temperature,
                slope_kwh_per_c=m.slope_kwh_per_c,
                samples=len(self._series.get(n, [])),
                changed_since=self.change_markers.get(n),
                uncertain=m.uncertain,
                new_samples=m.new_samples,
                meter_lag_hours=self.meter_lags.get(n, 0),
            )
            for n, m in self._models.items()
        }
        acc, bias, days = self._accuracy
        return LoadCastData(
            hours=hours,
            components=components,
            fitted_at=self._models[BASE_COMPONENT].fitted_at
            if BASE_COMPONENT in self._models
            else None,
            ready=bool(self._models) and all(m.ready for m in self._models.values()),
            uncertain_components=[
                n
                for n, m in self._models.items()
                if m.uncertain and included.get(n, True)
            ],
            accuracy_yesterday_pct=acc,
            bias_7d_kwh=bias,
            error_days=days,
        )

    def _compute_accuracy(
        self, now: datetime, tz
    ) -> tuple[float | None, float | None, list[dict[str, Any]]]:
        """Replay the last 7 days: day-ahead forecast vs actual (included loads).

        Each day is forecast by a model fitted only on data before that day's
        midnight, using the measured temperatures (a perfect weather forecast),
        so this measures the model itself rather than the weather service.
        """
        included = self._included()
        today = dt_util.start_of_local_day(now)
        days: list[dict[str, Any]] = []
        for back in range(7, 0, -1):
            day = today - timedelta(days=back)
            day_end = day + timedelta(days=1)
            forecast = actual = 0.0
            complete = True
            for name, samples in self._series.items():
                if not included.get(name, True):
                    continue
                day_samples = [s for s in samples if day <= s.start < day_end]
                if len(day_samples) < 23:  # 23 on DST days
                    complete = False
                    break
                m = AdaptiveComponentModel(tz=tz, window_days=self.window_days)
                m.fit(samples, now=day)
                if not m.ready:
                    complete = False
                    break
                forecast += sum(m.predict(s.start, s.temp_c).kwh for s in day_samples)
                actual += sum(s.kwh for s in day_samples)
            if complete and actual > 0:
                days.append(
                    {
                        "date": day.date().isoformat(),
                        "forecast_kwh": round(forecast, 2),
                        "actual_kwh": round(actual, 2),
                    }
                )
        if not days:
            return None, None, []
        last = days[-1]
        acc = round(
            100 * abs(last["forecast_kwh"] - last["actual_kwh"]) / last["actual_kwh"], 1
        )
        bias = round(
            sum(d["forecast_kwh"] - d["actual_kwh"] for d in days) / len(days), 2
        )
        return acc, bias, days

    # ----------------------------------------------------------- queries

    def energy_between(
        self,
        start: datetime,
        end: datetime,
        percentile: int = 50,
        components: list[str] | None = None,
    ) -> float:
        """Forecast kWh between two instants, pro-rating partial hours."""
        if self.data is None or end <= start:
            return 0.0
        start_u, end_u = dt_util.as_utc(start), dt_util.as_utc(end)
        total = 0.0
        for h in self.data.hours:
            h_end = h.start + timedelta(hours=1)
            overlap = (min(h_end, end_u) - max(h.start, start_u)).total_seconds()
            if overlap <= 0:
                continue
            frac = overlap / 3600
            if components:
                preds = [p for n, p in h.components.items() if n in components]
            else:
                preds = None
            if preds is None:
                value = {10: h.p10, 90: h.p90}.get(percentile, h.kwh)
            else:
                value = sum(
                    {10: p.p10, 90: p.p90}.get(percentile, p.kwh) for p in preds
                )
            total += value * frac
        return round(total, 3)
