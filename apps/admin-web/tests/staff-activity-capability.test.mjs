import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { computed, reactive, ref } from "vue";

function editor() {
  const source = readFileSync(new URL("../src/views/seiwajyuku/staff-management.vue", import.meta.url), "utf8");
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    .replace(/import[\s\S]*?from\s+["'][^"']+["'];/g, "");
  const context = { computed, reactive, ref, defineOptions() {}, onMounted() {} };
  vm.runInNewContext(ts.transpileModule(script + `
    globalThis.editor = {catalog, currentRecord, form, resetForm, capabilityRoles,
      canEditCapabilityRole, authorizationReasonRequired, validateForm,
      payloadFromForm, removeCreditGrant};
  `, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText, context);
  const state = context.editor;
  state.catalog.value = {
    roles: [
      { role_key: "attendance_operator", role_name: "活动签到运营" },
      { role_key: "credit_settlement_approver", role_name: "学分审批" },
      { role_key: "employee_learning_management", role_name: "岗位权限" }
    ],
    positions: [{ position_key: "learning", role_key: "employee_learning_management" }],
    institutions: [{ id: "synthetic-institution", scope_root_org_unit_id: "synthetic-center", scope_available: true }],
    org_units: [{ id: "synthetic-center", name: "合成负责分中心", parent_id: null }]
  };
  const record = {
    id: 91, name: "合成专员", login_account: "synthetic-staff", phone_masked: "138****0000",
    gender: "MALE", institution_id: "synthetic-institution", is_active: true,
    positions: [{ position_key: "learning" }],
    scopes: [{ org_unit_id: "synthetic-center", scope_type: "SUBTREE" }],
    authorization_grants: [
      { role_key: "employee_learning_management", org_unit_id: "synthetic-center", scope_type: "SUBTREE" },
      { role_key: "credit_settlement_poster", org_unit_id: "synthetic-center", scope_type: "UNIT" }
    ]
  };
  state.currentRecord.value = record;
  state.resetForm(record);
  return { state, record };
}

test("staff editor offers activity operations and requires a reason without replacing position or restricted credits", () => {
  const { state } = editor();
  assert.deepEqual(Array.from(state.capabilityRoles.value, r => r.role_key), ["attendance_operator", "credit_settlement_approver"]);
  state.form.credit_grants.push({ role_key: "attendance_operator", org_unit_id: "synthetic-center", scope_type: "SUBTREE" });
  assert.equal(state.authorizationReasonRequired.value, true);
  assert.match(state.validateForm(), /授权原因/);
  state.form.authorization_reason = "开通现有负责范围内活动管理";
  assert.equal(state.validateForm(), "");
  const grants = JSON.parse(JSON.stringify(state.payloadFromForm().grants));
  assert.deepEqual(grants.map(g => g.role_key).sort(), ["attendance_operator", "credit_settlement_poster", "employee_learning_management"]);
  assert.ok(grants.every(g => g.org_unit_id === "synthetic-center"));
  const locked = state.form.credit_grants.findIndex(g => g.role_key === "credit_settlement_poster");
  assert.equal(state.canEditCapabilityRole("credit_settlement_poster"), false);
  state.removeCreditGrant(locked);
  assert.ok(state.form.credit_grants.some(g => g.role_key === "credit_settlement_poster"));
});

test("existing activity capability loads once, ordinary edits preserve it, explicit removal is audited as a permission change", () => {
  const { state, record } = editor();
  record.authorization_grants.push({ role_key: "attendance_operator", org_unit_id: "synthetic-center", scope_type: "SUBTREE" });
  state.resetForm(record);
  assert.equal(state.form.credit_grants.filter(g => g.role_key === "attendance_operator").length, 1);
  state.form.department_name = "合成运营部门";
  assert.equal(state.payloadFromForm().grants, undefined);
  state.removeCreditGrant(state.form.credit_grants.findIndex(g => g.role_key === "attendance_operator"));
  assert.equal(state.authorizationReasonRequired.value, true);
  assert.match(state.validateForm(), /授权原因/);
  assert.ok(state.payloadFromForm().grants.every(g => g.role_key !== "attendance_operator"));
  assert.ok(state.payloadFromForm().grants.some(g => g.role_key === "credit_settlement_poster"));
});
