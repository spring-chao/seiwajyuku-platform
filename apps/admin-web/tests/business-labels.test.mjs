import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { computed, reactive, ref } from "vue";
import * as labels from "../src/utils/business-labels.mjs";

test("only class meetings show morning/afternoon/konpa; other activities keep one Chinese session", () => {
  for (const type of ["other", "STAFF_TRAINING", "course", "GROUP_MEETING"]) {
    assert.equal(labels.activitySessionLabel(type, "MORNING"), "整场");
    assert.equal(
      labels.activityDisplayTitle("新学员签到表 · 上午", type, "MORNING"),
      "新学员签到表"
    );
  }
  assert.equal(labels.activitySessionLabel("class_meeting", "MORNING"), "上午");
  assert.equal(
    labels.activityDisplayTitle(
      "圆融五班班级学习会 · 上午",
      "CLASS_MEETING",
      "MORNING"
    ),
    "圆融五班班级学习会 · 上午"
  );
  assert.equal(
    labels.activitySessionLabel("CLASS_MEETING", "AFTERNOON"),
    "下午"
  );
  assert.equal(
    labels.activitySessionLabel("CLASS_MEETING", "KONPA"),
    "晚上空巴"
  );
});

test("business labels accept engine and platform casing and never expose unknown codes", () => {
  assert.equal(
    labels.activityTypeLabel("STAFF_TRAINING"),
    "班主任辅导员培训会"
  );
  assert.equal(labels.taskTypeLabel("ENTERPRISE_VISIT"), "企业走访");
  assert.equal(labels.taskTypeLabel("NEW_INTERNAL_CODE"), "其他学习任务");
  assert.equal(labels.activityTypeLabel("NEW_INTERNAL_CODE"), "其他活动");
  assert.equal(labels.volunteerStatusLabel("SUSPENDED"), "已暂停");
  assert.equal(labels.organizationTypeLabel("REGIONAL_CENTER"), "分中心");
});

test("registration denominators never fall back to the count of synchronized signed rows", () => {
  assert.equal(
    labels.registrationCount({ record_count: 14, registration_count: 20 }),
    20
  );
  assert.equal(
    labels.registrationCount({ record_count: 14, registration_count: null }),
    null
  );
  assert.equal(labels.registrationCount({ record_count: 14 }), null);
  assert.equal(labels.registrationCount({ registration_count: 0 }), 0);
});

function volunteerHarness(write) {
  const source = readFileSync(
    new URL(
      "../src/views/seiwajyuku/volunteer-management.vue",
      import.meta.url
    ),
    "utf8"
  );
  const script = source.match(
    /<script setup lang="ts">([\s\S]*?)<\/script>/
  )[1];
  const output = ts
    .transpileModule(script, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022
      }
    })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, "")
    .replace(/export \{\};?/, "");
  const calls = [];
  const context = vm.createContext({
    ...labels,
    computed,
    reactive,
    ref,
    watch() {},
    onMounted() {},
    defineOptions() {},
    ElMessage: { success() {}, warning() {} },
    changeVolunteerAppointmentStatus: async (...args) => {
      calls.push(args);
      await write?.();
    },
    getVolunteerAppointments: async () => ({ data: [] }),
    getVolunteerServiceUnits: async () => ({ data: [] }),
    getVolunteerRecommendationRules: async () => ({ data: [] })
  });
  vm.runInContext(
    output +
      "\nglobalThis.api={changeStatus,submitStatus,statusForm,statusDialogVisible,statusSaving};",
    context
  );
  return { ...context.api, calls };
}

test("Chinese status dialog opens without a write and retains required explanation and internal API values", async () => {
  const h = volunteerHarness();
  h.changeStatus({ id: 7, status: "ACTIVE", member_name: "合成学长" });
  assert.equal(h.calls.length, 0);
  assert.equal(h.statusDialogVisible.value, true);
  assert.equal(h.statusForm.status, "SUSPENDED");
  await h.submitStatus();
  assert.equal(h.calls.length, 0);
  h.statusForm.reason = "本次服务已暂停";
  await h.submitStatus();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], 7);
  assert.equal(h.calls[0][1].status, "SUSPENDED");
  assert.equal(h.statusDialogVisible.value, false);
});

test("gender selector offers known choices without rewriting a historical unspecified value", () => {
  const source = readFileSync(
    new URL("../src/views/seiwajyuku/members.vue", import.meta.url),
    "utf8"
  );
  const block = source.match(
    /const selectedGender = computed\(\{[\s\S]*?\n\}\);/
  )[0];
  const output = ts.transpileModule(block, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const form = reactive({ gender: "UNSPECIFIED" });
  const context = vm.createContext({ form, computed });
  vm.runInContext(output + "\nglobalThis.selection = selectedGender;", context);
  assert.equal(context.selection.value, "");
  assert.equal(form.gender, "UNSPECIFIED");
  context.selection.value = "MALE";
  assert.equal(form.gender, "MALE");
  context.selection.value = "";
  assert.equal(form.gender, "");
});
