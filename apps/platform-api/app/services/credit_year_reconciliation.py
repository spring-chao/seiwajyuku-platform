"""Reconcile a confirmed annual source by posting only its audited deficit."""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.db import connect, execute, transaction
from app.services import credit_year_allocations as year
from app.services import credit_opening_balances as opening
from app.services.audit import write_audit
from app.services.learning_credits import LearningCreditError, _db_timestamp

MIGRATION = '0071_learning_credit_year_reconciliation.sql'
TABLES = {'learning_credit_year_reconciliation_rows', 'learning_credit_opening_adjustments'}
HASHES = {'mysql': '2b8bd6791692e008e2a6b52967c08196b689d86d1a62c58f6c4666f64d997944', 'sqlite': '09555ea2f15f5c76694db2e464721baedfce1df8cac4006f647f457687ec68f5'}
MODE = 'RECONCILE_TO_SOURCE'


def present_tables(c):
    sql = "SELECT name AS n FROM sqlite_master WHERE type='table'" if isinstance(c, sqlite3.Connection) else 'SELECT TABLE_NAME AS n FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE()'
    return {r['n'] for r in execute(c, sql).fetchall()} & TABLES


def available(c):
    if present_tables(c) != TABLES or not execute(c, 'SELECT version FROM schema_migrations WHERE version=?', (MIGRATION,)).fetchone():
        return False
    expected = {
        'learning_credit_year_reconciliation_rows': {'import_id','member_id','source_name','member_code','previous_import_id','previous_points','supplement_points'},
        'learning_credit_opening_adjustments': {'id','member_id','import_id','year_import_id','org_unit_id','points','cutoff_date','posted_by','posted_at'},
    }
    return all(fields == {v[0] for v in execute(c, 'SELECT * FROM '+table+' WHERE 1=0').description} for table, fields in expected.items())


def managed(c, import_id):
    return available(c) and bool(execute(c, 'SELECT member_id FROM learning_credit_year_reconciliation_rows WHERE import_id=? LIMIT 1', (import_id,)).fetchone())


def managed_opening(c, import_id):
    return available(c) and bool(execute(c, 'SELECT r.member_id FROM learning_credit_year_rows r JOIN learning_credit_year_reconciliation_rows s ON s.import_id=r.import_id AND s.member_id=r.member_id WHERE r.opening_import_id=? LIMIT 1', (import_id,)).fetchone())


def _overrides(value):
    try:
        if len(value) > 10000:
            raise ValueError
        pairs = json.loads(value, object_pairs_hook=lambda p: p)
        if not isinstance(pairs, list) or len(pairs) > opening.MAX_ROWS:
            raise ValueError
        result = {}
        for key, code in pairs:
            if not isinstance(key, str) or not isinstance(code, str) or not key.strip() or len(key)>255 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',code):
                raise ValueError
            key = year.normalized_name(key)
            if key in result:
                raise ValueError
            result[key] = code
        return result
    except (ValueError, TypeError) as exc:
        raise LearningCreditError('姓名对应关系需为不重复的原表姓名与学长编号') from exc


def _balance(c, member_id, binding, cutoff):
    base = execute(c, 'SELECT b.* FROM learning_credit_opening_balances b JOIN learning_credit_opening_imports i ON i.id=b.import_id AND i.status=\'POSTED\' WHERE b.member_id=?', (member_id,)).fetchone()
    if not base:
        if execute(c,'SELECT member_id FROM learning_credit_opening_balances WHERE member_id=?',(member_id,)).fetchone():
            raise LearningCreditError('已有余额来源状态不完整，请核对原入账记录')
        return None, Decimal(0)
    if base['org_unit_id'] != binding['class_org_unit_id'] or str(base['cutoff_date'])[:10] != cutoff.isoformat():
        raise LearningCreditError('已有余额班级或截止日不同，请核对，不能覆盖')
    adjustments = execute(c, 'SELECT a.points,a.org_unit_id,a.cutoff_date FROM learning_credit_opening_adjustments a JOIN learning_credit_opening_imports i ON i.id=a.import_id AND i.status=\'POSTED\' JOIN learning_credit_year_imports y ON y.id=a.year_import_id AND y.status=\'POSTED\' WHERE a.member_id=?', (member_id,)).fetchall() if available(c) else []
    if any(a['org_unit_id'] != binding['class_org_unit_id'] or str(a['cutoff_date'])[:10] != cutoff.isoformat() for a in adjustments):
        raise LearningCreditError('已有补差属于其他班级或截止日，请核对')
    return int(base['import_id']), Decimal(str(base['points'])) + sum((Decimal(str(a['points'])) for a in adjustments), Decimal(0))


def _progress(c, binding, year_index, cutoff):
    progress = execute(c, 'SELECT * FROM learning_credit_class_progress WHERE binding_id=?', (binding['id'],)).fetchone()
    if progress and (int(progress['completed_days']) != year_index*12 or str(progress['cutoff_date'])[:10] != cutoff.isoformat()):
        raise LearningCreditError('班级已有不同历史进度，请核对，不能覆盖')


def _checked(c, actor, content, binding_id, year_index, cutoff_text, note, overrides):
    cutoff, note = date.fromisoformat(cutoff_text), str(note).strip()
    if cutoff > datetime.now(ZoneInfo('Asia/Shanghai')).date() or cutoff.year<2000 or not 8<=len(note)<=1000:
        raise LearningCreditError('请填写有效截止日期及至少8个字的年度确认说明')
    binding = year._binding(c,actor,binding_id)
    title, rows = year.parse_source(content,year_index)
    if year.normalized_name(binding['class_name']) not in year.normalized_name(title):
        raise LearningCreditError('原表班级与选择班级不同')
    codes = _overrides(overrides)
    if set(codes)-{r['name_key'] for r in rows}:
        raise LearningCreditError('姓名对应关系包含原表不存在的姓名，请核对')
    roster = opening._roster(c,actor)
    names = {}
    for m in roster.values():
        if m['org_unit_id']==binding['class_org_unit_id'] and not m['ambiguous_class']:
            names.setdefault(year.normalized_name(m['name']),[]).append(m)
    for r in rows:
        matches = [roster[codes[r['name_key']]]] if r['name_key'] in codes and codes[r['name_key']] in roster else ([] if r['name_key'] in codes else names.get(r['name_key'],[]))
        if len(matches)!=1 or matches[0]['org_unit_id']!=binding['class_org_unit_id'] or matches[0]['ambiguous_class']:
            r['errors'].append('原表姓名或指定编号未唯一匹配本班有效名册')
            continue
        m = matches[0]
        r.update(member_id=int(m['id']),member_code=m['member_code'],member_name=m['name'],class_label=m['class_label'],org_unit_id=m['org_unit_id'])
        try:
            previous, amount = _balance(c,m['id'],binding,cutoff)
            r.update(previous_import_id=previous,previous_points=format(amount,'.2f'),supplement_points=format(Decimal(r['points'])-amount,'.2f'))
            if Decimal(r['supplement_points'])<0:
                r['errors'].append('年度原表低于已有余额，本入口只补缺额，不扣除已有分数')
            allocations = execute(c,'SELECT year_index,binding_id FROM learning_credit_year_allocations WHERE member_id=?',(m['id'],)).fetchall()
            if allocations:
                r['errors'].append('已有历史年度归属，请核对，不能重复补差或覆盖')
        except LearningCreditError as exc:
            r['errors'].append(str(exc))
    matched = [r for r in rows if 'member_id' in r]
    opening._validate(c,actor,cutoff,matched,check_balances=False)
    _progress(c,binding,year_index,cutoff)
    return binding,rows,cutoff,note


def _fingerprint(binding, rows, year_index, cutoff, note):
    fields = ('member_id','member_code','source_name','previous_import_id','previous_points','supplement_points','points','excel_row')
    payload = {'mode':MODE,'binding_id':int(binding['id']),'class_id':binding['class_org_unit_id'],'year_index':year_index,'cutoff_date':cutoff.isoformat(),'note':note,'rows':sorted([{k:r[k] for k in fields} for r in rows],key=lambda r:r['member_id'])}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def preview(content,binding_id,year_index,cutoff,note,actor,overrides):
    c=connect()
    try:
        binding,rows,cutoff,note=_checked(c,actor,content,binding_id,year_index,cutoff,note,overrides)
        errors=sum(bool(r['errors']) for r in rows)
        return {'rows':rows,'row_count':len(rows),'error_count':errors,'total_points':format(sum((Decimal(r['points']) for r in rows),Decimal(0)),'.2f'),
                'supplement_points':format(sum((Decimal(r.get('supplement_points','0')) for r in rows),Decimal(0)),'.2f'),
                'fingerprint':_fingerprint(binding,rows,year_index,cutoff,note) if not errors else None,
                'year_index':year_index,'current_year_after_import':year_index+1,'completed_days':year_index*12,'class_name':binding['class_name'],
                'cutoff_date':cutoff.isoformat(),'source_note':note,'storage_available':available(c),'adds_points':True,'mode':MODE}
    finally:c.close()


def register(content,binding_id,year_index,cutoff,note,filename,fingerprint,actor,overrides):
    with transaction() as c, opening._operation(c):
        year._ready(c)
        if not available(c):raise LearningCreditError('补差存储尚未准备完成')
        existing=execute(c,'SELECT id FROM learning_credit_year_imports WHERE content_fingerprint=?',(fingerprint,)).fetchone()
        if existing:
            batch,rows=year._batch(c,actor,existing['id'])
            if not managed(c,batch['id']) or batch['status']=='CANCELLED' or batch['file_sha256']!=hashlib.sha256(content).hexdigest() or batch['binding_id']!=binding_id or batch['year_index']!=year_index or str(batch['cutoff_date'])[:10]!=cutoff or batch['source_note']!=note.strip():
                raise LearningCreditError('来源已变化或取消，请重新检查')
            title,source=year.parse_source(content,year_index)
            saved={(r['excel_row'],r['source_name'],format(Decimal(str(r['points'])),'.2f')) for r in rows}
            if saved!={(r['excel_row'],r['source_name'],r['points']) for r in source}:
                raise LearningCreditError('年度原分值已变化')
            codes=_overrides(overrides)
            if any(r['name_key'] in codes and codes[r['name_key']]!=next(s['member_code'] for s in rows if s['excel_row']==r['excel_row']) for r in source) or set(codes)-{r['name_key'] for r in source}:
                raise LearningCreditError('姓名对应关系已变化')
            return {'id':batch['id'],'status':batch['status'],'idempotent':True}
        binding,rows,cutoff,note=_checked(c,actor,content,binding_id,year_index,cutoff,note,overrides)
        if any(r['errors'] for r in rows) or _fingerprint(binding,rows,year_index,cutoff,note)!=fingerprint:
            raise LearningCreditError('名册、余额或来源已变化，请重新检查')
        now=_db_timestamp(c);digest=hashlib.sha256(content).hexdigest()
        import_id=int(execute(c,'INSERT INTO learning_credit_year_imports(content_fingerprint,file_sha256,original_filename,binding_id,year_index,cutoff_date,source_note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(fingerprint,digest,filename[:255],binding_id,year_index,cutoff.isoformat(),note,actor,now)).lastrowid)
        opening_id=int(execute(c,'INSERT INTO learning_credit_opening_imports(content_fingerprint,file_sha256,original_filename,cutoff_date,created_by,created_at) VALUES (?,?,?,?,?,?)',(hashlib.sha256((fingerprint+':deficit').encode()).hexdigest(),digest,('年度补差：'+filename)[:255],cutoff.isoformat(),actor,now)).lastrowid)
        for r in rows:
            execute(c,'INSERT INTO learning_credit_year_rows(import_id,member_id,opening_import_id,excel_row,points) VALUES (?,?,?,?,?)',(import_id,r['member_id'],opening_id,r['excel_row'],r['points']))
            execute(c,'INSERT INTO learning_credit_year_reconciliation_rows(import_id,member_id,source_name,member_code,previous_import_id,previous_points,supplement_points) VALUES (?,?,?,?,?,?,?)',(import_id,r['member_id'],r['source_name'],r['member_code'],r['previous_import_id'],r['previous_points'],r['supplement_points']))
            execute(c,'INSERT INTO learning_credit_opening_rows(import_id,member_id,org_unit_id,member_name,class_label,excel_row,points) VALUES (?,?,?,?,?,?,?)',(opening_id,r['member_id'],r['org_unit_id'],r['member_name'],r['class_label'],r['excel_row'],r['supplement_points']))
        write_audit(c,actor_user_id=actor,action='learning.credit_year.reconcile.submit',resource_type='credit_year_import',resource_id=str(import_id),org_unit_id=binding['class_org_unit_id'],after={'mode':MODE,'file_sha256':digest,'fingerprint':fingerprint,'opening_import_id':opening_id,'count':len(rows),'supplement_points':format(sum((Decimal(r['supplement_points']) for r in rows),Decimal(0)),'.2f'),'mappings':[{k:r[k] for k in ('source_name','member_code')} for r in rows]})
        return {'id':import_id,'status':'PENDING_APPROVAL','idempotent':False}


def enrich(c,batch,rows):
    saved={int(r['member_id']):dict(r) for r in execute(c,'SELECT * FROM learning_credit_year_reconciliation_rows WHERE import_id=?',(batch['id'],)).fetchall()}
    if not saved:return
    if len(saved)!=len(rows):raise LearningCreditError('补差来源行不完整，请核验')
    for r in rows:
        s=saved[int(r['member_id'])]
        r.update(source_name=s['source_name'],member_code=s['member_code'],previous_import_id=s['previous_import_id'],previous_points=format(Decimal(str(s['previous_points'])),'.2f'),supplement_points=format(Decimal(str(s['supplement_points'])),'.2f'),points=format(Decimal(str(r['points'])),'.2f'))
    batch.update(mode=MODE,adds_points=True,supplement_points=format(sum((Decimal(r['supplement_points']) for r in rows),Decimal(0)),'.2f'))


def action(c,batch,rows,actor,operation,fingerprint):
    binding=year._binding(c,actor,batch['binding_id']);status=batch['status']
    opening_ids={r['opening_import_id'] for r in rows}
    if len(opening_ids)!=1:raise LearningCreditError('补差来源不完整')
    opening_id=next(iter(opening_ids))
    header=execute(c,'SELECT * FROM learning_credit_opening_imports WHERE id=?',(opening_id,)).fetchone()
    if not header or header['status']!=status or header['file_sha256']!=batch['file_sha256'] or header['content_fingerprint']!=hashlib.sha256((fingerprint+':deficit').encode()).hexdigest() or str(header['cutoff_date'])[:10]!=str(batch['cutoff_date'])[:10]:
        raise LearningCreditError('补差与年度来源状态不一致，请核验')
    if status=='POSTED':
        if operation=='cancel':raise LearningCreditError('已入账来源不能取消，请做前向更正')
        return {'id':batch['id'],'status':status,'idempotent':True}
    if status=='CANCELLED':
        if operation!='cancel':raise LearningCreditError('来源已取消')
        return {'id':batch['id'],'status':status,'idempotent':True}
    cutoff=date.fromisoformat(str(batch['cutoff_date'])[:10])
    if operation!='cancel':
        roster={int(m['id']):m for m in opening._roster(c,actor).values()}
        checks=[]
        for r in sorted(rows,key=lambda r:r['member_id']):
            execute(c,'SELECT id FROM members WHERE id=?'+('' if isinstance(c,sqlite3.Connection) else ' FOR UPDATE'),(r['member_id'],)).fetchone()
            m=roster.get(int(r['member_id']))
            if not m or m['member_code']!=r['member_code'] or m['ambiguous_class'] or m['org_unit_id']!=binding['class_org_unit_id']:
                raise LearningCreditError('学长编号或班级已变化，请重新核对')
            previous,amount=_balance(c,r['member_id'],binding,cutoff)
            if previous!=r['previous_import_id'] or amount!=Decimal(r['previous_points']) or Decimal(r['points'])-amount!=Decimal(r['supplement_points']) or Decimal(r['supplement_points'])<0:
                raise LearningCreditError('原余额或补差已变化，请重新检查')
            if execute(c,'SELECT member_id FROM learning_credit_year_allocations WHERE member_id=?',(r['member_id'],)).fetchone():raise LearningCreditError('已有年度归属，不能重复补差')
            source=execute(c,'SELECT * FROM learning_credit_opening_rows WHERE import_id=? AND member_id=?',(opening_id,r['member_id'])).fetchone()
            if not source or Decimal(str(source['points']))!=Decimal(r['supplement_points']) or source['org_unit_id']!=binding['class_org_unit_id'] or source['member_name']!=m['name'] or source['class_label']!=m['class_label'] or source['excel_row']!=r['excel_row']:
                raise LearningCreditError('补差明细或名册已变化，请核对')
            checks.append({'member_code':m['member_code'],'member_name':m['name'],'class_label':m['class_label'],'errors':[]})
        opening._validate(c,actor,cutoff,checks,check_balances=False)
        if any(r['errors'] for r in checks):raise LearningCreditError('截止日前账本或名册已变化，请重新检查')
        _progress(c,binding,batch['year_index'],cutoff)
        if _fingerprint(binding,rows,batch['year_index'],cutoff,batch['source_note'])!=fingerprint:raise LearningCreditError('补差内容校验失败，请重新检查')
    now=_db_timestamp(c)
    if operation=='cancel':status='CANCELLED'
    elif operation=='approve':
        if status=='APPROVED':return {'id':batch['id'],'status':status,'idempotent':True}
        status='APPROVED'
    else:
        if status!='APPROVED' or not batch['approved_by'] or header['approved_by']!=batch['approved_by']:raise LearningCreditError('请先复核年度原分值和补差')
        for r in rows:
            if r['previous_import_id'] is None:
                execute(c,'INSERT INTO learning_credit_opening_balances(member_id,import_id,org_unit_id,points,cutoff_date,posted_by,posted_at) VALUES (?,?,?,?,?,?,?)',(r['member_id'],opening_id,binding['class_org_unit_id'],r['supplement_points'],cutoff.isoformat(),actor,now))
            elif Decimal(r['supplement_points'])>0:
                execute(c,'INSERT INTO learning_credit_opening_adjustments(member_id,import_id,year_import_id,org_unit_id,points,cutoff_date,posted_by,posted_at) VALUES (?,?,?,?,?,?,?,?)',(r['member_id'],opening_id,batch['id'],binding['class_org_unit_id'],r['supplement_points'],cutoff.isoformat(),actor,now))
            execute(c,'INSERT INTO learning_credit_year_allocations(member_id,opening_import_id,binding_id,year_index,points,import_id) VALUES (?,?,?,?,?,?)',(r['member_id'],opening_id,batch['binding_id'],batch['year_index'],r['points'],batch['id']))
        if not execute(c,'SELECT binding_id FROM learning_credit_class_progress WHERE binding_id=?',(batch['binding_id'],)).fetchone():
            execute(c,'INSERT INTO learning_credit_class_progress(binding_id,completed_days,cutoff_date,import_id) VALUES (?,?,?,?)',(batch['binding_id'],batch['year_index']*12,cutoff.isoformat(),batch['id']))
        status='POSTED'
    for table,identity in [('learning_credit_year_imports',batch['id']),('learning_credit_opening_imports',opening_id)]:
        if status=='CANCELLED':execute(c,'UPDATE '+table+' SET status=? WHERE id=?',(status,identity))
        else:
            prefix='approved' if status=='APPROVED' else 'posted'
            execute(c,'UPDATE '+table+' SET status=?,'+prefix+'_by=?,'+prefix+'_at=? WHERE id=?',(status,actor,now,identity))
    write_audit(c,actor_user_id=actor,action='learning.credit_year.reconcile.'+operation,resource_type='credit_year_import',resource_id=str(batch['id']),org_unit_id=binding['class_org_unit_id'],after={'fingerprint':fingerprint,'status':status,'count':len(rows),'total_points':batch['total_points'],'supplement_points':batch['supplement_points'],'opening_import_id':opening_id,'original_balances_preserved':True})
    return {'id':batch['id'],'status':status,'idempotent':False}


def setup(actor,commit,digest):
    user=opening._permission(actor,opening.OPENING_PERMISSION,'plans:production_rule_reconciliation_apply')
    if 'system_admin' not in user.get('roles',[]):raise PermissionError('仅管理员可以准备补差存储')
    year._write_allowed();settings=year.get_settings()
    if not settings.credit_opening_setup_enabled or settings.run_bootstrap_on_startup or commit!=year.get_build_info()['commit_sha'] or not re.fullmatch('[a-f0-9]{40}',commit):raise LearningCreditError('发布设置或版本已变化，请刷新核对')
    c=connect()
    try:
        with opening._operation(c):
            dialect='sqlite' if isinstance(c,sqlite3.Connection) else 'mysql'
            content=(year.MIGRATION_ROOT/dialect/MIGRATION).read_bytes()
            if hashlib.sha256(content).hexdigest()!=digest or digest!=HASHES[dialect]:raise LearningCreditError('固定补差迁移校验失败')
            if available(c):return {'status':'READY','idempotent':True}
            if present_tables(c) or execute(c,'SELECT version FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone() or execute(c,"SELECT id FROM audit_logs WHERE action='production.credit_year.reconcile.setup' LIMIT 1").fetchone():raise LearningCreditError('存在已执行或中断记录，请核验实际存储，不自动重试')
            if not year.available(c) or not opening.storage_available(c):raise LearningCreditError('请先完成期初余额和年度归属存储')
            write_audit(c,actor_user_id=actor,action='production.credit_year.reconcile.setup',resource_type='schema_migration',resource_id=MIGRATION,result='STARTED',after={'commit':commit,'sha256':digest,'tables':sorted(TABLES),'adds_points':False})
            c.commit()
            for sql in year._split_mysql(content.decode()):execute(c,sql)
            execute(c,'INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)',(MIGRATION,_db_timestamp(c)))
            write_audit(c,actor_user_id=actor,action='production.credit_year.reconcile.setup.complete',resource_type='schema_migration',resource_id=MIGRATION,after={'commit':commit,'sha256':digest,'adds_points':False})
            c.commit();return {'status':'READY','idempotent':False}
    finally:c.close()


def setup_state(c,user):
    ready=available(c)
    reserved=execute(c,"SELECT id FROM audit_logs WHERE action='production.credit_year.reconcile.setup' LIMIT 1").fetchone()
    marker=execute(c,'SELECT version FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()
    tables=present_tables(c)
    return {'reconciliation_available':ready,'reconciliation_setup_allowed':year.available(c) and 'system_admin' in user.get('roles',[]) and 'plans:production_rule_reconciliation_apply' in user['permissions'] and year.get_settings().credit_opening_setup_enabled and not tables and not marker and not reserved,
            'reconciliation_setup_incomplete':not ready and bool(tables or marker or reserved),'reconciliation_migration_sha256':HASHES['sqlite' if isinstance(c,sqlite3.Connection) else 'mysql']}
