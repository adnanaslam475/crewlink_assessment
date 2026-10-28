import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
SAMPLE = "aa270000-0000-4000-8000-000000000001"

PASS, FAIL = [], []


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login(u, p):
    s, d = call("POST", "/api/auth/login/", body={"username": u, "password": p})
    assert s == 200, (s, d)
    return d["access"]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + detail) if detail else ""))


# --- Leadership flow ---------------------------------------------------------
t27 = login("lead27", "crewlink-dev")
s, me = call("GET", "/api/me/", t27)
check("me = Local 27 leadership", me.get("local_name") == "Local 27" and me.get("role") == "leadership", str(me))

s, a = call("POST", "/api/announcements/", t27,
            {"title": "Test callout", "body": "hello crew", "push_preview": "test",
             "needs_ack": True, "classification_filter": None})
aid = a.get("id")
check("create draft", s == 201 and a.get("state") == "draft", str(s))

s, _ = call("POST", f"/api/announcements/{aid}/approve/", t27)
check("approve (draft -> approved)", s == 200, str(s))

s, sd = call("POST", f"/api/announcements/{aid}/send/", t27)
check("send -> 202 Accepted", s == 202, str(sd))

final = None
for _ in range(30):
    s, st = call("GET", f"/api/announcements/{aid}/status/", t27)
    final = st
    if st.get("state") == "sent":
        break
    time.sleep(1)
check("fan-out completed (state=sent)", bool(final) and final.get("state") == "sent", str(final))
check("sent == total == 2001 active members", bool(final) and final.get("sent") == final.get("total") == 2001, str(final))

# --- Rule 2: retry does not double-send --------------------------------------
s, d = call("POST", f"/api/announcements/{aid}/send/", t27)
check("second send -> 409 (Rule 2, no second fan-out)", s == 409, str(s))

# --- Rule 1: cross-local reads are refused -----------------------------------
t58 = login("lead58", "crewlink-dev")
s, d = call("GET", f"/api/announcements/{aid}/", t58)
check("lead58 GET Local-27 announcement -> 404 (Rule 1)", s == 404, str(s))
s, d = call("GET", f"/api/announcements/{SAMPLE}/status/", t58)
check("lead58 status of Local-27 sample -> 404 (Rule 1)", s == 404, str(s))
s, d = call("GET", f"/api/announcements/{SAMPLE}/", t27)
check("lead27 sees its own sample announcement", s == 200, str(s))

# --- Roles + member flow -----------------------------------------------------
tm = login("member27", "crewlink-dev")
s, inbox = call("GET", "/api/inbox/", tm)
check("member27 inbox non-empty", s == 200 and isinstance(inbox, list) and len(inbox) >= 1,
      f"items={len(inbox) if isinstance(inbox, list) else inbox}")
s, d = call("POST", f"/api/inbox/{SAMPLE}/ack/", tm)
check("member27 can acknowledge the sample", s == 200, str(s))
s, d = call("POST", "/api/announcements/", tm, {"title": "x", "body": "y"})
check("member27 cannot create announcement -> 403 (role)", s == 403, str(s))

tm58 = login("member58", "crewlink-dev")
s, d = call("POST", f"/api/inbox/{SAMPLE}/ack/", tm58)
check("member58 cannot ack Local-27 sample -> 404 (Rule 1)", s == 404, str(s))

print("\nSUMMARY: %d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED:", FAIL)
    raise SystemExit(1)
