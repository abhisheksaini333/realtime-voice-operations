import asyncio
import json
import sys
import unittest
from pathlib import Path

from voiceops.speech import (
    AudioTurn,
    SpeechConfig,
    SpeechFailure,
    validate_intake,
    run_process,
)


class SpeechBoundaryTests(unittest.TestCase):
    def test_turn_buffer_rejects_overflow_without_partial_transcription(self):
        turn = AudioTurn(sample_rate=16000, seconds=1)
        turn.append(bytes(32000))
        turn.append(b"\x00\x00")
        with self.assertRaises(SpeechFailure):
            turn.finish()
        turn.append(bytes(6400))
        self.assertEqual(len(turn.finish()), 6400)

    def test_input_requires_bounded_mono_pcm(self):
        turn = AudioTurn()
        with self.assertRaises(SpeechFailure):
            turn.append(b"x")
        with self.assertRaises(SpeechFailure):
            turn.finish()

    def test_model_result_has_no_tool_authority_and_rejects_unsupported_name(self):
        self.assertEqual(
            validate_intake(
                '{"name":"Alex","category":"account"}', "My name is Alex. Account help."
            ),
            {"name": "Alex", "category": "account"},
        )
        for value in [
            '{"name":"Alex","category":[]}',
            '{"name":"Sam","category":"account"}',
            '{"name":"Alex","category":"transfer"}',
            '{"name":"Alex","category":"account","tool":"transfer"}',
        ]:
            with self.assertRaises(SpeechFailure):
                validate_intake(value, "My name is Alex.")

    def test_qwen_think_and_fences_are_removed_before_strict_validation(self):
        result = validate_intake(
            '<think>untrusted explanation</think>\n```json\n{"name":"Alex","category":"other"}\n```',
            "Alex calling",
        )
        self.assertEqual(result["category"], "other")

    def test_endpoint_must_be_explicit_loopback_without_credentials_or_queries(self):
        for endpoint in [
            "https://example.org/v1/chat/completions",
            "http://localhost.evil/v1/chat/completions",
            "http://user:pass@127.0.0.1:18100/v1/chat/completions",
            "http://127.0.0.1:18100/v1/chat/completions?x=1",
        ]:
            with self.assertRaises(ValueError):
                SpeechConfig(llm_url=endpoint)
        self.assertEqual(SpeechConfig().max_audio_seconds, 20)


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_kills_child(self):
        start = asyncio.get_running_loop().time()
        with self.assertRaises(SpeechFailure):
            await run_process(
                [sys.executable, "-c", "import time;time.sleep(20)"], timeout=0.05
            )
        self.assertLess(asyncio.get_running_loop().time() - start, 2)

    async def test_failure_does_not_leak_child_output(self):
        with self.assertRaises(SpeechFailure) as error:
            await run_process(
                [sys.executable, "-c", 'import sys;print("sensitive-text");sys.exit(1)']
            )
        self.assertNotIn("sensitive-text", str(error.exception))

    async def test_output_limit_is_enforced(self):
        with self.assertRaises(SpeechFailure):
            await run_process([sys.executable, "-c", 'print("x"*65537)'], limit=65536)


class ModelTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_response_is_read_within_total_limit(self):
        from aiohttp import web
        from voiceops.speech import LocalSpeechBackend

        async def response(request):
            stream = web.StreamResponse(headers={"Content-Type": "application/json"})
            await stream.prepare(request)
            payload = json.dumps(
                {
                    "choices": [
                        {"message": {"content": '{"name":"Alex","category":"account"}'}}
                    ]
                }
            ).encode()
            await stream.write(payload[:10])
            await asyncio.sleep(0.01)
            await stream.write(payload[10:])
            await stream.write_eof()
            return stream

        app = web.Application()
        app.router.add_post("/v1/chat/completions", response)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            backend = LocalSpeechBackend(
                SpeechConfig(llm_url=f"http://127.0.0.1:{port}/v1/chat/completions")
            )
            self.assertEqual(
                (await backend.complete("Alex calling"))["category"], "account"
            )
        finally:
            await runner.cleanup()
