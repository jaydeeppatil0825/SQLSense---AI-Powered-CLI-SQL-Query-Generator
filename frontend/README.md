# SQLSense Frontend

React + TypeScript application shell for the SQLSense API gateway.

## Run locally

```powershell
npm.cmd install
npm.cmd run dev
```

Default browser API calls are same-origin: `GET /health` and `POST /api/v1/gateway`.

For local split-server development, set `VITE_DEV_API_PROXY` in `.env.local`:

```powershell
VITE_DEV_API_PROXY=http://127.0.0.1:8000
```

`VITE_API_BASE_URL` is optional and should not contain credentials.

Production-style build:

```powershell
npm.cmd run build
```

Serve the built SPA with fallback for frontend routes only. Do not route `/health` or `/api/v1/gateway` to the SPA.

## Scope

- Responsive shell, routes, badges, theme, and status panels.
- Status integration for health, session, database, and knowledge base.
- No database credential persistence.
- No client-side SQL authority.
- Local/internal deployment only until authentication and production hardening are added.
