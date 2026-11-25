# Backend setup

## Intelligent Monitoring

Run the schema migration before starting the API:

```powershell
python -m alembic upgrade head
```

For local development, if `ALERT_AUTH_SECRET` is unset or shorter than 32
characters, the backend generates a cryptographically random, temporary
secret on the first authentication request. This lets an existing admin sign
in without entering a token; login creates and returns the normal bearer
access token. The temporary secret is process-local, so rate-limit bucket
identifiers can change after a backend restart. Set a stable secret if that is
undesirable.

Production still requires an explicitly configured stable secret. Configure:

```text
ALERT_AUTH_SECRET=<at-least-32-random-characters>
ALERT_BOOTSTRAP_TOKEN=<one-time-admin-bootstrap-token>
```

Create the first administrator at `POST /api/monitoring/auth/bootstrap-admin`
with the one-time bootstrap token. Existing administrators sign in through
`POST /api/monitoring/auth/login`; they do not need the bootstrap token.
Public signup creates viewer accounts only. An
administrator can create operator accounts, change roles, and assign or revoke
Plant access. Operators and viewers can access only assigned Plants; viewers
are read-only, while operators can manage monitoring data, rules, and alerts
within their assigned Plants. Dataset deletion and Plant creation/deletion are
administrator-only. Plant access is checked from the database on each request,
so revoking an assignment takes effect without waiting for token expiry.
Legacy bcrypt password hashes are verified at login and upgraded to the
backend's scrypt format after a successful authentication.
Because stored password hashes do not reveal the original password length,
successful login verifies whether a legacy password is shorter than 12
characters. In that case the backend revokes existing sessions and requires
one one-time password reset, using a short-lived reset token returned only to
that authenticated login. The new password must be at least 12 characters
before the user can sign in normally.

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

## Hardware analysis jobs

Hardware analysis submitted from the main analysis page is stored in
PostgreSQL and processed by background workers started with the FastAPI
lifespan. The existing synchronous `POST /api/hardware/analyze` endpoint
remains available for compatible clients; new clients should submit to
`POST /api/hardware/jobs` and poll `GET /api/hardware/jobs/{job_id}`. Jobs are
visible only to their owner unless the caller is an administrator. The
paginated `GET /api/hardware/jobs` endpoint lists the caller's recent jobs.

The queue uses PostgreSQL row locks and `SKIP LOCKED`; no Redis or separate
message broker is required. Uploads are limited to 10 MB, the default global
pending queue limit is 50, each user may have 3 pending/running jobs, and a
failed job is retried up to 3 times. Worker concurrency defaults to 2 per API
process, so configure it against the total number of API processes and the
available CPU/memory. Override these defaults with:

```text
HARDWARE_ANALYSIS_JOBS_ENABLED=true
HARDWARE_ANALYSIS_JOB_WORKERS=2
HARDWARE_ANALYSIS_JOB_MAX_PENDING=50
HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER=3
HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS=3
HARDWARE_ANALYSIS_JOB_POLL_SECONDS=1
HARDWARE_ANALYSIS_JOB_LEASE_SECONDS=3600
```

Apply migrations before starting the API:

```powershell
python -m alembic upgrade head
```

The uploaded bytes and custom prompt are cleared when a job reaches a terminal
state. The completed result remains available through the job status endpoint
and is linked to the persisted analysis-history record.

## Manufacturing Intelligence foundation (Phase 2.0)

`POST /api/intelligence/analyze` accepts an authenticated administrator or
operator multipart upload (`file`) in CSV/XLSX format. Optional form fields
are `analysis_question`, `column_mapping` (a JSON source-to-canonical object),
`source_timezone` (IANA timezone, default `UTC`), `downtime_unit` (`seconds`,
`minutes`, or `hours`), `production_unit`, `include_ai`, and `plant_id`.
When a Plant is supplied, the caller must have access to it. Analysis output
is saved to the shared Analysis History with the source checksum, evidence,
caller, and optional Plant scope.

The canonical data contract requires non-negative Production and treats
Defects, Downtime, timestamps, product/process identifiers, and other
dimensions as optional. Naive timestamps are interpreted in the selected
source timezone and stored in UTC; downtime is normalized to minutes. Empty,
duplicate, invalid, and rejected rows are counted in Data Quality. Invalid
required Production rows are excluded; invalid populated optional values are
reported and excluded from that metric.

The versioned `schema_version: "2.0"` response includes request classification,
execution plan, data quality, KPI results, descriptive statistics,
correlations, daily time series, anomaly and bottleneck screening results,
evidence references, heuristic confidence, and analysis limitations. The
legacy hardware analysis response also embeds this result as
`unified_analysis`, preserving existing response fields. The shared engine
reuses the existing CSV/XLSX parser, canonical aliases, Python KPI/statistical
calculations, Gemini client, and AI audit context rather than creating a
separate analytics database or a second queue.

Gemini interpretation is optional. If no API key is configured, the request
can still return Python-computed results; Gemini failures or rejected
evidence-bound output do not discard those results. AI statements must cite
evidence IDs produced by the engine and are rejected if they contain
unreferenced IDs or numeric claims. Correlation is association, not causation;
root causes remain hypotheses; downtime/production bottleneck results are
screening signals, not capacity proof; confidence is a documented heuristic,
not a probability. The current connector remains user-triggered CSV/XLSX
ingestion; direct ERP/MES/SCADA/PLC protocols and causal analysis are not
claimed as implemented.

## Production Intelligence (Phase 2.1)

`POST /api/production/analyze` accepts an authenticated administrator/operator
CSV or XLSX upload. Optional form fields are `source_timezone` (IANA timezone,
default `UTC`), `production_unit`, `cycle_time_unit`, `duration_unit`,
`downtime_unit`, and `include_ai`. The `/production` page provides the upload
controls, plan/actual and period views, line/shift/process breakdowns,
anomaly and bottleneck screening, evidence-linked interpretation, and a
printable report. Results are saved to the shared Analysis History.

The shared manufacturing data foundation recognizes explicit plan, actual
production, production start/end, cycle and standard-cycle time, available,
operating and waiting time, plus product, process, line, machine, shift, and
date fields. Naive timestamps are interpreted in the selected timezone and
normalized to UTC; cycle time is normalized to seconds and duration fields to
minutes. Daily, weekly, and monthly trend buckets use the selected source
timezone, although timestamps remain stored in UTC. The source rows must
represent additive production quantities: do not
repeat the same final output on every process row unless each row is a distinct
process output that should be summed.

Production achievement and plan variance use only rows with an explicit plan.
Production rate divides actual output by available hours; throughput divides
output associated with operating-time rows by operating hours. Start/end
elapsed time may be used only as a clearly labeled fallback, not as confirmed
machine operating time. Cycle time uses the production-weighted explicit
cycle-time values, or operating time divided by output when explicit cycle
time is absent. Takt time requires paired plan and available-time data. Line
efficiency requires standard cycle time and available time; utilization
requires paired operating and available time. Unsupported or incomplete
denominators remain `null`, rather than treating raw output as efficiency.

Repeated duration values are counted once within an identifiable
line/machine/shift/date or timestamp-interval bucket; rows without line or
machine identity are retained per row. Supply line or machine, shift, and date
or start/end fields to disambiguate schedules. Separate non-overlapping
durations that share the same bucket need distinct intervals. Production
quantities and plans are still summed per valid row.

Plan shortfalls are compared directly with explicit plan values. Daily
production, production achievement, and per-process cycle time are screened
against the prior eight periods using a 1.5-IQR boundary. Severity labels are
heuristics (high at a 20% or greater relative change, medium at 10% or
greater, otherwise low), not risk probabilities. Bottleneck candidates are
screening signals based on process throughput and explicit waiting time;
without process sequence and capacity data, line impact and causal root
cause are not estimated. Gemini interpretation is optional and evidence
validated; Python calculations remain available when AI is unconfigured or
its output fails validation.

## Quality Intelligence (Phase 2.2)

`POST /api/quality/analyze` accepts an authenticated administrator/operator
CSV or XLSX upload. Optional form fields are `column_mapping` (JSON
source-to-canonical mapping), `source_timezone`, `downtime_unit`,
`production_unit`, `measurement_unit`, and `include_ai`. Quality-only files do
not need a Production column. Results are saved to the shared Analysis History;
the `/quality` page presents KPI, Pareto, trend, SPC, evidence, and AI report
views.

The shared manufacturing data contract recognizes inspected quantity,
defective units, defect counts and types, first-pass inspected/passed counts,
rework and scrap quantities, product/process/line/shift, process sequence,
measurement characteristic/value, subgroup, and lower/upper specification
limits. Numeric sample measurements and specification limits may be negative;
count and quantity fields must remain non-negative. Invalid optional values
are reported in Data Quality and excluded from calculations.

Total inspected and defect totals use explicitly populated fields. Defect rate
uses `Defective Units / Inspected` only with complete unit-count coverage; if
those counts are absent, it uses explicit defect-event counts or one event per
classified row. Incomplete or mixed defective-unit coverage is reported as
unavailable rather than combining incompatible numerator types. FPY uses
explicit first-pass passed/inspected counts, and uses the final process
sequence only when process sequence is complete. RTY requires complete
first-pass data, at least two ordered stages, and a consistent route across
products. Rework and scrap rates require complete explicit numerators and
inspected denominators. Unsupported denominators remain `null`.

Defect Pareto and product/process/line/shift breakdowns are based on defect
events and available dimensions. Trend and anomaly screening uses the prior
eight valid periods and a 1.5-IQR boundary. P charts require defective-unit
counts; C charts require a constant inspected sample size; U charts support
varying inspected sizes. X-bar/R charts require explicit subgroup IDs or batch
IDs, equal subgroup sizes from 2–10, and at least 20 subgroups for a stable
classification. Limits use three-sigma rules; Western Electric/Nelson run
rules are not implemented. Cp/Cpk are emitted only for a stable X-bar/R
process with at least 30 measurements, one consistent specification window,
and a Jarque–Bera normality screen of p >= 0.05. These assumptions are
screening safeguards, not proof of process capability.

Gemini interpretation is optional and evidence validated; Python calculations
remain available if AI is not configured or its output fails validation.
Recommendations and root-cause statements are hypotheses, not causal proof.
Direct ERP/MES/SCADA/PLC ingestion, gauge-system validation, and advanced SPC
run rules are not claimed as implemented.

## Root Cause Intelligence (Phase 2.3)

`POST /api/root-cause/analyze` accepts either one combined CSV/XLSX file or
separate production and quality files. Split files must share the selected
time key and grouping dimensions (product, process, line, shift, machine, or
material) to be linked. Each file can provide its own source-to-canonical
column mapping. The optional `plant_id` is checked against the caller's
plant access and the result, evidence, and source checksum are saved to
Analysis History. The `/root-cause` page exposes correlation, 6M evidence,
ranked hypotheses, a five-whys worksheet, and proposed corrective actions.

The engine aggregates normalized records by day, week, or month plus the
selected dimensions. It reports pairwise-complete Pearson and Spearman
correlations and the number of paired observations for each cell. Notable
associations use an absolute coefficient threshold of 0.3 and at least five
paired groups; this is a screening threshold, not a significance test.
Hypothesis support scores are transparent heuristics derived from correlation
magnitude or stratified defect-rate lift, not probabilities, causal effect
estimates, or confidence intervals. The analysis does not infer a cause when
the selected outcome or its denominator is unavailable.

Fishbone evidence is grouped into Man, Machine, Method, Material, Measurement,
and Environment. Missing inputs are listed instead of being filled with
assumptions. The five-whys section is an investigation worksheet unless a
validated Gemini report is available. AI statements must reference existing
evidence IDs and are rejected when they include unlinked numeric claims;
Python calculations and ranked results are retained on AI errors. AI-generated
causes remain hypotheses. To create a corrective-action case, select a plant
and action; its owner, priority, due date, qualitative expected impact, and
verification plan are stored in the continuous-improvement tracker. A due date
is a proposed target and must be confirmed by the responsible manager.
Migration `9f3c7a2d6e10` adds the action-planning fields to
`improvement_cases`.

## Machine Intelligence (Phase 2.4)

`POST /api/machine-intelligence/analyze` accepts a CSV/XLSX machine dataset.
The `/machine-intelligence` page supports source timezone and duration units;
the result is saved to Analysis History. Recognized columns include Machine,
Machine Status, Available Time, Operating Time, Downtime, Downtime Type,
Downtime Planned, Standard Cycle Time, Defects/inspection counts, Line, and
Shift. Maintenance Action and Maintenance Outcome are included when supplied.
Machine/status/downtime and maintenance fields also accept common snake_case
aliases.

Availability uses operating time / scheduled time after planned downtime;
Performance uses standard cycle time × production / operating time; Quality
uses first-pass or inspection counts, then falls back to production less
defects. OEE is their product and is withheld when a component or denominator
is missing or outside 0–100%. MTBF and MTTR use observed failure counts and
operating/unplanned-downtime durations. Missing inputs remain null; metrics
are not imputed.

Machine events are normalized from supplied row-level status, downtime,
planned flag, and reason; the engine does not infer event intervals or machine
state transitions from sampled signals. Downtime Pareto and maintenance risk
are descriptive screening results. Anomaly checks use historical IQR or
within-machine first-half/second-half performance; they require enough data
and are not predictive maintenance. Maintenance outcomes are displayed from
uploaded history, not written back as work orders or automatically verified.

## Material Intelligence (Phase 2.5)

`POST /api/material-intelligence/analyze` accepts CSV/XLSX and saves the result
to Analysis History. The `/material-intelligence` page supports material
consumption, cost, lot traceability, supplier quality, and process-loss
analysis. Canonical inputs include Material/Material Type/Material Lot,
Material Unit, Planned/Actual Consumption, Material Unit Cost/Material Cost,
Material Loss/Scrap Material, Material Currency, Supplier, Product, Process,
Batch, Production, Defects/inspection counts, and Date. Common snake_case
aliases are accepted.

Mass is normalized to grams, volume to milliliters, and piece/count units to
pieces. Unknown units are preserved as their own unit and are not converted
across unit families. Consumption variance uses records with both planned and
actual values; usage rate uses paired material consumption and production.
Yield/waste require explicit loss or scrap and actual consumption. Costs use
explicit total cost or actual quantity × unit price; totals across different
or unspecified currencies are not combined.

Material-lot traceability joins lot, material, supplier, product, process, and
production batch present on the same input records. Defect-associated lots
represent observed association only, not causation. Supplier defect rates use
paired defect and inspected/produced quantities; monthly trends require valid
timestamps. Supplier risk tiers are transparent screening heuristics and are
marked insufficient with fewer than three paired quality records. With enough
records, high risk is screened at a defect rate of at least five percent or
monthly volatility of at least five percentage points; medium risk is above
the peer median or has volatility of at least two points. These are review
thresholds, not supplier certification.

Gemini interpretation is opt-in (`include_ai=false` by default); Python KPIs
and evidence remain available without Gemini. When enabled, derived material
aggregates, traceability mappings, and evidence are submitted for
interpretation; returned claims must reference known evidence IDs. Do not
enable it for sensitive datasets unless their external processing is
permitted.

## Predictive Intelligence (Phase 2.6)

`POST /api/predictive-intelligence/analyze` accepts an authenticated CSV/XLSX
upload and saves the result to Analysis History. The
`/predictive-intelligence` page exposes a 1–30 day horizon, a configurable
quality threshold, and explicitly parameterized what-if inputs. Date,
Production, planned production, inspection/defect counts, machine state,
downtime, available/operating time, material, actual consumption, and material
stock are optional; unsupported predictions are returned as
`insufficient_data`.

The engine aggregates timestamped records by observed calendar day. Features
use only prior observed periods (lag-1, lag-7, rolling mean/standard deviation,
and elapsed days). Each target receives separate chronological train,
validation, and test ranges. Validation selects between last-observation and
seven-observation moving-average baselines; the holdout test is excluded from
selection and reports MAE, RMSE, and sMAPE. A forecast requires at least 15
valid observations, eight feature rows, 50% calendar coverage, and minimum
train/validation/test sizes. Missing dates and invalid/missing production
values are not filled with zero. No machine learning model is trained.
Production plan completion is screened from the
forecasted daily actual-to-plan ratio when both quantities are explicitly
available.

Prediction intervals, when at least five holdout errors exist, use an empirical
90th-percentile absolute test error scaled by the square root of the forecast
horizon. Their coverage is not guaranteed. Confidence is a low/medium/high
screen based on holdout sMAPE and sample count, not a probability.
Quality-threshold flags compare point forecasts with the configured threshold.
Machine failure probability is not inferred: with at least 20 labeled days and
three labeled failure days, the API reports historical failure-day frequency
and a Wilson interval only, including per-machine rates when Machine is
identified. Material shortage signals require explicit stock and forecastable
consumption in matching normalized units. Material quality-risk forecasts are
available only when material can be paired with inspected/defective or
production/defect data. Drift compares the
last seven observed values with the earlier baseline; it is an offline screen,
not ongoing model monitoring.

What-if scenarios are deterministic sensitivity calculations: the entered
capacity and material-shortage changes scale output linearly; the downtime
scenario scales output with runtime while holding other factors fixed; quality
improvement adjusts the forecast defect-rate points. These assumptions are
returned with the result and are not causal estimates or validated capacity
models. The upload is not sent to Gemini or another external AI provider.

## Enterprise Intelligence (Phase 2.7)

`GET /api/enterprise-intelligence/overview` combines the latest Production,
Quality, Machine, Material, Predictive, and Root Cause analysis with active
monitoring alerts, improvement cases, user activity, AI-audit aggregates,
source traceability, and report-source readiness. Administrators can view all
analysis histories; other roles see only their own analysis histories and
activity, plus alerts, improvement cases, and governed reports within their
assigned Plant scope. AI audit details are administrator-only, and prompts or
responses are never included in this overview.

The recommendation engine ranks deterministic Python recommendations already
present in saved analyses, machine maintenance priorities, material-loss
signals and screened supplier-quality risks, predictive threshold/shortage
signals, and root-cause actions. Each recommendation includes source Analysis
History ID, evidence IDs when present, priority, and an observed-impact basis.
It is a review queue, not an
automatically-created maintenance order or a causal claim. Analysis History
remains the recommendation source record; the existing improvement-case
workflow records action owner, priority, deadline, implementation, before/after
measurements, confounders, and evaluation.

Existing monitoring alerts retain their severity, deduplication, assignment,
acknowledgement/status lifecycle, and status history. The overview includes
active alerts without changing their state. Improvement deadlines are flagged
as overdue and require escalation; notification/escalation routing is still a
manager action, not an automatic scheduler. User actions are read from the
existing audit trail.

Daily Production, Weekly Quality, Monthly Performance, Machine Reliability,
Material Consumption, Predictive Risk, and Executive Manufacturing templates
show whether their source analyses are current, missing, or stale for the
selected 1–365 day window. They provide source history IDs and summaries for
the existing Governed Reports workflow, which persists versions and supports
review, approval, publication, and export. This endpoint reports readiness; it
does not silently publish or fabricate a period report when a source is absent.

## System Integration & Final Acceptance (Phase 2.8)

`POST /api/system-integration/analyze` accepts one combined CSV/XLSX file and
runs the shared Manufacturing Analytics Engine plus Production, Quality, Root
Cause, Machine, Material, and Predictive Intelligence. The request uses one
column mapping, source timezone, and unit configuration; the response carries
a versioned unified result, a consistent per-domain status/summary/evidence
envelope, cross-domain findings, and one saved `system_integration` Analysis
History record. History and audit records link the result to the user and
source-file checksum.

Numeric cross-domain associations are reported only when at least eight
paired observations are available and absolute Spearman correlation is at
least 0.6. Material lot findings are traceability links to recorded defects,
not proof that a lot caused them. Machine failure frequency combined with a
production forecast is a joint review signal, not a forecast of machine-caused
production loss. Evidence IDs in the combined result are domain-scoped to avoid
collisions between the component engines. Missing statistical history returns
an insufficient/limited status instead of a fabricated forecast.

Gemini is off by default. When explicitly enabled, only the Root Cause
cross-domain interpretation invokes the configured Gemini service; computed
domain results remain deterministic Python outputs. The endpoint is limited
to administrators/operators, checks optional Plant access, and is rate-limited
to three runs per minute. The shared parser enforces 10 MB and 100,000 rows;
this multi-engine route further caps analysis at 2,000 rows per request. A
local synthetic smoke measurement took about 1.8 seconds at 1,000 rows and
4.0 seconds at 2,000 rows, compared with 17.8 seconds at 5,000 rows. These
single-process measurements are not an SLA or a production load test. The
browser view is available at `/system-integration`.

Acceptance coverage includes individual engine KPI/evidence tests, forecasting
backtests and leakage checks, Root Cause evidence validation, enterprise
workflow/history tests, and an end-to-end integrated upload covering all six
domains, mapping, cross-domain correlations/traceability, audit persistence,
history retrieval, sparse-data handling, missing required production data, and
unauthenticated access. A PostgreSQL-gated integration test also persists and
reads the unified result and audit JSON; the CI PostgreSQL job runs migrations
and enables this test with `RUN_POSTGRES_INTEGRATION=1`.

### Architecture and acceptance status

The six domain services are separate modules, not six copies of a data model:
they consume the shared `ManufacturingDataset` normalization/data-quality
contract, while the Manufacturing Analytics Engine and System Integration
orchestrator provide the common analysis path. Domain-specific KPI and
statistical calculations remain deterministic Python code. This is a shared
core with specialized analyzers, not a single monolithic calculator.

| Requirement | Implementation / evidence | Acceptance boundary |
| --- | --- | --- |
| Single Core Engine | Shared data foundation, Manufacturing Analytics Engine, and unified orchestrator | Domain modules retain specialized calculations; do not treat correlation or the orchestration wrapper as causal analysis. |
| Deterministic KPI | KPI formulas are implemented in Python and covered by expected-value tests | LLM output is not the source of KPI values. |
| Evidence-based AI | Evidence IDs, validation, and limitation handling are covered by engine and integration tests | Gemini is optional; no live Gemini call is part of local verification. |
| Data Quality First | Shared normalization validates/excludes invalid records and reports quality and warnings | Results remain limited when required data/history is absent. |
| Explainability | Results include evidence, summaries, warnings, and analysis limitations | Heuristic findings are not presented as proven causes. |
| Confidence-aware | Confidence and insufficient-data states are emitted and tested | Confidence is heuristic, not a calibrated probability. |
| Modular Architecture | Production, Quality, Root Cause, Machine, Material, Predictive, and Enterprise services/routes | Each module remains responsible for its domain-specific logic. |
| Shared Data Model | Domain services use the canonical manufacturing dataset and mappings | Source-specific columns/units still require valid mapping and configuration. |
| Traceability | Source checksum, Analysis History, evidence references, and action audit | Traceability is limited to supplied source data and persisted records. |
| Human-in-the-loop | Enterprise workflow supports assignment, deadlines, status, and review/approval steps | AI recommendations do not autonomously execute corrective actions. |
| Business logic and formulas | Domain tests exercise supported metrics and insufficient-data cases | No claim is made for unavailable source fields. |
| API | Authenticated routes and integration tests cover the supported analysis/workflow APIs | File ingestion is CSV/XLSX; direct MES/ERP/PLC connectors are not included. |
| Frontend | `npm run lint` and `npm run build` pass; `/system-integration` was smoke-tested in a browser | No automated browser end-to-end suite currently covers all pages and authenticated workflows. |
| PostgreSQL | CI upgrades PostgreSQL migrations and runs the PostgreSQL-gated suite; the added integration test checks JSON history/audit round-trip | PostgreSQL was not available/enabled in this local test run; CI execution is the verification gate. |
| Standard test data | Deterministic synthetic datasets/fixtures exercise domain and integrated scenarios | No representative, governed customer/MES dataset was supplied. |
| Accuracy evaluation | Expected-value KPI tests and predictive backtests/leakage checks run in the test suite | No production-data accuracy certification or cross-customer benchmark is claimed. |
| Integration testing | Backend suite includes integrated upload, history/audit, auth, sparse-data, and domain tests | Local PostgreSQL-gated tests are skipped unless the CI database settings are provided. |
| Documentation and evidence | This README describes behavior, formulas/limits, test coverage, and deployment caveats | Final operational sign-off requires target-environment evidence. |

Latest local verification for this workspace: backend `327 passed, 20 skipped,
12 subtests passed`; frontend lint and production build passed; the integration
page rendered in the browser. The skipped tests include environment-gated
PostgreSQL cases. No Gemini request was made and `backend/.env` was not read.

This is deterministic integration and test acceptance, not certification for
unbounded production scale. No representative customer/MES data, sustained
concurrent-load test, database load test, memory stress test, or Gemini
latency/SLA test is part of this acceptance. Validate those deployment-specific
limits with the target database, infrastructure, and governed production
dataset before a broad rollout.

## Industrial file integrations

The integration registry currently advertises only the implemented CSV/XLSX
file connector. `GET /api/integrations/registry` describes supported target
fields and limits; `POST /api/integrations/preview` previews headers and sample
rows; authenticated administrators/operators can register a connector scoped
to an accessible Plant and optional Line, configure source-to-target mapping,
then upload a file to `POST /api/integrations/connectors/{id}/sync`.

The connector requires `Date` and `Production`. Optional production fields
include Product, Model, Component, Supplier, Batch, Defects, Downtime, Defect
Type, Quality Status, Machine, Process, Order, and Material. Additional source
columns can be mapped to `attributes.<field>` so the persisted dataset can
retain source-specific attributes without adding vendor-specific database
columns. Dates are normalized from the configured IANA time zone to UTC;
Downtime can be provided in seconds, minutes, or hours and is stored in
minutes. Invalid required values reject the whole file, and the sync history
stores row/field/error-code samples without storing the invalid raw values.

Successful file syncs create a Monitoring dataset, evaluate it using the
existing Python alert pipeline, and persist the source checksum, mapping
snapshot, normalization settings, connector, user, and dataset IDs as lineage.
Re-uploading an already successful file with the same connector mapping and
normalization is idempotent. Changing the mapping or normalization creates a
new ingestion version for the same source checksum.

Continuous-improvement cases are available under `/api/improvements`. Cases
are plant-scoped and retain a baseline, assigned owner, suspected root cause,
action, implementation time, after-measurement, confounders, and evaluation.
KPI deltas are calculated from submitted measurements. Evidence-confidence
labels describe documentation completeness only; they are not statistical
confidence intervals and do not establish that an action caused the observed
change. The `/improvements` frontend guides the case through those steps.

Hardware CSV/XLSX ingestion is capped at 10 MB and 100,000 rows per file;
multi-file requests are capped at 10 files, 25 MB combined, and 100,000 rows
combined. Merged datasets are compressed and persisted in PostgreSQL for one
hour so trend/intervention requests can reach any API replica. The shared cache
evicts oldest datasets to stay within 25 entries and a 256 MiB uncompressed
data budget; records are owner-scoped. Multi-file responses include at most
1,000 merged-row previews (`merged_rows_truncated` indicates truncation); the
full dataset remains available by its returned `dataset_id` until cache expiry
or eviction.
Alembic revision `b27f4a8d1c30` creates the shared cache table.
Failed uploads are retained in sync history and can be retried by uploading the
file again after correcting the source or mapping. Uploaded source files are
not retained after processing.

File sync is currently user-triggered: an uploaded file is transient and is
not a durable polling source, so no automatic file sync scheduler or automatic
retry loop is claimed. ERP/MES/SCADA/PLC connections, their credential
management, and scheduled recovery should be added only after the actual
vendor, protocol, data contract, and permitted network access are established.

## 13C security and operations

Configure these values per environment; do not commit `.env` files or
credentials. Use a secret manager in staging/production. `ALERT_AUTH_SECRET`
must be a randomly generated value of at least 32 characters. Keep the
bootstrap token secret and remove or rotate it after the first administrator
has been created. Configure the application URL and exact trusted frontend
origins:

```text
APP_ENV=production
APP_BASE_URL=https://app.example.com
CORS_ALLOWED_ORIGINS=["https://app.example.com"]
SECURITY_RATE_LIMITS_ENABLED=true
```

Production startup rejects a missing/short auth secret, disabled rate limits,
a non-PostgreSQL database URL, and non-HTTPS application/CORS URLs. Apply
Alembic migrations through `head` before deploying application instances;
revision `c69f2a13b8d4` creates the shared rate-limit table and
`b27f4a8d1c30` creates the shared dataset-cache table. The limiter uses atomic
PostgreSQL upserts, so limits are shared across API processes without Redis.
It stores an HMAC of the route and client address, not the raw address.
Default limits are 30/minute for login, 10/minute for signup, password-reset
completion and analysis/upload operations, and 5/hour for reset requests.
Tune them using observed traffic and keep limits enabled for public endpoints.

The application uses the direct ASGI client address. If deployed behind a
reverse proxy, configure that proxy and the ASGI server to trust forwarded
addresses only from known proxy IPs; never accept arbitrary client-supplied
`X-Forwarded-For` values as identity. CORS is not an authorization boundary:
backend authentication, role checks, and Plant/Factory/Department scope checks
remain mandatory.

Uploads are limited to 10 MB per file, with archive expansion capped at 50 MB,
1,000 archive members, 500 tabular columns, and 100,000 rows. PDF ingestion is
capped at 100 pages. Multi-file hardware and monitoring uploads are limited to
10 files, 25 MB combined, and 100,000 rows combined. Production analysis uses
the same bounded parser and runs CPU-heavy parsing/analysis outside the event
loop. Set the reverse proxy's request-body limit to about 26 MB to reject
oversized multipart bodies before the application parser spools them.

### PostgreSQL backup and recovery

Choose and document organization-specific recovery point and recovery time
objectives, backup frequency, retention, encryption, and off-site storage.
Backups contain sensitive production and identity data: restrict access and
encrypt them at rest and in transit. A custom-format backup can be created
from PowerShell with:

```powershell
pg_dump --format=custom --no-owner --file="$env:BACKUP_DIR\hardwareai.dump" "$env:DATABASE_URL"
```

Restore into a newly created, isolated recovery database (not the live
database) and verify the restored schema and application data before relying
on the backup:

```powershell
pg_restore --no-owner --dbname="$env:RESTORE_DATABASE_URL" "$env:BACKUP_DIR\hardwareai.dump"
python -m alembic current
```

Run and record restore drills regularly, including verification of users,
Plant assignments, monitoring snapshots, analysis lineage, integrations, and
audit history. A successful dump alone does not demonstrate recoverability.

## 13D logging and observability

The API writes structured JSON request logs with a generated `X-Request-ID`,
route template, HTTP status, and latency. Do not log request bodies, auth
headers, uploaded file contents, or AI prompts/responses. Centralize stdout
logs in the deployment's log platform and configure alerts for sustained 5xx
rates, database readiness failures, and repeated job/integration errors. No
external error-tracking vendor is configured by default.

`GET /health/live` is a liveness probe. `GET /health/ready` checks PostgreSQL
connectivity and returns 503 when it cannot connect. The admin-only
`GET /api/ops/health` reports database query latency, local worker/scheduler
state, queued/expired hardware jobs, recent integration sync status, and
24-hour AI activity. `GET /api/ops/metrics` reports HTTP request counts,
5xx counts, per-route latency buckets, recent throughput, and AI usage.
Operational endpoints require an authenticated administrator; do not make
them public through a reverse proxy.

HTTP counters and latency buckets are in-memory and scoped to one application
process. Scrape each worker/process and aggregate in the monitoring platform;
they reset on restart and are not intended as durable billing or SLO records.
AI audit records are persisted in PostgreSQL. Token counts are captured only
when the provider returns usage metadata. Estimated cost is left null unless
both model-specific rates are explicitly configured, in USD per million
input/output tokens:

```text
GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD=<configured-provider-rate>
GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD=<configured-provider-rate>
```

These are estimates based on returned usage and configured rates, not a
provider invoice; retries or provider-side adjustments may not be represented.
Apply Alembic revision `d8a4c73e19b2` before deploying this version to persist
AI token and estimated-cost fields. The operational API reports priced-call
coverage so a missing estimate is not presented as zero cost.

## 13F Docker deployment and environments

The repository root contains a multi-stage backend/frontend build and
`compose.yaml` for PostgreSQL, a one-shot migration service, the API, and the
Next.js frontend. PostgreSQL 18 stores data in the named `postgres_data_v18`
volume mounted at `/var/lib/postgresql`; the database and API ports are not
published to the host. This volume is intentionally separate from the
PostgreSQL 16 `postgres_data` volume, which remains untouched. Compose does not
migrate or copy existing data; back up and restore into a planned PG18
deployment before switching if the existing database must be retained. The
frontend is bound to `127.0.0.1:3000` by default so an HTTPS reverse proxy can
terminate TLS and forward to it. Do not expose this port publicly without TLS
and appropriate edge controls.

For a local Compose environment, copy the example and replace the placeholder
secrets. Keep `DATABASE_URL`'s username, password, and database synchronized
with the `POSTGRES_*` values. Use URL-safe passwords (letters, digits, `-`,
`_`) or percent-encode reserved characters in the connection URL. In
PowerShell, a strong URL-safe password can be generated with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
Copy-Item .env.example .env
```

Set development values in `.env`, then start the complete stack:

```powershell
docker compose --env-file .env config
docker compose --env-file .env up --build -d
docker compose --env-file .env ps
docker compose --env-file .env logs -f backend frontend migrate
```

`migrate` waits for PostgreSQL health and applies Alembic `head`; the backend
does not start unless that one-shot migration exits successfully. The frontend
uses a same-origin `/api` route rewrite to the internal backend service, so
browser requests do not embed a private container address or require a
production `NEXT_PUBLIC_API_URL`. For direct local development outside Compose,
the existing API URL override can still be set at frontend build/runtime as
appropriate.

Use separate environment files and independent secret values for development,
staging, and production. Keep `.env` outside version control; production
secrets should be injected by the deployment secret manager. Production must
set `APP_ENV=production`, HTTPS `APP_BASE_URL`, exact HTTPS
`CORS_ALLOWED_ORIGINS`, PostgreSQL `DATABASE_URL`, a strong
`ALERT_AUTH_SECRET`, and `SECURITY_RATE_LIMITS_ENABLED=true`. Remove the
bootstrap token after initial administrator creation. The frontend container
is built with `BACKEND_INTERNAL_URL=http://backend:8000`; when deploying
outside Compose, provide a reachable backend URL at frontend image build time.

### Sharing the local database with the Docker backend (Windows development)

The local backend and the default Compose stack can point at different
PostgreSQL databases. To run a Docker backend against the same local database
and source tree, use the separate `compose.shared-backend.yaml` service:

```powershell
docker compose -f compose.yaml -f compose.shared-backend.yaml up -d --no-deps backend
docker compose -f compose.yaml -f compose.shared-backend.yaml ps
```

Run the local Alembic migration first. This override reuses the Compose
backend's existing image and network, binds `backend/` into the container with
Uvicorn reload, and routes its `DATABASE_URL` to `host.docker.internal`; the
local backend continues to use `localhost`. Ensure the repository `.env` and
`backend/.env` `DATABASE_URL` values identify the same database. The shared
Docker backend listens on `127.0.0.1:8001` for local checks. `--no-deps`
avoids starting or replacing the separate Compose PostgreSQL container; its
volume is left untouched, and no data copy is performed.

Keep the local backend and frontend running, then start the Quick Tunnel with:

```powershell
cloudflared tunnel --url http://127.0.0.1:3000
```

Only the local frontend is exposed by that command. Its `/api` rewrite reaches
the local backend on port `8000`; the Docker backend on port `8001` shares the
same database and source. Stop it with
`docker compose -f compose.yaml -f compose.shared-backend.yaml stop backend`.

GitHub Actions runs backend tests and Alembic offline SQL validation, frontend
lint/build, and container builds on pull requests. A successful push to `main`
publishes immutable SHA-tagged images to GitHub Container Registry (and
updates `latest`). Configure registry access and an environment-specific
deployment controller to roll those images out; this repository does not
assume a particular cloud host or automatically SSH into production.
For a controlled rollout, set `BACKEND_IMAGE` and `FRONTEND_IMAGE` to the same
SHA tag, authenticate/pull from GHCR, validate the Compose configuration, then
run `docker compose pull` followed by `docker compose up -d --no-build`.
Monitor container health and `/api/ops/health` before directing traffic.

### Release and rollback

1. Take and verify a PostgreSQL backup before the release; see
   [PostgreSQL backup and recovery](#postgresql-backup-and-recovery).
2. Record the currently deployed image SHA and database Alembic revision.
3. Review the migration SQL. Prefer backward-compatible, expand/contract
   migrations so the currently running image remains compatible during
   rollout. The migration service runs automatically before the new backend.
4. Deploy both frontend and backend images from the same commit, then check
   readiness, worker/scheduler status, job queue, integration syncs, and
   application logs.
5. If the application release fails, pin both services back to their previous
   SHA-tagged images and restart the stack. Do not automatically downgrade
   Alembic: schema downgrades can remove newly written data and may not be
   compatible with the previous version. Use a reviewed corrective
   forward-migration where possible.
6. If recovery requires restoring data, restore a verified backup into an
   isolated database first, validate the application and records, and only
   then plan a controlled cutover. Never restore over the live database as an
   initial rollback step.

Run a release migration explicitly when operating a deployment controller
that does not use this Compose dependency graph:

```powershell
docker compose run --rm migrate
```

### Backup and restore scripts

The scripts under `deploy/` require PostgreSQL client tools on the operator's
machine and use `DATABASE_URL` / `BACKUP_DIR` for backups. The restore script
requires `RESTORE_DATABASE_URL`, `BACKUP_FILE`, and the explicit
`CONFIRM_RESTORE=RESTORE_TO_EMPTY_DATABASE` guard. Restore only to a newly
created isolated, empty database; the script never drops a database or uses
`pg_restore --clean`. Restrict access to dumps and checksum files and encrypt
backup storage according to the organization's key-management policy.

```powershell
$env:BACKUP_DIR = "D:\protected-backups\hardwareai"
.\deploy\backup-postgres.ps1

$env:BACKUP_FILE = "D:\protected-backups\hardwareai\hardwareai-<timestamp>.dump"
$env:RESTORE_DATABASE_URL = "postgresql+psycopg://user:password@recovery-host:5432/hardwareai_restore"
$env:CONFIRM_RESTORE = "RESTORE_TO_EMPTY_DATABASE"
.\deploy\restore-postgres.ps1
```

After restoring, verify the revision using the recovery database URL:

```powershell
$env:DATABASE_URL = $env:RESTORE_DATABASE_URL
python -m alembic current
```

Run a documented application-data validation before considering the recovery
successful.

## 13H executive dashboard and UX

The authenticated `/dashboard` page summarizes the latest saved analysis, its
daily production trend, product/supplier defect rankings, open alert severity,
recent analysis lineage, and improvement cases. KPI cards and trend details
link to the saved analysis record; alert counts link to the Alert Center. The
dashboard uses the same server-side authorization and Plant-scoped endpoints
as the rest of the application, and labels production KPIs as belonging to
the latest analysis rather than presenting them as plant-wide aggregates.
Missing data is shown as an empty state, and failed sections retain their
request ID where available.

### Phase 13 readiness boundaries

- **13E:** The only executable integration is user-uploaded CSV/XLSX. ERP,
  MES, SCADA, PLC, and machine-system connectors, durable scheduled sync, and
  automatic external-connection recovery remain pending selection of a real
  vendor/protocol and its data contract. The file connector's sync history and
  idempotency do not imply those external connector capabilities.
- **13B:** Query/index and upload limits are implemented, but a representative
  production load/stress baseline has not been measured. Keep Redis or another
  broker out until measured PostgreSQL queue throughput or contention shows a
  need; test with the expected deployment replica count and worker settings.
- **13D:** Structured logs, health endpoints, PostgreSQL/job/integration/AI
  metrics are available, but external error tracking and centralized metrics
  alerting must be configured in the target operations platform.
- **13F:** CI builds and publishes commit-tagged images; actual environment
  provisioning, secret-manager integration, and automated rollout remain
  deployment-platform responsibilities. Validate production migration
  compatibility and run a restore drill before release.
