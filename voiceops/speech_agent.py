"""LiveKit Agents 1.0 adapters with explicit DTMF end-of-turn control."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
import uuid

from livekit import rtc
from livekit.agents import Agent, AgentSession, APIError, llm, stt, tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.voice.io import AudioOutput
from livekit.agents.voice.room_io import RoomIO, RoomInputOptions, RoomOutputOptions

from .speech import AudioTurn, LocalSpeechBackend, SpeechFailure


def _ignore(*args):
    pass


class WhisperSTT(stt.STT):
    def __init__(self, backend, observe=_ignore):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self.backend, self.observe = backend, observe

    async def _recognize_impl(
        self, buffer, *, language=None, conn_options=DEFAULT_API_CONNECT_OPTIONS
    ):
        frame = rtc.combine_audio_frames(buffer)
        if frame.sample_rate != 16000 or frame.num_channels != 1:
            raise APIError(
                "Expected mono 16 kHz transcription audio", body=None, retryable=False
            )
        started = time.monotonic()
        try:
            text = await self.backend.transcribe(bytes(frame.data))
        except SpeechFailure as exc:
            raise APIError(str(exc), body=None, retryable=False) from exc
        self.observe(
            "stt",
            {
                "latency_ms": round((time.monotonic() - started) * 1000),
                "audio_ms": round(frame.duration * 1000),
            },
        )
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=str(uuid.uuid4()),
            alternatives=[stt.SpeechData(language="en", text=text)],
        )


class IntakeLLM(llm.LLM):
    def __init__(self, backend, observe=_ignore):
        super().__init__()
        self.backend, self.observe = backend, observe

    def chat(
        self,
        *,
        chat_ctx,
        tools=None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        **kwargs,
    ):
        if tools:
            raise ValueError("Intake model has no tool authority")
        return IntakeStream(
            self,
            chat_ctx=chat_ctx,
            tools=[],
            conn_options=APIConnectOptions(max_retry=0),
        )


class IntakeStream(llm.LLMStream):
    async def _run(self):
        messages = [
            item
            for item in self.chat_ctx.items
            if isinstance(item, llm.ChatMessage) and item.role == "user"
        ]
        if not messages:
            raise APIError(
                "A caller transcript is required", body=None, retryable=False
            )
        started = time.monotonic()
        try:
            intake = await self._llm.backend.complete(messages[-1].text_content or "")
        except SpeechFailure as exc:
            raise APIError(str(exc), body=None, retryable=False) from exc
        self._llm.observe(
            "llm",
            {
                "latency_ms": round((time.monotonic() - started) * 1000),
                "category": intake["category"],
            },
        )
        greeting = "Thank you" + (" " + intake["name"] if intake["name"] else "")
        article = "an" if intake["category"] in {"account", "other"} else "a"
        text = f"{greeting}. This is {article} {intake['category']} request. It is ready for review."
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id=str(uuid.uuid4()),
                delta=llm.ChoiceDelta(role="assistant", content=text),
            )
        )


class EspeakTTS(tts.TTS):
    def __init__(self, backend, observe=_ignore):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=22050,
            num_channels=1,
        )
        self.backend, self.observe = backend, observe

    def synthesize(self, text, *, conn_options=None):
        return EspeakStream(
            tts=self, input_text=text, conn_options=APIConnectOptions(max_retry=0)
        )


class EspeakStream(tts.ChunkedStream):
    async def _run(self):
        started = time.monotonic()
        try:
            rate, pcm = await self._tts.backend.synthesize(self.input_text)
        except SpeechFailure as exc:
            raise APIError(str(exc), body=None, retryable=False) from exc
        self._tts.observe(
            "tts",
            {
                "latency_ms": round((time.monotonic() - started) * 1000),
                "audio_ms": round(len(pcm) / 2 / rate * 1000),
            },
        )
        request = str(uuid.uuid4())
        chunk = rate // 50 * 2
        for offset in range(0, len(pcm), chunk):
            data = pcm[offset : offset + chunk]
            self._event_ch.send_nowait(
                tts.SynthesizedAudio(
                    frame=rtc.AudioFrame(data, rate, 1, len(data) // 2),
                    request_id=request,
                    is_final=offset + chunk >= len(pcm),
                )
            )


class LocalSpeechAgent(Agent):
    def __init__(self, backend: LocalSpeechBackend, observe=_ignore):
        super().__init__(
            instructions="Collect a bounded support intake record. No call-control tools are available.",
            stt=WhisperSTT(backend, observe),
            llm=IntakeLLM(backend, observe),
            tts=EspeakTTS(backend, observe),
            turn_detection="manual",
            allow_interruptions=True,
        )
        self.backend, self.observe = backend, observe
        self.buffer = AudioTurn(seconds=backend.config.max_audio_seconds)
        self._turns = asyncio.Queue(maxsize=1)
        self._busy = False
        self.turns = 0
        self._recognition = None

    def finish_turn(self):
        if self._busy or self.turns >= 20 or self._turns.full():
            return False
        try:
            data = self.buffer.finish()
        except SpeechFailure:
            self.observe("turn_rejected", {"reason": "audio_bounds"})
            return False
        self._turns.put_nowait(data)
        self.turns += 1
        self._busy = True
        return True

    async def stt_node(self, audio, model_settings):
        async def collect():
            async for frame in audio:
                if frame.sample_rate != 16000 or frame.num_channels != 1:
                    raise SpeechFailure("Unexpected room audio format")
                if not self._busy:
                    self.buffer.append(bytes(frame.data))

        reader = asyncio.create_task(collect())
        try:
            while True:
                pcm = await self._turns.get()
                try:
                    frame = rtc.AudioFrame(pcm, 16000, 1, len(pcm) // 2)
                    self._recognition = asyncio.create_task(
                        self.stt.recognize(
                            frame, conn_options=APIConnectOptions(max_retry=0)
                        )
                    )
                    event = await self._recognition
                    yield event
                    self.session.commit_user_turn()
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    self._busy = False
                except (SpeechFailure, APIError) as exc:
                    self.observe(
                        "speech_failed", {"stage": "stt", "type": type(exc).__name__}
                    )
                    self._busy = False
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    def reset_turn(self):
        if self._recognition and not self._recognition.done():
            self._recognition.cancel()
        while not self._turns.empty():
            self._turns.get_nowait()
        self.release_turn()

    def release_turn(self):
        self.buffer.data.clear()
        self.buffer.overflow = False
        self._busy = False


class ObservedAudioOutput(AudioOutput):
    """Count frames only after the RTC sink accepts them, never from TTS estimates."""

    def __init__(self, sink, observe):
        super().__init__(next_in_chain=sink, sample_rate=sink.sample_rate)
        self.sink, self.observe = sink, observe
        self.samples = 0

    async def capture_frame(self, frame):
        await self.sink.capture_frame(frame)
        await super().capture_frame(frame)
        self.samples += round(frame.samples_per_channel * 8000 / frame.sample_rate)

    def _report(self):
        if self.samples:
            self.observe("rtc_output", {"samples_8khz": self.samples})
            self.samples = 0

    def flush(self):
        super().flush()
        self.sink.flush()
        self._report()

    def clear_buffer(self):
        self.sink.clear_buffer()
        self._report()


@dataclass
class SpeechSession:
    session: AgentSession
    agent: LocalSpeechAgent
    room_io: RoomIO
    room: rtc.Room
    dtmf_handler: object
    _closed: bool = False

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        self.room.off("sip_dtmf_received", self.dtmf_handler)
        self.agent.reset_turn()
        try:
            try:
                await asyncio.wait_for(self.session.interrupt(), 2)
                await asyncio.wait_for(self.session.drain(), 2)
            except asyncio.TimeoutError:
                pass  # Close cancels the remaining SDK tasks after this bounded grace.
            finally:
                await self.session.aclose()
        finally:
            await self.room_io.aclose()


async def open_session(
    room, participant_identity, config, *, transcript=_ignore, observe=_ignore
):
    """Attach one session to one SIP identity. Caller owns consent and call admission."""
    if not participant_identity:
        raise ValueError("A participant identity is required")
    participant = room.remote_participants.get(participant_identity)
    if participant is None:
        raise ValueError("The participant must already be connected")
    participant_sid = participant.sid
    agent = LocalSpeechAgent(LocalSpeechBackend(config), observe)
    session = AgentSession(allow_interruptions=True, turn_detection="manual")
    room_io = RoomIO(
        session,
        room,
        participant=participant_identity,
        input_options=RoomInputOptions(text_enabled=False, audio_sample_rate=16000),
        output_options=RoomOutputOptions(
            transcription_enabled=False, audio_sample_rate=24000
        ),
    )

    @session.on("user_input_transcribed")
    def transcribed(event):
        if event.is_final:
            transcript("caller", event.transcript)

    @session.on("conversation_item_added")
    def item_added(event):
        if isinstance(event.item, llm.ChatMessage) and event.item.role == "assistant":
            transcript("agent", event.item.text_content or "")
            observe("reply_complete", {})
            agent.release_turn()

    @session.on("error")
    def error(event):
        observe(
            "speech_failed", {"stage": "session", "type": type(event.error).__name__}
        )
        agent.release_turn()

    def dtmf(event):
        if (
            event.participant
            and event.participant.identity == participant_identity
            and event.participant.sid == participant_sid
        ):
            if event.digit == "#":
                agent.finish_turn()
            elif event.digit == "*":
                session.interrupt()
                session.clear_user_turn()
                agent.reset_turn()

    room.on("sip_dtmf_received", dtmf)
    try:
        await asyncio.wait_for(room_io.start(), 15)
        session.output.audio = ObservedAudioOutput(room_io.audio_output, observe)
        await session.start(agent)
    except BaseException:
        room.off("sip_dtmf_received", dtmf)
        await room_io.aclose()
        raise
    return SpeechSession(session, agent, room_io, room, dtmf)
