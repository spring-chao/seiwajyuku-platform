type Snapshot = {applicationId: number; referrer: string; centerId: string; enabled: boolean};
type Suggestion = {status: "MATCHED" | "MANUAL"; referrer_org_unit_id: string | null};

/** Suggestions stay in the review form until its normal Save action. */
export function createReferrerCenterAutofill(deps: {
  current: () => Snapshot;
  lookup: (id: number, name: string) => Promise<Suggestion>;
  apply: (center: string) => void;
  notify: (loading: boolean, message: string) => void;
}) {
  let generation = 0;
  let automatic: {applicationId: number; centerId: string} | null = null;
  const reset = () => { generation++; automatic = null; deps.notify(false, ""); };
  const edited = () => {
    generation++;
    const now = deps.current();
    if (automatic?.applicationId === now.applicationId && automatic.centerId === now.centerId) {
      deps.apply("");
    }
    automatic = null;
    deps.notify(false, deps.current().centerId ? "推荐人已更改，请核对已选分中心" : "");
  };
  const run = async () => {
    const request = ++generation;
    const start = deps.current();
    const name = start.referrer.trim();
    if (!start.enabled || !name || start.centerId) return;
    deps.notify(true, "正在匹配推荐人所属分中心…");
    let hint = "";
    try {
      const result = await deps.lookup(start.applicationId, name);
      const now = deps.current();
      if (request !== generation || !now.enabled || now.applicationId !== start.applicationId ||
          now.referrer.trim() !== name || now.centerId !== start.centerId) return;
      if (result.status === "MATCHED" && result.referrer_org_unit_id) {
        deps.apply(result.referrer_org_unit_id);
        automatic = {applicationId: start.applicationId, centerId: result.referrer_org_unit_id};
        hint = "已按推荐人档案带出，可修改；保存审核后生效";
      } else {
        hint = "未找到姓名唯一且归属明确的推荐人，请手动选择分中心";
      }
    } catch {
      hint = "暂未完成自动匹配，请手动选择分中心";
    } finally {
      if (request === generation) deps.notify(false, hint);
    }
  };
  return {run, edited, reset, manual: reset};
}
