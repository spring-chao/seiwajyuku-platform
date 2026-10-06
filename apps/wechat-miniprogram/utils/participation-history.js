const LEARNING_TYPES = ["学习会", "班级学习日", "小组学习会", "开班", "课程"];

function historyPath({ kind = "learning", year = "", page = 1, pageSize = 20 } = {}) {
  let path = `/api/v1/wechat/participation-history?kind=${kind}&page=${page}&page_size=${pageSize}`;
  if (year) path += `&year=${encodeURIComponent(year)}`;
  return path;
}

function historyDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : "时间待确认";
}

function readHistory(data, offset = 0) {
  if (!data || !Array.isArray(data.records) || !Number.isInteger(data.total) || data.total < 0 ||
      !Number.isInteger(data.learning_count) || data.learning_count < 0 ||
      !Number.isInteger(data.activity_count) || data.activity_count < 0) {
    throw new Error("参与记录暂时无法加载，请重试。");
  }
  return {
    records: data.records.map((item, index) => ({
      title: item.title || "参与记录",
      learning_type: item.learning_type || "活动",
      status_name: item.status_name || "",
      occurredAtLabel: historyDate(item.occurred_at),
      scopeLabel: [item.class_name, item.group_name].filter(Boolean).join(" · "),
      uiKey: `${item.occurred_at || "date"}-${item.title || "record"}-${offset + index}`
    })),
    total: data.total,
    learningCount: data.learning_count,
    activityCount: data.activity_count,
    hasMore: data.has_more === true,
    historyVersion: typeof data.history_version === "string" ? data.history_version : null,
    categoryCounts: data.category_counts || {},
    availableYears: (data.available_years || []).filter(year => Number.isInteger(year))
  };
}

function categoriesFor(kind, counts) {
  const names = kind === "learning" ? LEARNING_TYPES : Object.keys(counts).filter(name => !LEARNING_TYPES.includes(name));
  return names.map(name => ({ name, count: Number(counts[name]) || 0 }));
}

module.exports = { historyPath, readHistory, categoriesFor, LEARNING_TYPES };
