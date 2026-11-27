# AIpicture
HardwareAI application workspace.

## Frontend API proxy targets

The frontend proxies browser API requests through its same-origin `/api` route.
By default, `next dev` targets the local backend at
`http://127.0.0.1:8000`; Vercel production and preview builds target the Railway
backend at `https://aistudymate-production.up.railway.app`.

The target can be overridden per environment with `BACKEND_INTERNAL_URL`.
Set it to the Railway origin in Vercel's Environment Variables for Production
and Preview, then redeploy. Keep it server-side; do not expose it through a
`NEXT_PUBLIC_*` variable.

For local development, start the backend on port 8000, then start the frontend.
To use Railway instead, set `BACKEND_INTERNAL_URL` in
`frontend/.env.local` to the Railway origin and restart Next.js:

```powershell
cd D:\AIpicture\frontend
npm run dev
```

When using the local target, data goes to the local backend's configured
database. When using Railway, data goes to Railway's database. A Cloudflare
tunnel to the local frontend still uses the local target because Next.js
forwards API requests server-side.

## Local deployment with Docker Compose

See the [backend deployment guide](./backend/README.md#13f-docker-deployment-and-environments)
for Docker Compose setup, environment and secret configuration, CI image
publishing, PostgreSQL backup/restore, and release rollback procedures.