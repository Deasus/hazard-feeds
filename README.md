# hazard-feeds

A scheduled mirror of public global hazard feeds, normalized to JSON and served from `raw.githubusercontent.com` (CORS-open). Browser clients can read it without hitting the upstream sources directly.

| File | Contents | Upstream |
|---|---|---|
| `data/gdacs.json` | Multi-hazard alerts (earthquake, flood, tropical cyclone, wildfire, drought, volcano) | [GDACS](https://www.gdacs.org) RSS |
| `data/volcano.json` | Volcanic-eruption events not already in `gdacs.json` | GDACS event API (`eventlist=VO`) |
| `data/status.json` | Per-feed health: `ok`, `count`, `last_success`, `last_change`, `error` | — |

Each data file has the form `{generated_at, upstream, count, alerts: [{title, event, severity, area, onset, expires, lat, lng, source, _gdacsLevel, _evCode}]}`.

- **Cadence:** about every 15 minutes, via a self-re-dispatching GitHub Actions chain. The cron is only a dead-man restart.
- **Failure behavior:** a failed fetch never replaces the last good data. The failure shows up in `status.json`.

Data © GDACS (European Commission JRC / UN OCHA), redistributed under its public terms.
