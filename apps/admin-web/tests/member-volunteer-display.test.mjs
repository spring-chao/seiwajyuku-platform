import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/utils/memberVolunteerDisplay.ts", import.meta.url),
  "utf8"
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const display = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const catalog = [
  {
    position_key: "volunteer_group_counselor",
    position_name: "辅导员",
    scope_level: "GROUP",
    is_active: true,
    sort_order: 10,
    capabilities: [],
    capability_names: []
  }
];

test("current position fallback uses the Chinese profile name, never the key", () => {
  const options = display.buildCurrentVolunteerPositionOptions(
    [],
    "volunteer_group_counselor",
    "辅导员",
    "GROUP"
  );
  assert.equal(options[0].position_name, "辅导员");
  assert.equal(
    display.volunteerPositionLabel("volunteer_group_counselor", null, options),
    "辅导员"
  );
  assert.doesNotMatch(options[0].position_name, /volunteer_group_counselor/);
  assert.equal(
    display.volunteerPositionLabel(
      "volunteer_group_counselor",
      "volunteer_group_counselor",
      catalog
    ),
    "辅导员"
  );
});

test("catalog entries remain stable and history labels stay user-facing", () => {
  assert.equal(
    display.buildCurrentVolunteerPositionOptions(
      catalog,
      "volunteer_group_counselor",
      "辅导员",
      "GROUP"
    ).length,
    1
  );
  assert.equal(display.volunteerAppointmentStatusLabel("ACTIVE"), "服务中");
  assert.equal(
    display.volunteerAppointmentStatusLabel("UNKNOWN_INTERNAL"),
    "待核对"
  );
  assert.equal(
    display.shouldShowLegacyVolunteerHint("辅导员", "辅导员"),
    false
  );
  assert.equal(
    display.shouldShowLegacyVolunteerHint("辅导员", "普通学长"),
    true
  );
});

test("member edit page uses compact current-role UI and read-only history", () => {
  const memberPage = readFileSync(
    new URL("../src/views/seiwajyuku/members.vue", import.meta.url),
    "utf8"
  );
  assert.match(memberPage, /class="current-volunteer-field"/);
  assert.doesNotMatch(memberPage, /label="当前志工岗位" class="full"/);
  assert.ok(
    memberPage.indexOf('label="班级组织"') <
      memberPage.indexOf('label="小组组织"') &&
      memberPage.indexOf('label="小组组织"') <
        memberPage.indexOf('label="当前志工岗位"')
  );
  assert.match(memberPage, /志工服务记录/);
  assert.match(memberPage, /const volunteerHistoryExpanded = ref<string\[\]>\(\[\]\)/);
  assert.doesNotMatch(memberPage, /志工岗位（正式任职）/);
  assert.doesNotMatch(memberPage, /添加正式志工任职/);
  assert.doesNotMatch(memberPage, /结束任职/);
});
