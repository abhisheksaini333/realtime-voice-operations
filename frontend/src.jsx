import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  approvalState,
  permissions,
  callIsActive,
  readable,
  when,
  pendingProposal,
} from "./model.js";
import "./style.css";

function Badge({ value }) {
  return (
    <span className={"badge " + (value || "unknown")}>{readable(value)}</span>
  );
}
function App() {
  const [key, setKey] = useState(""),
    [session, setSession] = useState(null);
  const [calls, setCalls] = useState([]),
    [detail, setDetail] = useState(null),
    [targets, setTargets] = useState({});
  const [drain, setDrain] = useState({}),
    [selectedId, setSelectedId] = useState(null);
  const [target, setTarget] = useState("operator"),
    [proposal, setProposal] = useState(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [lastSync, setLastSync] = useState(null),
    [now, setNow] = useState(Date.now() / 1000);
  const [consent, setConsent] = useState({
      transcript: false,
      recording: false,
    }),
    [attestation, setAttestation] = useState("");
  const [hangupReview, setHangupReview] = useState(false),
    [drainSeconds, setDrainSeconds] = useState("60");
  const [recording, setRecording] = useState(null);
  const token = useRef(""),
    epoch = useRef(0),
    currentId = useRef(null),
    refreshController = useRef(null),
    activeRecording = useRef(null);
  const rights = permissions(session?.role);
  const call = detail?.call;
  const gate = approvalState(proposal, call, now);
  const active = callIsActive(call);
  const existingProposal = pendingProposal(detail?.transfers, now);
  const pendingTransfer = detail?.transfers?.find(
    (t) =>
      ["dispatched", "outcome_unknown"].includes(t.state) ||
      (t.state === "approved" && t.expires_at > now),
  );

  async function api(path, data, signal) {
    const generation = epoch.current;
    const response = await fetch("/api" + path, {
      method: data === undefined ? "GET" : "POST",
      headers: {
        Authorization: "Bearer " + token.current,
        "Content-Type": "application/json",
      },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal,
    });
    const payload = await response.json();
    if (generation !== epoch.current)
      throw new DOMException("Workspace locked", "AbortError");
    if (!response.ok)
      throw new Error(
        payload.message || readable(payload.error) || "Request failed",
      );
    return payload;
  }
  function clearRecording() {
    if (activeRecording.current) URL.revokeObjectURL(activeRecording.current);
    activeRecording.current = null;
    setRecording(null);
  }
  function lock() {
    epoch.current++;
    token.current = "";
    refreshController.current?.abort();
    currentId.current = null;
    setKey("");
    setSession(null);
    setCalls([]);
    setDetail(null);
    setSelectedId(null);
    setProposal(null);
    setError("");
    setNotice("");
    setHangupReview(false);
    setAttestation("");
    setBusy(false);
    clearRecording();
  }
  async function refresh(id = currentId.current) {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const generation = epoch.current;
    try {
      const [listing, configured, selected] = await Promise.all([
        api("/calls", undefined, controller.signal),
        api("/targets", undefined, controller.signal),
        id
          ? api(
              "/calls/" + encodeURIComponent(id),
              undefined,
              controller.signal,
            )
          : Promise.resolve(null),
      ]);
      if (generation !== epoch.current || controller.signal.aborted) return;
      setCalls(listing.calls || []);
      setDrain(listing.drain || {});
      setTargets(configured.targets || {});
      setLastSync(Date.now() / 1000);
      if (id === currentId.current) setDetail(selected);
    } catch (e) {
      if (e.name !== "AbortError" && generation === epoch.current)
        setError(e.message);
    }
  }
  async function work(fn) {
    setBusy(true);
    setError("");
    setNotice("");
    const generation = epoch.current;
    try {
      await fn();
    } catch (e) {
      if (generation === epoch.current) setError(e.message);
    } finally {
      if (generation === epoch.current) setBusy(false);
    }
  }
  async function connect(event) {
    event.preventDefault();
    await work(async () => {
      token.current = key;
      const value = await api("/session");
      setSession(value);
      setKey("");
      await refresh();
    });
  }
  function choose(id) {
    currentId.current = id;
    setSelectedId(id);
    setDetail(null);
    setProposal(null);
    setHangupReview(false);
    setAttestation("");
    clearRecording();
    refresh(id);
  }
  useEffect(() => {
    if (!session) return;
    const timer = setInterval(() => refresh(), 3000);
    return () => {
      clearInterval(timer);
      refreshController.current?.abort();
    };
  }, [session]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    setConsent({
      transcript: Boolean(detail?.consent?.transcript),
      recording: Boolean(detail?.consent?.recording),
    });
  }, [selectedId, detail?.consent?.transcript, detail?.consent?.recording]);
  useEffect(() => {
    if (!consent.recording) clearRecording();
  }, [consent.recording]);
  useEffect(
    () => () => {
      if (activeRecording.current) URL.revokeObjectURL(activeRecording.current);
    },
    [],
  );
  async function proposeTransfer() {
    if (existingProposal) {
      setProposal(existingProposal);
      return;
    }
    await work(async () => {
      const id = currentId.current;
      const result = await api("/calls/" + id + "/transfer", { target });
      if (currentId.current === id) setProposal(result);
      await refresh(id);
    });
  }
  async function cancelTransfer() {
    await work(async () => {
      await api("/transfers/" + proposal.id + "/cancel", {});
      setProposal(null);
      setNotice("Transfer proposal canceled before dispatch.");
      await refresh();
    });
  }
  async function approveTransfer() {
    if (!gate.allowed) return;
    await work(async () => {
      const exact = proposal;
      const result = await api("/transfers/" + exact.id + "/approve", {
        digest: exact.digest,
      });
      setProposal(null);
      setNotice(
        result.state === "outcome_unknown"
          ? "Transfer outcome is uncertain. Wait for observed evidence; do not retry."
          : "Transfer dispatched. Waiting for the destination to answer.",
      );
      await refresh();
    });
  }
  async function saveConsent(event) {
    event.preventDefault();
    await work(async () => {
      await api("/calls/" + call.id + "/consent", { ...consent, attestation });
      setAttestation("");
      setNotice("Consent preference recorded.");
      await refresh();
    });
  }
  async function loadRecording() {
    await work(async () => {
      const id = call.id;
      const generation = epoch.current;
      const response = await fetch("/api/calls/" + id + "/recording", {
        headers: { Authorization: "Bearer " + token.current },
      });
      if (!response.ok) {
        const value = await response.json();
        throw new Error(
          value.message || "No consented recording is available.",
        );
      }
      const blob = await response.blob();
      if (generation !== epoch.current || id !== currentId.current) return;
      clearRecording();
      const url = URL.createObjectURL(blob);
      activeRecording.current = url;
      setRecording(url);
    });
  }
  return (
    <>
      <header className="masthead">
        <a className="brand" href="/" aria-label="Relay home">
          <span className="brand-mark">r</span>
          <span>
            relay<span className="brand-caption">VOICE OPERATIONS</span>
          </span>
        </a>
        <div className="header-right">
          <span className="local-label">
            <i /> Owned local environment
          </span>
          {session && (
            <>
              <span className="session-name">
                {session.name}
                <small>{session.role}</small>
              </span>
              <button className="quiet on-dark" onClick={lock}>
                Lock workspace
              </button>
            </>
          )}
        </div>
      </header>
      {!session ? (
        <main className="login">
          <section>
            <p className="eyebrow">CALLS. CONTEXT. CONTROL.</p>
            <h1>
              A clear view of
              <br />
              every call.
            </h1>
            <p className="login-copy">
              Supervise the conversation, review the destination, and follow the
              evidence through every handoff.
            </p>
            <div className="principles">
              <span>
                01<span>Observe media</span>
              </span>
              <span>
                02<span>Review the transfer</span>
              </span>
              <span>
                03<span>Confirm the outcome</span>
              </span>
            </div>
          </section>
          <form className="login-card" onSubmit={connect}>
            <span className="small-label">OPERATOR ACCESS</span>
            <h2>Open your workspace</h2>
            <label>
              Access key
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                required
                spellCheck={false}
              />
            </label>
            <p className="help">
              Use your local access key. It stays in memory and clears when you
              lock this workspace.
            </p>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button className="primary" disabled={busy}>
              {busy ? "Opening…" : "Open operations"}
              <span aria-hidden="true">↗</span>
            </button>
          </form>
        </main>
      ) : (
        <main className="workspace">
          <section className="page-title">
            <div>
              <p className="eyebrow">SUPERVISION DESK</p>
              <h1>Live operations</h1>
              <p>Observed call activity. Deliberate handoffs.</p>
            </div>
            <div className="sync">
              <span className={"sync-dot " + (error ? "warning" : "")} />
              <span>
                {error ? "Refresh needs attention" : "Workspace connected"}
                <small>Last refreshed {when(lastSync)}</small>
              </span>
              <button
                className="quiet"
                onClick={() => work(() => refresh())}
                disabled={busy}
                aria-label="Refresh calls"
              >
                ↻
              </button>
            </div>
          </section>
          {error && (
            <div className="message error" role="alert">
              {error}
              <button
                className="quiet"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                ×
              </button>
            </div>
          )}
          {notice && (
            <div className="message notice" role="status">
              {notice}
            </div>
          )}
          <section className="overview" aria-label="Service overview">
            <div>
              <span>Active conversations</span>
              <strong>
                {calls.filter(callIsActive).length.toString().padStart(2, "0")}
              </strong>
            </div>
            <div>
              <span>Call history</span>
              <strong>{calls.length.toString().padStart(2, "0")}</strong>
            </div>
            <div>
              <span>Admission</span>
              <strong className="word">
                {drain.draining ? "Draining" : "Open"}
              </strong>
              <small>
                {drain.draining
                  ? drain.complete
                    ? "All active calls have settled"
                    : `Deadline ${when(drain.deadline)}`
                  : "Ready for new calls"}
              </small>
            </div>
            <div className="admission-controls">
              {rights.admin ? (
                <>
                  <label>
                    Drain window
                    <select
                      value={drainSeconds}
                      onChange={(e) => setDrainSeconds(e.target.value)}
                      disabled={busy}
                    >
                      <option value="30">30 seconds</option>
                      <option value="60">60 seconds</option>
                      <option value="120">2 minutes</option>
                    </select>
                  </label>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      work(async () => {
                        await api(
                          drain.draining ? "/resume" : "/drain",
                          drain.draining
                            ? {}
                            : { seconds: Number(drainSeconds) },
                        );
                        await refresh();
                      })
                    }
                  >
                    {drain.draining ? "Resume admission" : "Drain service"}
                  </button>
                </>
              ) : (
                <p className="help">
                  Service admission is managed by an administrator.
                </p>
              )}
            </div>
          </section>
          <div className="workspace-grid">
            <aside className="call-rail" aria-label="Call queue">
              <div className="section-heading">
                <h2>Conversations</h2>
                <span>{calls.length}</span>
              </div>
              <p className="help">Select a call to inspect its evidence.</p>
              {!calls.length ? (
                <div className="empty">
                  <span className="empty-symbol">⌁</span>
                  <h3>Standing by</h3>
                  <p>Calls will appear when the SIP worker observes them.</p>
                </div>
              ) : (
                calls.map((item) => (
                  <button
                    key={item.id}
                    className={
                      "call-row " + (item.id === selectedId ? "selected" : "")
                    }
                    onClick={() => choose(item.id)}
                    disabled={busy}
                    aria-pressed={item.id === selectedId}
                  >
                    <span className="caller">
                      {item.caller || "Unknown caller"}
                      <Badge value={item.status} />
                    </span>
                    <span className="call-meta">
                      {when(item.created_at)}
                      <code>{item.id.slice(0, 8)}</code>
                    </span>
                  </button>
                ))
              )}
            </aside>
            <div className="detail-column">
              {!detail ? (
                <section className="panel welcome">
                  <span className="empty-symbol">↗</span>
                  <h2>
                    {selectedId
                      ? "Loading the conversation…"
                      : "Keep the whole handoff in view."}
                  </h2>
                  <p>
                    Select a conversation to review media, consent and observed
                    events before taking action.
                  </p>
                </section>
              ) : (
                <>
                  <section
                    className="panel call-heading"
                    aria-label="Selected conversation"
                  >
                    <div>
                      <p className="eyebrow">
                        CONVERSATION / {call.id.slice(0, 8)}
                      </p>
                      <h2>{call.caller || "Unknown caller"}</h2>
                      <p>
                        {call.room} <span>·</span> Updated{" "}
                        {when(call.updated_at)}
                      </p>
                    </div>
                    <Badge value={call.status} />
                  </section>
                  {(call.status === "transfer_unknown" ||
                    pendingTransfer?.state === "outcome_unknown") && (
                    <div className="uncertain" role="status">
                      <strong>The transfer outcome is uncertain.</strong>
                      <p>
                        The journal does not yet confirm which endpoint owns
                        this call. Wait for observed events or reconcile it
                        through the service runbook. A second transfer is
                        blocked.
                      </p>
                    </div>
                  )}
                  <section
                    className="media-grid"
                    aria-label="Media observations"
                  >
                    <div className="panel media-card">
                      <span className="small-label">INBOUND AUDIO</span>
                      <div>
                        <strong>
                          {(
                            detail.media?.received_samples || 0
                          ).toLocaleString()}
                        </strong>
                        <span>samples received</span>
                      </div>
                      <p>
                        <span
                          className={
                            "signal " + (detail.media?.stale ? "stale" : "")
                          }
                        />
                        Last observed {when(detail.media?.last_received)}
                      </p>
                    </div>
                    <div className="panel media-card">
                      <span className="small-label">OUTBOUND AUDIO</span>
                      <div>
                        <strong>
                          {(detail.media?.sent_samples || 0).toLocaleString()}
                        </strong>
                        <span>samples sent</span>
                      </div>
                      <p>
                        <span
                          className={
                            "signal " + (detail.media?.stale ? "stale" : "")
                          }
                        />
                        {detail.media?.stale
                          ? "Media observation is stale"
                          : "Latest sample"}{" "}
                        · {when(detail.media?.last_sent)}
                      </p>
                    </div>
                  </section>
                  <section className="panel transfer-panel">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">HUMAN HANDOFF</p>
                        <h2>Transfer with an exact destination</h2>
                      </div>
                      <span className="step">REVIEW → APPROVE → OBSERVE</span>
                    </div>
                    <p className="help">
                      Approval binds this call version and the destination
                      below. A dispatched request stays pending until an outcome
                      is observed.
                    </p>
                    {rights.write ? (
                      <div className="transfer-controls">
                        <label>
                          Transfer destination
                          <select
                            value={target}
                            onChange={(e) => {
                              setTarget(e.target.value);
                              setProposal(null);
                            }}
                            disabled={
                              busy ||
                              !!pendingTransfer ||
                              !!existingProposal ||
                              call.status !== "answered"
                            }
                          >
                            {Object.entries(targets).map(([name, uri]) => (
                              <option key={name} value={name}>
                                {readable(name)} · {uri}
                              </option>
                            ))}
                          </select>
                        </label>
                        <button
                          className="primary"
                          onClick={proposeTransfer}
                          disabled={
                            busy ||
                            !!pendingTransfer ||
                            call.status !== "answered"
                          }
                        >
                          Review transfer <span aria-hidden="true">↗</span>
                        </button>
                      </div>
                    ) : (
                      <p className="role-note">
                        Your viewer role can inspect metadata. An operator must
                        review and approve transfers.
                      </p>
                    )}
                    {proposal && (
                      <section
                        className="approval-review"
                        aria-label="Exact transfer review"
                      >
                        <div className="section-heading">
                          <h3>Review the exact transfer</h3>
                          <span>Expires {when(proposal.expires_at)}</span>
                        </div>
                        <dl>
                          <div>
                            <dt>Destination</dt>
                            <dd>{proposal.target_uri}</dd>
                          </div>
                          <div>
                            <dt>Call</dt>
                            <dd>{proposal.call_id}</dd>
                          </div>
                          <div>
                            <dt>Call version</dt>
                            <dd>{proposal.call_version}</dd>
                          </div>
                          <div>
                            <dt>Approval fingerprint</dt>
                            <dd className="digest">{proposal.digest}</dd>
                          </div>
                        </dl>
                        {!gate.allowed && <p className="help">{gate.reason}</p>}
                        <div className="review-actions">
                          <button
                            className="quiet"
                            onClick={cancelTransfer}
                            disabled={
                              busy ||
                              !rights.write ||
                              !["proposed", "approved"].includes(proposal.state)
                            }
                          >
                            Cancel proposal
                          </button>
                          <button
                            className="quiet"
                            onClick={() => setProposal(null)}
                            disabled={busy}
                          >
                            Close review
                          </button>
                          <button
                            className="primary"
                            onClick={approveTransfer}
                            disabled={busy || !rights.write || !gate.allowed}
                          >
                            Approve exact transfer
                          </button>
                        </div>
                      </section>
                    )}
                    {!!detail.transfers?.length && (
                      <div className="transfer-history">
                        {detail.transfers.map((t) => (
                          <div key={t.id}>
                            <span>
                              {readable(t.target)}
                              <code>{t.target_uri}</code>
                            </span>
                            <Badge value={t.state} />
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                  <div className="support-grid">
                    <section className="panel">
                      <div className="section-heading">
                        <h2>Conversation record</h2>
                        <span className="small-label">CONSENT FIRST</span>
                      </div>
                      {rights.write ? (
                        <>
                          <form className="consent-form" onSubmit={saveConsent}>
                            <label className="check">
                              <input
                                type="checkbox"
                                checked={consent.transcript}
                                onChange={(e) =>
                                  setConsent({
                                    ...consent,
                                    transcript: e.target.checked,
                                  })
                                }
                                disabled={busy}
                              />
                              Retain transcript
                            </label>
                            <label className="check">
                              <input
                                type="checkbox"
                                checked={consent.recording}
                                onChange={(e) =>
                                  setConsent({
                                    ...consent,
                                    recording: e.target.checked,
                                  })
                                }
                                disabled={busy}
                              />
                              Retain recording
                            </label>
                            <label>
                              Consent confirmation
                              <input
                                value={attestation}
                                onChange={(e) => setAttestation(e.target.value)}
                                placeholder="How did the caller give consent?"
                                minLength={8}
                                maxLength={256}
                                required
                                disabled={busy}
                              />
                            </label>
                            <button
                              className="secondary"
                              disabled={busy || attestation.trim().length < 8}
                            >
                              Save consent
                            </button>
                          </form>
                          <div
                            className="transcript"
                            aria-label="Consented transcript"
                          >
                            {detail.transcript?.length ? (
                              detail.transcript.map((line, index) => (
                                <div key={index}>
                                  <div>
                                    <strong>{readable(line.speaker)}</strong>
                                    <time>{when(line.observed_at)}</time>
                                  </div>
                                  <p>{line.text}</p>
                                </div>
                              ))
                            ) : (
                              <p className="help">
                                No retained transcript. Text appears only when
                                consent and a transcript source are available.
                              </p>
                            )}
                          </div>
                          <button
                            className="quiet recording-button"
                            disabled={busy || !detail.consent?.recording}
                            onClick={loadRecording}
                          >
                            Load consented recording
                          </button>
                          {recording && (
                            <audio
                              controls
                              src={recording}
                              aria-label="Consented call recording"
                            />
                          )}
                        </>
                      ) : (
                        <p className="help">
                          Transcript, recording and consent controls are
                          restricted to operators.
                        </p>
                      )}
                    </section>
                    <section className="panel events">
                      <div className="section-heading">
                        <h2>Observed events</h2>
                        <span>{detail.events?.length || 0}</span>
                      </div>
                      <ol>
                        {[...(detail.events || [])]
                          .reverse()
                          .map((event, index) => (
                            <li key={index}>
                              <span className="event-dot" />
                              <div>
                                <strong>{readable(event.kind)}</strong>
                                <time>{when(event.observed_at)}</time>
                                <details>
                                  <summary>Event evidence</summary>
                                  <pre>
                                    {JSON.stringify(event.data, null, 2)}
                                  </pre>
                                </details>
                              </div>
                            </li>
                          ))}
                      </ol>
                      {!detail.events?.length && (
                        <p className="help">
                          No lifecycle observations recorded yet.
                        </p>
                      )}
                    </section>
                  </div>
                  {rights.write && active && (
                    <section className="end-call">
                      <div>
                        <h3>End this conversation</h3>
                        <p>
                          The caller will be disconnected after the service
                          observes hangup.
                        </p>
                      </div>
                      {!hangupReview ? (
                        <button
                          className="danger"
                          onClick={() => setHangupReview(true)}
                          disabled={busy}
                        >
                          End call…
                        </button>
                      ) : (
                        <div>
                          <button
                            className="quiet"
                            onClick={() => setHangupReview(false)}
                            disabled={busy}
                          >
                            Keep call
                          </button>
                          <button
                            className="danger"
                            disabled={busy}
                            onClick={() =>
                              work(async () => {
                                await api("/calls/" + call.id + "/hangup", {});
                                setHangupReview(false);
                                setNotice(
                                  "Hangup requested. Check the observed call state.",
                                );
                                await refresh();
                              })
                            }
                          >
                            Confirm end call
                          </button>
                        </div>
                      )}
                    </section>
                  )}
                </>
              )}
            </div>
          </div>
          <footer>
            RELAY / LOCAL VOICE OPERATIONS
            <span>
              Call state comes from service observations. Uncertainty stays
              visible.
            </span>
          </footer>
        </main>
      )}
    </>
  );
}
createRoot(document.getElementById("root")).render(<App />);
