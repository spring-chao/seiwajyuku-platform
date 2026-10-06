"""Read local participation workbooks and prepare review candidates, never write a DB.

Source workbooks stay untouched. The output folder contains personal names for local
identity review, but no phones, company details, financial fields, or source notes.
Only explicit check-ins, exact local identity matches, and an optional verified
roster-row -> platform member_code map can produce an anonymous legacy bundle.
Install openpyxl in the selected analysis runtime to run this standalone script.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import openpyxl

MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
NAME_HEADERS = {'姓名', '名字'}
CENTER_HEADERS = {'分中心', '所属分中心', '所在分中心'}
CHECKIN_HEADERS = {'签到', '签到表', '是否签到'}
POSITIVE = {'√', '✓', '✔', '参加', '已签到', '签到', '出席', '到', '是', '1', '1.0'}
NEGATIVE = {'×', '✗', 'x', 'X', '请假', '未到', '未参加', '缺席', '0', '0.0', '否', '未签到', '未出席'}
COURSE_ALIASES = ('六项精进', '成功方程式', '经营十二条', '稻盛和夫会计学',
                  '核算表', '经营管理体系', '人财评价', '人事评价', '年度战略', '研修营')
SOURCES = {2025: '2025年活动统计.xlsx', 2026: '2026年活动数据.xlsx'}
CANDIDATE_FIELDS = ('source_file', 'source_sheet', 'source_row', 'event_title',
                    'occurred_on', 'occurred_month', 'source_table', 'participation_status', 'name',
                    'center', 'roster_row', 'match_method', 'review_status', 'external_id')


def text(value: Any) -> str:
    return re.sub(r'\s+', '', str(value or '')).strip()


def center_key(value: Any) -> str:
    # Compound affiliation values stay unresolved; choosing one part can cross people.
    value = PHONE.sub('[已移除号码]', text(value))
    return value.removesuffix('分中心')


def safe_title(value: Any) -> str:
    return PHONE.sub('[已移除号码]', re.sub(r'\s+', ' ', str(value or '')).strip())


def person_name(value: Any) -> str:
    value = text(value)
    if (not value or len(value) > 16 or re.search(r'\d', value)
            or any(word in value for word in ('合计', '人数', '参会率', '签到', '学习会', '分中心'))
            or value in NAME_HEADERS | {'总计', '小计', '备注', '汇总'}):
        return ''
    return value


def parse_date(title: str, year: int) -> str:
    """Take a written event's first day; never invent a day for month-only titles."""
    patterns = (
        r'(?:(20\d{2})[年/-])?(\d{1,2})月(\d{1,2})(?!\d)',
        r'(?:(20\d{2})[-/])?(\d{1,2})[./](\d{1,2})(?!\d)',
    )
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            explicit_year, month, day = match.groups()
            try:
                return date(int(explicit_year or year), int(month), int(day)).isoformat()
            except ValueError:
                return ''
    compact = re.match(r'^(\d{3,4})(?!\d)', text(title))
    if compact:
        number = compact.group(1)
        try:
            return date(year, int(number[:-2]), int(number[-2:])).isoformat()
        except ValueError:
            pass
    return ''


def parse_month(title: str, year: int) -> str:
    full_date = parse_date(title, year)
    if full_date:
        return full_date[:7]
    match = re.search(r'(?:(20\d{2})年)?(\d{1,2})月', title)
    if match and 1 <= int(match.group(2)) <= 12:
        return f'{int(match.group(1) or year):04d}-{int(match.group(2)):02d}'
    return ''


def classification(title: str, sheet: str) -> str:
    title = text(title)
    if '志工' in sheet or '志愿者' in title or '志工' in title:
        return 'other_activities'
    if '开班' in title:
        return 'class_openings'
    if '小组学习' in title or '小组会' in title:
        return 'group_sessions'
    if '班级学习日' in title or '班级学习会' in title or re.search(r'班.*学习会', title):
        return 'class_study_days'
    if '学习会' in title:
        return 'learning_meetings'
    if sheet == '课程' or '课程' in title or '小班课' in title or any(v in title for v in COURSE_ALIASES):
        return 'courses'
    return 'other_activities'


def attendance(value: Any, has_column: bool) -> str:
    if not has_column:
        return 'UNCONFIRMED'
    normalized = text(value)
    if normalized in POSITIVE:
        return 'PRESENT'
    if normalized in NEGATIVE:
        return 'ABSENT'
    return 'UNCONFIRMED'


def canonical_title(title: str) -> str:
    value = text(title).replace('《', '').replace('》', '')
    value = re.sub(r'(?:签到表|签到|参会率|参会情况|参会|考勤)$', '', value)
    return value


def external_id(name: str, center: str, occurred_on: str, title: str) -> str:
    identity = json.dumps([text(name), center_key(center), occurred_on, canonical_title(title)], ensure_ascii=False, separators=(',', ':'))
    return 'workbook-' + base64.b32encode(hashlib.sha256(identity.encode('utf-8')).digest()).decode('ascii').rstrip('=').lower()


def read_sheets(path: Path) -> list[tuple[str, list[tuple[Any, ...]], dict[str, int]]]:
    """Bound reads by actual valued XML rows, ignoring million-row formatting tails."""
    with zipfile.ZipFile(path) as archive:
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {node.attrib['Id']: node.attrib['Target'] for node in relations}
        workbook_xml = ET.fromstring(archive.read('xl/workbook.xml'))
        sheet_stats = {}
        for sheet in workbook_xml.findall(f'.//{{{MAIN_NS}}}sheet'):
            target = targets[sheet.attrib[f'{{{REL_NS}}}id']]
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            root = ET.fromstring(archive.read(target))
            valued = [int(row.attrib['r']) for row in root.findall(f'.//{{{MAIN_NS}}}row')
                      if any(cell.find(f'{{{MAIN_NS}}}v') is not None or cell.find(f'{{{MAIN_NS}}}is') is not None for cell in row)]
            sheet_stats[sheet.attrib['name']] = {'last_value_row': max(valued, default=0), 'valued_rows': len(valued)}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [(sheet.title, list(sheet.iter_rows(max_row=sheet_stats[sheet.title]['last_value_row'], values_only=True)) if sheet_stats[sheet.title]['last_value_row'] else [], sheet_stats[sheet.title]) for sheet in wb.worksheets]
    finally:
        wb.close()


def load_roster(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    roster = []
    for sheet, rows, _ in read_sheets(path):
        if not rows:
            continue
        headers = [text(v) for v in rows[0]]
        if '名字' not in headers or '所在分中心' not in headers:
            continue
        name_col, center_col = headers.index('名字'), headers.index('所在分中心')
        class_col = headers.index('所属班级') if '所属班级' in headers else None
        for row_number, row in enumerate(rows[1:], 2):
            name = person_name(row[name_col] if name_col < len(row) else None)
            center = center_key(row[center_col] if center_col < len(row) else None)
            if name:
                roster.append({'roster_row': row_number, 'name': name, 'center': center,
                               'class_name': text(row[class_col]) if class_col is not None and class_col < len(row) else ''})
    pairs = Counter((r['name'], r['center']) for r in roster)
    names = Counter(r['name'] for r in roster)
    return roster, {'people_rows': len(roster), 'duplicate_name_center_keys': sum(v > 1 for v in pairs.values()), 'duplicate_name_keys': sum(v > 1 for v in names.values()), 'missing_centers': sum(not r['center'] for r in roster)}


def schemas(row: tuple[Any, ...]) -> list[dict[str, int | None]]:
    headers = [text(v) for v in row]
    name_cols = [i for i, v in enumerate(headers) if v in NAME_HEADERS]
    result = []
    for index, name_col in enumerate(name_cols):
        end = name_cols[index + 1] if index + 1 < len(name_cols) else len(headers)
        local = range(max(0, name_col-1), end)
        center = next((i for i in local if headers[i] in CENTER_HEADERS), None)
        checkin = next((i for i in local if headers[i] in CHECKIN_HEADERS), None)
        title_col = next((i for i in range(name_col) if headers[i] == '活动'), None)
        result.append({'name': name_col, 'center': center, 'checkin': checkin, 'title': title_col, 'end': end})
    return result


def looks_like_event(value: Any) -> bool:
    value = text(value)
    return bool(value and ((re.search(r'\d{1,2}(?:月|[./])\d{1,2}', value) or re.match(r'^\d{3,4}(?!\d)', value)) or (re.search(r'\d{1,2}月', value) and re.search(r'会|学习|班|游学|课', value)) or (len(value) > 8 and re.search(r'合宿会|志工名单|期.*成员', value))))


def extract_rows(path: Path, year: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates, summaries = [], []
    for sheet, rows, xml_stats in read_sheets(path):
        if sheet == '活动名册':
            summaries.append({'source_file': path.name, 'sheet': sheet, **xml_stats, 'excluded': 'monthly_summary_matrix'})
            continue
        active, titles, previous_heading = [], {}, ''
        start = len(candidates)
        header_count = 0
        for row_number, row in enumerate(rows, 1):
            found = schemas(row)
            if found:
                active = found
                titles = {}
                header_count += 1
                for i, schema in enumerate(active):
                    prefix = row[active[i-1]['name']+1 if i else 0:schema['name']]
                    heading = next((safe_title(v) for v in prefix if looks_like_event(v)), '')
                    titles[i] = heading or previous_heading
                previous_heading = ''
                continue
            # Standalone headings before a header, or inline B titles below one.
            title_values = [(c, safe_title(v)) for c, v in enumerate(row[:3]) if looks_like_event(v)]
            if not active:
                if title_values:
                    previous_heading = title_values[0][1]
                continue
            # On sheets with name in column A, an A heading starts a new segment.
            if title_values and any(s['name'] == title_values[0][0] for s in active):
                previous_heading = title_values[0][1]
                active = []
                continue
            if title_values and not any(person_name(row[s['name']] if s['name'] < len(row) else None) for s in active):
                previous_heading = title_values[0][1]
            for i, schema in enumerate(active):
                name_col = schema['name']
                explicit_col = schema['title']
                heading = (safe_title(row[explicit_col]) if explicit_col is not None and explicit_col < len(row) and looks_like_event(row[explicit_col]) else '')
                if not heading:
                    heading = next((v for c, v in title_values if c < name_col and (i == 0 or c >= active[i-1]['end']-1)), '')
                if heading:
                    titles[i] = heading
                name = person_name(row[name_col] if name_col < len(row) else None)
                if not name:
                    continue
                center_col, checkin_col = schema['center'], schema['checkin']
                center = center_key(row[center_col] if center_col is not None and center_col < len(row) else '')
                title = titles.get(i, '')
                candidates.append({'source_file': path.name, 'source_sheet': sheet, 'source_row': row_number,
                                   'event_title': title, 'occurred_on': parse_date(title, year),
                                   'occurred_month': parse_month(title, year),
                                   'source_table': classification(title, sheet), 'participation_status': attendance(row[checkin_col] if checkin_col is not None and checkin_col < len(row) else None, checkin_col is not None),
                                   'name': name, 'center': center})
        summaries.append({'source_file': path.name, 'sheet': sheet, **xml_stats, 'header_blocks': header_count, 'person_candidates': len(candidates)-start})
    return candidates, summaries


def prepare(roster_path: Path, workbooks: dict[int, Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    source_paths = {'roster': roster_path, **{str(year): path for year, path in workbooks.items()}}
    original_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in source_paths.items()}
    roster, roster_stats = load_roster(roster_path)
    if not roster:
        raise ValueError('Roster needs a header row with 名字 and 所在分中心 and at least one person.')
    by_pair, by_name = defaultdict(list), defaultdict(list)
    for person in roster:
        by_pair[(person['name'], person['center'])].append(person)
        by_name[person['name']].append(person)
    candidates, source_stats = [], []
    for year, path in workbooks.items():
        rows, stats = extract_rows(path, year)
        candidates.extend(rows)
        source_stats.extend(stats)
    issues, seen = [], {}
    for candidate in candidates:
        problems = []
        center = candidate['center']
        matches = by_pair[(candidate['name'], center)] if center else []
        candidate['match_method'] = 'name_center' if center else 'unresolved'
        name_matches = by_name[candidate['name']]
        # A complete class name must also be the unique person's own roster class.
        # Event venues and host centers never prove a participant's affiliation.
        if not matches and len(name_matches) == 1 and '/' not in center and '／' not in center:
            person = name_matches[0]
            own_class = person['class_name']
            if len(own_class) >= 3 and person['center']:
                if text(center) == own_class:
                    matches = [person]
                    candidate['match_method'] = 'name_exact_roster_class'
                elif not center and own_class in text(candidate['event_title']):
                    matches = [person]
                    candidate['match_method'] = 'name_exact_event_roster_class'
                if matches:
                    center = person['center']
                    candidate['center'] = center
        candidate['roster_row'] = matches[0]['roster_row'] if len(matches) == 1 else ''
        if len(matches) != 1:
            candidate['match_method'] = 'ambiguous' if len(matches) > 1 else 'unresolved'
            problems.append('ambiguous_identity' if len(matches) > 1 else 'unmatched_identity')
        if not candidate['event_title']:
            problems.append('missing_event_title')
        if not candidate['occurred_on']:
            problems.append('missing_or_invalid_date')
        elif candidate['occurred_on'][:4] not in {'2025', '2026'}:
            problems.append('outside_requested_years')
        if candidate['participation_status'] == 'UNCONFIRMED':
            problems.append('attendance_unconfirmed')
        candidate['external_id'] = external_id(candidate['name'], center, candidate['occurred_on'], candidate['event_title'])
        candidate['review_status'] = 'REVIEW' if problems else ('EXCLUDED_ABSENT' if candidate['participation_status'] == 'ABSENT' else 'READY_FOR_MAPPING')
        key = candidate['external_id']
        if not problems and candidate['participation_status'] == 'PRESENT':
            if key in seen:
                candidate['review_status'] = 'DUPLICATE'
                problems.append('duplicate_person_event')
            else:
                seen[key] = candidate
        if problems:
            issues.append({'source_file': candidate['source_file'], 'source_sheet': candidate['source_sheet'],
                           'source_row': candidate['source_row'], 'event_title': candidate['event_title'],
                           'name': candidate['name'], 'center': center, 'roster_row': candidate['roster_row'],
                           'suggested_roster_rows': [r['roster_row'] for r in by_name[candidate['name']]], 'problems': problems})
    ready = [r for r in candidates if r['review_status'] == 'READY_FOR_MAPPING']
    overlap_groups = defaultdict(list)
    for row in ready:
        overlap_groups[(row['roster_row'], row['occurred_on'], row['source_table'])].append(row)
    possible_overlaps = [group for group in overlap_groups.values() if len({canonical_title(r['event_title']) for r in group}) > 1]
    for group in possible_overlaps:
        for row in group:
            row['review_status'] = 'REVIEW'
            issues.append({'problems': ['possible_same_day_overlap'], 'roster_row': row['roster_row'],
                           'occurred_on': row['occurred_on'], 'source_table': row['source_table'],
                           'source_file': row['source_file'], 'source_sheet': row['source_sheet'],
                           'source_row': row['source_row'], 'event_title': row['event_title'],
                           'name': row['name'], 'center': row['center'],
                           'overlapping_source_rows': [{'file': r['source_file'], 'sheet': r['source_sheet'], 'row': r['source_row']} for r in group]})
    ready = [r for r in candidates if r['review_status'] == 'READY_FOR_MAPPING']
    final_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in source_paths.items()}
    if final_hashes != original_hashes:
        raise ValueError('A source workbook changed during preparation; rerun with a stable source snapshot.')
    summary = {'roster': roster_stats, 'sources': source_stats, 'candidate_rows': len(candidates),
               'attendance': dict(Counter(r['participation_status'] for r in candidates)),
               'review_status': dict(Counter(r['review_status'] for r in candidates)),
               'ready_by_year_category': dict(Counter(f"{r['occurred_on'][:4]}:{r['source_table']}" for r in ready)),
               'issue_counts': dict(Counter(p for issue in issues for p in issue['problems'])),
               'unique_ready_people': len({r['roster_row'] for r in ready}),
               'ready_match_methods': dict(Counter(r['match_method'] for r in ready)),
               'month_only_candidates': sum(not r['occurred_on'] and bool(r['occurred_month']) for r in candidates),
               'possible_same_day_overlap_groups': len(possible_overlaps),
               'possible_same_day_overlap_rows': sum(len(group) for group in possible_overlaps),
               'roster_sha256': original_hashes['roster'],
               'source_sha256': original_hashes, 'source_files_unchanged': original_hashes == final_hashes,
               'date_range': [min((r['occurred_on'] for r in ready), default=''), max((r['occurred_on'] for r in ready), default='')],
               'notes': ['活动名册 monthly counts excluded to avoid double counting.',
                         'Blank check-ins and attendance lists without check-in columns remain unconfirmed.',
                         'Written date ranges use their first day; month-only event dates are not fabricated.',
                         'Same-person date overlaps with differing titles require review; strict duplicates use normalized titles.',
                         'Local roster rows are review keys, never platform member IDs or codes.']}
    return candidates, issues, summary


def anonymous_bundle(candidates: list[dict[str, Any]], member_map: dict[str, Any], roster_sha256: str) -> dict[str, Any]:
    if not isinstance(member_map, dict) or member_map.get('roster_sha256') != roster_sha256:
        raise ValueError('Member map must bind to the current roster_sha256.')
    members = member_map.get('members')
    if not isinstance(members, dict) or any(not str(row).isdigit() or not isinstance(code, str) or not code.strip() or PHONE.search(code) for row, code in members.items()):
        raise ValueError('Member map must contain nonempty verified member_code strings without phone numbers.')
    if len({code.strip() for code in members.values()}) != len(members):
        raise ValueError('Member map must be one-to-one: two roster rows cannot share a member_code.')
    facts = []
    for row in candidates:
        code = members.get(str(row['roster_row']))
        if row['review_status'] != 'READY_FOR_MAPPING' or not code:
            continue
        facts.append({'source_table': row['source_table'], 'external_id': row['external_id'],
                      'member_code': code.strip(), 'occurred_on': row['occurred_on'],
                      'participation_status': 'PRESENT', 'title': row['event_title']})
    return {'bundle_version': 1, 'source_system': 'activity_workbooks',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'privacy_contract': {'matching_key': 'member_code', 'contains_names': False, 'contains_phones': False, 'contains_narratives': False},
            'facts': facts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook-2025', required=True, type=Path)
    parser.add_argument('--workbook-2026', required=True, type=Path)
    parser.add_argument('--roster', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--member-map', type=Path, help='Optional JSON {roster_sha256, members: {roster_row: verified member_code}}.')
    args = parser.parse_args(argv)
    candidates, issues, summary = prepare(args.roster, {2025: args.workbook_2025, 2026: args.workbook_2026})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir/'participation_candidates.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(candidates)
    (args.output_dir/'participation_issues.json').write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output_dir/'participation_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.member_map:
        bundle = anonymous_bundle(candidates, json.loads(args.member_map.read_text(encoding='utf-8-sig')), summary['roster_sha256'])
        (args.output_dir/'activity_workbooks_bundle.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
