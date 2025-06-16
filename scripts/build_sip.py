"""Build the immutable SIP bridge using an installed Go 1.24.2 and native codecs."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

REVISION = "368750a3d4e394e3058d1400aae8459d4eed7ede"
SHA256 = "b705c140d6eabb00e949daf5f98f91ffb0b2d1a2dffc8edad78ff1242d55dfc5"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go")
    parser.add_argument("--output", default=".runtime/native")
    args = parser.parse_args()
    version = subprocess.check_output([args.go, "version"], text=True)
    if "go1.24.2 " not in version:
        raise SystemExit("Install Go 1.24.2 or pass its absolute executable path with --go")
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "sip-source.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(f"https://codeload.github.com/livekit/sip/tar.gz/{REVISION}", timeout=60) as response:
            with archive.open("xb") as stream:
                shutil.copyfileobj(response, stream)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
            raise SystemExit("SIP source checksum mismatch")
    source = root / ("sip-" + REVISION)
    if not source.exists():
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                target = (root / member.name).resolve()
                if not target.is_relative_to(root) or not (member.isdir() or member.isfile()):
                    raise SystemExit("Unexpected source archive member")
            bundle.extractall(root)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if member.isfile():
                target = root / member.name
                if target.is_symlink() or not target.is_file() or target.read_bytes() != bundle.extractfile(member).read():
                    raise SystemExit("Extracted SIP source differs from the immutable archive")
    output = root / "livekit-sip"
    subprocess.run([args.go, "build", "-o", str(output), "./cmd/livekit-sip"], cwd=source, check=True)
    print(output)


if __name__ == "__main__":
    main()
