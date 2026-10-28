import json
import threading
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8080"  # through nginx


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def login(u, p):
    s, h, d = call("POST", "/api/auth/login/", body={"username": u, "password": p})
    assert s == 200, (s, d)
    return d["access"]


t = login("lead27", "crewlink-dev")

# Show both instances actually serve traffic through nginx.
seen = set()
for _ in range(6):
    s, h, d = call("GET", "/api/me/", t)
    if h.get("X-Served-By"):
        seen.add(h.get("X-Served-By"))
print("instances seen via nginx:", seen)

# Create + approve an announcement, then fire TWO sends concurrently.
s, h, a = call("POST", "/api/announcements/", t,
               {"title": "LB test", "body": "across instances", "push_preview": "lb",
                "needs_ack": False, "classification_filter": None})
aid = a["id"]
call("POST", f"/api/announcements/{aid}/approve/", t)

results = []


def fire():
    s, h, d = call("POST", f"/api/announcements/{aid}/send/", t)
    results.append((s, h.get("X-Served-By")))


threads = [threading.Thread(target=fire) for _ in range(2)]
for x in threads:
    x.start()
for x in threads:
    x.join()

print("concurrent send results (status, served-by):", results)
codes = sorted(s for s, _ in results)
ok_guard = codes == [202, 409]
print(("PASS  " if ok_guard else "FAIL  ")
      + f"exactly one send accepted across instances: {codes}")

st = {}
for _ in range(30):
    s, h, st = call("GET", f"/api/announcements/{aid}/status/", t)
    if st.get("state") == "sent":
        break
    time.sleep(1)
print("final status:", st)
ok_nodouble = st.get("sent") == st.get("total")
print(("PASS  " if ok_nodouble else "FAIL  ")
      + f"no double delivery: sent={st.get('sent')} total={st.get('total')}")

raise SystemExit(0 if (ok_guard and ok_nodouble) else 1)
