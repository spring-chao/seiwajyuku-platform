import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import dayjs from "dayjs";
import { computed, ref } from "vue";

function harness(confirm) {
  const source = readFileSync(new URL("../src/views/seiwajyuku/activities.vue", import.meta.url), "utf8");
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const output = ts.transpileModule(script, { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022
  }}).outputText.replace(/^import [\s\S]*?;\r?\n/gm, "").replace(/export \{\};?/, "");
  const calls = [], messages = [];
  const context = vm.createContext({ computed, ref, dayjs, defineOptions() {}, onMounted() {},
    useUserStoreHook: () => ({permissions:["attendance:manage", "attendance:view"]}),
    ElMessageBox: { confirm }, ElMessage: { success: text => messages.push(text) },
    deleteAttendanceEventGroup: async (...args) => calls.push(args),
    getAttendanceActivityRows: async () => ({data:[]}),
    getAttendanceSyncStatus: async () => ({data:null}),
    getAttendanceReconciliationSummary: async () => ({data:{items:[]}})
  });
  vm.runInContext(output + "\nglobalThis.testApi={rows,deleteActivity,deletingGroupId,canDeleteActivity};", context);
  return { ...context.testApi, calls, messages };
}

test("cancelling the deletion confirmation sends no write", async () => {
  const ui = harness(async () => {throw Error("cancel");});
  await ui.deleteActivity({id:9,title:"合成测试活动"});
  assert.equal(ui.calls.length,0);
  assert.equal(ui.deletingGroupId.value,null);
});

test("deletion confirms the whole activity group and prevents duplicate requests while dialog is open", async () => {
  let complete, message;
  const ui=harness(text=>{message=text;return new Promise(resolve=>{complete=resolve;});});
  ui.rows.value=[{id:9,session_id:1},{id:9,session_id:2},{id:10,session_id:3}];
  const first=ui.deleteActivity({id:9,title:"合成测试活动"});
  await ui.deleteActivity({id:9,title:"合成测试活动"});
  assert.equal(ui.calls.length,0);
  assert.match(message,/2 个场次/);
  complete(true);
  await first;
  assert.equal(ui.calls.length,1);
  assert.equal(ui.calls[0][0],9);
  assert.equal(ui.calls[0][1],"合成测试活动");
  assert.equal(ui.rows.value.length,0);
  assert.equal(ui.deletingGroupId.value,null);
  assert.equal(ui.messages[0],"活动已删除");
});
