"""Small RTP/PCMU inspectors used by the local media verification fixtures."""
from dataclasses import dataclass
import struct


class InvalidMedia(ValueError):
    """A packet cannot be inspected safely."""


@dataclass(frozen=True)
class RTPPacket:
    sequence: int
    timestamp: int
    ssrc: int
    payload_type: int
    marker: bool
    payload: bytes


def parse_rtp(raw):
    if not isinstance(raw, (bytes, bytearray)) or not 12 <= len(raw) <= 65535:
        raise InvalidMedia("Invalid RTP packet length")
    flags, kind, sequence, clock, ssrc = struct.unpack("!BBHII", raw[:12])
    if flags >> 6 != 2:
        raise InvalidMedia("RTP version must be 2")
    if flags & 0x3F:
        raise InvalidMedia("Extended RTP headers are not supported by this inspector")
    return RTPPacket(sequence, clock, ssrc, kind & 127, bool(kind & 128), bytes(raw[12:]))


def decode_pcmu(payload):
    samples = []
    for encoded in payload:
        value = ~encoded & 255
        magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
        sample = magnitude - 132
        samples.append(-sample if value & 128 else sample)
    return samples
