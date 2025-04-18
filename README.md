# Realtime Voice Operations

A workspace for supervising SIP voice calls, verifying media, and transferring
callers to an answered operator endpoint. The call ledger records observed
outcomes and keeps uncertain operations visible for reconciliation.

The reference topology uses LiveKit, its SIP bridge, Redis, and local SIP peers.
The Python application owns admission, approvals, lifecycle records and recovery;
the bridge owns signaling and media transport.

## Development

Use Python 3.11 or newer. Run the core checks from this directory:

```sh
python -m unittest discover -s tests -v
```

The acceptance suite and operator console are developed alongside the core.
See [the architecture](docs/architecture.md) for the integration boundaries.
