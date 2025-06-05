export function approvalState(proposal, call, now = Date.now() / 1000) {
  if (!proposal || !call || proposal.call_id !== call.id)
    return {
      allowed: false,
      reason: "Select this call to review its transfer.",
    };
  if (proposal.state !== "proposed")
    return {
      allowed: false,
      reason:
        "This proposal has already been handled. Wait for observed results.",
    };
  if (
    !proposal.digest ||
    !proposal.target_uri ||
    !Number.isFinite(proposal.expires_at) ||
    proposal.expires_at <= now
  )
    return {
      allowed: false,
      reason: "This proposal is incomplete or expired. Request a fresh review.",
    };
  if (call.status !== "answered" || call.version !== proposal.call_version)
    return {
      allowed: false,
      reason:
        "The call changed after this proposal. Refresh before continuing.",
    };
  return { allowed: true, reason: "" };
}
export function permissions(role) {
  return {
    write: role === "admin" || role === "operator",
    admin: role === "admin",
  };
}
export function callIsActive(call) {
  return (
    !!call &&
    !["ended", "completed", "failed", "rejected", "transferred"].includes(
      call.status,
    )
  );
}
export function readable(value) {
  return String(value || "unknown").replaceAll("_", " ");
}
export function when(value) {
  if (!value) return "Not observed";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.valueOf())
    ? "Not observed"
    : date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}
export function pendingProposal(transfers = [], now = Date.now() / 1000) {
  return transfers.find(
    (transfer) => transfer.state === "proposed" && transfer.expires_at > now,
  );
}
