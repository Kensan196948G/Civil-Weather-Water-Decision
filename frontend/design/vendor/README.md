# Vendored Frontend Assets

These files remove runtime dependency on public CDNs for the generated WebUI shell.

| Asset | Version | Source | License |
|---|---:|---|---|
| React UMD | 18.3.1 | `unpkg.com/react@18.3.1/umd/` | MIT |
| ReactDOM UMD | 18.3.1 | `unpkg.com/react-dom@18.3.1/umd/` | MIT |
| Babel standalone | 7.26.4 | `unpkg.com/@babel/standalone@7.26.4/` | MIT |
| Leaflet | 1.9.4 | `unpkg.com/leaflet@1.9.4/dist/` | BSD-2-Clause |

License texts are stored next to each vendored package.
Leaflet image assets referenced from `leaflet.css` are stored in `leaflet/images/`.
`frontend/test/vendor-assets-policy.cjs` verifies local references, Leaflet CSS images,
React/ReactDOM SRI hashes, and license-file presence in CI.

Map tiles are disabled by default. Set `window.__CW_TILE_URL__` before the app boots to point at
an internal tile service. `frontend/serve.py` also accepts `CW_TILE_URL`; unset, `none`, `off`,
`disabled`, or an empty value keeps the no-tile fallback for restricted/offline environments.
Use `window.__CW_TILE_ATTRIBUTION__` or `CW_TILE_ATTRIBUTION` when the configured tile service
requires attribution text.
