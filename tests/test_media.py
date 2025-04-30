import struct
import unittest

from voiceops.media import decode_pcmu, parse_rtp, InvalidMedia


class RTPTests(unittest.TestCase):
    def test_parse_pcmu_packet_preserves_identifiers_and_payload(self):
        raw = struct.pack("!BBHII", 0x80, 0x80, 65535, 160, 42) + b"\xff" * 160
        packet = parse_rtp(raw)
        self.assertEqual((packet.sequence, packet.timestamp, packet.ssrc), (65535, 160, 42))
        self.assertEqual(packet.payload_type, 0)
        self.assertTrue(packet.marker)
        self.assertEqual(packet.payload, b"\xff" * 160)

    def test_truncated_or_wrong_version_packets_are_rejected(self):
        for raw in [b"", b"\x80" * 11, struct.pack("!BBHII", 0x40, 0, 1, 0, 2)]:
            with self.subTest(raw=raw), self.assertRaises(InvalidMedia):
                parse_rtp(raw)

    def test_pcmu_reference_values_decode_to_signed_pcm(self):
        self.assertEqual(decode_pcmu(bytes([0xff, 0x7f, 0, 0x80])), [0, 0, -32124, 32124])
