import type { EnrollmentVisitData } from "@/api/enrollment";

export type VisitField = { label: string; value: string; full?: boolean };
export type VisitSection = { title: string; fields: VisitField[] };
const text = (value: unknown) =>
  value == null || value === "" ? "未填写" : String(value);
const option = (value: unknown, labels: Record<string, string>) =>
  labels[String(value)] || text(value);
const field = (label: string, value: unknown, full = false): VisitField => ({
  label,
  value: text(value),
  full
});
const visitTime = (value: unknown) => {
  if (!value) return "未填写";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? text(value)
    : date.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false
      });
};
function historicalInvoiceFields(
  value: string | null | undefined
): VisitField[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      return [field("历史开票资料", value, true)];
    const labels: Record<string, string> = {
      invoice_type: "发票类型",
      invoice_title: "发票抬头",
      invoice_tax_id: "发票税号",
      company_tax_id: "企业税号",
      invoice_registered_address: "注册地址",
      invoice_phone: "注册电话",
      invoice_bank: "开户银行",
      invoice_account: "银行账号"
    };
    return Object.entries(parsed)
      .filter(([, item]) => item != null && item !== "")
      .map(([key, item]) =>
        field(
          `历史${labels[key] || key}`,
          key === "invoice_type"
            ? option(item, {
                NORMAL: "普票",
                SPECIAL: "专票",
                NONE: "无需开票"
              })
            : typeof item === "object"
              ? JSON.stringify(item)
              : item,
          true
        )
      );
  } catch {
    return [field("历史开票资料", value, true)];
  }
}

export function buildVisitSections(data: EnrollmentVisitData): VisitSection[] {
  const a = data.application;
  const sections: VisitSection[] = [
    {
      title: "基本资料",
      fields: [
        field("姓名", a.name),
        field("手机号", a.phone),
        field(
          "性别",
          option(a.gender, { MALE: "男", FEMALE: "女", OTHER: "其他" })
        ),
        field("生日", a.birthday),
        field("政治面貌", a.political_status),
        field("社会任职", a.social_role),
        field("邮箱", a.email),
        field("推荐人", a.referrer),
        field("推荐人所属分中心/指导团", a.referrer_org_unit_name),
        field("申请加入的塾", a.target_shuku_name),
        field("正式归属分中心/指导团", a.org_unit_name),
        field("入塾日期", a.join_date)
      ]
    },
    {
      title: "企业资料",
      fields: [
        field("企业名称", a.company_name, true),
        field("职务", a.position),
        field("行业大类", a.industry_category || a.industry),
        field("其他行业", a.industry_other),
        field("员工人数", a.employee_count),
        field("年销售额（万）", a.annual_sales),
        field(
          "利润率",
          option(a.profit_margin, {
            GE_10_PERCENT: "10%及以上",
            LT_10_PERCENT: "0%～10%以下",
            LOSS: "亏损"
          })
        ),
        field("公司地址", a.company_address, true),
        field("主要产品或服务", a.company_products, true)
      ]
    },
    {
      title: "开票资料",
      fields: [
        field(
          "发票类型",
          option(a.invoice_type, {
            NORMAL: "普票",
            SPECIAL: "专票",
            NONE: "无需开票"
          })
        ),
        field("发票抬头", a.invoice_title),
        field("发票税号", a.invoice_tax_id || a.company_tax_id),
        ...(
          [
            "invoice_registered_address",
            "invoice_phone",
            "invoice_bank",
            "invoice_account"
          ] as const
        )
          .filter(key => a.invoice_type === "SPECIAL" || Boolean(a[key]))
          .map(key =>
            field(
              {
                invoice_registered_address: "注册地址",
                invoice_phone: "注册电话",
                invoice_bank: "开户银行",
                invoice_account: "银行账号"
              }[key],
              a[key]
            )
          ),
        ...historicalInvoiceFields(a.invoice_info)
      ]
    },
    {
      title: "学习期望",
      fields: [
        field("计划学习年限", a.goal_years ? `${a.goal_years}年` : null),
        field(
          "业绩提升目标",
          a.revenue_growth_target === "UNSET"
            ? "暂不设定"
            : a.revenue_growth_target
              ? `${a.revenue_growth_target}倍`
              : null
        ),
        field(
          "利润提升目标",
          a.profit_growth_target === "UNSET"
            ? "暂不设定"
            : a.profit_growth_target
              ? `${a.profit_growth_target}倍`
              : null
        )
      ]
    }
  ];
  const legacy = [
    ["所读稻盛和夫著作", a.books_read],
    ["认同的哲学理念", a.enrollment_reason_philosophy],
    ["期望改变或努力方向", a.enrollment_reason_change],
    ["入塾初心的其他内容", a.enrollment_reason_other],
    ["学习年限目标", a.learning_years_goal],
    ["学习与活动参与目标", a.learning_participation_goal],
    ["公司业绩目标", a.business_goal],
    ["其他目标", a.other_goal]
  ].filter(([, value]) => Boolean(value));
  if (legacy.length)
    sections.push({
      title: "历史学习资料",
      fields: legacy.map(([label, value]) => field(label!, value, true))
    });
  sections.push({
    title: "其他补充",
    fields: [
      field("补充内容", a.notes, true),
      field("审核备注", a.review_note, true)
    ]
  });
  sections.push({
    title: "申请进度",
    fields: [
      field(
        "申请状态",
        option(a.computed_status, {
          PENDING_REVIEW: "待审核",
          PENDING_PAYMENT: "待收款",
          PENDING_CENTER: "待分中心",
          PENDING_ENROLLMENT: "待正式入塾",
          ENROLLED: "已入塾",
          REJECTED: "已驳回",
          CANCELLED: "已取消"
        })
      ),
      field("守则与缴费说明", a.rules_acknowledged ? "已确认" : "未确认"),
      field("提交时间", visitTime(a.created_at)),
      field("审核时间", visitTime(a.reviewed_at)),
      field("收款确认时间", visitTime(a.payment_confirmed_at)),
      field("正式入塾时间", visitTime(a.converted_at))
    ]
  });
  return sections;
}

/** Wrap every character and explicit newline; values are never truncated. */
export function wrapVisitText(
  value: string,
  width: number,
  measure: (text: string) => number
): string[] {
  return value.split(/\r?\n/).flatMap(paragraph => {
    const lines: string[] = [];
    let current = "";
    for (const character of Array.from(paragraph)) {
      if (current && measure(current + character) > width) {
        lines.push(current);
        current = "";
      }
      current += character;
    }
    lines.push(current);
    return lines;
  });
}

export function layoutVisitSections(
  sections: VisitSection[],
  measure: (text: string) => number,
  pageHeight = 12000
) {
  const pages: Array<
    Array<{
      kind: "section" | "field";
      x: number;
      y: number;
      width: number;
      label: string;
      lines: string[];
    }>
  > = [[]];
  let y = 164;
  const reserve = (height: number) => {
    if (y + height > pageHeight - 100) {
      pages.push([]);
      y = 164;
    }
  };
  for (const section of sections) {
    reserve(112);
    pages.at(-1)!.push({
      kind: "section",
      x: 48,
      y,
      width: 904,
      label: section.title,
      lines: []
    });
    y += 58;
    for (let index = 0; index < section.fields.length;) {
      const left = section.fields[index++];
      const right =
        !left.full && !section.fields[index]?.full
          ? section.fields[index++]
          : undefined;
      const width = left.full ? 904 : 440;
      const leftLines = wrapVisitText(left.value, width - 32, measure);
      const rightLines = right ? wrapVisitText(right.value, 408, measure) : [];
      // Extremely long values continue onto the next image instead of clipping.
      let offset = 0;
      while (offset < Math.max(leftLines.length, rightLines.length)) {
        reserve(104);
        const capacity = Math.max(
          1,
          Math.floor((pageHeight - 100 - y - 60) / 30)
        );
        const l = leftLines.slice(offset, offset + capacity),
          r = rightLines.slice(offset, offset + capacity);
        const height = 60 + Math.max(l.length, r.length) * 30;
        if (l.length)
          pages.at(-1)!.push({
            kind: "field",
            x: 48,
            y,
            width,
            label: left.label + (offset ? "（续）" : ""),
            lines: l
          });
        if (r.length)
          pages.at(-1)!.push({
            kind: "field",
            x: 512,
            y,
            width: 440,
            label: right!.label + (offset ? "（续）" : ""),
            lines: r
          });
        y += height;
        offset += capacity;
      }
    }
    y += 20;
  }
  return pages;
}

export async function renderVisitImages(
  data: EnrollmentVisitData
): Promise<Blob[]> {
  if (
    !data.application.phone ||
    !data.application.financial_fields_visible ||
    !data.application.invoice_fields_visible
  ) {
    throw new Error("完整资料未加载，请核对查看权限后重试");
  }
  await document.fonts.ready;
  const measureCanvas = document.createElement("canvas");
  const measureContext = measureCanvas.getContext("2d");
  if (!measureContext) throw new Error("浏览器不支持图片导出");
  const font = '"Microsoft YaHei", "PingFang SC", sans-serif';
  measureContext.font = `22px ${font}`;
  const pages = layoutVisitSections(
    buildVisitSections(data),
    value => measureContext.measureText(value).width
  );
  return Promise.all(
    pages.map((items, index) => {
      const height =
        Math.max(
          400,
          ...items.map(item => item.y + 60 + item.lines.length * 30)
        ) + 104;
      const canvas = document.createElement("canvas");
      canvas.width = 2000;
      canvas.height = height * 2;
      const ctx = canvas.getContext("2d")!;
      ctx.scale(2, 2);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, 1000, height);
      ctx.fillStyle = "#17664f";
      ctx.fillRect(0, 0, 1000, 8);
      ctx.fillStyle = "#17352c";
      ctx.font = `bold 32px ${font}`;
      ctx.fillText("入塾申请走访资料", 48, 66);
      ctx.font = `18px ${font}`;
      ctx.fillStyle = "#64736d";
      ctx.fillText(
        `${data.application.application_no} · 第 ${index + 1}/${pages.length} 张`,
        48,
        102
      );
      ctx.fillText(`用途：${data.purpose}`, 48, 132);
      for (const item of items) {
        if (item.kind === "section") {
          ctx.fillStyle = "#eaf3ef";
          ctx.fillRect(item.x, item.y, item.width, 42);
          ctx.fillStyle = "#17664f";
          ctx.font = `bold 23px ${font}`;
          ctx.fillText(item.label, item.x + 16, item.y + 29);
        } else {
          ctx.fillStyle = "#68766f";
          ctx.font = `18px ${font}`;
          ctx.fillText(item.label, item.x + 16, item.y + 22);
          ctx.fillStyle = "#243a30";
          ctx.font = `22px ${font}`;
          item.lines.forEach((line, row) =>
            ctx.fillText(line, item.x + 16, item.y + 54 + row * 30)
          );
          ctx.strokeStyle = "#e7ece9";
          ctx.beginPath();
          ctx.moveTo(item.x, item.y + 66 + (item.lines.length - 1) * 30);
          ctx.lineTo(
            item.x + item.width,
            item.y + 66 + (item.lines.length - 1) * 30
          );
          ctx.stroke();
        }
      }
      ctx.fillStyle = "#7b8781";
      ctx.font = `16px ${font}`;
      ctx.fillText(
        `导出人：${data.exported_by} · ${new Date(data.exported_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`,
        48,
        height - 48
      );
      return new Promise<Blob>((resolve, reject) =>
        canvas.toBlob(
          blob => (blob ? resolve(blob) : reject(new Error("图片生成失败"))),
          "image/png"
        )
      );
    })
  );
}
