import test from "node:test";
import assert from "node:assert/strict";
import { approvalState, permissions, callIsActive } from "../model.js";

test("an exact transfer is not approvable after expiry or after call selection changes", () => {
  const call = { id: "c1", status: "answered", version: 2 };
  const proposal = {
    id: "t1",
    call_id: "c1",
    call_version: 2,
    target: "operator",
    target_uri: "sip:operator@127.0.0.1:18160",
    digest: "abc",
    state: "proposed",
    expires_at: 200,
  };
  assert.equal(approvalState(proposal, call, 100).allowed, true);
  assert.equal(
    approvalState(proposal, { ...call, id: "c2" }, 100).allowed,
    false,
  );
  assert.equal(approvalState(proposal, call, 201).allowed, false);
  assert.equal(
    approvalState({ ...proposal, state: "outcome_unknown" }, call, 100).allowed,
    false,
  );
  assert.equal(
    approvalState({ ...proposal, digest: "" }, call, 100).allowed,
    false,
  );
  assert.equal(
    approvalState(proposal, { ...call, version: 3 }, 100).allowed,
    false,
  );
  assert.equal(
    approvalState(proposal, { ...call, status: "transferred" }, 100).allowed,
    false,
  );
});
test("viewer controls and service admission are limited by session role", () => {
  assert.deepEqual(permissions("viewer"), { write: false, admin: false });
  assert.deepEqual(permissions("operator"), { write: true, admin: false });
  assert.deepEqual(permissions("admin"), { write: true, admin: true });
  assert.deepEqual(permissions("unknown"), { write: false, admin: false });
});
test("terminal calls cannot be transferred or hung up again", () => {
  for (const status of [
    "ended",
    "completed",
    "failed",
    "rejected",
    "transferred",
  ])
    assert.equal(callIsActive({ status }), false);
  assert.equal(callIsActive({ status: "connected" }), true);
});

test("a queued proposal can be reopened until expiry, then permits a fresh request", async () => {
  const { pendingProposal } = await import("../model.js");
  assert.equal(typeof pendingProposal, "function");
  assert.equal(
    pendingProposal([{ id: "p1", state: "proposed", expires_at: 20 }], 10)?.id,
    "p1",
  );
  assert.equal(
    pendingProposal([{ id: "p1", state: "proposed", expires_at: 20 }], 21),
    undefined,
  );
});

test("static build emits a complete HTML asset graph without a development server", async () => {
  const { readFile } = await import("node:fs/promises");
  const { execFileSync } = await import("node:child_process");
  execFileSync(process.execPath, [
    new URL("../build.mjs", import.meta.url).pathname,
  ]);
  const html = await readFile(
    new URL("../dist/index.html", import.meta.url),
    "utf8",
  );
  const script = html.match(/src="(\/assets\/[^\"]+\.js)"/);
  const style = html.match(/href="(\/assets\/[^\"]+\.css)"/);
  assert.ok(
    script && style,
    "The served HTML must include the generated script and stylesheet",
  );
  assert.ok(
    (await readFile(new URL("../dist" + script[1], import.meta.url))).length >
      0,
  );
  assert.ok(
    (await readFile(new URL("../dist" + style[1], import.meta.url))).length > 0,
  );
});
