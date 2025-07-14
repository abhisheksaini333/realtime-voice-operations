# Reproducing the local runtime

These manifests identify the dependencies used by the local voice reference
system. They contain package and model hashes, upstream URLs and artifact
release evidence. Runtime binaries and model weights are downloaded separately.
Run the commands below from the repository root.

The qualified machine is macOS on Apple Silicon. Use **CPython 3.11.11**, Go
**1.24.2**, a C compiler, `make`, micromamba, Docker Desktop and FFmpeg. The
Qwen3 MLX wheel requires macOS 15 or newer. Other operating systems and
architectures need their own wheel, codec and media-network validation.

| Profile | Exact package count | Latest selected artifact upload | Evidence |
|---|---:|---|---|
| LiveKit worker | 36 | April 12, 2025 | [Versions](livekit/requirements.lock), [wheel audit](livekit/selected-artifact-audit.json) |
| Whisper / Torch | 23 | June 7, 2024 | [Versions](whisper/requirements.lock), [wheel audit](whisper/selected-artifact-audit.json) |
| Qwen2.5 / MLX | 23 | December 24, 2024 | [Versions](mlx-qwen25/requirements.lock), [wheel audit](mlx-qwen25/selected-artifact-audit.json) |
| Qwen3 / MLX | 24 | May 12, 2025 | [Versions](mlx-qwen3/requirements.lock), [wheel audit](mlx-qwen3/selected-artifact-audit.json) |

The Qwen3 combined dependency profile is available from May 13, 2025 in India.
Artifact availability is separate from the time a test was executed. The
reproducible Whisper profile uses Python 3.11.11 with 23 exact package versions.
Its [execution environment](whisper/execution-environment.json) records the
tested host, while the wheel audit records the selected published artifacts.

## Python environments

[Python provenance](native/python.json) identifies the exact standalone
CPython archive and SHA-256. Install that archive or provide an independently
verified CPython 3.11.11 executable as `python3.11` before proceeding. No global
Python environment needs to be changed.

```sh
python3.11 -c 'import sys; assert sys.version_info[:3] == (3, 11, 11)'
python3.11 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r dependencies/livekit/requirements-macos-arm64.lock
.venv/bin/python -m pip install --no-deps -e .

python3.11 -m venv .runtime/whisper
.runtime/whisper/bin/python -m pip install --only-binary=:all: --require-hashes -r dependencies/whisper/requirements-macos-arm64.lock

python3.11 -m venv .runtime/mlx-qwen25
.runtime/mlx-qwen25/bin/python -m pip install --only-binary=:all: --require-hashes -r dependencies/mlx-qwen25/requirements-macos-arm64.lock

python3.11 -m venv .runtime/mlx-qwen3
.runtime/mlx-qwen3/bin/python -m pip install --only-binary=:all: --require-hashes -r dependencies/mlx-qwen3/requirements-macos-arm64.lock
```

Keep these environments separate: Whisper needs NumPy 1.26.4, while the MLX
profiles and worker use their own NumPy versions. The hash locks contain the
actual tested wheels. The adjacent plain `requirements.lock` files preserve
the complete version set for auditing, but a different platform's wheel is not
implicitly covered by this validation. Packaging tools used by editable
installation are build tooling, not entries in the application runtime lock.

## Immutable models and local servers

```sh
.venv/bin/python dependencies/fetch_models.py whisper .runtime/models/whisper-tiny.en
.venv/bin/python dependencies/fetch_models.py mlx-qwen25 .runtime/models/qwen25-1.5b-4bit
.venv/bin/python dependencies/fetch_models.py mlx-qwen3 .runtime/models/qwen3-0.6b-4bit
```

The downloader checks every file's size and SHA-256 before installation. It
refuses to replace a file with a different digest. Add `--verify-only` to check
an existing download without network access. These files total about 1.4 GB;
allow additional space for environments, container layers and build caches.

| Model | Immutable revision | Manifest |
|---|---|---|
| openai/whisper-tiny.en | `87c7102498dcde7456f24cfd30239ca606ed9063` | [12 files](whisper/model-manifest.json) |
| mlx-community/Qwen2.5-1.5B-Instruct-4bit | `8b403126fc14f14cfc99bb4cfa72ecbc129ea677` | [10 files](mlx-qwen25/model-manifest.json) |
| mlx-community/Qwen3-0.6B-4bit | `73e3e38d981303bc594367cd910ea6eb48349da8` | [10 files](mlx-qwen3/model-manifest.json) |

Start one server in its own terminal; the worker defaults to the Qwen2.5 port.

```sh
.runtime/mlx-qwen25/bin/python -m mlx_lm.server --model .runtime/models/qwen25-1.5b-4bit --host 127.0.0.1 --port 18100
```

Or use the Qwen3 environment and set the worker's `llm_url` to port 18101:

```sh
.runtime/mlx-qwen3/bin/python -m mlx_lm.server --model .runtime/models/qwen3-0.6b-4bit --host 127.0.0.1 --port 18101
```

The MLX servers are local development servers. Keep them on loopback. Use
the exact local model directories; the voice adapter loads Whisper offline
and does not enable arbitrary remote model code. See the copied upstream
[Qwen2.5](mlx-qwen25/MODEL_CARD.md) and [Qwen3](mlx-qwen3/MODEL_CARD.md)
cards for attribution and Apache-2.0 model license links. Those upstream cards
contain generic installation examples; use this project's pinned commands.
Whisper's model card is included in its downloaded manifest; the upstream
[Whisper project](https://github.com/openai/whisper/blob/main/LICENSE) uses MIT.

## eSpeak NG 1.51

The tested standalone synthesizer is eSpeak NG 1.51. Its
[source provenance](espeak/source-provenance.json) records both official source
archives and their SHA-256 values. The release tarball omits some files needed
by its generated Makefile. The build used the same-tag archive to supply only
missing files, without replacing release files. Reproduce that source tree as
follows:

```sh
mkdir -p .runtime/espeak-source/release .runtime/espeak-source/tag
curl -fL https://github.com/espeak-ng/espeak-ng/releases/download/1.51/espeak-ng-1.51.tar.gz -o .runtime/espeak-source/release.tar.gz
curl -fL https://github.com/espeak-ng/espeak-ng/archive/refs/tags/1.51.tar.gz -o .runtime/espeak-source/tag.tar.gz
.venv/bin/python - <<'PY'
import hashlib
from pathlib import Path
expected = {
    "release.tar.gz": "027fa5dfa8616d5cc13f883209ff5f735eee6559f7689a019d5b2d01d290cd39",
    "tag.tar.gz": "f0e028f695a8241c4fa90df7a8c8c5d68dcadbdbc91e758a97e594bbb0a3bdbf",
}
for name, digest in expected.items():
    path = Path(".runtime/espeak-source") / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"Archive checksum mismatch: {name}")
PY
tar -xzf .runtime/espeak-source/release.tar.gz -C .runtime/espeak-source/release
tar -xzf .runtime/espeak-source/tag.tar.gz -C .runtime/espeak-source/tag
.venv/bin/python - <<'PY'
from pathlib import Path
import shutil
source = Path(".runtime/espeak-source/tag/espeak-ng-1.51")
destination = Path(".runtime/espeak-source/release/espeak-ng-1.51")
for path in source.rglob("*"):
    target = destination / path.relative_to(source)
    if path.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
PY
VOICEOPS_ESPEAK_PREFIX="$PWD/.runtime/espeak-ng"
(
  cd .runtime/espeak-source/release/espeak-ng-1.51
  ./configure --prefix="$VOICEOPS_ESPEAK_PREFIX" --without-async --without-pcaudiolib --without-klatt --without-mbrola --without-sonic
  make -j2
  make install
)
.runtime/espeak-ng/bin/espeak-ng --version
```

The configuration above matches the tested source build. It synthesizes mono
PCM16 at 22,050 Hz through a child process; no system audio playback is needed.
The source is GPL-licensed and also contains components covered by the copied
[GPL](licenses/espeak-ng/COPYING), [BSD-2-Clause](licenses/espeak-ng/COPYING.BSD2),
[Apache-2.0](licenses/espeak-ng/COPYING.APACHE) and
[Unicode](licenses/espeak-ng/COPYING.UCD) notices. Retain the applicable notices
and source obligations when redistributing that independently built runtime.
The repository's MIT license applies to its original code, not these programs
or the attributed Apache-licensed SIP fixture.

## SIP, containers and console

The [Go artifact](native/go.json), [SIP source digest](native/sip.json),
[eight codec artifact records](native/codec-artifacts.json) and
[explicit codec environment](native/codecs-osx-arm64.txt) identify the native
build inputs. The explicit file is also supplied under `infra/` for the
[runbook's build commands](../docs/runbook.md). `scripts/build_sip.py` checks
the source archive hash and rejects changes to the extracted upstream tree.
The fixture's separate dependency graph is locked in `fixtures/go.mod` and
`fixtures/go.sum`; the upstream license stays in `fixtures/sipphone/LICENSE`.

[Container evidence](native/containers.json) records the exact Compose image
digests and inspected build metadata. Build timestamps and registry publication
timestamps have different meanings; neither is a test execution timestamp.
The [frontend's dependency provenance](../frontend/dependency-provenance.json)
and [build instructions](../frontend/README.md) cover the console separately.

## Boundaries of the audit

Hashes establish artifact identity and the release ledgers establish
availability. They do not establish that an old package is free of current
vulnerabilities. These profiles reproduce a bounded local reference environment;
they are not a current production dependency recommendation. No all-dependency
security certification or carrier interoperability claim is made. Keep model
servers and demo credentials local, load only the identified model files, and
review security advisories before adapting or deploying the stack elsewhere.

FFmpeg, Docker Desktop, the host OS and compiler are host tools rather than
vendored components of these wheel profiles. Native build bytes can differ
across compilers and operating systems. The local acceptance checks establish
the documented single-call behavior, not bit-for-bit native reproducibility,
production speech accuracy, or an unlimited concurrency target.
