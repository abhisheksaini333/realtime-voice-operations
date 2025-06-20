"""Provision or remove only this demo's authenticated local SIP dispatch."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from livekit import api
from voiceops.ledger import CallLedger
from voiceops.transfers import TransferService

try:
    from .fixture_lifecycle import FixtureCleanup, require_number_available
except ImportError:
    from fixture_lifecycle import FixtureCleanup, require_number_available


async def call(args):
    """Reconcile this owned SIP fixture's observed transfer, never a browser claim."""
    state = json.loads(Path(args.state).read_text())
    root = Path(__file__).resolve().parents[1]
    phone = await asyncio.create_subprocess_exec(
        os.environ.get("SIP_FIXTURE", str(root / "fixtures/call")),
        args.target,
        env=dict(os.environ, SIP_TIMEOUT_SECONDS="600"),
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        ready = await asyncio.wait_for(phone.stdout.readline(), 30)
        print(ready.decode().strip(), flush=True)
        output, _ = await asyncio.wait_for(phone.communicate(), 600)
        if phone.returncode:
            raise RuntimeError(
                "SIP fixture failed; the transfer outcome remains unresolved"
            )
        result = json.loads(output.splitlines()[-1])
        ledger = CallLedger(args.database)
        with ledger.connection() as db:
            rows = db.execute(
                """SELECT t.* FROM transfers t JOIN calls c ON c.id=t.call_id
                JOIN events e ON e.call_id=c.id AND e.kind='sip_dialog_observed'
                WHERE t.state IN ('dispatched','outcome_unknown') AND c.owner=? AND c.room=?
                AND json_extract(e.data,'$.sip_call_id')=?""",
                (args.owner, state["room"], result["refer_call_id"]),
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                "No unique pending transfer matches the observed SIP dialog"
            )
        operation = dict(rows[0])
        service = TransferService(
            ledger, targets={args.target: f"sip:{args.target}@127.0.0.1:18160"}
        )
        if result["outcome"] == "connected":
            outcome = "connected"
            evidence = {
                "dialog_id": result["second_dialog_id"],
                "answered": result["second_answered"],
                "audio_sent": result["audio_sent"],
                "audio_received": result["audio_received"],
            }
        else:
            # This finite fixture closes the original dialog after checking that
            # its media survived the rejected/unanswered transfer.
            outcome = "rejected" if result["outcome"] == "reject" else "unanswered"
            evidence = {
                "original_active": False,
                "audio_sent": result["audio_sent"],
                "audio_received": result["audio_received"],
                "media_survived_transfer": result["original_audio"],
            }
        reconciled = service.reconcile(
            operation["id"], args.owner, outcome, evidence, now=time.time()
        )
        print(json.dumps({"phone": result, "operation": reconciled}))
    finally:
        if phone.returncode is None:
            phone.kill()
            await phone.wait()


async def run(args):
    if args.operation == "call":
        await call(args)
        return
    state = Path(args.state)
    async with FixtureCleanup() as connection_cleanup:
        client = api.LiveKitAPI(
            "http://127.0.0.1:18140",
            os.environ.get("LIVEKIT_API_KEY", "devkey"),
            os.environ.get(
                "LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"
            ),
        )
        connection_cleanup.defer("LiveKit API client", client.aclose)
        if args.operation == "remove":
            value = json.loads(state.read_text())
            async with FixtureCleanup() as resources:
                resources.defer(
                    "recorded demo trunk",
                    lambda: client.sip.delete_sip_trunk(
                        api.DeleteSIPTrunkRequest(sip_trunk_id=value["trunk_id"])
                    ),
                )
                resources.defer(
                    "recorded demo dispatch rule",
                    lambda: client.sip.delete_sip_dispatch_rule(
                        api.DeleteSIPDispatchRuleRequest(
                            sip_dispatch_rule_id=value["rule_id"]
                        )
                    ),
                )
            state.unlink()
            print("Removed the recorded demo trunk and dispatch rule")
            return
        if state.exists():
            raise SystemExit(
                "A demo is already recorded; remove it before provisioning another"
            )
        await require_number_available(client, api)
        async with FixtureCleanup(on_error_only=True) as rollback:
            trunk = await client.sip.create_sip_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(
                        name="voiceops-reference",
                        numbers=["2000"],
                        auth_username="voiceops",
                        auth_password="local-pass",
                    )
                )
            )
            rollback.defer(
                "new demo trunk",
                lambda: client.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
                ),
            )
            rule = await client.sip.create_sip_dispatch_rule(
                api.CreateSIPDispatchRuleRequest(
                    name="voiceops-reference",
                    trunk_ids=[trunk.sip_trunk_id],
                    rule=api.SIPDispatchRule(
                        dispatch_rule_direct=api.SIPDispatchRuleDirect(
                            room_name=args.room
                        )
                    ),
                )
            )
            rollback.defer(
                "new demo dispatch rule",
                lambda: client.sip.delete_sip_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(
                        sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                    )
                ),
            )
            state.parent.mkdir(parents=True, exist_ok=True)
            value = {
                "trunk_id": trunk.sip_trunk_id,
                "rule_id": rule.sip_dispatch_rule_id,
                "room": args.room,
            }
            descriptor = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            rollback.defer(
                "incomplete demo state", lambda: state.unlink(missing_ok=True)
            )
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream, indent=2)
                stream.write("\n")
            print(json.dumps(value))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("provision", "remove", "call"))
    parser.add_argument("--state", default=".runtime/demo-dispatch.json")
    parser.add_argument("--room", default="voiceops-local")
    parser.add_argument("--database", default=".runtime/calls.sqlite")
    parser.add_argument("--owner", default="local-team")
    parser.add_argument(
        "--target", choices=("operator", "reject", "unanswered"), default="operator"
    )
    asyncio.run(run(parser.parse_args()))
