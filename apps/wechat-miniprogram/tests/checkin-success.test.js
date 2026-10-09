const test = require("node:test");
const assert = require("node:assert/strict");
const { successCard } = require("../utils/checkin-success");

const event = { event_id: "meeting", event_date: "2026-10-10", activity_type_name: "班级学习会", name: "合成学习会" };

test("success retains legacy welcome, seating, activity and organisation display", () => {
  const card = successCard({ receipt: { name: "合成学员", event, group_num: 3, dinner_table_num: 8,
    group_type: "班级", group_value: "合成一班", company: "合成企业" } });
  assert.deepEqual(card, { welcome: "欢迎 合成学员学长！",
    seats: ["大会现场：第 3 组", "空巴晚宴：第 8 桌"],
    lines: ["活动：2026-10-10 · 班级学习会 · 合成学习会", "班级：合成一班", "公司：合成企业"] });
});

test("legacy hidden seating stays hidden and organisation priority remains unchanged", () => {
  const card = successCard({ receipt: { name: "合成学员", group_num: 3, dinner_table_num: 8,
    show_group: "false", show_dinner_table: "false", class_name: "合成一班", center: "合成分中心", group_name: "合成小组" } });
  assert.deepEqual(card.seats, []);
  assert.deepEqual(card.lines, ["班级：合成一班"]);
  assert.deepEqual(successCard({ data: { name: "合成来宾" } }).lines, ["签到成功"]);
});

test("cross class, team and repeated checkin retain legacy disclosures and China time", () => {
  assert.deepEqual(successCard({ receipt: { name: "外班学员", attendance_role: "CROSS_CLASS_MEMBER", home_class_name: "原班级" } }).lines,
    ["本次作为外班学长参加", "原班级：原班级"]);
  const card = successCard({ status: "ALREADY_CHECKED_IN", checked_count: 2,
    receipt: { name: "实际参加人", attendance_role: "EVENT_TEAM_MEMBER", registered_name: "报名联系人",
      multi_total: 4, multi_checked: 3, checked_at: "2026-10-10T00:00:00Z" } });
  assert.equal(card.welcome, "欢迎 实际参加人学长！");
  assert.deepEqual(card.lines.slice(0, 3), ["本次使用团队报名名额签到", "报名登记姓名：报名联系人", "多人报名：已签到 3/4 人（本次 2 人）"]);
  assert.equal(card.lines[3], `（已签到 · ${new Date("2026-10-10T00:00:00Z").toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}）`);
  assert.ok(!card.lines[3].includes("T00:00"));
});

test("duplicate guest uses saved attendee name, and older contexts retain verified identity", () => {
  assert.equal(successCard({ status: "ALREADY_CHECKED_IN", receipt: { name: "原签到姓名" } }, { name: "再次输入姓名" }).welcome, "欢迎 原签到姓名学长！");
  assert.equal(successCard({ receipt: {} }, { member: { name: "已绑定本人" }, registration: { group_num: 5 } }).seats[0], "大会现场：第 5 组");
});
