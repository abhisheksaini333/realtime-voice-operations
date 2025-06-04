"""Non-destructive SQLite snapshot and verified restore helpers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as snapshot:
                original.backup(snapshot)
                integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError("Backup integrity check failed")
                snapshot.execute("PRAGMA journal_mode=DELETE")
        manifest = {"sha256": digest(destination), "bytes": destination.stat().st_size, "integrity": integrity}
        with destination.with_suffix(destination.suffix + ".json").open("x") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
        return manifest
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def restore_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    manifest = json.loads(source.with_suffix(source.suffix + ".json").read_text())
    if digest(source) != manifest["sha256"]:
        raise ValueError("Backup checksum does not match")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as snapshot:
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database is corrupt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    function = backup_database if args.operation == "backup" else restore_database
    print(function(args.source, args.destination))
