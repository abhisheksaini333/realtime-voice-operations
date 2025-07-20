# Local API

API routes require a configured bearer key and the call's owner. Foreign Host/Origin values are rejected. Store a key in a local `VOICEOPS_KEY` environment variable:

```sh
curl -H "Authorization: Bearer $VOICEOPS_KEY" http://127.0.0.1:18170/api/session
curl -H "Authorization: Bearer $VOICEOPS_KEY" http://127.0.0.1:18170/api/calls
curl -H "Authorization: Bearer $VOICEOPS_KEY" http://127.0.0.1:18170/api/targets
```

`GET /api/calls/{id}` returns state, events, media, consent, transfers and operator-visible retained transcript. `GET /api/calls/{id}/recording` returns authenticated WAV data to operators/admins. Other owners' IDs are unavailable.

| POST route | JSON body | Effect |
|---|---|---|
| `/api/calls/{id}/transfer` | `{ "target": "operator" }` | Create an exact proposal |
| `/api/transfers/{id}/approve` | `{ "digest": "proposal-digest" }` | Approve, claim once and request transfer |
| `/api/transfers/{id}/cancel` | `{}` | Cancel before dispatch |
| `/api/calls/{id}/consent` | `{ "transcript": true, "recording": false, "attestation": "Caller consent recorded by operator" }` | Retention choices |
| `/api/calls/{id}/hangup` | `{}` | Request hangup and verify absence |
| `/api/drain` | `{ "seconds": 30 }` | Administrator stops admission; range10–300 |
| `/api/resume` | `{}` | Administrator resumes after active calls end |

POST requires one Content-Length and `Content-Type: application/json`, with an object no larger than64KiB. Connections and total request time are bounded. A viewer cannot POST. No public endpoint accepts fabricated transfer evidence.

Approval acknowledgement does not prove connection. Fetch the operation and inspect observed results; `outcome_unknown` blocks another transfer. A409 indicates input, approval or state validation failure;401/403 indicate authentication/authorization;404 hides unavailable objects. Do not repeat an external effect blindly after a lost response.
