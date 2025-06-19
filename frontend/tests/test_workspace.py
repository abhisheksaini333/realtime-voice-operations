"""UI contract tests against the real API and an isolated journal; no SIP dispatch."""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from voiceops.api import server
from voiceops.ledger import CallLedger
from voiceops.transfers import TransferService
from voiceops.supervisor import Supervisor


class NoDispatch:
    async def transfer(self, *args):
        raise AssertionError("This UI contract test must not dispatch SIP")


@unittest.skipUnless(
    os.environ.get("RUN_VOICE_UI_TESTS"), "requires built UI and Chromium"
)
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        now = time.time()
        call = self.ledger.admit(
            owner="ui-team",
            caller="1002",
            room="ui-fixture",
            participant="sip-ui",
            now=now - 2,
        )
        self.call = self.ledger.transition(
            call["id"], "ui-team", "answered", now=now - 1, version=1
        )
        self.transfers = TransferService(
            self.ledger, targets={"operator": "sip:operator@127.0.0.1:18160"}
        )
        tokens = {
            role[0] * 40: {"name": role, "owner": "ui-team", "role": role}
            for role in ["admin", "operator", "viewer"]
        }
        self.http = server(
            self.ledger,
            self.transfers,
            Supervisor(self.ledger, self.transfers, NoDispatch()),
            tokens,
            port=0,
            ui_dir=Path(__file__).resolve().parents[1] / "dist",
        )
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH") or None,
            headless=True,
        )
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def login(self, role):
        self.page.goto("http://127.0.0.1:" + str(self.http.server_port))
        self.page.get_by_label("Access key").fill(role[0] * 40)
        self.page.get_by_role("button", name="Open operations").click()
        self.page.locator(".call-row").first.click()

    def test_viewer_cannot_see_write_or_audio_controls(self):
        from playwright.sync_api import expect

        self.login("viewer")
        expect(
            self.page.get_by_text("Your viewer role can inspect metadata.", exact=False)
        ).to_be_visible()
        expect(
            self.page.get_by_role("button", name="Review transfer", exact=True)
        ).to_have_count(0)
        expect(
            self.page.get_by_role("button", name="Drain service", exact=True)
        ).to_have_count(0)
        expect(
            self.page.get_by_role("button", name="Load consented recording")
        ).to_have_count(0)

    def test_operator_cancels_exact_proposal_before_dispatch(self):
        from playwright.sync_api import expect

        self.login("operator")
        expect(
            self.page.get_by_role("button", name="Drain service", exact=True)
        ).to_have_count(0)
        self.page.get_by_role("button", name="Review transfer", exact=True).click()
        self.page.get_by_role("button", name="Cancel proposal", exact=True).click(
            timeout=1500
        )
        expect(
            self.page.locator(".transfer-history").get_by_text("canceled", exact=True)
        ).to_be_visible()
        self.page.get_by_role("button", name="Review transfer", exact=True).click()
        expect(
            self.page.get_by_role("button", name="Approve exact transfer")
        ).to_be_enabled()
        with self.ledger.connection() as db:
            states = [
                r[0]
                for r in db.execute("SELECT state FROM transfers ORDER BY created_at")
            ]
        self.assertEqual(states, ["canceled", "proposed"])

    def test_unknown_transfer_stays_visible_and_disables_retry(self):
        from playwright.sync_api import expect

        now = time.time()
        proposal = self.transfers.propose(
            self.call["id"], "ui-team", "operator", now=now
        )
        self.transfers.approve(
            proposal["id"], "ui-team", proposal["digest"], "admin", now=now
        )
        self.transfers.claim(proposal["id"], "ui-team", now=now)
        self.transfers.mark_unknown(proposal["id"], "ui-team", "lost_response", now=now)
        self.login("admin")
        expect(
            self.page.get_by_text("The transfer outcome is uncertain.", exact=True)
        ).to_be_visible()
        expect(
            self.page.get_by_role("button", name="Review transfer", exact=True)
        ).to_be_disabled()
