type StoragePort = Pick<Storage, "getItem" | "setItem">;
export const creditRuleApplyJournalKey = "credit-rule-reconciliation-submitted";

export async function submitCreditRuleApplyOnce<T>(
  storage: StoragePort,
  record: { commit: string; fingerprint: string; submitted_at: string },
  dispatch: () => Promise<T>
): Promise<T> {
  if (storage.getItem(creditRuleApplyJournalKey) !== null) {
    throw new Error("本浏览器已提交过规则收口请求，请先核验执行结果。");
  }
  storage.setItem(creditRuleApplyJournalKey, JSON.stringify(record));
  return dispatch();
}
