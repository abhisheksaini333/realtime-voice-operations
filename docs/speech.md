# Local speech intake

The speech adapter uses the real LiveKit Agents 1.0 `AgentSession` pipeline. A
participant-specific `RoomIO` receives 16 kHz mono audio from the SIP caller.
Whisper tiny.en transcribes the buffered turn, a local Qwen model extracts a
strict intake object, and eSpeak NG renders a short confirmation. LiveKit
publishes that audio back through the SIP bridge.

Press `*` to clear an old turn or interrupt a reply, speak, then press `#` to
submit. Each turn is limited to twenty seconds and each session to twenty
submissions. A second `#` while inference is in progress is ignored. This is a
manual turn-taking profile: it does not claim automatic voice activity detection
or natural spoken barge-in. After a long idle period, press `*` before speaking.

Only `name` and `category` are accepted from the model. Categories are `account`,
`billing`, `technical`, and `other`. The name must occur in the transcript.
Malformed or extra fields fail closed. The adapter has no transfer, hangup,
network-fetch or arbitrary tool functions. A model classification is a suggestion
for a support intake, not an authorization to change call routing.

## Integration

```python
from voiceops.speech import SpeechConfig
from voiceops.speech_agent import open_session

speech = await open_session(
    room,
    participant.identity,
    SpeechConfig(
        whisper_python=".runtime/whisper/bin/python",
        whisper_model=".runtime/models/whisper-tiny.en",
        espeak=".runtime/espeak-ng/bin/espeak-ng",
        llm_url="http://127.0.0.1:18100/v1/chat/completions",
    ),
    transcript=retain_if_consented,
    observe=record_stage_metrics,
)
# On call disconnect, worker drain or shutdown:
await speech.aclose()
```

Start the session when the SIP participant appears. The bridge waits for audio
subscriptions before answering; waiting for the SIP phone to finish `Dial`
before starting the session can deadlock the fixture.

`transcript(speaker, text)` receives `caller` and `agent` messages. The owning
worker must check the current `PrivacyStore` consent before persistence and must
handle a concurrent withdrawal. No callback means no transcript persistence.
Room transcription broadcasts are disabled. Temporary input and output audio
remain in bounded process memory; isolated Whisper reads PCM from stdin and does
not create audio files. The verification script separately retains explicitly
synthetic audio fixtures and returned audio for inspection.

`observe(stage, details)` reports stage latency and bounded operational metadata.
It does not contain transcript text. STT and model requests have a thirty-second
default deadline, synthesis has ten seconds, and cancelled child processes are
killed and reaped. The LLM endpoint must be an explicit loopback address; redirects
and environment proxies are disabled. Both response size and synthesis duration
are bounded. HTTP connections are closed after each inference request.

## Installing local engines

Follow the [complete dependency setup](../dependencies/README.md) first. It
includes the 36-pin LiveKit worker environment, the separate 23-pin Whisper
environment, both MLX profiles, every model file's immutable URL and digest,
and exact eSpeak source-build commands. The qualified profile uses CPython
3.11.11 on macOS ARM64; the Qwen3 wheel requires macOS 15 or newer.

For Whisper, the repository-root commands are:

```sh
python3.11 -m venv .runtime/whisper
.runtime/whisper/bin/python -m pip install --only-binary=:all: --require-hashes -r dependencies/whisper/requirements-macos-arm64.lock
.venv/bin/python dependencies/fetch_models.py whisper .runtime/models/whisper-tiny.en
```

This installs Torch 2.3.0 and Transformers 4.41.2 with all transitive versions
pinned. The model is `openai/whisper-tiny.en` at revision
`87c7102498dcde7456f24cfd30239ca606ed9063`. Runtime downloads are disabled.
Use the same downloader with `--verify-only` to check a pre-existing cache.

The eSpeak recipe installs `.runtime/espeak-ng/bin/espeak-ng`. It must produce
mono PCM16 at 22,050 Hz. The application invokes it without a shell and passes
text through stdin. The [eSpeak notices](../dependencies/licenses/espeak-ng/COPYING)
apply to that independently installed program.

After installing the selected MLX environment and model from the dependency
guide, start the corresponding server in another terminal:

```sh
.runtime/mlx-qwen25/bin/python -m mlx_lm.server --model .runtime/models/qwen25-1.5b-4bit --host 127.0.0.1 --port 18100
# Alternatively:
.runtime/mlx-qwen3/bin/python -m mlx_lm.server --model .runtime/models/qwen3-0.6b-4bit --host 127.0.0.1 --port 18101
```

The endpoint accepts OpenAI-style chat messages and the `default_model` alias.
Qwen3 think sections and JSON fences are removed before schema validation;
incomplete or invalid output is rejected. The [Qwen3 selected-wheel audit](../dependencies/mlx-qwen3/selected-artifact-audit.json)
places this combined profile after the May 12 artifact releases.

## Verification

```sh
python -m unittest discover -s tests/speech -v
export WHISPER_PYTHON="$PWD/.runtime/whisper/bin/python"
export WHISPER_MODEL="$PWD/.runtime/models/whisper-tiny.en"
export ESPEAK_BIN="$PWD/.runtime/espeak-ng/bin/espeak-ng"
export VOICEOPS_REAL_SPEECH=1
python -m unittest discover -s tests/speech -v
```

For a full local call, first start the LiveKit/SIP topology from the runbook.
Build `fixtures/cmd/speech` with the same native libraries as the other phone
fixtures. Set `SIP_SPEECH_FIXTURE` to its binary and `VOICEOPS_EVIDENCE` to an
external evidence directory. FFmpeg must be on `PATH`.

```sh
export SIP_SPEECH_FIXTURE="$PWD/fixtures/speech"
export VOICEOPS_EVIDENCE="$PWD/.runtime/speech-evidence"
export VOICEOPS_LLM_URL=http://127.0.0.1:18100/v1/chat/completions
PYTHONPATH=. python scripts/speech_verify.py
```

The script creates and removes a private fixture trunk for number `2002`, a
unique dispatch rule, and a unique room. It sends synthetic speech over actual
SIP/RTP, submits `#`, verifies the caller transcript and structured category,
records the spoken reply at the remote phone, and independently transcribes that
returned audio. The input Matroska container contains PCM in twenty-millisecond
frames, matching the upstream phone's sender cadence. Leading inference silence
is removed before its independent ASR pass; pauses inside speech are retained.
These checks establish a bounded local integration, not carrier availability,
production speech quality or latency guarantees.

## Worker and operator integration

Supply the same fields as `SpeechConfig` in an operator-owned JSON file (maximum
4 KiB). For example, create `.runtime/speech.json` from the repository root:

```json
{
  "whisper_python": ".runtime/whisper/bin/python",
  "whisper_model": ".runtime/models/whisper-tiny.en",
  "espeak": ".runtime/espeak-ng/bin/espeak-ng",
  "llm_url": "http://127.0.0.1:18100/v1/chat/completions",
  "max_audio_seconds": 20,
  "inference_timeout": 30
}
```

Start the existing worker with `--speech-config .runtime/speech.json`.
This opt-in mode disables its echo output. The worker retains one-call admission,
RTC session identity checks, consent-versioned recording, transfer supervision,
recovery and bounded drain. A new SIP participant session receives a new speech
session, even when the caller identity string is reused.

The worker retains a transcript only when the current call has explicit
transcript consent. SDK callbacks from an obsolete participant SID are ignored.
DTMF controls are also bound to both identity and SID. Lifecycle metrics contain
stage names, timing and validated category metadata; they exclude transcript
text. Each session has a bounded metrics budget.

Generated TTS duration and RTC audio acceptance are separate events. The
`speech_rtc_output` event counts frames only after the actual RTC output sink
accepts them, expressed as 8 kHz equivalent samples. These counters do not prove
remote playback; the SIP verifier independently captures the remote recording.
The worker and operator API use an `intermittent` outbound media policy for
speech sessions: inbound frames remain the liveness requirement while the agent
is listening or thinking. Echo sessions continue to require both directions.

`PYTHONPATH=. python scripts/speech_worker_verify.py` runs two sequential calls
through the real worker on fixture number `2002`: one with no transcript
retention, then one with explicit synthetic-fixture consent. It checks different
call IDs, zero versus two retained messages, caller-side recorded speech,
separate RTC acceptance metrics, terminal call states and clean worker shutdown.
Use the same engine environment variables as the standalone verifier.

The final CPython 3.11.11 profile passed the full local SDK/SIP path with both
models. Each used the caller sentence, “My name is John. I cannot log into my
account.” Independent ASR of both remote recordings returned, “Thank you, John.
This is an account request. It is ready for review.”

| Local model | Caller playback through call finish | Input ASR | Model inference | TTS generation, two chunks |
|---|---:|---:|---:|---:|
| Qwen2.5 1.5B 4-bit | 20.040 s | 7.586 s | 2.191 s | 0.127 s |
| Qwen3 0.6B 4-bit | 17.672 s | 4.703 s | 2.721 s | 0.125 s |

The first timing includes caller playback, the spoken reply and the phone
process finishing. Independent ASR of the returned recording runs afterward.
TTS generation time excludes audio playback. Both recordings contained 93,048
samples after conversion to 16 kHz; that recording measurement is separate from
the RTC sink's 39,418 accepted 8 kHz equivalent samples.

The two actual-worker cases retained zero messages without consent and two
with explicit consent. Their verification intervals were 24.709 s and
18.230 s respectively, including hangup and independent returned-recording
ASR. Both reached an ended call state and the worker shut down cleanly. The
first worker recording's final words were imperfectly transcribed, while its
account-request topic remained clear.

A contended CPU run exceeded the inference deadline. An ambiguous initial
request was misclassified before category definitions were clarified, and a
shorter spoken confirmation failed returned-audio recognition before the
confirmation wording was improved. These retained failure cases matter when
evaluating another environment. The measurements cover small deterministic
fixtures with one active call; they are not a speech accuracy study or a
production latency target.
