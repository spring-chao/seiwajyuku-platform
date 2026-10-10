// Display only: keep the exact decimal values in responses and the ledger.
function integerPoints(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.trunc(number) || 0) : null;
}

function pointsLabel(value, signed = false) {
  const integer = integerPoints(value);
  if (integer === null) return "待补充";
  return `${signed && Number(integer) > 0 ? "+" : ""}${integer}分`;
}

function creditSummaryDisplay(summary) {
  const year = summary.current_learning_year;
  const currentYearKnown = Number.isInteger(year) && year > 0;
  const labels = ["第一年度", "第二年度", "第三年度"];
  const yearLabel = index => labels[index - 1] || `第${index}年度`;
  const yearIndexes = [...new Set([1, 2, 3, ...(summary.learning_years || []).map(row => row.year_index), ...(currentYearKnown ? [year] : [])])].sort((a, b) => a - b);
  return {
    ...summary,
    currentLearningYearLabel: currentYearKnown ? yearLabel(year) : "学习年度待配置",
    hasLearningYearProgress: currentYearKnown && Number.isInteger(summary.current_learning_year_completed_days),
    currentLearningYearPointsLabel: currentYearKnown ? pointsLabel(summary.current_learning_year_points) : "—",
    learningYears: yearIndexes.map(index => {
      const row = (summary.learning_years || []).find(item => item.year_index === index);
      return { yearIndex: index, label: yearLabel(index),
        pointsLabel: row ? pointsLabel(row.points) : "待补充", isCurrent: currentYearKnown && index === year };
    }),
    totalPointsLabel: pointsLabel(summary.total_points),
    openingPointsLabel: pointsLabel(summary.opening_balance_points),
    unallocatedPointsLabel: pointsLabel(summary.unallocated_learning_year_points)
  };
}

module.exports = { integerPoints, pointsLabel, creditSummaryDisplay };
