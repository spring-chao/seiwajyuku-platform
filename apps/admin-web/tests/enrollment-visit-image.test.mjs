import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
const compiled = ts.transpileModule(
  readFileSync(
    new URL("../src/utils/enrollmentVisitImage.ts", import.meta.url),
    "utf8"
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022
    }
  }
).outputText;
const {
  buildVisitSections,
  wrapVisitText,
  layoutVisitSections,
  renderVisitImages
} = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("complete visit fields retain unmasked values, zero headcount and labels", () => {
  const sections = buildVisitSections({
    application: {
      name: "合成申请人",
      phone: "合成完整联系方式",
      employee_count: 0,
      annual_sales: "合成销售额",
      profit_margin: "GE_10_PERCENT",
      invoice_type: "SPECIAL",
      invoice_tax_id: "SYNTHETIC-TAX-ID",
      invoice_account: "SYNTHETIC-ACCOUNT",
      invoice_info: JSON.stringify({
        invoice_type: "NORMAL",
        invoice_tax_id: "SYNTHETIC-LEGACY"
      }),
      created_at: "2026-10-09T09:00:00+00:00",
      referrer_org_unit_name: "合成分中心",
      goal_years: "3",
      revenue_growth_target: "UNSET",
      profit_growth_target: "1.5",
      notes: "多行\n补充",
      books_read: "合成书目",
      financial_fields_visible: true,
      invoice_fields_visible: true
    }
  });
  const fields = sections.flatMap(section => section.fields);
  assert.equal(
    fields.find(field => field.label === "手机号").value,
    "合成完整联系方式"
  );
  assert.equal(fields.find(field => field.label === "员工人数").value, "0");
  assert.equal(
    fields.find(field => field.label === "历史发票类型").value,
    "普票"
  );
  assert.equal(
    fields.find(field => field.label === "历史发票税号").value,
    "SYNTHETIC-LEGACY"
  );
  assert.match(
    fields.find(field => field.label === "提交时间").value,
    /17:00:00/
  );
  assert.equal(
    fields.find(field => field.label === "银行账号").value,
    "SYNTHETIC-ACCOUNT"
  );
  assert.equal(
    fields.find(field => field.label === "业绩提升目标").value,
    "暂不设定"
  );
  assert.equal(
    fields.find(field => field.label === "计划学习年限").value,
    "3年"
  );
  assert.equal(
    fields.find(field => field.label === "补充内容").value,
    "多行\n补充"
  );
});

test("long Chinese text, Unicode and explicit newlines are not truncated", () => {
  assert.deepEqual(
    wrapVisitText("你好世界\n😀ABC", 2, value => Array.from(value).length),
    ["你好", "世界", "😀A", "BC"]
  );
  const value = "长".repeat(16000);
  const pages = layoutVisitSections(
    [{ title: "补充", fields: [{ label: "备注", value, full: true }] }],
    value => value.length * 22,
    500
  );
  assert.ok(pages.length > 1);
  const all = pages.flat().filter(item => item.kind === "field");
  assert.equal(all.flatMap(item => item.lines).join(""), value);
  assert.ok(all.every(item => item.y + 60 + item.lines.length * 30 <= 400));
});

test("incomplete authorized data cannot silently produce a masked or empty export", async () => {
  await assert.rejects(
    renderVisitImages({
      application: {
        phone: "",
        financial_fields_visible: true,
        invoice_fields_visible: true
      }
    }),
    /完整资料未加载/
  );
});
