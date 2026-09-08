import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const staffPage = readFileSync(
  new URL("../src/views/seiwajyuku/staff-management.vue", import.meta.url),
  "utf8"
);
const createDrawer = staffPage.slice(
  staffPage.indexOf("<el-drawer"),
  staffPage.indexOf("</el-drawer>")
);

test("ordinary staff creation exposes only business fields", () => {
  for (const label of [
    "姓名",
    "性别",
    "手机号",
    "登录账号",
    "所属机构",
    "岗位",
    "负责范围"
  ]) {
    assert.match(createDrawer, new RegExp(`label="${label}"`));
  }
  assert.match(createDrawer, /label="男" value="MALE"/);
  assert.match(createDrawer, /label="女" value="FEMALE"/);
  assert.doesNotMatch(createDrawer, /UNSPECIFIED|未说明|label="角色"/);
  assert.doesNotMatch(createDrawer, /授权依据|敏感权限扩大|权限预览|二次确认/);
  assert.match(createDrawer, /更多信息（可选）/);
  assert.match(createDrawer, /v-if="isEditing"/);
  assert.match(createDrawer, />保存<\/el-button>/);
});

test("password and network errors use the simplified business rules", () => {
  assert.match(staffPage, /const PASSWORD_MIN_LENGTH = 6/);
  assert.doesNotMatch(staffPage, /至少 9 位|至少 10 位/);
  assert.match(staffPage, /暂时无法连接服务，请检查网络后重试/);
  assert.doesNotMatch(staffPage, /return error\?\.message/);
  assert.doesNotMatch(staffPage, /previewStaffChange|getAuthorizationMigrationPreview/);
});
