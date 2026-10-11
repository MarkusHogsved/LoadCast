# Changelog

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
