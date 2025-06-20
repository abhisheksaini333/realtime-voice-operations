import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts.fixture_lifecycle import FixtureCleanup


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_cleanup_callbacks_run_and_preserve_original_failure(self):
        observed = []

        async def cleanup(label):
            observed.append(label)
            raise RuntimeError(label + " failure")

        with self.assertRaises(BaseExceptionGroup) as caught:
            async with FixtureCleanup() as resources:
                for label in ["client", "trunk", "rule", "room"]:
                    resources.defer(label, lambda label=label: cleanup(label))
                raise ValueError("setup failure")
        self.assertEqual(observed, ["room", "rule", "trunk", "client"])
        self.assertEqual(
            [str(e) for e in caught.exception.exceptions],
            [
                "setup failure",
                "room failure",
                "rule failure",
                "trunk failure",
                "client failure",
            ],
        )

    async def test_provisioned_resources_are_retained_only_on_success(self):
        cleanup = AsyncMock()
        async with FixtureCleanup(on_error_only=True) as resources:
            resources.defer("persistent resource", cleanup)
        cleanup.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "setup failed"):
            async with FixtureCleanup(on_error_only=True) as resources:
                resources.defer("persistent resource", cleanup)
                raise ValueError("setup failed")
        cleanup.assert_awaited_once()


class FixtureScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = types.SimpleNamespace(
            sip=types.SimpleNamespace(
                list_sip_inbound_trunk=AsyncMock(
                    return_value=types.SimpleNamespace(items=[])
                ),
                create_sip_inbound_trunk=AsyncMock(
                    return_value=types.SimpleNamespace(sip_trunk_id="owned-trunk")
                ),
                create_sip_dispatch_rule=AsyncMock(
                    side_effect=RuntimeError("rule setup failed")
                ),
                delete_sip_dispatch_rule=AsyncMock(),
                delete_sip_trunk=AsyncMock(),
            ),
            room=types.SimpleNamespace(
                list_rooms=AsyncMock(return_value=types.SimpleNamespace(rooms=[])),
                delete_room=AsyncMock(),
            ),
            aclose=AsyncMock(),
        )

        class API:
            def __getattr__(self, name):
                return lambda **values: types.SimpleNamespace(**values)

        self.api = API()
        self.api.LiveKitAPI = Mock(return_value=self.client)
        self.fake = types.SimpleNamespace(api=self.api, rtc=types.SimpleNamespace())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def load(self, name):
        sys.modules.pop(name, None)
        with patch.dict(sys.modules, {"livekit": self.fake}):
            module = importlib.import_module(name)
        self.addCleanup(lambda: sys.modules.pop(name, None))
        return module

    async def test_partial_rule_setup_failure_cleans_trunk_and_client(self):
        for name, method in [
            ("scripts.verify_transfers", "one"),
            ("scripts.verify_worker_runtime", "verify"),
        ]:
            with (
                self.subTest(script=name),
                patch.dict("os.environ", {"VOICEOPS_EVIDENCE": self.temp.name}),
            ):
                module = self.load(name)
                self.client.sip.delete_sip_trunk.reset_mock()
                self.client.aclose.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "rule setup failed"):
                    await getattr(module, method)(
                        "operator" if method == "one" else "restart"
                    )
                self.client.sip.delete_sip_trunk.assert_awaited_once()
                self.client.aclose.assert_awaited_once()
                self.client.sip.delete_sip_dispatch_rule.assert_not_awaited()

    async def test_existing_2000_fixture_is_never_replaced_or_deleted(self):
        self.client.sip.list_sip_inbound_trunk.return_value = types.SimpleNamespace(
            items=[types.SimpleNamespace(numbers=["2000"])]
        )
        for name, method in [
            ("scripts.verify_transfers", "one"),
            ("scripts.verify_worker_runtime", "verify"),
        ]:
            with (
                self.subTest(script=name),
                patch.dict("os.environ", {"VOICEOPS_EVIDENCE": self.temp.name}),
            ):
                module = self.load(name)
                with self.assertRaisesRegex(RuntimeError, "already in use"):
                    await getattr(module, method)(
                        "operator" if method == "one" else "restart"
                    )
                self.client.sip.create_sip_inbound_trunk.assert_not_awaited()
                self.client.sip.delete_sip_trunk.assert_not_awaited()

    async def test_demo_remove_attempts_trunk_and_client_after_rule_cleanup_failure(
        self,
    ):
        module = self.load("scripts.demo")
        state = Path(self.temp.name) / "state.json"
        state.write_text(
            json.dumps(
                {
                    "rule_id": "owned-rule",
                    "trunk_id": "owned-trunk",
                    "room": "owned-room",
                }
            )
        )
        self.client.sip.delete_sip_dispatch_rule.side_effect = RuntimeError(
            "rule deletion failed"
        )
        args = types.SimpleNamespace(operation="remove", state=str(state))
        with self.assertRaisesRegex(RuntimeError, "rule deletion failed"):
            await module.run(args)
        self.client.sip.delete_sip_trunk.assert_awaited_once()
        self.client.aclose.assert_awaited_once()
        self.assertTrue(state.exists(), "Failed cleanup must retain the recorded IDs")

    async def test_demo_rule_setup_failure_rolls_back_only_its_created_trunk(self):
        module = self.load("scripts.demo")
        state = Path(self.temp.name) / "provision.json"
        args = types.SimpleNamespace(
            operation="provision", state=str(state), room="owned-room"
        )
        with self.assertRaisesRegex(RuntimeError, "rule setup failed"):
            await module.run(args)
        self.client.sip.delete_sip_trunk.assert_awaited_once()
        self.assertEqual(
            self.client.sip.delete_sip_trunk.await_args.args[0].sip_trunk_id,
            "owned-trunk",
        )
        self.client.sip.delete_sip_dispatch_rule.assert_not_awaited()
        self.client.aclose.assert_awaited_once()
        self.assertFalse(state.exists())

    async def test_demo_state_failure_retains_all_cleanup_failures(self):
        module = self.load("scripts.demo")
        self.client.sip.create_sip_dispatch_rule.side_effect = None
        self.client.sip.create_sip_dispatch_rule.return_value = types.SimpleNamespace(
            sip_dispatch_rule_id="owned-rule"
        )
        self.client.sip.delete_sip_dispatch_rule.side_effect = RuntimeError(
            "rule deletion failed"
        )
        self.client.sip.delete_sip_trunk.side_effect = RuntimeError(
            "trunk deletion failed"
        )
        self.client.aclose.side_effect = RuntimeError("client close failed")
        args = types.SimpleNamespace(
            operation="provision",
            state=str(Path(self.temp.name) / "state.json"),
            room="owned-room",
        )
        with patch.object(
            module.os, "open", side_effect=PermissionError("state write failed")
        ):
            with self.assertRaises(BaseExceptionGroup) as caught:
                await module.run(args)

        def leaves(error):
            if isinstance(error, BaseExceptionGroup):
                return [
                    message for nested in error.exceptions for message in leaves(nested)
                ]
            return [str(error)]

        self.assertEqual(
            leaves(caught.exception),
            [
                "state write failed",
                "rule deletion failed",
                "trunk deletion failed",
                "client close failed",
            ],
        )
        self.client.sip.delete_sip_trunk.assert_awaited_once()
        self.client.aclose.assert_awaited_once()

    async def test_successful_demo_keeps_provisioned_ids_but_closes_client(self):
        module = self.load("scripts.demo")
        self.client.sip.create_sip_dispatch_rule.side_effect = None
        self.client.sip.create_sip_dispatch_rule.return_value = types.SimpleNamespace(
            sip_dispatch_rule_id="owned-rule"
        )
        state = Path(self.temp.name) / "state.json"
        args = types.SimpleNamespace(
            operation="provision", state=str(state), room="owned-room"
        )
        await module.run(args)
        self.assertEqual(json.loads(state.read_text())["trunk_id"], "owned-trunk")
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.client.sip.delete_sip_trunk.assert_not_awaited()
        self.client.sip.delete_sip_dispatch_rule.assert_not_awaited()
        self.client.aclose.assert_awaited_once()
