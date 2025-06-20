"""Verify answered, rejected and unanswered transfers on an owned local fixture."""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from livekit import api, rtc

try:
    from .fixture_lifecycle import (
        FixtureCleanup,
        delete_owned_room,
        require_number_available,
        stop_process,
        stop_tasks,
    )
except ImportError:
    from fixture_lifecycle import (
        FixtureCleanup,
        delete_owned_room,
        require_number_available,
        stop_process,
        stop_tasks,
    )


async def one(mode):
    evidence_directory = Path(os.environ.get("VOICEOPS_EVIDENCE", ".runtime/evidence"))
    evidence_directory.mkdir(parents=True, exist_ok=True)
    room_name = "voiceops-transfer-" + mode + "-" + uuid.uuid4().hex[:12]
    key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    secret = os.environ.get(
        "LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"
    )
    evidence = {"frames": 0, "samples": 0, "dtmf": []}
    async with FixtureCleanup() as cleanup:
        client = api.LiveKitAPI("http://127.0.0.1:18140", key, secret)
        cleanup.defer("LiveKit API client", client.aclose)
        await require_number_available(client, api)
        trunk = await client.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="voiceops-local",
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
                name="voiceops-local",
                trunk_ids=[trunk.sip_trunk_id],
                rule=api.SIPDispatchRule(
                    dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=room_name)
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
        cleanup.defer(
            "owned transfer room", lambda: delete_owned_room(client, api, room_name)
        )
        room = rtc.Room()
        source = rtc.AudioSource(8000, 1)
        cleanup.defer("audio source", source.aclose)
        cleanup.defer("RTC room connection", room.disconnect)
        track = rtc.LocalAudioTrack.create_audio_track("verified-echo", source)
        tasks = []
        cleanup.defer("echo tasks", lambda: stop_tasks(tasks))

        async def echo(remote):
            async for event in rtc.AudioStream(
                remote, sample_rate=8000, num_channels=1
            ):
                evidence["frames"] += 1
                evidence["samples"] += event.frame.samples_per_channel
                await source.capture_frame(event.frame)

        @room.on("track_subscribed")
        def subscribed(remote, publication, participant):
            if remote.kind == rtc.TrackKind.KIND_AUDIO:
                tasks.append(asyncio.create_task(echo(remote)))

        @room.on("sip_dtmf_received")
        def dtmf(event):
            evidence["dtmf"].append(str(event))

        token = (
            api.AccessToken(key, secret)
            .with_identity("echo-worker")
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        await room.connect("ws://127.0.0.1:18140", token)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        error_log = (evidence_directory / ("transfer-" + mode + ".log")).open("wb")
        cleanup.defer("SIP fixture log", error_log.close)
        process = await asyncio.create_subprocess_exec(
            os.environ.get("SIP_FIXTURE", "fixtures/call"),
            mode,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=error_log,
        )
        cleanup.defer("SIP fixture process", lambda: stop_process(process))
        first = await asyncio.wait_for(process.stdout.readline(), 15)
        json.loads(first)
        participants = await client.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        participant = next(
            item for item in participants.participants if item.identity != "echo-worker"
        )
        evidence["sip_participant"] = participant.identity
        evidence["attributes"] = dict(participant.attributes)
        started = time.perf_counter()
        try:
            await asyncio.wait_for(
                client.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=room_name,
                        participant_identity=participant.identity,
                        transfer_to="sip:" + mode + "@127.0.0.1:18160",
                    )
                ),
                25,
            )
            evidence["api_result"] = "ok"
        except Exception as error:
            evidence["api_result"] = type(error).__name__ + ": " + str(error)
        evidence["api_latency_ms"] = round((time.perf_counter() - started) * 1000)
        print(json.dumps(evidence), flush=True)
        output, _ = await asyncio.wait_for(process.communicate(), 20)
        evidence.update(
            returncode=process.returncode,
            client=(first + output).decode(),
            trunk_id=trunk.sip_trunk_id,
            dispatch_id=rule.sip_dispatch_rule_id,
        )
        print(json.dumps(evidence), flush=True)
        (evidence_directory / ("transfer-" + mode + ".json")).write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        assert (
            evidence["returncode"] == 0 and evidence["frames"] > 0 and evidence["dtmf"]
        ), evidence


async def main():
    for mode in ["operator", "reject", "unanswered"]:
        await one(mode)


if __name__ == "__main__":
    asyncio.run(main())
