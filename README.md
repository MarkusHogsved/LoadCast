<p align="center"><img src="assets/icon.svg" width="128" alt="LoadCast"></p>

# LoadCast

**Household load forecasting for Home Assistant** – learned from your own
history, temperature-aware, and honest about its own accuracy.

LoadCast does for consumption what Solcast does for solar: it publishes a
24–48 h forecast of how much electricity your home will use, so battery
planners, EV schedulers and other automations can make better decisions.
It never controls anything itself.

## Why

Most home-energy automations estimate future consumption with fixed constants
or hand-tuned regressions. They drift silently when the house changes – a heat
pump fails, the pool closes for the season – and the resulting plans buy
energy that is never needed.

LoadCast learns the real profile continuously, per sub-load, and **chooses its
own memory length from outcomes**: every hour it replays the last few days with
several candidate memory lengths and uses the one that would have forecast
best. A stable house gets a steady, long-memory forecast; when a load changes
level, short memory wins and the forecast follows within days – in both
directions.

On the author's house (28 days of real data, see `tests/backtest.py`):

| | Daily forecast error (MAPE) |
|---|---|
| LoadCast | **11.3 %** |
| Hand-tuned temperature regression it replaces | 19.2 % |

The regression kept planning ~30 kWh/day for a heat pump that had failed;
LoadCast learned the new level within days.

## Features

- Learns from recorder long-term statistics – nothing to tune
- Total consumption from one sensor (kWh or W), or computed from grid,
  solar and battery flows
- Separate models for metered sub-loads (heat pump, EV charger, pool …);
  each can be excluded from the total (e.g. EV charging you plan elsewhere)
- Outdoor temperature from history, hourly weather forecast for the future
- P10 / P90 uncertainty band
- Daily forecast-vs-actual accuracy sensor
- `loadcast.get_forecast` service for "how much until …?" questions
- Native integration: works on Container, Core and OS installs, no add-on

## Installation

1. HACS → ⋮ → *Custom repositories* → `https://github.com/MarkusHogsved/LoadCast`,
   type *Integration*.
2. Install **LoadCast**, restart Home Assistant.
3. *Settings → Devices & services → Add integration → LoadCast*.

LoadCast needs a few days of long-term statistics for the sensors you pick
(any `total_increasing` energy sensor or `measurement` power sensor has them).
It uses up to 28 days by default.

## Configuration

**Setup** asks for:

- *Total consumption* – one consumption sensor, **or** grid import / export,
  solar production and battery charge / discharge (load = import − export +
  solar + discharge − charge).
- *Temperature* (optional) – outdoor temperature sensor and a `weather` entity.

**Options** (Configure on the integration):

- *Add a metered sub-load* – name + energy sensor, and whether it counts in
  the total. The base load is total minus all sub-loads.
- *Model settings* – history window, forecast horizon.

## Entities

| Entity | Meaning |
|---|---|
| `sensor.loadcast_forecast_next_24_h` | kWh over the next 24 h. Attribute `forecast` holds the hourly forecast (`period_start`, `kwh`, `kwh_p10`, `kwh_p90`); `components` shows each model's memory, temperature slope and 24 h total. |
| `sensor.loadcast_forecast_rest_of_today` | kWh from now until midnight |
| `sensor.loadcast_forecast_next_hour` | kWh over the next 60 minutes |
| `sensor.loadcast_forecast_error_yesterday` | Diagnostic: yesterday's day-ahead error in %, attributes `bias_7d_kwh` and the last 7 days' forecast vs actual |

## Using the forecast

Energy needed until the evening peak, in a script or automation:

```yaml
- action: loadcast.get_forecast
  data:
    end: "{{ today_at('19:30') }}"
    percentile: 50        # 10 = optimistic, 90 = cautious
  response_variable: load
- variables:
    kwh_until_peak: "{{ load.kwh }}"
```

Roughly the same in a template, from the forecast attribute (whole hours –
the service pro-rates partial hours and is the precise way):

```jinja
{% set peak = today_at('19:30') %}
{{ state_attr('sensor.loadcast_forecast_next_24_h', 'forecast')
   | selectattr('period_start', 'lt', peak.isoformat())
   | map(attribute='kwh') | sum | round(1) }}
```

Use the P90 value where running short is expensive (a battery reserve),
and P50 where you want the expected outcome.

## Development

```bash
pip install -r requirements_test.txt
pytest -q                    # unit + end-to-end tests with a real recorder
python -m tests.backtest     # rolling backtest on the real-house fixture
```

Design: [SPEC.md](SPEC.md).

## License

MIT – see [LICENSE](LICENSE).
