"""Opt-in real local model test; no downloads and no external provider calls."""

import os
import unittest
from voiceops import speech


@unittest.skipUnless(
    os.environ.get("VOICEOPS_REAL_SPEECH") == "1", "local speech models not configured"
)
class LocalInferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_whisper_qwen_espeak_roundtrip(self):
        self.assertTrue(
            hasattr(speech, "LocalSpeechBackend"), "local model backend is missing"
        )
        backend = speech.LocalSpeechBackend(
            speech.SpeechConfig(
                whisper_python=os.environ["WHISPER_PYTHON"],
                whisper_model=os.environ["WHISPER_MODEL"],
                espeak=os.environ["ESPEAK_BIN"],
                llm_url=os.environ.get(
                    "VOICEOPS_LLM_URL", "http://127.0.0.1:18100/v1/chat/completions"
                ),
            )
        )
        rate, pcm = await backend.synthesize(
            "My name is John. I need help with my account."
        )
        self.assertEqual(rate, 22050)
        from livekit import rtc

        resampler = rtc.AudioResampler(rate, 16000, num_channels=1)
        frame = rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)
        frames = resampler.push(frame) + resampler.flush()
        transcript = await backend.transcribe(b"".join(bytes(f.data) for f in frames))
        self.assertIn("account", transcript.casefold())
        result = await backend.complete(transcript)
        self.assertEqual(result["category"], "account")
        self.assertTrue(result["name"])
        output_rate, output = await backend.synthesize(
            "Thanks. Your account request is ready for review."
        )
        self.assertGreater(len(output), output_rate)
