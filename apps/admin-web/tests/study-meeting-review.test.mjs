import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { ref, computed, reactive, watch, nextTick } from "vue";

function harness(confirm) {
  const source = readFileSync(new URL("../src/views/seiwajyuku/study-meetings.vue", import.meta.url), "utf8");
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    .replaceAll("import.meta.env.DEV", "false").replaceAll("import.meta.env.VITE_API_BASE_URL", '""');
  const compiled = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, "");
  const calls = [];
  const context = vm.createContext({ ref, computed, reactive, watch,
    defineOptions() {}, onMounted() {}, onBeforeUnmount() {},
    useUserStoreHook: () => ({ permissions: [] }),
    ElMessage: { success: value => calls.push({ message: value }), error: value => calls.push({ error: value }), warning: value => calls.push({ warning: value }) },
    studyMeetingErrorMessage: () => "失败",
    confirmStudyMeetingReview: async (id, fingerprint) => { calls.push({ id, fingerprint }); return confirm(id); },
    getStudyMeetingRecords: async () => ({ data: { records: [{ id: 1, review_status: "CONFIRMED" }] } })
  });
  vm.runInContext(compiled + "\nglobalThis.state = { detail, detailVisible, records, reviewing, editingAttendees, editingCourses }; globalThis.confirmReviewAction = confirmReview;", context);
  context.state.detail.value = { id: 1, review_fingerprint: "fresh", review_status: "UNCONFIRMED" };
  context.state.detailVisible.value = true;
  return { state: context.state, run: context.confirmReviewAction, calls };
}

test("confirmation sends the displayed fingerprint, prevents double submits and refreshes the list", async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const h = harness(() => pending);
  const first = h.run();
  await h.run();
  assert.equal(h.calls.filter(x => x.id).length, 1);
  assert.equal(h.calls[0].fingerprint, "fresh");
  resolve({ data: { id: 1, review_status: "CONFIRMED" } });
  await first;
  assert.equal(h.state.detail.value.review_status, "CONFIRMED");
  assert.equal(h.state.records.value[0].review_status, "CONFIRMED");
  assert.equal(h.state.reviewing.value, false);
});

test("unsaved edits block confirmation and an old response cannot replace another meeting", async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const h = harness(() => pending);
  h.state.editingAttendees.value = true;
  await h.run();
  assert.equal(h.calls.length, 0);
  h.state.editingAttendees.value = false;
  const first = h.run();
  h.state.detail.value = { id: 2, review_status: "UNCONFIRMED" };
  resolve({ data: { id: 1, review_status: "CONFIRMED" } });
  await first;
  assert.equal(h.state.detail.value.id, 2);
});

test("edited detail immediately updates the corresponding list verification state", async () => {
  const h = harness(async () => ({ data: {} }));
  h.state.records.value = [{ id: 1, review_status: "CONFIRMED" }, { id: 2, review_status: "CONFIRMED" }];
  h.state.detail.value = { id: 1, review_status: "UNCONFIRMED", home_group_attendance_rate: 50 };
  await nextTick();
  assert.equal(h.state.records.value[0].review_status, "UNCONFIRMED");
  assert.equal(h.state.records.value[0].home_group_attendance_rate, 50);
  assert.equal(h.state.records.value[1].review_status, "CONFIRMED");
});


test("automatic settlement reports posted success and retains explicit retry warnings", async () => {
  const posted = harness(async () => ({ data: { id: 1, review_status: "CONFIRMED", credit_settlement: { status: "POSTED", posted_points: "8.00" } } }));
  await posted.run();
  assert.ok(posted.calls.some(x => x.message === "本场已核实，学分已自动结算入账"));
  assert.ok(!posted.calls.some(x => x.error || x.warning));
  const partial = harness(async () => ({ data: { id: 1, review_status: "CONFIRMED", credit_settlement: { status: "PARTIAL_FAILED", message: "本场已核实，学分尚未全部入账" } } }));
  await partial.run();
  assert.ok(partial.calls.some(x => x.warning === "本场已核实，学分尚未全部入账"));
  assert.ok(!partial.calls.some(x => x.message));
});
