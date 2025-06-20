"""Dial twice from one SIP identity and verify distinct, terminated ledger calls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

from voiceops.ledger import CallLedger


async def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get("VOICEOPS_EVIDENCE", ".runtime/evidence")).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    room = f"voiceops-repeat-{time.time_ns()}"
    state, database = evidence / f"{room}.json", evidence / f"{room}.sqlite"
    demo = [sys.executable, str(root / "scripts/demo.py")]
    worker = None
    provisioned = False
    with (evidence / f"{room}.log").open("wb") as log:
        try:
            process = await asyncio.create_subprocess_exec(*demo, "provision", "--state", str(state), "--room", room,
                                                           stdout=log, stderr=log)
            assert await process.wait() == 0, "Provisioning failed; inspect the evidence log"
            provisioned = True
            worker = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "voiceops.worker", "--database", str(database), "--room", room,
                stdout=asyncio.subprocess.PIPE, stderr=log)
            ready = json.loads(await asyncio.wait_for(worker.stdout.readline(), 30))
            assert ready["event"] == "ready", ready
            calls = []
            for number in range(2):
                phone = await asyncio.create_subprocess_exec(
                    os.environ.get("SIP_FIXTURE", str(root / "fixtures/call")), "finish",
                    stdout=asyncio.subprocess.PIPE, stderr=log)
                try:
                    output, _ = await asyncio.wait_for(phone.communicate(), 30)
                    assert phone.returncode == 0, output.decode()
                finally:
                    if phone.returncode is None:
                        phone.kill()
                        await phone.wait()
                ledger = CallLedger(database)
                for _ in range(50):
                    rows = ledger.list_calls("local-team")
                    if len(rows) == number + 1 and all(row["status"] == "ended" for row in rows):
                        break
                    await asyncio.sleep(0.1)
                assert len(rows) == number + 1 and all(row["status"] == "ended" for row in rows), rows
                calls = rows
            assert calls[0]["participant"] == calls[1]["participant"], calls
            assert calls[0]["id"] != calls[1]["id"], calls
            result = {"calls": calls, "same_identity_distinct_calls": True}
            (evidence / "repeated-calls.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
        finally:
            if worker and worker.returncode is None:
                worker.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(worker.wait(), 15)
                except TimeoutError:
                    worker.kill()
                    await worker.wait()
            if provisioned:
                process = await asyncio.create_subprocess_exec(*demo, "remove", "--state", str(state), stdout=log, stderr=log)
                assert await process.wait() == 0, "Cleanup failed; inspect the evidence log"


if __name__ == "__main__":
    asyncio.run(main())
