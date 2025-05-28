import unittest
from voiceops.ledger import InvalidCall
from voiceops.livekit_gateway import LiveKitGateway


class GatewayTests(unittest.TestCase):
    def test_external_or_ambiguous_gateway_endpoints_are_rejected(self):
        for url in ("https://example.org", "http://127.0.0.1@evil.test", "http://127.0.0.1/private",
                    "http://127.0.0.1?redirect=evil", "file:///private"):
            with self.assertRaises(InvalidCall):
                LiveKitGateway(url, "key", "secret")

    def test_loopback_endpoint_is_retained(self):
        gateway = LiveKitGateway("http://127.0.0.1:18140", "key", "secret")
        self.assertEqual(gateway.url, "http://127.0.0.1:18140")
