# AIpicture
HardwareAI application workspace.

## Run the local frontend with the deployed backend

The local frontend is configured through `frontend/.env.local` to proxy API
requests to the Railway backend. Local development therefore uses the same
backend and PostgreSQL data as the public frontend; it does not require the
local backend or Docker Compose services to be started.

Next.js uses the Railway HTTPS origin as the default API rewrite target.
For Vercel, set `BACKEND_INTERNAL_URL` to that same origin in the project's
Environment Variables for Production (and Preview if used), then redeploy.
The value is used by the server-side rewrite and should not be exposed as a
`NEXT_PUBLIC_*` variable.

To use a different backend, copy `frontend/.env.local.example` to
`frontend/.env.local` and set `BACKEND_INTERNAL_URL` to that backend's public
HTTPS origin. Then start only the frontend:

```powershell
cd frontend
npm run dev
```

The local PostgreSQL database is separate and is not automatically copied to
Railway. Data written through this frontend goes to the configured remote
backend and its database.

## Local deployment with Docker Compose

See the [backend deployment guide](./backend/README.md#13f-docker-deployment-and-environments)
for Docker Compose setup, environment and secret configuration, CI image
publishing, PostgreSQL backup/restore, and release rollback procedures.