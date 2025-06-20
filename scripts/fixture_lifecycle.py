"""Clean up only resources created by an owned local verification fixture."""

import asyncio
import inspect


class FixtureCleanup:
    """Run every registered cleanup, retaining setup and cleanup exceptions."""

    def __init__(self, on_error_only=False):
        self.callbacks = []
        self.on_error_only = on_error_only

    async def __aenter__(self):
        return self

    def defer(self, label, callback):
        self.callbacks.append((label, callback))

    async def __aexit__(self, exc_type, exception, traceback):
        if self.on_error_only and exception is None:
            return False
        errors = [exception] if exception is not None else []
        for label, callback in reversed(self.callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except BaseException as error:
                error.add_note("Fixture cleanup failed: " + label)
                errors.append(error)
        if len(errors) > 1:
            raise BaseExceptionGroup(
                "Fixture setup or cleanup failed", errors
            ) from None
        if errors and errors[0] is not exception:
            raise errors[0]
        return False


async def require_number_available(client, api, number="2000"):
    trunks = await client.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
    if any(number in trunk.numbers for trunk in trunks.items):
        raise RuntimeError(
            f"Number {number} is already in use; stop its owned fixture before proceeding"
        )


async def delete_owned_room(client, api, name):
    rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[name]))
    if any(room.name == name for room in rooms.rooms):
        await client.room.delete_room(api.DeleteRoomRequest(room=name))


async def stop_process(process):
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), 5)


async def stop_tasks(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [
        result
        for result in results
        if isinstance(result, BaseException)
        and not isinstance(result, asyncio.CancelledError)
    ]
    if errors:
        raise BaseExceptionGroup("Fixture background task failed", errors)
