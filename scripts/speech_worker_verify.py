"""Two real local SIP calls through the worker: no retention, then explicit consent."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
import wave

from livekit import api
from voiceops.ledger import CallLedger
from voiceops.privacy import PrivacyStore
from voiceops.observations import Observations
from voiceops.speech import LocalSpeechBackend, SpeechConfig, run_process

ROOT = Path(__file__).resolve().parents[1]


async def main():
    evidence = Path(
        os.environ.get("VOICEOPS_EVIDENCE", ".runtime/speech-worker")
    ).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    config = SpeechConfig(
        whisper_python=os.environ["WHISPER_PYTHON"],
        whisper_model=os.environ["WHISPER_MODEL"],
        espeak=os.environ["ESPEAK_BIN"],
        llm_url=os.environ.get(
            "VOICEOPS_LLM_URL", "http://127.0.0.1:18100/v1/chat/completions"
        ),
    )
    config_path = evidence / "speech-config.json"
    config_path.write_text(json.dumps(config.__dict__))
    backend = LocalSpeechBackend(config)
    rate, pcm = await backend.synthesize(
        "My name is John. I cannot log into my account."
    )
    wav = evidence / "caller.wav"
    with wave.open(str(wav), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    await run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-ar",
            "8000",
            "-ac",
            "1",
            "-af",
            "aresample=8000,asetnsamples=n=160:p=1",
            "-c:a",
            "pcm_s16le",
            "-f",
            "matroska",
            str(evidence / "caller.webm"),
        ]
    )
    room = "voiceops-speech-worker-" + str(time.time_ns())
    database = evidence / "calls.sqlite"
    ledger = CallLedger(database)
    privacy = PrivacyStore(ledger)
    key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    secret = os.environ.get(
        "LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"
    )
    url = os.environ.get("LIVEKIT_URL", "http://127.0.0.1:18140")
    client = api.LiveKitAPI(url, key, secret)
    worker = phone = trunk = rule = None
    logs = []
    results = []
    try:
        trunk = await client.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name=room,
                    numbers=["2002"],
                    auth_username="voiceops",
                    auth_password="local-pass",
                )
            )
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
        log = (evidence / "worker.log").open("wb")
        logs.append(log)
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "voiceops.worker",
            "--database",
            str(database),
            "--room",
            room,
            "--speech-config",
            str(config_path),
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
        )
        ready = await asyncio.wait_for(worker.stdout.readline(), 20)
        assert json.loads(ready)["event"] == "ready", ready
        for consent in [False, True]:
            before = {c["id"] for c in ledger.list_calls("local-team")}
            name = "consented" if consent else "no-consent"
            log = (evidence / (name + "-phone.log")).open("wb")
            logs.append(log)
            phone = await asyncio.create_subprocess_exec(
                os.environ["SIP_SPEECH_FIXTURE"],
                str(evidence / "caller.webm"),
                str(evidence / (name + "-received.webm")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
            ready = await asyncio.wait_for(phone.stdout.readline(), 20)
            assert json.loads(ready)["stage"] == "ready", ready
            rows = [c for c in ledger.list_calls("local-team") if c["id"] not in before]
            assert len(rows) == 1, rows
            call = rows[0]
            for _ in range(50):
                events = ledger.events(call["id"], "local-team")
                if any(e["kind"] == "speech_session_started" for e in events):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Worker speech session did not start")
            if consent:
                privacy.consent(
                    call["id"],
                    "local-team",
                    transcript=True,
                    recording=False,
                    attestation="Explicit synthetic fixture consent",
                    now=time.time(),
                )
            await asyncio.sleep(0.5)
            start = time.monotonic()
            phone.stdin.write(b"start\n")
            await phone.stdin.drain()
            spoken = await asyncio.wait_for(phone.stdout.readline(), 15)
            assert json.loads(spoken)["stage"] == "spoken", spoken
            for _ in range(400):
                events = ledger.events(call["id"], "local-team")
                assert not any(
                    e["kind"] == "speech_speech_failed" for e in events
                ), events
                if any(e["kind"] == "speech_reply_complete" for e in events):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Worker did not complete an actual speech reply")
            await asyncio.sleep(0.5)
            phone.stdin.write(b"finish\n")
            await phone.stdin.drain()
            await asyncio.wait_for(phone.wait(), 5)
            assert phone.returncode == 0
            phone = None
            for _ in range(50):
                call = ledger.get(call["id"], "local-team")
                if call["status"] == "ended":
                    break
                await asyncio.sleep(0.1)
            assert call["status"] == "ended", call
            retained = privacy.read(call["id"], "local-team")
            if consent:
                assert len(retained) == 2, retained
                assert "account" in retained[0]["text"].lower(), retained
                assert "account request" in retained[1]["text"].lower(), retained
            else:
                assert retained == [], retained
            health = Observations(ledger).health(
                call["id"], "local-team", now=time.time()
            )
            assert (
                health["received_samples"] > 8000 and health["sent_samples"] > 8000
            ), health
            received = await run_process(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(evidence / (name + "-received.webm")),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-af",
                    "silenceremove=start_periods=1:start_duration=0.1:start_silence=0.2:start_threshold=-50dB",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                limit=640000,
            )
            remote_text = await backend.transcribe(received)
            assert "account" in remote_text.lower(), remote_text
            results.append(
                {
                    "case": name,
                    "call_id": call["id"],
                    "status": call["status"],
                    "retained_transcripts": len(retained),
                    "received_samples": health["received_samples"],
                    "rtc_accepted_samples_8khz": health["sent_samples"],
                    "remote_recording_transcription": remote_text,
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                    "stage_events": [
                        e for e in events if e["kind"].startswith("speech_")
                    ],
                }
            )
        worker.send_signal(signal.SIGTERM)
        await asyncio.wait_for(worker.wait(), 10)
        assert worker.returncode == 0
        result = {
            "status": "passed",
            "scope": "two synthetic local SIP calls through the actual worker",
            "calls": results,
            "clean_shutdown": True,
        }
        (evidence / "worker-speech-evidence.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(json.dumps(result), flush=True)
    finally:
        for process in [phone, worker]:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
        cleanup = []
        if rule:
            cleanup.append(
                client.sip.delete_sip_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(
                        sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                    )
                )
            )
        if trunk:
            cleanup.append(
                client.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
                )
            )
        cleanup.append(client.room.delete_room(api.DeleteRoomRequest(room=room)))
        await asyncio.gather(*cleanup, return_exceptions=True)
        await client.aclose()
        for log in logs:
            log.close()


if __name__ == "__main__":
    asyncio.run(main())
