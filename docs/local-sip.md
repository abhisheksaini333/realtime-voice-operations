# Local SIP verification

The fixture calls an authenticated LiveKit SIP trunk, exchanges a frequency-coded PCMU signal, sends DTMF, receives REFER and opens a second real SIP dialog at Asterisk. The `operator` destination is an **automated echo endpoint**. It proves a second answered dialog and continuing media; it does not represent an attended human call.

`reject` returns busy. `unanswered` rings for three seconds and terminates without answering. Both checks require the original call to retain bidirectional media without a BYE. An API result alone never satisfies transfer acceptance.

The adapted upstream signal observer has a cancellation and cleanup fix: its previous completed observer could block the RTP reader, preventing a second media check. Original copyright and Apache license are retained under `fixtures/sipphone`.

Build with Go 1.24.2, a C compiler, pkg-config, libopus, opusfile, libogg and libsoxr installed. From `fixtures`, run `go build -o call ./cmd/call`. Set `SIP_FIXTURE` to the built executable and run `python scripts/verify_transfers.py` using the locked LiveKit environment. Full topology setup is described in the operating runbook.

Only loopback fixture SIP destinations are supported. Asterisk needs `media_address=127.0.0.1` in this native-host/Docker topology; otherwise its container address is unreachable from the host phone. These files have no carrier routes or public telephone numbers.
