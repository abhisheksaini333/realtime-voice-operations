# Local operation and recovery

## Build the media topology

The complete media path was tested on macOS ARM64 with Docker Desktop. Install Python3.11.11, Node20+, Go**1.24.2**, a C compiler and micromamba. Other systems need equivalent codecs and their own SIP/RTP addressing verification; the macOS explicit environment is not for Linux. Create the Python environment and console as shown in the README, then run from the repository root:

```sh
micromamba create -y -p "$PWD/.runtime/codecs" -f infra/native-codecs-osx-arm64.txt
export PKG_CONFIG="$PWD/.runtime/codecs/bin/pkgconf"
export PKG_CONFIG_PATH="$PWD/.runtime/codecs/lib/pkgconfig"
export CGO_ENABLED=1
export CGO_CFLAGS="-I$PWD/.runtime/codecs/include"
export CGO_LDFLAGS="-L$PWD/.runtime/codecs/lib -Wl,-rpath,$PWD/.runtime/codecs/lib"
export DYLD_LIBRARY_PATH="$PWD/.runtime/codecs/lib"
python scripts/build_sip.py
(cd fixtures && go build -o call ./cmd/call && go build -o speech ./cmd/speech)
docker compose up -d --wait
.runtime/native/livekit-sip --config infra/sip.yaml
```

The builder verifies the complete source archive and extracted file contents. It rejects a wrong Go version or modified extracted source without replacing user edits. Keep the bridge running; activate `.venv` in each application terminal and retain the native library environment for Go phones. Start the provisioner, worker, API and phone using the README commands.

The provisioner refuses an existing saved state or fixture number2000. It records its own trunk/rule IDs in `.runtime/demo-dispatch.json`; `python scripts/demo.py remove` removes only those resources after calls finish. The call helper correlates its observed SIP Call-ID with one pending ledger operation before writing the outcome.

| Port | Purpose |
|---|---|
| 18140 / 18141 / 18142 | LiveKit HTTP, RTC TCP and RTC UDP |
| 18149 | Loopback Redis |
| 18150;18500–18520 | Host SIP signaling and RTP |
| 18160;18800–18820 | Asterisk SIP and RTP |
| 18170 | Operator console/API |

Configured entry points bind to loopback. SIP fixture authentication is `voiceops` / `local-pass`; the PBX registrar fixture is `1002` / `local-operator-pass`. These are public demo credentials. Asterisk's `operator` answers with Echo, `reject` returns busy, and `unanswered` rings for three seconds. There are no carrier routes.

## Console and service control

To configure principals, omit `--demo` and set `VOICEOPS_TOKENS` to a JSON mapping from long random keys to `{ "name": "operator-name", "owner": "team-name", "role": "operator" }`. Roles are `viewer`, `operator` and `admin`. Protect the local environment file; match the worker's `--owner` to the desired owner. Viewer responses omit transcripts/audio. Administrator admission controls affect this single-worker service.

Transcript and recording retention start off. Save consent with an attestation before expecting retained data; withdrawal deletes the corresponding live data. Review the exact destination/version/digest/expiry before approval. Cancel before dispatch when needed. An uncertain operation blocks another transfer until independent evidence resolves it.

Drain in the admin UI or send SIGTERM to the worker. Admission stops immediately. SIGTERM allows ten seconds for the call to finish, then requests hangup. If control remains unavailable, bounded shutdown retains unresolved state rather than inventing a successful hangup. A replacement worker reconciles and terminates orphaned calls; it cannot reconstruct an old inference session. After active calls end, an administrator can resume admission. Restart alone does not clear a persisted drain.

Use `--speech-config /path/to/config.json` to select local intake instead of echo. [Speech setup](speech.md) describes the engines, configuration, consent behavior and manual turn controls. Only one speech-enabled call has been qualified.

## Verify

```sh
python -m unittest discover -s tests -v
npm --prefix frontend test
python scripts/verify_registration.py
python scripts/verify_transfers.py
python scripts/verify_worker_runtime.py
python scripts/verify_repeated_calls.py
```

SIP checks require the running topology and exclusive fixture number2000; remove the interactive demo dispatch first. `SIP_FIXTURE` selects an absolute phone executable; `VOICEOPS_EVIDENCE` selects a synthetic evidence directory. The media failure check uses `--allow-test-faults`, enabling SIGUSR1 to stop application echo output. It does not claim raw network RTP silence detection.

The [frontend guide](../frontend/README.md) distinguishes browser contracts from its actual-call acceptance test. Hosted CI covers portable core/UI contracts; it does not provide a SIP media environment, carrier or model-quality measurement.

## Backup, restore and stop

```sh
python -m voiceops.storage backup .runtime/calls.sqlite backups/calls-snapshot.sqlite
python -m voiceops.storage restore backups/calls-snapshot.sqlite .runtime/restored-calls.sqlite
```

SQLite's snapshot API includes committed WAL content. Commands refuse to overwrite the destination. Retain the adjacent checksum/integrity JSON. Stop worker/API before switching both to `--database .runtime/restored-calls.sqlite`; the next worker reconciles against actual participants. Restoring records cannot recreate a SIP dialog.

Snapshots contain opted-in audio/transcripts and must stay private. Live withdrawal cannot erase separately retained backups; manage those according to your retention policy. Do not copy only an active database file while ignoring its WAL.

After calls finish, stop worker/API, remove the recorded demo dispatch, stop the native bridge, then `docker compose stop`. `docker compose down` removes this project's containers/network. Avoid machine-wide cleanup commands.
