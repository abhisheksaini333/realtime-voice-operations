"""Verify synthetic speech through a real local SIP dialog and Agents 1.0 session."""

import asyncio
import io
import json
import os
from pathlib import Path
import time
import wave

from livekit import api, rtc
from voiceops.speech import LocalSpeechBackend, SpeechConfig, run_process
from voiceops.speech_agent import open_session


async def main():
    evidence = Path(os.environ.get("VOICEOPS_EVIDENCE", ".runtime/speech")).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    config = SpeechConfig(
        whisper_python=os.environ["WHISPER_PYTHON"],
        whisper_model=os.environ["WHISPER_MODEL"],
        espeak=os.environ["ESPEAK_BIN"],
        llm_url=os.environ.get(
            "VOICEOPS_LLM_URL", "http://127.0.0.1:18100/v1/chat/completions"
        ),
    )
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
    room_name = "voiceops-speech-" + str(time.time_ns())
    key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    secret = os.environ.get(
        "LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"
    )
    url = os.environ.get("LIVEKIT_URL", "http://127.0.0.1:18140")
    lk = api.LiveKitAPI(url, key, secret)
    room = rtc.Room()
    participant_future = asyncio.get_running_loop().create_future()

    @room.on("participant_connected")
    def connected(participant):
        if "sip.callID" in participant.attributes and not participant_future.done():
            participant_future.set_result(participant)

    trunk = rule = phone = session = None
    observed = []
    transcripts = []
    complete = asyncio.Event()

    def observe(stage, details):
        observed.append(dict(stage=stage, **details))

    def transcript(speaker, text):
        # This verification uses explicit synthetic fixtures; there is no real caller data.
        transcripts.append({"speaker": speaker, "text": text})
        if speaker == "agent":
            complete.set()

    log = (evidence / "phone.log").open("wb")
    try:
        trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name=room_name,
                    numbers=["2002"],
                    auth_username="voiceops",
                    auth_password="local-pass",
                )
            )
        )
        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name=room_name,
                trunk_ids=[trunk.sip_trunk_id],
                rule=api.SIPDispatchRule(
                    dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=room_name)
                ),
            )
        )
        token = (
            api.AccessToken(key, secret)
            .with_identity("speech-agent")
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        await room.connect(url.replace("http://", "ws://", 1), token)
        phone = await asyncio.create_subprocess_exec(
            os.environ["SIP_SPEECH_FIXTURE"],
            str(evidence / "caller.webm"),
            str(evidence / "received.webm"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
        )
        participant = await asyncio.wait_for(participant_future, 15)
        session = await open_session(
            room, participant.identity, config, transcript=transcript, observe=observe
        )
        ready = await asyncio.wait_for(phone.stdout.readline(), 15)
        assert json.loads(ready)["stage"] == "ready", ready
        # Allow the new audio subscription to finish before transmitting the fixture.
        await asyncio.sleep(1)
        start = time.monotonic()
        phone.stdin.write(b"start\n")
        await phone.stdin.drain()
        spoken = await asyncio.wait_for(phone.stdout.readline(), 15)
        assert json.loads(spoken)["stage"] == "spoken", spoken
        await asyncio.wait_for(complete.wait(), 40)
        await asyncio.sleep(1)
        phone.stdin.write(b"finish\n")
        await phone.stdin.drain()
        await asyncio.wait_for(phone.wait(), 5)
        assert phone.returncode == 0
        elapsed = round((time.monotonic() - start) * 1000)
        assert "account" in transcripts[0]["text"].lower(), transcripts
        assert "account request" in transcripts[-1]["text"].lower(), transcripts
        await session.aclose()
        session = None
        received = await run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(evidence / "received.webm"),
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
        assert len(received) > 32000, len(received)
        (evidence / "pipeline-observations.json").write_text(
            json.dumps(
                {
                    "transcripts": transcripts,
                    "observations": observed,
                    "turn_elapsed_ms": elapsed,
                },
                indent=2,
            )
            + "\n"
        )
        returned_text = await backend.transcribe(received)
        assert "account" in returned_text.lower(), returned_text
        assert {row["stage"] for row in observed} >= {"stt", "llm", "tts"}, observed
        result = {
            "status": "passed",
            "fixture": "synthetic local SIP caller",
            "sdk": "livekit-agents1.0.0",
            "profile": config.llm_url,
            "call_id": participant.attributes["sip.callID"],
            "dialog_id": participant.attributes.get("sip.callIDFull"),
            "transcripts": transcripts,
            "returned_audio_transcription": returned_text,
            "returned_audio_samples": len(received) // 2,
            "turn_elapsed_ms": elapsed,
            "observations": observed,
        }
        (evidence / "speech-evidence.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(json.dumps(result), flush=True)
    finally:
        if session:
            await session.aclose()
        if phone and phone.returncode is None:
            phone.kill()
            await phone.wait()
        await room.disconnect()
        cleanup = []
        if rule:
            cleanup.append(
                lk.sip.delete_sip_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(
                        sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                    )
                )
            )
        if trunk:
            cleanup.append(
                lk.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
                )
            )
        cleanup.append(lk.room.delete_room(api.DeleteRoomRequest(room=room_name)))
        results = await asyncio.gather(*cleanup, return_exceptions=True)
        await lk.aclose()
        log.close()
        if any(isinstance(result, Exception) for result in results):
            print(
                json.dumps(
                    {
                        "cleanup": "incomplete",
                        "room": room_name,
                        "trunk": trunk.sip_trunk_id if trunk else None,
                        "rule": rule.sip_dispatch_rule_id if rule else None,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
