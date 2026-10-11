# LoadCast – specification (draft v0.1)

LoadCast is a Home Assistant custom integration that forecasts **household
electricity consumption** for the next 24–48 hours, learned from the home's own
recorded history. It does for load what Solcast does for solar and Nordpool
does for price: it publishes a forecast in a format other automations can
consume. It does **not** control anything.

## 1. Problem

Battery planners, EV charging schedulers and heat-pump automations all need to
know how much the house will draw over the coming hours. Typical approaches are
hard-coded constants or hand-tuned temperature regressions. They break silently
when the house changes: a heat pump fails, the pool is shut for the season, a
new appliance is added. Corrections are usually asymmetric ("never lower the
estimate too fast") which makes them unable to learn that consumption went
*down*.

Motivating incident (2026-10-11): the heat pump was out of service, real
evening consumption was ~17 kWh, the regression-based forecast assumed ~48 kWh.
A battery planner held a 94 % battery idle and bought grid power for hours.

## 2. Goals

- Learn the load profile from the recorder – no manual model parameters.
- Learn **symmetrically**: adapt down as quickly as up. Conservatism belongs in
  the consumer's decision margins, not in the estimate.
- Model sub-loads (heat pump, EV charger, pool, …) separately when the user has
  meters for them, so a sub-load switching off is learned in days, not weeks.
- Temperature-aware, using the weather forecast for future hours.
- Publish an honest uncertainty band and continuously report its own accuracy.
- Run on every install type (Container, Core, OS) – no add-on/app required.
- Zero external services, no cloud, no heavy ML dependencies.

## 3. Non-goals

- Battery, EV or heat-pump control. LoadCast only forecasts.
- Solar or price forecasting (use Solcast/Forecast.Solar/Nordpool).
- Appliance-level disaggregation from a single meter (NILM).

## 4. Prior art

| Project | Relation |
|---|---|
| EMHASS | Full optimiser with an ML load forecaster (skforecast). Heavy; the forecast is not usable standalone. |
| Predbat | Battery planner that derives load from previous days internally. Not reusable outside Predbat. |
| ML Forecast Lab | Generic sensor forecasting as an HA app (add-on) – unavailable on Container/Core installs. |

LoadCast fills the gap: a small native integration that only produces the load
forecast and is easy to consume from templates and other planners.

## 5. Configuration (config flow)

**Step 1 – Total load source**, one of:
- *Consumption sensor*: an energy sensor (kWh, `total_increasing`) measuring
  total household consumption.
- *Computed*: grid import, grid export, solar production, battery charge,
  battery discharge (all kWh). Load = import − export + solar + discharge − charge.

**Step 2 – Sub-loads (optional, repeatable)**: name + energy sensor. Each
sub-load is modelled separately and subtracted from the total; the remainder is
modelled as *base load*. Examples: heat pump circuit, EV charger, pool pump.
Per sub-load the user can mark it as **excluded from the total forecast**
(e.g. EV charging that a separate scheduler plans).

**Step 3 – Temperature**: outdoor temperature sensor (history) and a `weather`
entity (hourly forecast via `weather.get_forecasts`). Temperature is optional;
without it the model is purely time-of-day based.

**Options**: history window (default 28 days), recency half-life (default
7 days), resolution (15 min / 60 min), forecast horizon (24 / 48 h).

## 6. Model (v1)

Deliberately simple, explainable and testable. One model per component
(base load + each sub-load), summed for the total.

- **Data**: hourly energy deltas from recorder long-term statistics
  (`statistics_during_period`, `change`), plus hourly mean outdoor temperature.
  Long-term statistics exist for any `total_increasing` sensor and survive the
  recorder purge.
- **Slots**: hour of day × day type (weekday / weekend). Configurable later.
- **Fit per slot**: weighted least squares `kWh = a + b · T` over the history
  window, weights `w = 0.5^(age_days / half_life)`. If temperature variance in
  the window is too small to estimate `b` reliably, fall back to `b = 0`
  (weighted mean). `b` is shrunk towards the component's all-day slope to
  avoid noisy per-hour slopes.
- **Clamp**: predictions ≥ 0.
- **Regime changes**: the recency half-life makes a sub-load that stops
  (heat pump failure) or starts dominate within a few days. A service
  `loadcast.reset_learning` (component, from date) discards history before a
  known change.
- **Uncertainty**: per slot, weighted residual quantiles → P10 / P90.
- **15-min output**: hourly prediction spread evenly over its quarters (v1).

Upgrade path (v2+): quarter-hour statistics from short-term stats, holiday
calendar, gradient-boosted model behind the same interface. Only if v1 accuracy
data shows a need.

## 7. Outputs

- `sensor.loadcast_<name>_forecast` – state: forecast kWh for the next 24 h.
  Attribute `forecast`: list of
  `{period_start, kwh, kwh_p10, kwh_p90}` at the configured resolution, same
  shape as Solcast's `detailedForecast` so existing template patterns carry over.
  Attribute `components`: same list per component.
- `sensor.loadcast_<name>_remaining_today` – forecast kWh from now until midnight.
- Service `loadcast.get_forecast` (returns response): `start`, `end`,
  optional `components`, optional `percentile` → kWh. Lets a planner ask
  "how much until the evening peak at 19:30?" without template loops.
- Diagnostics sensors: `accuracy_yesterday` (MAPE %), `bias_7d` (kWh/day,
  signed), `last_fit`. Daily forecast-vs-actual is the integration's own
  test in production.

## 8. Runtime

- Refit nightly at 00:05 and on options change (`DataUpdateCoordinator`).
- Recompute forecast every 15 min (weather forecast and current hour change).
- Fit in the executor; for 28 days × few components it is milliseconds.
- No writes to the recorder beyond the integration's own entities.

## 9. Quality

- `pytest-homeassistant-custom-component`; model code is pure Python and
  unit-tested without HA.
- Frozen real-world datasets (CSV of hourly statistics) as regression fixtures,
  including the 2026-10-11 heat-pump-outage case: the forecast must converge to
  the new level within 3 days.
- CI: hassfest, HACS validation, ruff, pytest.

## 10. Milestones

1. Model library + fixtures + tests (no HA).
2. Integration skeleton: config flow (consumption-sensor mode), forecast sensor.
3. Sub-loads, computed-load mode, temperature + weather forecast.
4. `get_forecast` service, accuracy sensors, diagnostics.
5. HACS release, README with consumption examples (battery planner, EV).
6. Migrate the author's battery planner to consume LoadCast.

## 11. Open questions

- License (MIT suggested for an HA integration).
- Should EV charging be forecast at all, or only offered as an exclusion?
- Minimum history before the forecast is published (proposal: 3 days, with
  `available` false and a repair issue until then).
