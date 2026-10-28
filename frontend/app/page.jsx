"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function api(path, { method = "GET", token, body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

function countsAreComplete(status, needsAck) {
  return status?.state === "sent"
    && status.read === status.total
    && (!needsAck || status.acked === status.total);
}

export default function Page() {
  const [token, setToken] = useState(null);
  const [me, setMe] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("crewlink_token");
    if (t) setToken(t);
  }, []);

  useEffect(() => {
    if (!token) return;
    api("/api/me/", { token })
      .then(setMe)
      .catch(() => {
        localStorage.removeItem("crewlink_token");
        setToken(null);
      });
  }, [token]);

  function logout() {
    localStorage.removeItem("crewlink_token");
    setToken(null);
    setMe(null);
  }

  if (!token || !me) {
    return <Login setToken={setToken} error={error} setError={setError} />;
  }
  if (me.role !== "leadership") {
    return (
      <Shell me={me} logout={logout}>
        <div className="card">
          This screen is for leadership. Logged in as a member — members use the
          inbox API (<code>/api/inbox/</code>).
        </div>
      </Shell>
    );
  }
  return (
    <Shell me={me} logout={logout}>
      <Compose token={token} />
    </Shell>
  );
}

function Shell({ me, logout, children }) {
  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: 20 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>CrewLink — Callout</h1>
        <div className="row">
          <span className="pill">
            {me.local_name} · {me.username} · {me.role}
          </span>
          <button className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </div>
      <p className="muted">
        You can only send to <b>{me.local_name}</b> — one local per account (Rule 1).
      </p>
      {children}
    </div>
  );
}

function Login({ setToken, error, setError }) {
  const [username, setUsername] = useState("lead27");
  const [password, setPassword] = useState("crewlink-dev");

  async function submit(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await api("/api/auth/login/", {
        method: "POST",
        body: { username, password },
      });
      localStorage.setItem("crewlink_token", data.access);
      setToken(data.access);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "80px auto", padding: 20 }}>
      <h1 style={{ fontSize: 22 }}>CrewLink</h1>
      <form className="card" onSubmit={submit}>
        <label>Username</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: "100%" }} />
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: "100%" }} />
        <button style={{ width: "100%", marginTop: 14 }}>Log in</button>
        {error && <p style={{ color: "#b91c1c" }}>{error}</p>}
        <p className="muted">Try lead27 / crewlink-dev (leadership, Local 27).</p>
      </form>
    </div>
  );
}

function Compose({ token }) {
  const [raw, setRaw] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState("");
  const [needsAck, setNeedsAck] = useState(true);
  const [classification, setClassification] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNote, setAiNote] = useState("");

  const [ann, setAnn] = useState(null); // {id, state, needs_ack}
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const announcementId = ann?.id;
  const acknowledgementRequired = ann?.needs_ack ?? true;
  const countsComplete = countsAreComplete(status, acknowledgementRequired);
  const trackStatus = (ann?.state === "sending" || ann?.state === "sent") && !countsComplete;

  useEffect(() => {
    if (!announcementId || !trackStatus) return;

    let cancelled = false;
    let complete = false;
    let inFlight = false;
    let timer;
    let interval = 3000;

    async function refreshStatus() {
      if (cancelled || complete || inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      try {
        const s = await api(`/api/announcements/${announcementId}/status/`, { token });
        if (cancelled) return;
        complete = countsAreComplete(s, acknowledgementRequired);
        interval = s.state === "sent" ? 10000 : 3000;
        setStatus(s);
        setStatusError("");
        setAnn((a) => (a?.id === announcementId ? { ...a, state: s.state } : a));
      } catch (e) {
        if (!cancelled) setStatusError("Could not refresh counts. Retrying automatically…");
      } finally {
        inFlight = false;
        if (!cancelled && !complete && document.visibilityState !== "hidden") {
          timer = setTimeout(refreshStatus, interval);
        }
      }
    }

    function onVisibilityChange() {
      clearTimeout(timer);
      if (document.visibilityState !== "hidden") refreshStatus();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    refreshStatus();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [announcementId, trackStatus, acknowledgementRequired, token]);

  async function generate() {
    setAiBusy(true);
    setAiNote("");
    try {
      const d = await api("/api/ai/draft/", { method: "POST", token, body: { raw_text: raw } });
      setTitle(d.title || "");
      setBody(d.body || "");
      setPreview(d.push_preview || "");
      setAiNote(d.warning ? `⚠ ${d.warning}` : `Draft from ${d.source}. Review before approving.`);
    } catch (e) {
      setAiNote(e.message);
    } finally {
      setAiBusy(false);
    }
  }

  async function saveDraft() {
    setBusy(true);
    setErr("");
    try {
      const a = await api("/api/announcements/", {
        method: "POST",
        token,
        body: {
          title,
          body,
          push_preview: preview,
          needs_ack: needsAck,
          classification_filter: classification || null,
        },
      });
      setAnn({ id: a.id, state: a.state, needs_ack: a.needs_ack ?? needsAck });
      setStatus(null);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    setErr("");
    try {
      const a = await api(`/api/announcements/${ann.id}/approve/`, { method: "POST", token });
      setAnn((x) => ({ ...x, state: a.state }));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setErr("");
    try {
      const a = await api(`/api/announcements/${ann.id}/send/`, { method: "POST", token });
      setAnn((x) => (x?.id === a.id ? { ...x, state: a.state } : x));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setAnn(null);
    setStatus(null);
    setStatusError("");
    setRaw("");
    setTitle("");
    setBody("");
    setPreview("");
    setClassification("");
    setErr("");
    setAiNote("");
  }

  const state = ann?.state;

  return (
    <div className="card">
      <label>Paste the messy note (optional — AI cleans it up)</label>
      <textarea
        rows={2}
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder="emergency mtg thurs 6pm hall re: contractor pulling crews, EVERYONE needs to be there"
        style={{ width: "100%" }}
      />
      <div className="row" style={{ marginTop: 6 }}>
        <button className="secondary" onClick={generate} disabled={aiBusy || !raw}>
          {aiBusy ? "Generating…" : "Generate draft (AI)"}
        </button>
        {aiNote && <span className="muted">{aiNote}</span>}
      </div>

      <label>Title</label>
      <input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: "100%" }} />

      <label>Body</label>
      <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} style={{ width: "100%" }} />

      <label>Push preview (≤120 chars) — {preview.length}/120</label>
      <input value={preview} maxLength={120} onChange={(e) => setPreview(e.target.value)} style={{ width: "100%" }} />

      <div className="row" style={{ marginTop: 10 }}>
        <label style={{ margin: 0 }}>
          <input type="checkbox" checked={needsAck} onChange={(e) => setNeedsAck(e.target.checked)} /> needs acknowledgement
        </label>
        <div>
          <label style={{ margin: 0 }}>Classification filter (optional)</label>
          <input
            value={classification}
            onChange={(e) => setClassification(e.target.value)}
            placeholder="e.g. Apprentice 3rd Year — blank = whole local"
            style={{ width: 320 }}
          />
        </div>
      </div>

      <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #e7e5e4" }} />

      <div className="row">
        <button onClick={saveDraft} disabled={busy || !title || !body || !!ann}>
          1 · Save draft
        </button>
        <button onClick={approve} disabled={busy || state !== "draft"}>
          2 · Approve
        </button>
        <button onClick={send} disabled={busy || state !== "approved"}>
          3 · Send
        </button>
        {ann && (
          <button className="secondary" onClick={reset}>
            New callout
          </button>
        )}
        {state && <span className="pill">state: {state}</span>}
      </div>
      {err && <p style={{ color: "#b91c1c" }}>{err}</p>}
      {statusError && <p role="status" style={{ color: "#b91c1c" }}>{statusError}</p>}

      {status && (
        <div style={{ marginTop: 18 }}>
          <div className="row">
            <div className="stat"><b>{status.total}</b>recipients</div>
            <div className="stat"><b>{status.sent}</b>sent</div>
            <div className="stat"><b>{status.read}</b>read</div>
            <div className="stat"><b>{status.acked}</b>acknowledged</div>
          </div>
          <p className="muted">
            {countsComplete
              ? (acknowledgementRequired
                ? "Everyone has read and acknowledged this callout."
                : "Everyone has read this callout.")
              : (status.state === "sent"
                ? "Sent. Read and acknowledgement counts keep updating."
                : "Sending…")}
          </p>
        </div>
      )}
    </div>
  );
}
