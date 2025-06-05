# Relay operator console

The React console is served by the voice API from `frontend/dist` so all requests use the same origin. It does not run a separate public development server.

```sh
npm ci --ignore-scripts
npm test
npm run build
```

Open the voice API on `http://127.0.0.1:18170` and paste a configured access key. The key stays in page memory; locking or reloading clears it. The server remains the authority for identity, ownership, roles and every operation.

The workspace polls observed calls and media every three seconds. Operators can review the exact target URI, call version, digest and expiry before approving a transfer. Closing a review preserves the pending proposal; reopening it does not create another operation. Cancel proposal retires it before dispatch. Expired or changed proposals cannot be approved. Dispatched or uncertain transfers prevent another transfer until the service resolves them.

Consent toggles require an attestation. Recording playback fetches authenticated audio into a temporary object URL, which is revoked when changing calls, withdrawing the recording preference, or locking. The console has no transcription model: it displays transcript entries actually retained by the configured service. Viewer sessions cannot access write controls or audio, and only administrators see service admission controls.

The dependency lock reuses the audited React 18.3.1 / Vite 5.4.11 profile, with transitive versions published before January 2025. Vite is used to build static assets only. These compatibility dependencies retain known historical advisories; do not expose a Vite development server or claim a patched production profile from this lock.

`tests/model.test.mjs` exercises stale and expired approvals, session roles and terminal call controls. Full browser verification additionally needs the running API and an answered local SIP fixture; the implementation evidence records actual transfer, media and responsive-layout checks separately from unit tests.


From the repository root, run the browser contract suite with a Python environment containing Playwright:

```sh
RUN_VOICE_UI_TESTS=1 python -m unittest discover -s frontend/tests -p test_workspace.py -v
```

That suite uses the real API and an isolated journal, and refuses any SIP dispatch. It covers role visibility, cancellation before dispatch, and uncertainty with retries blocked.

`frontend/tests/verify_live_call.py` is a separate, mutating acceptance check for an already answered local SIP fixture. Set `VOICE_UI_KEY` to its operator key, optionally `CHROMIUM_PATH` and `VOICE_UI_EVIDENCE_DIR`, then run the script from the repository root. It records fixture consent and approves a real handoff. Success requires the transfer operation to reach observed `connected`, not merely an API acknowledgement. Do not run it against personal or production calls.
