"""Verify local worker draining, failure recovery and media supervision."""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from livekit import api
from voiceops.ledger import CallLedger

try:
    from .fixture_lifecycle import (
        FixtureCleanup,
        delete_owned_room,
        require_number_available,
        stop_process,
    )
except ImportError:
    from fixture_lifecycle import (
        FixtureCleanup,
        delete_owned_room,
        require_number_available,
        stop_process,
    )

ROOT = Path(__file__).resolve().parents[1]
KEY = "local-demo-secret-voice-operations-123456"


async def verify(mode):
    evidence_directory = Path(
        os.environ.get("VOICEOPS_EVIDENCE", ".runtime/evidence")
    ).resolve()
    evidence_directory.mkdir(parents=True, exist_ok=True)
    room = "voiceops-worker-" + mode + "-" + str(time.time_ns())
    database = evidence_directory / (room + ".sqlite")
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    logs = []
    results = {"scenario": mode}
    async with FixtureCleanup() as cleanup:
        client = api.LiveKitAPI("http://127.0.0.1:18140", "devkey", KEY)
        cleanup.defer("LiveKit API client", client.aclose)
        await require_number_available(client, api)
        trunk = await client.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name=room,
                    numbers=["2000"],
                    auth_username="voiceops",
                    auth_password="local-pass",
                )
            )
        )
        cleanup.defer(
            "owned SIP trunk",
            lambda: client.sip.delete_sip_trunk(
                api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
            ),
        )
        rule = await client.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name=room,
                trunk_ids=[trunk.sip_trunk_id],
                rule=api.SIPDispatchRule(
                    dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=room)
                ),
            )
        )
        cleanup.defer(
            "owned dispatch rule",
            lambda: client.sip.delete_sip_dispatch_rule(
                api.DeleteSIPDispatchRuleRequest(
                    sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                )
            ),
        )
        cleanup.defer("owned worker room", lambda: delete_owned_room(client, api, room))

        async def worker():
            log = (
                evidence_directory / (room + "-worker-" + str(len(logs)) + ".log")
            ).open("wb")
            logs.append(log)
            cleanup.defer("worker log", log.close)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "voiceops.worker",
                "--database",
                str(database),
                "--room",
                room,
                "--media-timeout",
                "5",
                "--allow-test-faults",
                cwd=ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
            cleanup.defer(
                "worker process", lambda process=process: stop_process(process)
            )
            line = await asyncio.wait_for(process.stdout.readline(), 20)
            assert line, line
            results.setdefault("workers", []).append(json.loads(line))
            return process

        active_worker = await worker()
        phone_log = (evidence_directory / (room + "-phone.log")).open("wb")
        cleanup.defer("phone log", phone_log.close)
        phone = await asyncio.create_subprocess_exec(
            os.environ.get("SIP_FIXTURE", str(ROOT / "fixtures/call")),
            "finish" if mode == "natural-drain" else "hold",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=phone_log,
        )
        cleanup.defer("phone process", lambda: stop_process(phone))
        ready = await asyncio.wait_for(phone.stdout.readline(), 15)
        assert json.loads(ready)["stage"] == "ready"
        rows = CallLedger(database).list_calls("local-team")
        assert len(rows) == 1
        results["call_id"] = rows[0]["id"]
        results["initial_status"] = rows[0]["status"]
        started = time.monotonic()
        if mode == "restart":
            active_worker.kill()
            await active_worker.wait()
            active_worker = await worker()
        elif mode in {"natural-drain", "deadline-drain"}:
            active_worker.send_signal(signal.SIGTERM)
        elif mode == "media-timeout":
            active_worker.send_signal(signal.SIGUSR1)
        output, _ = await asyncio.wait_for(phone.communicate(), 20)
        assert phone.returncode == 0, output
        results["phone"] = json.loads(output.splitlines()[-1])
        results["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        for _ in range(30):
            row = CallLedger(database).get(results["call_id"], "local-team")
            if row["status"] == "ended":
                break
            await asyncio.sleep(0.1)
        assert row["status"] == "ended", row
        results["final_status"] = row["status"]
        results["events"] = CallLedger(database).events(row["id"], "local-team")
        if active_worker.returncode is None and mode not in {
            "natural-drain",
            "deadline-drain",
        }:
            active_worker.send_signal(signal.SIGTERM)
        await asyncio.wait_for(active_worker.wait(), 15)
        assert active_worker.returncode == 0, active_worker.returncode
        return results


async def main():
    results = []
    for mode in ["natural-drain", "deadline-drain", "restart", "media-timeout"]:
        value = await verify(mode)
        results.append(value)
        print(json.dumps(value), flush=True)
    directory = Path(os.environ.get("VOICEOPS_EVIDENCE", ".runtime/evidence")).resolve()
    (directory / "worker-runtime-evidence.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
