// Only public reading material crosses the detail-page event channel.
// Applicant fields and the enrollment-entry token stay on the form page.
function enrollmentRulesDocument(metadata = {}) {
  const scope = metadata.target_shuku_org_unit_id || "";
  const supplied = metadata.joining_rules_document;
  const detail = supplied && supplied.scope_org_unit_id === scope ? supplied : null;
  const generic = Array.isArray(metadata.joining_rules) ? metadata.joining_rules
    : metadata.joining_notice ? [metadata.joining_notice] : [];
  const ready = metadata.business_config_status === "READY";
  const payment = ready && metadata.payment ? metadata.payment : {};
  const contacts = ready && Array.isArray(metadata.contacts) ? metadata.contacts
    : ready && metadata.contact ? [{ name: metadata.contact.contact_name, phone: metadata.contact.contact_phone }] : [];
  return {
    scopeOrgUnitId: scope,
    title: detail ? detail.title : "加入守则与缴费说明",
    introduction: detail ? detail.introduction : "请认真阅读所申请塾的加入守则及缴费说明。",
    summary: detail ? detail.summary : ["请确认加入条件与学习约定，详细阅读缴费和联系说明后，再提交申请。"],
    sections: detail ? detail.sections.map(section => ({
      id: section.id, title: section.title, paragraphs: section.paragraphs || [],
      points: (section.points || []).map(item => ({ label: item.label, points: item.points })),
      note: section.note || ""
    })) : [
      { id: "eligibility", title: "一、加入守则", paragraphs: generic },
      { id: "payment", title: "二、缴费说明", paragraphs: [] }
    ],
    readingNote: detail ? detail.reading_note : "提交申请不代表已正式入塾，请按工作人员通知完成后续流程。",
    businessReady: ready,
    feeAmount: ready ? metadata.fee_amount || "" : "",
    feeUnit: ready ? metadata.fee_unit || "" : "",
    paymentInstructions: ready ? metadata.payment_instructions || "" : "",
    payment: { payeeName: payment.payee_name || "", bankName: payment.bank_name || "", bankAccount: payment.bank_account || "" },
    contacts: contacts.map(item => ({ name: item.name || "", phone: item.phone || "" })),
    serviceAddress: ready ? metadata.service_address || (metadata.contact && metadata.contact.contact_address) || "" : ""
  };
}

module.exports = { enrollmentRulesDocument };
