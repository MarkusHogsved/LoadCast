# Changelog

## 0.1.6

- Sub-load meters that report late are realigned. Each sub-load's lag
  (0-2 h) is detected from its correlation with the total load and its
  readings are moved back to when the load ran. The Zaptec charger's meter
  counts about an hour late: in the author's house the base load got a false
  peak in every charging hour and negative hours after it (correlation with
  the total 0.45 at lag 0, 0.90 at lag 1). Shown per component as
  `meter_lag_hours`.
- Base load no longer invents energy. Hours where the sub-loads exceed the
  total used to be clipped at 0, which kept the false peak (110 kWh in 31
  days here). A negative hour now borrows from the hours around it, at most
  3 h away, so the base load's energy matches total − sub-loads.
- Backtest on 31 days of real data (fixture extended to 2026-10-14):
  daily MAPE 10.8 → 9.6 %, bias +2.6 → −0.8 kWh/day, hourly MAE
  0.570 → 0.527 kWh, P10-P90 coverage 84 → 79 %. The backtest now scores
  both variants against the energy-conserving actual and reports bias,
  hourly MAE and band coverage.
- The error sensor gets `bias_days`: how many days `bias_7d_kwh` is based on
  (fewer than 7 after `loadcast.mark_change`).

## 0.1.5

- README logo now loads in the HACS documentation view: absolute PNG URL
  instead of a relative SVG path (HACS has no base URL for relative images).

## 0.1.4

- After `loadcast.mark_change` the forecast no longer becomes unavailable for
  a day. Until the component has 24 hours of new data it is forecast from its
  old profile moved to the level of the hours since the change, with a wider
  P10/P90 band. The sensors stay available and `ready: true`.
- New attributes `uncertain` and `uncertain_components` on the 24 h forecast
  sensor, `uncertain` and `new_samples` per component, and `uncertain` in the
  `loadcast.get_forecast` response.

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
