import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compiled = ts.transpileModule(readFileSync(new URL("../src/utils/member-roster-result.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
}).outputText;
const { rosterReceiptMessage, rosterImportFailure, applyWithRosterReceipt, rosterFieldLabel } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("repeat upload without historical statistics never invents zero updates", () => {
  const text = rosterReceiptMessage({ status: "ALREADY_APPLIED", batch_id: 1 });
  assert.match(text, /此前已成功导入/);
  assert.match(text, /未提供/);
  assert.doesNotMatch(text, /更新 0/);
});
test("successful import reports actual counts and meaningful field labels", () => {
  assert.match(rosterReceiptMessage({ status: "APPLIED", created: 2, updated: 3, relations: 4, skipped_manual_review: 1 }), /更新 3 人/);
  assert.equal(rosterFieldLabel("birthday"), "生日");
  assert.equal(rosterFieldLabel("annual_sales"), "销售收入（加密）");
});
test("timeouts and server errors are unconfirmed, validation and permission rejection failed", () => {
  for (const error of [{ code: "ETIMEDOUT" }, { response: { status: 500 } }, { response: { status: 504 } }])
    assert.equal(rosterImportFailure(error).kind, "unknown");
  for (const status of [400, 401, 403, 422])
    assert.equal(rosterImportFailure({ response: { status } }).kind, "failed");
});
test("committed receipt survives refresh failure, without executing apply again", async () => {
  const order = [];
  let saved;
  const receipt = { status: "APPLIED", created: 1 };
  const failed = await applyWithRosterReceipt(async () => { order.push("apply"); return receipt; }, r => { saved = r; order.push("receipt"); }, async () => { order.push("refresh"); throw Error("offline"); });
  assert.equal(saved, receipt);
  assert.equal(failed, true);
  assert.deepEqual(order, ["apply", "receipt", "refresh"]);
});
test("failed apply never saves a success receipt or refreshes", async () => {
  await assert.rejects(applyWithRosterReceipt(async () => { throw Error("rejected"); }, () => assert.fail("saved"), async () => assert.fail("refresh")), /rejected/);
});
