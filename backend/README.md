# Backend setup

## Intelligent Monitoring

Run the schema migration before starting the API:

```powershell
python -m alembic upgrade head
```

Set these variables in the backend environment before using monitoring
authentication:

```text
ALERT_AUTH_SECRET=<at-least-32-random-characters>
ALERT_BOOTSTRAP_TOKEN=<one-time-admin-bootstrap-token>
```

Create the first administrator at `POST /api/monitoring/auth/bootstrap-admin`
with that bootstrap token. Public signup creates viewer accounts only. An
administrator can create operator accounts, change roles, and assign or revoke
Plant access. Operators and viewers can access only assigned Plants; viewers
are read-only, while operators can manage monitoring data, rules, and alerts
within their assigned Plants. Dataset deletion and Plant creation/deletion are
administrator-only. Plant access is checked from the database on each request,
so revoking an assignment takes effect without waiting for token expiry.

After upgrading an existing deployment, assign Plants to each operator and
viewer in the monitoring dashboard. Existing accounts are not automatically
granted access. Alert status changes, assignments, feedback, reopenings, and
snooze events are retained in the alert history; reopening an alert preserves
its previous resolution timestamp.

Snapshots are stored in PostgreSQL without automatic expiration. This is
intentional: the scheduler requires durable data and the selected retention
policy is manual. Restrict database access, include snapshot storage in the
organization's backup/retention policy, and remove old snapshots through an
approved database maintenance process or the monitoring dashboard. Deleting a
scope's newest snapshot pauses its remaining schedules until a new snapshot is
uploaded. Alert records are separate and are not removed when a snapshot is
removed.

The scheduler defaults to an evaluation interval of one day per snapshot.
Operators can choose hourly, daily, or weekly intervals in the monitoring
dashboard. Uploading a snapshot evaluates it immediately. Only the newest
enabled snapshot for a plant/line is scheduled.

Threshold rules and anomaly existence are computed by Python. The statistical
detector uses only prior valid periods, requires at least eight baseline
periods, and combines rolling IQR with Z-score. Batch outliers require eight
other valid batch observations. Gemini, when configured, only adds a
non-authoritative explanation and suggested checks; its failure does not
remove or downgrade a Python-confirmed alert.

In-app notifications are always enabled. Optional delivery configuration:

```text
ALERT_SMTP_HOST=<smtp-host>
ALERT_SMTP_PORT=587
ALERT_SMTP_USERNAME=<smtp-user>
ALERT_SMTP_PASSWORD=<smtp-password>
ALERT_SMTP_FROM=<sender-address>
ALERT_SMTP_USE_TLS=true
ALERT_WEBHOOK_URL=https://<approved-host>/<path>
```

SMTP delivery uses STARTTLS by default (port 465 uses implicit TLS). Webhooks
must use HTTPS and resolve only to public IP addresses; redirects are not
followed. Delivery attempts are persisted, retried with backoff, and include a
stable idempotency key. PostgreSQL advisory locks elect one scheduler owner and
serialize evaluation and delivery jobs across workers. Set
`ALERT_SCHEDULER_ENABLED=false` on API workers when running a dedicated
scheduler process.
