import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { computed, ref, watch, nextTick } from "vue";

function harness(api = {}) {
  const source = readFileSync(new URL("../src/views/seiwajyuku/components/BulkGroupReassignment.vue", import.meta.url), "utf8");
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const output = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, "").replace(/export \{\};?/, "");
  const calls = [];
  const context = vm.createContext({
    computed, ref, watch,
    defineProps: () => ({ classes: [], initialClass: "class-1" }), defineEmits: () => () => {},
    ElMessage: { warning() {}, success() {}, error() {} }, studyMeetingErrorMessage: () => "请求失败",
    getGroupReassignmentRoster: api.roster || (async () => ({ data: { members: [{ id: 1, group_org_unit_id: "old" }], groups: [] } })),
    previewGroupReassignment: async body => { calls.push({ kind: "preview", body }); return api.preview ? api.preview(body) : { data: { preview_fingerprint: "fingerprint", changed_count: 1, rows: [] } }; },
    applyGroupReassignment: async body => { calls.push({ kind: "apply", body }); return { data: { changed_count: 1 } }; }
  });
  vm.runInContext(output + "\nglobalThis.state = { visible, classId, roster, targets, selected, unifiedTarget, reason, preview, assignments }; globalThis.actions = { open, loadRoster, assignSelected, runPreview, save };", context);
  return { state: context.state, actions: context.actions, calls };
}

test("an old roster response cannot replace a newly selected class", async () => {
  let resolveOld;
  const old = new Promise(resolve => { resolveOld = resolve; });
  const h = harness({ roster: id => id === "class-1" ? old : Promise.resolve({ data: { class_name: "new", members: [], groups: [] } }) });
  h.state.visible.value = true;
  h.state.classId.value = "class-1";
  const first = h.actions.loadRoster();
  h.state.classId.value = "class-2";
  await h.actions.loadRoster();
  resolveOld({ data: { class_name: "old", members: [], groups: [] } });
  await first;
  assert.equal(h.state.roster.value.class_name, "new");
});

test("only changed members are previewed; editing an arrangement invalidates permission to save", async () => {
  const h = harness();
  h.state.visible.value = true;
  h.state.classId.value = "class-1";
  await h.actions.loadRoster();
  h.state.targets.value[1] = "new";
  await nextTick();
  await h.actions.runPreview();
  assert.equal(h.calls[0].body.assignments[0].group_org_unit_id, "new");
  h.state.targets.value[1] = "another";
  await nextTick();
  assert.equal(h.state.preview.value, undefined);
  await h.actions.save();
  assert.equal(h.calls.filter(call => call.kind === "apply").length, 0);
});

test("a preview and a reason are required before dispatching an atomic reassignment", async () => {
  const h = harness();
  h.state.visible.value = true;
  h.state.classId.value = "class-1";
  await h.actions.loadRoster();
  h.state.targets.value[1] = "new";
  await nextTick();
  await h.actions.runPreview();
  await h.actions.save();
  assert.equal(h.calls.filter(call => call.kind === "apply").length, 0);
  h.state.reason.value = "重新开班重组";
  await h.actions.save();
  assert.equal(h.calls.filter(call => call.kind === "apply").length, 1);
  assert.equal(h.calls[1].body.preview_fingerprint, "fingerprint");
  assert.equal(h.state.visible.value, false);
});
