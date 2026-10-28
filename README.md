# CrewLink — Member Callout

Union leadership sends one callout to their local and sees who got it, read it, and
acknowledged it. **Rule 1:** one local never sees another's data. **Rule 2:** a retry or a
crashed/restarted process never delivers to the same member twice.

Design and the decisions behind it: **[DESIGN.md](DESIGN.md)**.
Stack: Django + DRF + Postgres, Celery + Redis, Next.js, Docker.

## Run it

```bash
docker compose up --build -d
```

Starts Postgres, Redis, the API (`:8000`), a Celery worker and recovery scheduler, and the Next.js leadership
screen (`:3000`). On first boot the API container creates the non-superuser app DB role,
runs migrations (including Row-Level Security), and seeds data — all idempotent.

- **Leadership UI:** http://localhost:3000 — log in as `lead27` / `crewlink-dev`
- **API:** http://localhost:8000
- **Counts:** refresh every ~3 seconds while sending, then every ~10 seconds until
  `state=sent`, `read=total` and (when `needs_ack=true`) `acked=total`. Polling pauses
  in background tabs and refreshes on return. Temporary errors retry automatically.
- **Health:** http://localhost:8000/api/health/ or http://localhost:8080/api/health/
  — public liveness check, returns `200 {"status":"ok"}` without authentication or
  database/Redis access. In the default stack, both ports reach the same API.

After changing backend code, rebuild the running API (the image contains the code):
`SEED_ON_START=0 docker compose up --build -d --no-deps backend` (keeps existing
seed data unchanged). Health regression checks:
`docker compose exec backend python manage.py test config`.

Local frontend development uses `.next-dev`; production builds use `.next`, so
running `npm run build` does not overwrite a running dev server's chunks.

Re-seed any time (re-runnable): `docker compose exec backend python manage.py seed`

### Two instances behind a load balancer (DevOps bonus)

```bash
docker compose -f docker-compose.yml -f docker-compose.lb.yml up --build -d
```

Adds a second API instance and nginx (`:8080`, round-robin); the frontend then talks to
the load balancer. The override keeps the direct API on `:8000` and reserves `:8080`
for nginx, avoiding a port conflict. Requires [Docker Compose 2.24.4 or newer](https://docs.docker.com/reference/compose-file/merge/#replace-value)
for `!override`. Used to prove Rule 2 holds across instances (below). When rebuilding
this stack, include both compose files so both API instances receive the new code.

## Test Accounts

```json
{
  "password_for_all_logins": "crewlink-dev",
  "locals": {
    "Local 27 (larger, ~2000 members)": {
      "id": "27272727-2727-4727-8727-272727272727",
      "leadership_login": "lead27",
      "member_login": "member27"
    },
    "Local 58 (smaller, ~200 members)": {
      "id": "58585858-5858-4858-8858-585858585858",
      "leadership_login": "lead58",
      "member_login": "member58"
    }
  },
  "existing_announcement_in_local_27": "aa270000-0000-4000-8000-000000000001",
  "member_in_local_58": "58000000-0000-4000-8000-000000000058",
  "auth": "JWT bearer. POST /api/auth/login/ {username,password} -> {access}. Send header 'Authorization: Bearer <access>'.",
  "endpoints": {
    "GET /api/health/": "public — API liveness, returns {status: ok}",
    "POST /api/auth/login/": "public — returns JWT access/refresh",
    "GET /api/me/": "any role",
    "POST /api/ai/draft/": "leadership — {raw_text} -> {title,body,push_preview} draft",
    "GET|POST /api/announcements/": "leadership — list / create draft",
    "GET /api/announcements/{id}/": "leadership",
    "POST /api/announcements/{id}/approve/": "leadership — draft -> approved",
    "POST /api/announcements/{id}/send/": "leadership — approved -> sending (202)",
    "GET /api/announcements/{id}/status/": "leadership — live counts",
    "GET /api/inbox/": "member — my announcements",
    "POST /api/inbox/{announcement_id}/read/": "member",
    "POST /api/inbox/{announcement_id}/ack/": "member"
  }
}
```

## Quick tour (curl)

All logins share the password `crewlink-dev`. Get a leadership token:

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"lead27","password":"crewlink-dev"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
```

Create → approve → send a callout to all of Local 27, then read the counts:

```bash
AID=$(curl -s localhost:8000/api/announcements/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Heads up","body":"Meeting tonight","push_preview":"Meeting tonight","needs_ack":true}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -s -X POST localhost:8000/api/announcements/$AID/approve/ -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8000/api/announcements/$AID/send/    -H "Authorization: Bearer $TOKEN"   # -> 202
curl -s localhost:8000/api/announcements/$AID/status/          -H "Authorization: Bearer $TOKEN"   # counts
```

Member read / acknowledge flow (no member UI needed):

```bash
MTOKEN=$(curl -s localhost:8000/api/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"member27","password":"crewlink-dev"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
SAMPLE=aa270000-0000-4000-8000-000000000001

curl -s localhost:8000/api/inbox/ -H "Authorization: Bearer $MTOKEN"
curl -s -X POST localhost:8000/api/inbox/$SAMPLE/read/ -H "Authorization: Bearer $MTOKEN"
curl -s -X POST localhost:8000/api/inbox/$SAMPLE/ack/  -H "Authorization: Bearer $MTOKEN"
```

### Rule 1 — a Local 27 login can't read Local 58's data

```bash
L58=$(curl -s localhost:8000/api/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"lead58","password":"crewlink-dev"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

# Local 27's sample announcement, requested by a Local 58 leader:
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/announcements/$SAMPLE/ \
  -H "Authorization: Bearer $L58"
# => 404  (Postgres Row-Level Security, not a hand-written view filter)
```

### Rule 2 — a retried send doesn't double-deliver

```bash
# Send the same (already sending/sent) announcement again — the "looks stuck, click again" case:
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/announcements/$AID/send/ \
  -H "Authorization: Bearer $TOKEN"
# => 409  (approved->sending is a conditional UPDATE; a second send matches no row)
```

**Automatic restart recovery:** worker startup and Celery beat (every 30 seconds)
requeue announcements still in `sending`, including sends whose initial queue publish
failed. Tasks acknowledge only after completion and are redelivered if a worker dies.
Committed recipient rows are reused; only `pending` recipients are dispatched, so a
recovery scan can overlap another worker without pushing to a member twice. Discovery
reads only announcement/local IDs through the owner connection; dispatch still uses RLS.
The worker and scheduler restart automatically after a container process failure.

Recovery regression tests (isolated Postgres test database, no push/AI network calls):
`docker compose exec backend python manage.py test crewlink.test_recovery`.

Data-level invariants across **all** rows (run as the DB owner, so nothing is hidden):

```bash
docker compose exec backend python manage.py check_rules
# Rule 1 — cross-local recipients: 0 => PASS
# Rule 2 — recipients delivered >1x: 0; pushes == distinct recipients => PASS
```

Across two instances (needs the LB compose). Fires two sends at once through nginx and
shows exactly one is accepted:

```bash
python3 scripts/verify_lb.py
# one 202 and one 409, served by different instances; sent == total
```

Full end-to-end API check (14 assertions): `python3 scripts/verify_api.py`

## Notes

- **Real push is mocked** to a `push_log` table (per brief); the worker logs each dispatch.
- **AI draft:** `POST /api/ai/draft/ {raw_text}`. With no `OPENAI_API_KEY` set it uses an
  offline fallback so the feature still works; set `OPENAI_API_KEY` (+ optional
  `OPENAI_MODEL`) in `.env` to use OpenAI. Output is always a draft that a human must
  approve before it can send.
- **AI billing errors:** An active key can still return `429 credit_balance_exhausted`
  when its organization's API credit balance is depleted. Add credits in that
  organization's [API billing settings](https://platform.openai.com/settings/organization/billing/).
  The draft warning distinguishes exhausted credits, quota errors, and temporary rate
  limits; it keeps the original note editable. See [OpenAI's error codes](https://developers.openai.com/api/docs/guides/error-codes).
  Run offline regression checks with `docker compose exec backend python manage.py test crewlink config`.
- **Dev conveniences, flagged for prod:** `DEBUG=1`, open CORS, no password validators, a
  default secret in compose. Change these before deploying.
