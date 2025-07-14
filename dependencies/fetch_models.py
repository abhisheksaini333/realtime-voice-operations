"""Download or verify one immutable model manifest without loading model code."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request


def verify(path, artifact):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size != artifact["bytes"] or digest.hexdigest() != artifact["sha256"]:
        raise ValueError(f"Artifact size or digest mismatch: {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("whisper", "mlx-qwen25", "mlx-qwen3"))
    parser.add_argument("destination", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = Path(__file__).parent / args.profile / "model-manifest.json"
    data = json.loads(manifest.read_text())
    artifacts = data["files"] if isinstance(data, dict) else data
    if not args.verify_only:
        args.destination.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        name = artifact["file"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Manifest filename must be a basename")
        if not artifact["url"].startswith("https://huggingface.co/"):
            raise ValueError("Manifest must use the pinned model publisher URL")
        target = args.destination / name
        if target.exists() or args.verify_only:
            verify(target, artifact)
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=args.destination, delete=False) as output:
                    temporary = Path(output.name)
                    with urllib.request.urlopen(artifact["url"], timeout=60) as response:
                        size = 0
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > artifact["bytes"]:
                                raise ValueError("Download exceeds manifest size")
                            output.write(chunk)
                verify(temporary, artifact)
                # Refuse to replace an existing artifact, including one created
                # by another installer while this download was in progress.
                target.hardlink_to(temporary)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        print(f"Verified {args.profile}/{name}")


if __name__ == "__main__":
    main()
