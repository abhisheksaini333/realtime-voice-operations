"""Small adapter for the pinned LiveKit API; credentials stay on the server."""
from urllib.parse import urlsplit

from .ledger import InvalidCall


class LiveKitGateway:
    def __init__(self, url, api_key, api_secret):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise InvalidCall("The reference gateway requires a loopback LiveKit endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise InvalidCall("Invalid LiveKit endpoint")
        self.url, self.api_key, self.api_secret = url, api_key, api_secret

    def client(self):
        from livekit import api
        return api.LiveKitAPI(self.url, self.api_key, self.api_secret)

    async def active(self, room, participant, *, expected_sid=None):
        from livekit import api
        async with self.client() as client:
            result = await client.room.list_participants(api.ListParticipantsRequest(room=room))
            return any(person.identity == participant and (expected_sid is None or person.sid == expected_sid)
                       for person in result.participants)

    async def hangup(self, room, participant, *, expected_sid=None):
        from livekit import api
        if expected_sid and not await self.active(room, participant, expected_sid=expected_sid):
            return
        async with self.client() as client:
            await client.room.remove_participant(api.RoomParticipantIdentity(room=room, identity=participant))

    async def transfer(self, room, participant, uri, *, expected_sid=None):
        from livekit import api
        if expected_sid and not await self.active(room, participant, expected_sid=expected_sid):
            raise InvalidCall("The approved SIP participant session is no longer present")
        async with self.client() as client:
            await client.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                room_name=room, participant_identity=participant, transfer_to=uri))
