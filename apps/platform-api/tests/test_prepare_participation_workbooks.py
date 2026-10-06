"""Synthetic XLSX fixtures exercise identity, attendance, and import boundaries."""

import importlib.util
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'prepare_participation_workbooks.py'
SPEC = importlib.util.spec_from_file_location('prepare_participation_workbooks', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def workbook(path, sheets):
    """Create tiny OOXML test data without changing or exporting user workbooks."""
    ns = MODULE.MAIN_NS
    workbook_root = ET.Element('workbook', xmlns=ns, attrib={'xmlns:r': MODULE.REL_NS})
    sheet_nodes = ET.SubElement(workbook_root, 'sheets')
    rels = ET.Element('Relationships', xmlns=MODULE.PACKAGE_REL_NS)
    content = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(content, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(content, 'Default', Extension='xml', ContentType='application/xml')
    ET.SubElement(content, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    root_rels = ET.Element('Relationships', xmlns=MODULE.PACKAGE_REL_NS)
    ET.SubElement(root_rels, 'Relationship', Id='rId1', Type=MODULE.REL_NS+'/officeDocument', Target='xl/workbook.xml')
    with zipfile.ZipFile(path, 'w') as archive:
        for index, (name, rows) in enumerate(sheets.items(), 1):
            ET.SubElement(sheet_nodes, 'sheet', name=name, sheetId=str(index), attrib={'r:id': f'rId{index}'})
            ET.SubElement(rels, 'Relationship', Id=f'rId{index}', Type=MODULE.REL_NS+'/worksheet', Target=f'worksheets/sheet{index}.xml')
            ET.SubElement(content, 'Override', PartName=f'/xl/worksheets/sheet{index}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            sheet = ET.Element('worksheet', xmlns=ns)
            ET.SubElement(sheet, 'dimension', ref='A1:K1048576')
            data = ET.SubElement(sheet, 'sheetData')
            for row_index, values in enumerate(rows, 1):
                row = ET.SubElement(data, 'row', r=str(row_index))
                for col_index, value in enumerate(values, 1):
                    if value is None:
                        continue
                    cell = ET.SubElement(row, 'c', r=MODULE.openpyxl.utils.get_column_letter(col_index)+str(row_index), t='inlineStr')
                    ET.SubElement(ET.SubElement(cell, 'is'), 't').text = str(value)
            archive.writestr(f'xl/worksheets/sheet{index}.xml', ET.tostring(sheet, encoding='utf-8'))
        archive.writestr('[Content_Types].xml', ET.tostring(content, encoding='utf-8'))
        archive.writestr('_rels/.rels', ET.tostring(root_rels, encoding='utf-8'))
        archive.writestr('xl/workbook.xml', ET.tostring(workbook_root, encoding='utf-8'))
        archive.writestr('xl/_rels/workbook.xml.rels', ET.tostring(rels, encoding='utf-8'))
    return path


def roster(tmp_path, rows=None):
    return workbook(tmp_path/'roster.xlsx', {'人员': [
        ['名字', '所在分中心', '所属班级'],
        *(rows or [['示例甲', '园区分中心', '圆融三班'], ['示例乙', '昆山分中心', '炎武一班']]),
    ]})


def prepare(tmp_path, rows, roster_rows=None):
    roster_file = roster(tmp_path, roster_rows)
    source = workbook(tmp_path/'source.xlsx', {'26年活动': rows})
    return MODULE.prepare(roster_file, {2026: source})


def test_dynamic_headers_blank_checkin_and_source_hash(tmp_path):
    candidates, issues, summary = prepare(tmp_path, [
        ['', '1月2日学习会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '园区分中心', '√'],
        ['', '', '示例乙', '昆山分中心', None],
        ['', '1月3日班级学习日', '组别', '姓名', '签到', '分中心'],
        ['', '', '一组', '示例乙', '参加', '昆山分中心'],
    ])
    assert len(candidates) == 3
    assert [r['participation_status'] for r in candidates] == ['PRESENT', 'UNCONFIRMED', 'PRESENT']
    assert candidates[2]['name'] == '示例乙'
    assert summary['source_files_unchanged']
    assert summary['sources'][0]['last_value_row'] == 5
    assert issues[0]['problems'] == ['attendance_unconfirmed']


def test_event_venue_and_compound_center_do_not_prove_identity(tmp_path):
    candidates, _, _ = prepare(tmp_path, [
        ['', '1月2日园区报告会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '', '√'],
        ['', '', '示例乙', '昆山分中心/园区分中心', '√'],
    ])
    assert all(row['review_status'] == 'REVIEW' for row in candidates)
    assert all(row['roster_row'] == '' for row in candidates)
    assert '/' in candidates[1]['center']


def test_exact_person_class_cross_check_and_no_mismatched_class(tmp_path):
    candidates, _, summary = prepare(tmp_path, [
        ['', '1月2日学习会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '圆融三班', '√'],
        ['', '1月3日炎武一班线下学习会', '姓名', '签到'],
        ['', '', '示例乙', '√'],
        ['', '', '示例甲', '√'],
    ])
    assert candidates[0]['match_method'] == 'name_exact_roster_class'
    assert candidates[1]['match_method'] == 'name_exact_event_roster_class'
    assert candidates[1]['source_table'] == 'class_study_days'
    assert candidates[2]['review_status'] == 'REVIEW'
    assert summary['review_status']['READY_FOR_MAPPING'] == 2


def test_duplicate_names_do_not_use_class_fallback(tmp_path):
    candidates, _, _ = prepare(tmp_path, [
        ['', '1月2日圆融三班线下学习会', '姓名', '签到'],
        ['', '', '示例甲', '√'],
    ], [['示例甲', '园区分中心', '圆融三班'], ['示例甲', '昆山分中心', '炎武一班']])
    assert candidates[0]['review_status'] == 'REVIEW'
    assert candidates[0]['roster_row'] == ''


def test_title_class_cannot_override_conflicting_source_center(tmp_path):
    candidates, issues, _ = prepare(tmp_path, [
        ['', '1月2日圆融三班线下学习会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '太仓分中心', '√'],
    ])
    assert candidates[0]['review_status'] == 'REVIEW'
    assert candidates[0]['roster_row'] == ''
    assert candidates[0]['center'] == '太仓'
    assert 'unmatched_identity' in issues[0]['problems']


def test_month_precision_no_fake_day_and_counts_are_not_checkins(tmp_path):
    candidates, _, summary = prepare(tmp_path, [
        ['', '3月圆融三班班级学习会', '姓名', '分中心', '参与人数'],
        ['', '', '示例甲', '园区分中心', '1'],
    ])
    assert candidates[0]['occurred_on'] == ''
    assert candidates[0]['occurred_month'] == '2026-03'
    assert candidates[0]['participation_status'] == 'UNCONFIRMED'
    assert summary['month_only_candidates'] == 1


def test_dedup_and_same_day_different_titles_block_bundle(tmp_path):
    candidates, issues, summary = prepare(tmp_path, [
        ['', '1月2日报告会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '园区分中心', '√'],
        ['', '', '示例甲', '园区分中心', '√'],
        ['', '1月2日经营发表会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '园区分中心', '√'],
    ])
    assert candidates[1]['review_status'] == 'DUPLICATE'
    assert candidates[0]['review_status'] == candidates[2]['review_status'] == 'REVIEW'
    assert summary['possible_same_day_overlap_groups'] == 1
    assert sum('possible_same_day_overlap' in issue['problems'] for issue in issues) == 2
    bundle = MODULE.anonymous_bundle(candidates, {'roster_sha256': summary['roster_sha256'], 'members': {'2': 'SYNTHETIC-A'}}, summary['roster_sha256'])
    assert bundle['facts'] == []


def test_member_map_hash_and_one_to_one_validation(tmp_path):
    candidates, _, summary = prepare(tmp_path, [
        ['', '1月2日学习会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '园区分中心', '√'],
    ])
    with pytest.raises(ValueError, match='roster_sha256'):
        MODULE.anonymous_bundle(candidates, {'2': 'SYNTHETIC-A'}, summary['roster_sha256'])
    with pytest.raises(ValueError, match='one-to-one'):
        MODULE.anonymous_bundle(candidates, {'roster_sha256': summary['roster_sha256'], 'members': {'2': 'SYNTHETIC-A', '3': 'SYNTHETIC-A'}}, summary['roster_sha256'])
    bundle = MODULE.anonymous_bundle(candidates, {'roster_sha256': summary['roster_sha256'], 'members': {'2': 'SYNTHETIC-A'}}, summary['roster_sha256'])
    assert len(bundle['facts']) == 1
    payload = json.dumps(bundle, ensure_ascii=False)
    assert '示例甲' not in payload
    assert '园区分中心' not in payload
    assert bundle['privacy_contract']['contains_phones'] is False


def test_title_before_second_header_preserved(tmp_path):
    source = workbook(tmp_path/'source.xlsx', {'游学': [
        ['1月2日游学', '姓名', '分中心', '签到'],
        ['', '示例甲', '园区分中心', '√'],
        ['2月3日游学'],
        ['分中心', '姓名', '参与人数'],
        ['昆山分中心', '示例乙', '1'],
    ]})
    candidates, _ = MODULE.extract_rows(source, 2026)
    assert candidates[1]['event_title'] == '2月3日游学'
    assert candidates[1]['participation_status'] == 'UNCONFIRMED'


def test_external_id_independent_of_row_file_and_source_phone_sanitized():
    assert MODULE.external_id('示例甲', '园区分中心', '2026-01-02', '1月2日 学习会签到表') == MODULE.external_id('示例甲', '园区', '2026-01-02', '1月2日学习会')
    synthetic_phone = '1' + '3' + '9' * 9
    assert synthetic_phone not in MODULE.safe_title('课程 '+synthetic_phone)


def test_written_month_day_without_day_suffix_is_not_month_only():
    assert MODULE.parse_date('11月14炎武一班班级学习日', 2025) == '2025-11-14'
    assert MODULE.parse_date('1月振华一班班级学习会', 2026) == ''
    assert MODULE.parse_date('2月31日班级学习会', 2026) == ''


def test_explicit_date_outside_requested_years_stays_review(tmp_path):
    candidates, issues, _ = prepare(tmp_path, [
        ['', '2027年1月2日学习会', '姓名', '分中心', '签到'],
        ['', '', '示例甲', '园区分中心', '√'],
    ])
    assert candidates[0]['occurred_on'] == '2027-01-02'
    assert candidates[0]['review_status'] == 'REVIEW'
    assert 'outside_requested_years' in issues[0]['problems']


def test_invalid_roster_fails_without_guessing_identity(tmp_path):
    bad = workbook(tmp_path/'bad.xlsx', {'人员': [['编号', '部门'], ['SYNTHETIC-A', '示例部门']]})
    with pytest.raises(ValueError, match='Roster needs'):
        MODULE.prepare(bad, {})
