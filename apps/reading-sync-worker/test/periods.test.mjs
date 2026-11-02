import {test} from 'node:test';
import assert from 'node:assert/strict';
import {periods,shanghaiToday} from '../periods.mjs';
test('month end is the last partial week, never the whole month',()=>{
  assert.deepEqual(periods('monthly','2026-11-01'),[{start:'2026-10-26',end:'2026-10-31'}]);
  assert.deepEqual(periods('monthly','2026-10-01'),[{start:'2026-09-28',end:'2026-09-30'}]);
});
test('Monday on day one is owned by monthly scheduler',()=>{
  assert.deepEqual(periods('weekly','2026-06-01'),[]);
  assert.deepEqual(periods('monthly','2026-06-01'),[{start:'2026-05-25',end:'2026-05-31'}]);
});
test('weekly intervals split without gaps or overlap across months and years',()=>{
  assert.deepEqual(periods('weekly','2026-11-02'),[{start:'2026-10-26',end:'2026-10-31'},{start:'2026-11-01',end:'2026-11-01'}]);
  assert.deepEqual(periods('weekly','2027-01-04'),[{start:'2026-12-28',end:'2026-12-31'},{start:'2027-01-01',end:'2027-01-03'}]);
});
test('leap year and Shanghai midnight are independent of server timezone',()=>{
  assert.deepEqual(periods('monthly','2028-03-01'),[{start:'2028-02-28',end:'2028-02-29'}]);
  assert.equal(shanghaiToday(new Date('2026-10-31T16:00:00Z')),'2026-11-01');
  assert.equal(shanghaiToday(new Date('2026-10-31T15:59:59Z')),'2026-10-31');
});
test('invalid schedules and calendar dates cannot dispatch',()=>{
  assert.throws(()=>periods('weekly','2026-11-03'));
  assert.throws(()=>periods('monthly','2026-11-02'));
  assert.throws(()=>periods('weekly','2026-02-30'));
});
