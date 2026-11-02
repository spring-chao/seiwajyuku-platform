import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createdExport} from '../collector.mjs';
const record=id=>({bizId:id,bizType:'BookClubTasKStat',fileName:'读书会打卡统计列表'});
const before={total:20,records:[record('old')]};
test('waits for an asynchronous task insertion instead of selecting an old file',()=>{
 assert.equal(createdExport(before,{total:20,records:[record('old')]}),null);
 assert.equal(createdExport(before,{total:21,records:[record('new'),record('old')]}).bizId,'new');
});
test('concurrent exports or unrelated report types never select a latest file blindly',()=>{
 assert.throws(()=>createdExport(before,{total:22,records:[record('one'),record('two'),record('old')]}),/ASSOCIATION/);
 assert.throws(()=>createdExport(before,{total:21,records:[{...record('new'),bizType:'OtherReport'},record('old')]}),/ASSOCIATION/);
 assert.throws(()=>createdExport(before,{total:21,records:[record('old')]}),/ASSOCIATION/);
});
