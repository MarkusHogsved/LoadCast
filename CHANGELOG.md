# Changelog

## 0.1.3

- New service `loadcast.mark_change` (component, optional `since`): forget a
  component's history before a known change – a repaired heat pump, a new
  appliance – and relearn from the new level, instead of waiting a couple of
  days for the adaptive memory. Until the component has 24 hours of new data
  the forecast reports `ready: false`, so consumers use their own fallback.
- New attribute `changed_since` per component on the 24 h forecast sensor.

## 0.1.2

- Model settings form now shows the current values: history window as a
  number box (days) and a pre-selected forecast horizon. Short explanation
  added to the form.

## 0.1.1

- Temperature dependence is now chosen per component from outcomes, like the
  memory length: each fit replays the last days with and without temperature
  and drops a slope that makes the forecasts noticeably worse. A load that
  only happened to coincide with the weather (typically EV charging) loses its
  spurious slope; heating keeps its real one. No new setting needed.
- New attribute `uses_temperature` per component on the 24 h forecast sensor.
- Backtest on the real-house data improves from 11.3 % to 10.7 % daily MAPE.

## 0.1.0

First functional release.

- Learns household consumption from recorder long-term statistics – nothing to tune.
- Total load from one sensor (kWh or W), or computed from grid, solar and battery flows.
- Metered sub-loads (heat pump, EV charger, pool …) each get their own model and can be excluded from the total.
- **Adaptive memory:** every hour the model replays the last days with several memory lengths and uses the one that would have forecast best, so a load that changes level is learned within days – in both directions.
- Temperature-aware; uses the hourly forecast from a `weather` entity for future hours.
- Sensors: forecast next 24 h (Solcast-style hourly `forecast` attribute with P10/P90 and per-component detail), rest of today, next hour, and yesterday's forecast error.
- `loadcast.get_forecast` service: forecast kWh between two times, with partial-hour pro-rating.
- English and Swedish translations.

Backtest on 28 days of real house data: **11.3 % daily MAPE** vs 19.2 % for the hand-tuned temperature regression it replaces.

**Upgrading from 0.0.x:** delete the old LoadCast entry and add it again – the placeholder entry has no sensors configured.
