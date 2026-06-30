"use strict";

const assert = require("node:assert/strict");
const { enforceSuccessfulGate, serviceEvidenceIssues } = require("./gui-simulator-demo.cjs");

for (const signature of [
  "panic: worker failed",
  "FATAL database unavailable",
  "out of memory",
  "OOMKilled",
  "Unhandled Exception",
  "stack trace follows",
]) {
  const issues = serviceEvidenceIssues(signature, [], {});
  assert.equal(issues.length, 1, `fatal signature was not gating: ${signature}`);
  assert.throws(() => enforceSuccessfulGate([{ name: "browser", passed: true }], issues), /service health scan found/);
}

const unhealthy = serviceEvidenceIssues("", [{ ID: "one", Service: "market", State: "running", Health: "unhealthy" }], { one: "0" });
assert.match(unhealthy[0], /health=unhealthy/);

const stopped = serviceEvidenceIssues("", [{ ID: "two", Service: "gateway", State: "exited", Health: "" }], { two: "0" });
assert.match(stopped[0], /state=exited/);

const restarted = serviceEvidenceIssues("", [{ ID: "three", Service: "worker", State: "running", Health: "healthy" }], { three: "2" });
assert.match(restarted[0], /restarted 2 times/);

assert.throws(
  () => enforceSuccessfulGate([{ name: "browser assertion", passed: false }], []),
  /GUI QA assertions failed: browser assertion/,
);
assert.doesNotThrow(() => enforceSuccessfulGate([{ name: "browser assertion", passed: true }], []));

process.stdout.write("GUI runner failure contracts passed\n");
