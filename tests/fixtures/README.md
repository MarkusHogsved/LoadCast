# Fixtures

## house_2026-09-14_2026-10-11.csv

Real hourly data from the author's home (Kungälv, Sweden), exported from Home
Assistant long-term statistics.

| Column | Source | Meaning |
|---|---|---|
| `hour_start_utc` | – | Start of the hour, UTC |
| `total_kwh` | hourly mean of the inverter's total load power | Whole-house consumption that hour |
| `heatpump_kwh` | hourly change of a Shelly Pro 3EM on the heat-pump circuit | Ground-source heat pump |
| `ev_kwh` | hourly change of the Zaptec charger's energy meter | EV charging |
| `temp_c` | hourly mean of the outdoor temperature sensor | °C |

Notable event: the heat pump compressor failed around **2026-09-23**. The
heat-pump circuit drops from ~12 kWh/day to ~3 kWh/day (circulation pumps and
controls only) and stays there. This is the regression case the model must
handle: it should follow the drop within a few days, while the hand-tuned
regression it replaces kept planning for ~30 kWh/day.
