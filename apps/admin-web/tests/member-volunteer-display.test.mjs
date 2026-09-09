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

test("member edit page supports compact multi-post volunteer maintenance", () => {
  const memberPage = readFileSync(
    new URL("../src/views/seiwajyuku/members.vue", import.meta.url),
    "utf8"
  );
  assert.match(memberPage, /label="志工任职"/);
  assert.match(memberPage, /label="班组委" value="CLASS_TEAM"/);
  assert.match(memberPage, /label="条线管理" value="LINE"/);
  assert.match(memberPage, /当前任职/);
  assert.match(memberPage, /服务班级/);
  assert.match(memberPage, /服务小组/);
  assert.match(memberPage, /添加任职/);
  assert.match(memberPage, /确认结束“\$\{appointmentLabel\}”任职吗/);
  assert.match(memberPage, /content="结束任职"/);
  assert.match(memberPage, /:closable="canManage"/);
  assert.match(memberPage, /createVolunteerAppointment/);
  assert.match(memberPage, /catalogResponse\.data\.positions \|\| \[\]/);
  assert.match(
    memberPage,
    /service_target_org_unit_id:\s*volunteerEditorForm\.service_target_org_unit_id/
  );
  assert.doesNotMatch(memberPage, /volunteerTypeUnits/);
  assert.match(memberPage, /changeVolunteerAppointmentStatus/);
  assert.match(
    memberPage,
    /const volunteerHistoryExpanded = ref<string\[\]>\(\[\]\)/
  );
  assert.doesNotMatch(memberPage, /志工任职已改由“志工任职管理”统一维护/);
  assert.doesNotMatch(memberPage, /主要岗位/);
});

test("member edit page defaults to the learner class and group but keeps both editable", () => {
  const memberPage = readFileSync(
    new URL("../src/views/seiwajyuku/members.vue", import.meta.url),
    "utf8"
  );
  assert.match(
    memberPage,
    /class_org_unit_id: form\.class_org_unit_id \|\| ""/
  );
  assert.match(
    memberPage,
    /scopeLevel === "GROUP"[\s\S]*form\.group_org_unit_id/
  );
  assert.match(
    memberPage,
    /org\.parent_id === volunteerEditorForm\.class_org_unit_id/
  );
  assert.match(memberPage, /@change="onVolunteerServiceClassChange"/);
  assert.doesNotMatch(memberPage, /必须与本人班级一致/);
  assert.doesNotMatch(memberPage, /必须与本人小组一致/);
});
