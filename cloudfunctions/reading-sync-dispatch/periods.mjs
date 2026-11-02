// Business dates are strings; UTC arithmetic prevents host timezone/DST drift.
const day = value => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw Error('INVALID_DATE');
  const parsed=new Date(value+'T00:00:00Z');
  if (!Number.isFinite(+parsed)||parsed.toISOString().slice(0,10)!==value) throw Error('INVALID_DATE');
  return parsed;
};
const format = value => value.toISOString().slice(0,10);
const add = (value, n) => new Date(+value+n*86400000);
export function shanghaiToday(now=new Date()) {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
export function periods(kind, runDate=shanghaiToday()) {
  const now=day(runDate), weekday=(now.getUTCDay()+6)%7;
  if(kind==='monthly') {
    if(now.getUTCDate()!==1) throw Error('NOT_MONTH_START');
    const end=add(now,-1), start=add(end,-((end.getUTCDay()+6)%7));
    return [{start:format(start),end:format(end)}];
  }
  if(kind!=='weekly'||weekday!==0) throw Error('NOT_WEEKLY_RUN_DAY');
  if(now.getUTCDate()===1) return [];
  const end=add(now,-1),start=add(now,-7);
  if(start.getUTCMonth()===end.getUTCMonth())return [{start:format(start),end:format(end)}];
  const boundary=new Date(Date.UTC(end.getUTCFullYear(),end.getUTCMonth(),1));
  return [{start:format(start),end:format(add(boundary,-1))},{start:format(boundary),end:format(end)}];
}
