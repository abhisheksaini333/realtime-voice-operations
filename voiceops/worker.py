"""One local RTC worker per ledger, with bounded recovery and media monitoring."""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from .ledger import CallLedger, InvalidCall
from .livekit_gateway import LiveKitGateway
from .observations import Observations
from .supervisor import Supervisor
from .transfers import TransferService


@contextmanager
def worker_lock(path):
    lock_path = Path(str(path) + ".worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another worker already owns this ledger") from exc
        yield
    finally:
        os.close(descriptor)


async def serve(args):
    from livekit import api, rtc
    ledger = CallLedger(args.database, max_active_calls=args.capacity)
    observations = Observations(ledger)
    gateway = LiveKitGateway(args.url, args.api_key, args.api_secret)
    transfers = TransferService(ledger, targets={"operator": "sip:operator@127.0.0.1:18160",
                                                "reject": "sip:reject@127.0.0.1:18160",
                                                "unanswered": "sip:unanswered@127.0.0.1:18160"})
    supervisor = Supervisor(ledger, transfers, gateway)
    transfers.recover_dispatched(now=time.time(), older_than=time.time() - 10)
    recovered = await supervisor.reconcile(args.owner)
    # A replacement worker cannot reconstruct the terminated inference session.
    for call_id in recovered["active"]:
        await supervisor.hangup(call_id, args.owner)
    room = rtc.Room()
    source = rtc.AudioSource(8000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("local-media-check", source)
    tasks = set()
    calls = {}
    stopping = False
    output_enabled = True

    def spawn(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        def finished(done):
            tasks.discard(done)
            if not done.cancelled() and done.exception():
                print(json.dumps({"event": "worker_task_failed", "type": type(done.exception()).__name__}), flush=True)
        task.add_done_callback(finished)

    def admitted(participant):
        if "sip.callID" not in participant.attributes:
            return None
        if participant.identity in calls:
            return calls[participant.identity]
        try:
            call = ledger.admit(owner=args.owner, caller=participant.attributes.get("sip.phoneNumber", ""),
                                room=args.room, participant=participant.identity, now=time.time())
            calls[participant.identity] = call["id"]
            with ledger.transaction() as db:
                ledger._event(db, call["id"], "sip_dialog_observed", time.time(),
                              {"sip_call_id": participant.attributes.get("sip.callIDFull"),
                               "bridge_call_id": participant.attributes["sip.callID"]})
            return call["id"]
        except InvalidCall:
            spawn(gateway.hangup(args.room, participant.identity))
            return None

    async def echo(remote, participant, call_id):
        pending_received = pending_sent = 0
        last_flush = time.monotonic()
        async for event in rtc.AudioStream(remote, sample_rate=8000, num_channels=1):
            call = ledger.get(call_id, args.owner)
            if call["status"] in {"admitted", "ringing"} and participant.attributes.get("sip.callStatus") == "active":
                ledger.transition(call_id, args.owner, "answered", now=time.time(), version=call["version"])
            pending_received += event.frame.samples_per_channel
            if output_enabled:
                await source.capture_frame(event.frame)
                pending_sent += event.frame.samples_per_channel
            if time.monotonic() - last_flush >= 0.2:
                observations.media(call_id, args.owner, received=pending_received, sent=pending_sent, now=time.time())
                pending_received = pending_sent = 0
                last_flush = time.monotonic()
        if pending_received or pending_sent:
            observations.media(call_id, args.owner, received=pending_received, sent=pending_sent, now=time.time())

    @room.on("participant_connected")
    def connected(participant):
        admitted(participant)

    @room.on("track_subscribed")
    def subscribed(remote, publication, participant):
        if remote.kind == rtc.TrackKind.KIND_AUDIO:
            call_id = admitted(participant)
            if call_id:
                spawn(echo(remote, participant, call_id))

    @room.on("sip_dtmf_received")
    def dtmf(event):
        participant = event.participant
        if participant and participant.identity in calls:
            observations.dtmf(calls[participant.identity], args.owner, participant.identity, event.digit, now=time.time())

    token = api.AccessToken(args.api_key, args.api_secret).with_identity("voiceops-worker").with_grants(
        api.VideoGrants(room_join=True, room=args.room)).to_jwt()

    def stop():
        nonlocal stopping
        if not stopping:
            stopping = True
            ledger.begin_drain(now=time.time(), deadline=time.time() + 10)

    def interrupt_output():
        nonlocal output_enabled
        output_enabled = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    if args.allow_test_faults:
        loop.add_signal_handler(signal.SIGUSR1, interrupt_output)
    try:
        await room.connect(args.url.replace("http://", "ws://", 1), token)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        for participant in room.remote_participants.values():
            admitted(participant)
        print(json.dumps({"event": "ready", "room": args.room, "recovery": recovered}), flush=True)
        while True:
            await supervisor.reconcile(args.owner)
            await supervisor.enforce_drain(args.owner)
            for call in ledger.list_calls(args.owner):
                if call["status"] not in {"ended", "failed", "transferred"} and observations.health(
                        call["id"], args.owner, now=time.time(), timeout=args.media_timeout)["stale"]:
                    try:
                        await supervisor.hangup(call["id"], args.owner)
                    except Exception:
                        pass  # State remains unresolved until a later actual lookup succeeds.
            if stopping and ledger.drain_status(now=time.time())["complete"]:
                break
            await asyncio.sleep(0.25)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await room.disconnect()
        await source.aclose()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=".runtime/calls.sqlite")
    parser.add_argument("--room", default="voiceops-local")
    parser.add_argument("--owner", default="local-team")
    parser.add_argument("--capacity", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--media-timeout", type=int, default=15, choices=range(5, 61))
    parser.add_argument("--allow-test-faults", action="store_true", help="Allow SIGUSR1 to stop local audio output for failure tests")
    parser.add_argument("--url", default=os.environ.get("LIVEKIT_URL", "http://127.0.0.1:18140"))
    parser.add_argument("--api-key", default=os.environ.get("LIVEKIT_API_KEY", "devkey"))
    parser.add_argument("--api-secret", default=os.environ.get("LIVEKIT_API_SECRET", "local-demo-secret-voice-operations-123456"))
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    with worker_lock(args.database):
        asyncio.run(serve(args))
