# CLAUDE.md – LoadCast

Context for AI-assisted development. Written 2026-10-11 as a handover from
the claude.ai project where LoadCast was designed and built (all in one day).
The owner, Markus Högsved (`MarkusHogsved`), communicates in **Swedish**;
code, docs, commit messages and UI strings stay in English, with Swedish
translations in `translations/sv.json`.

Read `SPEC.md` (design), `README.md` (user docs) and `CHANGELOG.md` first.

## What LoadCast is

LoadCast is a Home Assistant custom integration (HACS) that forecasts
household electricity consumption for the next 24–48 h. It learns from the
recorder's long-term statistics and publishes the forecast in a
Solcast-like shape. It never controls anything.

Its first consumer is Markus' battery automations
(`MarkusHogsved/HA-EnergyAutomations`: `ladda_vakthund.yaml`,
`laddning_vakthund.yaml`). Those read the attribute
`sensor.loadcast_forecast_next_24_h.forecast` with `state_attr()` and
pro-rate it between two times. **Do not change that attribute's shape
(`period_start`, `kwh`, `kwh_p10`, `kwh_p90`) or the entity id without a
migration plan for those automations.**

## Why it exists (the incident)

On 2026-10-11 the heat pump had been broken since about 2026-09-23. The
hand-tuned temperature regression used by the battery planner still assumed
~70 kWh/day. Its self-calibration could not lower a factor fast enough
(asymmetric by design). Real consumption was ~44 kWh. The planner held a
94 % battery idle in Battery First and bought grid power for hours.

Design consequence: **learn symmetrically, and put conservatism in the
consumer's margins, not in the estimate.**

## Layout

```
custom_components/loadcast/
  model.py        pure Python, no HA imports: Sample, Prediction,
                  ComponentModel, AdaptiveComponentModel
  coordinator.py  recorder statistics -> samples, refit hourly, weather
                  forecast, energy_between(), 7-day accuracy replay
  sensor.py       4 sensors (next 24 h, rest of today, next hour, error)
  __init__.py     setup, loadcast.get_forecast service (response),
                  ConfigEntryError for placeholder entries from 0.0.x
  config_flow.py  user -> sensor|computed -> temperature;
                  options menu: add_subload / remove_subload / settings
  brand/          icon.png, icon@2x.png (HA 2026.3+ local brand images)
tests/
  conftest.py     autouse recorder_mock + enable_custom_integrations
  test_model.py, test_config_flow.py, test_init.py (end-to-end with a real
  recorder: imports statistics, sets up the entry, checks sensors + service)
  backtest.py     rolling day-ahead backtest on the real-house fixture
  fixtures/house_2026-09-14_2026-10-11.csv  hourly total/heatpump/ev/temp
assets/           icon.svg + PNG renders
.github/workflows validate.yml (hassfest, HACS action, ruff, pytest,
                  backtest), release.yml (see Releases)
```

## Model (v0.1.x)

- One model per component: base load = total − all sub-loads, plus each
  metered sub-load. Total = sum of components with `include_in_total`.
- Hourly slots: hour of day × weekday/weekend, kernel-weighted across
  neighbouring hours. Recency weights `0.5^(age_days / half_life)`.
- Weighted least squares `kWh = a + b·T` per slot, with the slope shrunk
  towards the component's all-day slope. Predictions are clamped at ≥ 0.
- **AdaptiveComponentModel** (the key idea). Every fit replays the last 3
  days day-ahead with candidate half-lives (0.75, 1.5, 3, 7, 14 days),
  each with and without temperature.
  - It prefers temperature on, then the longest memory, among candidates
    within tolerance `max(5 % of best error, 2 % of mean daily energy)`.
  - The absolute 2 % term was added after short memory won on pure noise.
  - Temperature-off was added in 0.1.1 to kill a spurious EV temperature
    slope. EV charging coincided with cold days.
  - Exposed as `half_life_days`, `uses_temperature`, `fitted_at` per
    component in the `components` attribute.
- P10/P90 from weighted residual quantiles.
- Backtest (28 days, real house): **10.7 % daily MAPE** versus 19.2 % for
  the regression it replaces. It follows the heat-pump drop within days.

## Data sources (coordinator)

- `recorder.statistics.statistics_during_period`, hourly:
  - `change` for kWh sensors (`total_increasing`)
  - `mean` for W sensors
  - unit handling for kWh/Wh/W/kW and °C
- Future temperature from `weather.get_forecasts` (hourly).
- Refit at most once per hour, forecast recomputed on each coordinator
  update.

## Markus' live install (for debugging and examples)

- HA Container 2026.10.1. Entry `01M4NPA1FK9Q00C4NCJGX2YC8Q`.
- Options:
  - `window_days` 28, `horizon_hours` 48
  - sub-loads "Värmepump" (`sensor.shellypro3em_34987a454ed8_total_active_energy`,
    in total) and "Elbil" (`sensor.zappy_energy_meter`, **excluded** from
    the total)
- Total load comes from the Growatt inverter's total load power.
- Heat pump repair is planned for 2026-10-13. That is a good live test that
  the adaptive memory picks up the increase within a couple of days.

## Development

A local venv with `requirements_test.txt` is needed:
pytest-homeassistant-custom-component, which pins a HA version, plus ruff.

```bash
pip install -r requirements_test.txt
pytest -q                 # 20 tests at 0.1.2
python -m tests.backtest  # prints MAPE per model
ruff check . && ruff format --check .
```

Gotchas found so far:

- Config-flow tests need the recorder. Hence `recorder_mock` is autouse in
  `conftest.py`, otherwise you get a `KeyError` on recorder data.
- Options forms must use `NumberSelector` / `SelectSelector` with defaults
  from current options, or the UI shows empty fields (fixed in 0.1.2).
- Sub-loads added through the options UI were once not saved. Verify with
  the entry's options after changing the flow.
- HACS validation needs `hacs.json` and the manifest in
  `custom_components/loadcast/`. Brand checks are ignored in CI (local
  `brand/` images are used).

## Releases

- Bump `version` in `custom_components/loadcast/manifest.json` and add a
  `## x.y.z` section to `CHANGELOG.md` in the same commit.
- `release.yml` creates the tag and the GitHub release (notes from that
  CHANGELOG section) when the manifest version changes on `main`.
- Pushing tags directly from the earlier automation environment was
  blocked (403), which is why releases go through the workflow.
- Released: 0.1.0, 0.1.1, 0.1.2. 0.1.3 adds `loadcast.mark_change`.
- Commits have been authored as `Markus Hogsved <markus.hogsved@gmail.com>`.

## Open items / ideas

- Diagnostics download (SPEC milestone 4).
- Pool as a metered sub-load (pump/heater switches, about 2 kW each). It is
  currently handled outside LoadCast in the automations.
- Minimum history before publishing (proposal: 3 days, `ready: false` plus
  a repair issue).
- Done in 0.1.3: `loadcast.mark_change` (component, optional `since`) for
  known changes. Markers live in the entry options (`change_markers`);
  history before a marker is dropped for that component, which is then not
  ready (and neither is the total) until it has 24 hourly samples. Markus'
  HA side calls it from `loadcast_varmepump_andring.yaml` when the heat pump
  starts while LoadCast still forecasts it as off.
- v2 ideas only if accuracy data asks for it: quarter-hour statistics,
  holiday calendar, gradient boosting behind the same interface.
- Keep it small: no cloud, no heavy ML dependencies, works on Container/Core/OS.
