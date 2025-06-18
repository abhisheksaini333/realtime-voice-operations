import importlib.util
import unittest


class AgentContractTests(unittest.TestCase):
    def test_livekit_session_has_explicit_participant_and_turn_boundary(self):
        self.assertIsNotNone(
            importlib.util.find_spec("voiceops.speech_agent"),
            "LiveKit speech integration is missing",
        )
        from voiceops.speech_agent import LocalSpeechAgent
        from voiceops.speech import LocalSpeechBackend, SpeechConfig
        from livekit.agents import Agent

        agent = LocalSpeechAgent(LocalSpeechBackend(SpeechConfig()))
        self.assertIsInstance(agent, Agent)
        self.assertFalse(agent.finish_turn())
        self.assertEqual(agent.turns, 0)
        self.assertEqual(agent.tools, [])
        self.assertTrue(agent.allow_interruptions)


class AgentTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_reset_cancels_pending_recognition_and_clears_queued_audio(
        self,
    ):
        from voiceops.speech_agent import LocalSpeechAgent
        from voiceops.speech import LocalSpeechBackend, SpeechConfig

        agent = LocalSpeechAgent(LocalSpeechBackend(SpeechConfig()))
        self.assertTrue(hasattr(agent, "reset_turn"), "manual cancellation is missing")
        import asyncio

        agent._recognition = asyncio.create_task(asyncio.sleep(30))
        agent.buffer.append(bytes(6400))
        self.assertTrue(agent.finish_turn())
        agent.reset_turn()
        await asyncio.sleep(0)
        self.assertTrue(agent._recognition.cancelled())
        self.assertTrue(agent._turns.empty())
        self.assertFalse(agent._busy)


class SessionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_io_closes_even_when_sdk_close_raises(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from voiceops.speech_agent import SpeechSession

        session = SimpleNamespace(
            interrupt=AsyncMock(),
            drain=AsyncMock(),
            aclose=AsyncMock(side_effect=RuntimeError("closed")),
        )
        io = SimpleNamespace(aclose=AsyncMock())
        agent = SimpleNamespace(reset_turn=Mock())
        resource = SpeechSession(
            session, agent, io, SimpleNamespace(off=Mock()), object()
        )
        with self.assertRaises(RuntimeError):
            await resource.aclose()
        io.aclose.assert_awaited_once()


class PublishedAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_observation_requires_successful_rtc_capture(self):
        from voiceops import speech_agent

        self.assertTrue(
            hasattr(speech_agent, "ObservedAudioOutput"),
            "RTC output observation is missing",
        )
        from livekit import rtc
        from livekit.agents.voice.io import AudioOutput

        class Sink(AudioOutput):
            def __init__(self):
                super().__init__(sample_rate=24000)
                self.fail = True

            async def capture_frame(self, frame):
                if self.fail:
                    raise RuntimeError("transport unavailable")
                await super().capture_frame(frame)

            def flush(self):
                super().flush()
                self.on_playback_finished(playback_position=0.02, interrupted=False)

            def clear_buffer(self):
                pass

        sink = Sink()
        observed = []
        output = speech_agent.ObservedAudioOutput(
            sink, lambda stage, data: observed.append((stage, data))
        )
        frame = rtc.AudioFrame(bytes(960), 24000, 1, 480)
        with self.assertRaises(RuntimeError):
            await output.capture_frame(frame)
        self.assertEqual(observed, [])
        sink.fail = False
        await output.capture_frame(frame)
        output.flush()
        self.assertEqual(observed, [("rtc_output", {"samples_8khz": 160})])
