"""Run the local operator console and API."""
import argparse
import json
import os
from pathlib import Path

from .api import server
from .ledger import CallLedger
from .livekit_gateway import LiveKitGateway
from .supervisor import Supervisor
from .transfers import TransferService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=".runtime/calls.sqlite")
    parser.add_argument("--port", type=int, default=18170)
    parser.add_argument("--ui", default="frontend/dist")
    parser.add_argument("--demo", action="store_true", help="Use the documented local fixture access key")
    args = parser.parse_args()
    tokens = json.loads(os.environ.get("VOICEOPS_TOKENS", "{}"))
    if args.demo:
        tokens = {"voiceops-local-operator-demo-key-32characters":
                  {"name": "local-operator", "owner": "local-team", "role": "admin"}}
    ledger = CallLedger(args.database, max_active_calls=1)
    transfers = TransferService(ledger, targets={name: f"sip:{name}@127.0.0.1:18160"
                                                for name in ("operator", "reject", "unanswered")})
    gateway = LiveKitGateway(os.environ.get("LIVEKIT_URL", "http://127.0.0.1:18140"),
                             os.environ.get("LIVEKIT_API_KEY", "devkey"),
                             os.environ.get("LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"))
    http = server(ledger, transfers, Supervisor(ledger, transfers, gateway), tokens,
                  port=args.port, ui_dir=Path(args.ui))
    print(json.dumps({"event": "ready", "url": f"http://127.0.0.1:{http.server_port}"}), flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()


if __name__ == "__main__":
    main()
