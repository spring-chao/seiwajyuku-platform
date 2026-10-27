import test from "node:test";
import assert from "node:assert/strict";
import { participationScene, participationTableRows } from "../src/utils/attendanceManagement.mjs";

test("overall, morning, afternoon and konpa retain separate class and group counts", () => {
  const result = { class_statistics: { class_name: "合成班级", present_count: 8, roster_count: 10, participation_rate: .8 },
    group_statistics: [{ group_name: "合成组", present_count: 6 }],
    class_session_statistics: ["am", "pm", "konpa"].map((event_id, index) => ({ event_id, present_count: 5 - index, roster_count: 10,
      participation_rate: (5 - index) / 10, group_statistics: [{ group_name: "合成组", present_count: 3 - index }], group_totals: { present_count: 3 - index } })) };
  assert.equal(participationScene(result), result);
  for (const [index, id] of ["am", "pm", "konpa"].entries()) {
    const view = participationScene(result, id);
    assert.equal(view.class_statistics.present_count, 5 - index);
    assert.equal(view.group_statistics[0].present_count, 3 - index);
    assert.equal(participationTableRows(view).at(-1).present_count, 5 - index);
  }
  assert.equal(participationScene(result, "missing").class_statistics, null);
});
