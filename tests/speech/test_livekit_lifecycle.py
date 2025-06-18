"""Opt-in real RTC setup/teardown check; no model inference or external provider."""

import asyncio
import os
import time
import unittest


@unittest.skipUnless(
    os.environ.get("VOICEOPS_LIVEKIT") == "1", "local RTC service not requested"
)
class LiveKitLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_session_closes_twice_without_leaving_agent_tasks(self):
        from livekit import api, rtc
        from voiceops.speech import SpeechConfig
        from voiceops.speech_agent import open_session

        key = os.environ.get("LIVEKIT_API_KEY", "devkey")
        secret = os.environ.get(
            "LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"
        )
        url = os.environ.get("LIVEKIT_URL", "http://127.0.0.1:18140")
        room_name = "speech-lifecycle-" + str(time.time_ns())
        client = api.LiveKitAPI(url, key, secret)
        agent_room, caller_room = rtc.Room(), rtc.Room()
        speech = None
        connected = asyncio.get_running_loop().create_future()

        @agent_room.on("participant_connected")
        def on_participant(participant):
            if participant.identity == "speech-caller" and not connected.done():
                connected.set_result(participant)

        try:
            for room, identity in [
                (agent_room, "speech-agent"),
                (caller_room, "speech-caller"),
            ]:
                token = (
                    api.AccessToken(key, secret)
                    .with_identity(identity)
                    .with_grants(api.VideoGrants(room_join=True, room=room_name))
                    .to_jwt()
                )
                await room.connect(url.replace("http://", "ws://", 1), token)
            participant = await asyncio.wait_for(connected, 5)
            speech = await open_session(agent_room, "speech-caller", SpeechConfig())
            self.assertEqual(speech.session.current_agent, speech.agent)
            from types import SimpleNamespace

            speech.agent.buffer.append(bytes(6400))
            agent_room.emit(
                "sip_dtmf_received",
                SimpleNamespace(
                    participant=SimpleNamespace(
                        identity="speech-caller", sid="PA_replaced"
                    ),
                    digit="#",
                ),
            )
            self.assertEqual(
                speech.agent.turns, 0, "A replaced session must not control this call"
            )
            agent_room.emit(
                "sip_dtmf_received", SimpleNamespace(participant=participant, digit="#")
            )
            self.assertEqual(speech.agent.turns, 1)
            speech.agent.reset_turn()
            await asyncio.wait_for(speech.aclose(), 5)
            await speech.aclose()
            self.assertTrue(speech._closed)
        finally:
            if speech:
                await speech.aclose()
            await agent_room.disconnect()
            await caller_room.disconnect()
            try:
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
            finally:
                await client.aclose()
