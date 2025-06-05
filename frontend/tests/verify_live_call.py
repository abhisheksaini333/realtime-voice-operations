"""Verify an already answered, disposable local SIP call through the operator UI.

This approves a real transfer. Start the project's local SIP fixture first.
"""

import os, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

out = Path(
    os.environ.get(
        "VOICE_UI_EVIDENCE_DIR",
        str(Path(__file__).resolve().parents[2] / ".runtime" / "voice-ui"),
    )
)
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get(
            "CHROMIUM_PATH",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ),
        headless=True,
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:18170")
    page.get_by_label("Access key").fill(os.environ["VOICE_UI_KEY"])
    page.get_by_role("button", name="Open operations").click()
    row = page.get_by_role("button", name=__import__("re").compile("answered")).first
    expect(row).to_be_visible(timeout=20000)
    row.click()
    expect(page.get_by_role("region", name="Media observations")).to_be_visible()
    page.get_by_label("Retain transcript", exact=True).check()
    page.get_by_label("Retain recording", exact=True).check()
    page.get_by_label("Consent confirmation").fill(
        "Local synthetic SIP fixture consented for this verification."
    )
    page.get_by_role("button", name="Save consent", exact=True).click()
    expect(page.get_by_text("Consent preference recorded.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Review transfer", exact=True).click()
    review = page.get_by_role("region", name="Exact transfer review")
    expect(review).to_be_visible()
    expect(
        review.get_by_text("sip:operator@127.0.0.1:18160", exact=True)
    ).to_be_visible()
    page.screenshot(path=str(out / "active-review-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(out / "active-review-mobile.png"), full_page=True)
    page.get_by_role("button", name="Close review").click()
    page.get_by_role("button", name="Review transfer", exact=True).click()
    expect(review).to_be_visible()
    page.get_by_role("button", name="Approve exact transfer").click()
    expect(
        page.get_by_text(
            "Transfer dispatched. Waiting for the destination to answer.", exact=True
        )
    ).to_be_visible(timeout=20000)
    expect(
        page.locator(".transfer-history").get_by_text("connected", exact=True)
    ).to_be_visible(timeout=25000)
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.screenshot(path=str(out / "transfer-observed.png"), full_page=True)
    assert not errors, errors
    (out / "active-call-result.json").write_text(
        json.dumps(
            {
                "exact_transfer_review": True,
                "review_reopened_without_duplicate": True,
                "consent_saved": True,
                "real_transfer_observed": True,
                "responsive": True,
                "page_errors": errors,
            },
            indent=2,
        )
        + "\n"
    )
    browser.close()
