const assert = require('node:assert/strict');
const test = require('node:test');
const { integerPoints, pointsLabel, creditSummaryDisplay } = require('../utils/credit-display');

test('integer display truncates towards zero while retaining source values', () => {
  for (const [raw, expected] of [['12.99', '12'], ['-12.99', '-12'], ['-0.99', '0'], ['0.99', '0'], ['0.00', '0']]) {
    assert.equal(integerPoints(raw), expected);
  }
  assert.equal(pointsLabel('2.99', true), '+2分');
  assert.equal(pointsLabel('-2.99', true), '-2分');
  assert.equal(pointsLabel('-0.25', true), '0分');
  assert.equal(pointsLabel('broken'), '待补充');
});

test('headline is the current learning year, not lifetime or calendar-year credits', () => {
  const raw = { current_learning_year: 2, current_learning_year_points: '0.00', total_points: '123.99', current_year_points: '98.50',
    learning_years: [{ year_index: 1, points: '123.99' }, { year_index: 2, points: '0.00' }, { year_index: 3, points: '0.00' }] };
  const display = creditSummaryDisplay(raw);
  assert.equal(display.currentLearningYearLabel, '第二年度');
  assert.equal(display.currentLearningYearPointsLabel, '0分');
  assert.equal(display.learningYears[0].pointsLabel, '123分');
  assert.equal(display.learningYears[1].isCurrent, true);
  assert.equal(raw.total_points, '123.99');
});

test('missing year data is explicit and later years are not silently clamped', () => {
  const missing = creditSummaryDisplay({ total_points: '200.00' });
  assert.equal(missing.currentLearningYearPointsLabel, '—');
  assert.equal(missing.learningYears[0].pointsLabel, '待补充');
  const fourth = creditSummaryDisplay({ current_learning_year: 4, current_learning_year_points: '2.50',
    learning_years: [{ year_index: 4, points: '2.50' }] });
  assert.equal(fourth.currentLearningYearLabel, '第4年度');
  assert.equal(fourth.currentLearningYearPointsLabel, '2分');
  assert.equal(fourth.learningYears[3].isCurrent, true);
});
