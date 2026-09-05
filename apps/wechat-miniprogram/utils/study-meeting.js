function normalizeMeetingPlan(plan, expectedCycleIndex) {
  if (!plan || !Array.isArray(plan.steps) || !plan.steps.length) return null;
  const planCycleIndex = Number(plan.learning_cycle_index || plan.cycle_index || 0);
  if (
    expectedCycleIndex !== undefined &&
    planCycleIndex &&
    planCycleIndex !== Number(expectedCycleIndex)
  ) return null;

  const steps = [];
  const visibleContentKeys = new Set();
  let terminalFound = false;
  for (const item of plan.steps) {
    if (!item || terminalFound) break;
    const stepNo = Number(item.step_no);
    if (!Number.isFinite(stepNo)) continue;
    const content = String(item.content || item.title || "").trim();
    if (!content) continue;
    const contentKeys = Array.isArray(item.learning_content_keys)
      ? item.learning_content_keys.map(key => String(key))
      : [];
    contentKeys.forEach(key => visibleContentKeys.add(key));
    const isTerminal = item.is_terminal === true;
    steps.push({
      stepNo,
      content,
      isTerminal,
      contentKeys
    });
    if (isTerminal) terminalFound = true;
  }

  // A valid flow must end at the first 空巴 boundary.  Do not render a
  // partial/malformed flow as if it were a valid learning plan.
  if (!terminalFound || !steps.length) return null;

  const sourceContents = Array.isArray(plan.learning_contents)
    ? plan.learning_contents
    : [];
  const learningContents = sourceContents
    .filter(item => item && visibleContentKeys.has(String(item.content_key || "")))
    .sort((left, right) => Number(left.sort_order || 0) - Number(right.sort_order || 0))
    .map((item, index) => {
      const contentKey = String(item.content_key || "").trim();
      const points = Number(item.credit_points);
      const hasCredit = Number.isFinite(points) && points > 0;
      const access = item.content_access || {};
      const hasResourceAccess = access.type === "QR";
      return {
        contentKey,
        uiKey: `${contentKey || "content"}-${index}`,
        title: String(item.title || "本期学习内容"),
        description: String(item.description || ""),
        required: item.required !== false,
        hasCredit,
        creditLabel: hasCredit ? `${points}学分课程` : "本内容不单独计课程学分",
        hasResourceAccess,
        resourceLabel: hasResourceAccess
          ? String(access.label || "扫码打开学习内容")
          : ""
      };
    });

  return {
    title: String(plan.title || "本期小组学习会"),
    learningCycleIndex: planCycleIndex,
    learningCycleLabel: String(plan.learning_cycle_label || ""),
    steps,
    learningContents
  };
}

function applyLearningContentResults(contents, savedResults) {
  const saved = new Map(
    (Array.isArray(savedResults) ? savedResults : [])
      .filter(item => item && item.content_key)
      .map(item => [String(item.content_key), {
        completed: item.completed === true,
        // Older local drafts only stored a true completion. Treat that as an
        // explicit confirmation, while an old false value remains unanswered.
        confirmed: item.confirmed === true || item.completed === true
      }])
  );
  return (Array.isArray(contents) ? contents : []).map(item => {
    const result = saved.get(item.contentKey);
    return {
      ...item,
      completed: Boolean(result && result.completed),
      confirmed: Boolean(result && result.confirmed)
    };
  });
}

function serializeLearningContentResults(contents) {
  return (Array.isArray(contents) ? contents : []).map(item => ({
    content_key: item.contentKey,
    completed: item.completed === true,
    confirmed: item.confirmed === true
  }));
}

function requiredLearningContentConfirmed(contents) {
  const required = (Array.isArray(contents) ? contents : []).filter(item => item.required);
  return required.length === 0 || required.every(item => item.confirmed === true);
}

function requiredLearningContentComplete(contents) {
  const required = (Array.isArray(contents) ? contents : []).filter(item => item.required);
  return required.length === 0 || required.every(item => item.completed === true);
}

module.exports = {
  normalizeMeetingPlan,
  applyLearningContentResults,
  serializeLearningContentResults,
  requiredLearningContentConfirmed,
  requiredLearningContentComplete
};
