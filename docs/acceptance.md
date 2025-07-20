# Acceptance and measured limits

The final local core suite passes **67 tests**, including ledger state, exact transfer approval, privacy, session replacement, HTTP bounds, snapshots and partial fixture cleanup. The speech suite has **18 tests:16 pass and two require opt-in runtime services**. Those optional paths were also exercised separately with actual local engines and RTC/SIP services. Frontend verification passes five unit/build checks and three real-API Chromium contract flows; a separate answered-call UI check verified consent, authenticated audio playback and observed transfer completion.

| Capability | Evidence and reproduction |
|---|---|
| Real SIP/media/DTMF | Authenticated INVITE/answer/ACK/BYE path, decoded PCMU both ways and digit5; `scripts/verify_transfers.py` |
| Answered transfer | Second Asterisk dialog and continuing audio after original BYE; public `scripts/demo.py call --target operator` |
| Rejected/unanswered transfer | Busy and three-second ring fixtures retain original media during failure; no original BYE before finite caller cleanup |
| Reused phone identity | Two sequential calls create different records and both terminate; `scripts/verify_repeated_calls.py` |
| PBX registrar | Wrong password401, valid registration200, deregistration200; `scripts/verify_registration.py` |
| Draining and restart | Natural finish, deadline hangup, SIGKILL/restart and stopped application output; `scripts/verify_worker_runtime.py` |
| Unavailable control | Paused owned LiveKit container; worker exited unresolved in23.303s; unpause/restart confirmed remote BYE and ended state |
| Exact approval/reconciliation | Stale version, expiry, changed destination, duplicate claim, cancellation and uncertain retry tests; actual demo reconciles matching SIP Call-ID |
| Privacy/ownership | Revoke/regrant fencing including identical timestamps, default-off transcript, authenticated audio and owner isolation |
| Speech worker | Same worker handled two actual SIP calls:zero retained transcripts without consent, two after explicit consent; both ended and caller recordings contained returned speech |
| SDK lifecycle | Actual RTC positive/current-SID and negative/replaced-SID DTMF, interruption and repeated cleanup |
| Backup/restore | Consistent active-WAL snapshot, checksum/integrity verification and tamper/no-overwrite regressions |
| Operator console | Actual answered-call transfer, audio and responsive views; viewer/cancel/unknown-state browser contracts |

[SIP outcome measurements](../evaluation/results/sip-verification.json) contain the portable summaries. Raw local execution logs retain earlier failures and absolute event times; published summaries focus on outcomes and durations. Reproduction commands and requirements are in [the runbook](runbook.md).

## Local model observations

Both profiles used Whisper tiny.en and eSpeak NG with synthetic English audio, one call at a time. The caller said “My name is John. I cannot log into my account.” Both validated model outputs selected `account`; synthesized replies were recorded at the SIP phone and independently transcribed.

| Profile | Complete turn | Returned audio samples | Outcome |
|---|---:|---:|---|
| Qwen2.5-1.5B | 20,040ms | 93,048 | Account request recognized in returned audio |
| Qwen3-0.6B, non-thinking prompt | 17,672ms | 93,048 | Account request recognized in returned audio |

[Qwen2.5 results](../evaluation/results/speech-qwen25.json), [Qwen3 results](../evaluation/results/speech-qwen3.json) and [integrated worker results](../evaluation/results/speech-worker.json) separate stage latency, generated text, accepted RTC frames and independently recognized returned audio. Worker-call wall times include the call fixture and verification and differ from these standalone turn timings. The final standalone recordings transcribed the full confirmation correctly. The integrated worker fixture still misrecognized the final word in one call; earlier confirmations had less reliable names/category recognition, motivating clearer sentence boundaries. Earlier attempts exposed CPU deadline failures, a packet-duration fixture error and an account/billing classification error; correcting the fixture/prompt did not establish broad model accuracy. These few synthetic cases are not a quality or capacity benchmark.

## Practical boundaries

The verified topology is local and the second endpoint is automated. There is no PSTN or attended-human proof. Echo health monitors application RTC flow, not raw RTP silence: bridge concealment can keep frames arriving. Speech has explicit DTMF turn boundaries, up to20seconds and20submissions, without automatic spoken barge-in. Recording retains only caller PCM for up to60seconds. The model proposes an intake category/name and has no call-control authority.

Call control uses a SID/dialog preflight before upstream identity-addressed actions; that is not an atomic server-side conditional update. One private room and one active call are required. Production IAM, public TLS, clustering, carrier routing, longer retention and throughput qualification remain separate work. Hosted CI runs portable contracts after publication; it does not replace the local SIP/model evidence.
