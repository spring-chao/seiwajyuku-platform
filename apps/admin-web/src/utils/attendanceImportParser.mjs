// Reused unchanged from spring-chao/signin public/admin.html (validated by admin_import_parser.test.js).

// This module adapts the existing spreadsheet parser; it does not define attendance rules.

export function detectColumns(headers) {
  var map = {
    name: -1,
    phone: -1,
    company: -1,
    center: -1,
    class_name: -1,
    group_name: -1,
    group_num: -1,
    dinner_table_num: -1,
    is_student: -1
  };
  function normalizeHeader(h) {
    return String(h || "")
      .trim()
      .toLowerCase()
      .replace(/[（(][^）)]*[）)]/g, "")
      .replace(/[\s*＊:：_\-/]+/g, "");
  }
  function match(h) {
    h = normalizeHeader(h);
    if (
      /^(姓名|名字|name|学员姓名|参会人|参会人姓名|报名人|报名人姓名|嘉宾姓名)$/i.test(
        h
      )
    )
      return "name";
    if (
      /^(手机号|手机号码|电话|phone|tel|mobile|联系电话|联系手机|报名手机)$/i.test(
        h
      )
    )
      return "phone";
    if (/^(公司|企业|单位|company|公司名称|工作单位)$/i.test(h))
      return "company";
    if (/^(分中心|中心|center|分部|所属分中心)$/i.test(h)) return "center";
    if (/^(班级|班|class|班级名称)$/i.test(h)) return "class_name";
    if (/^(组号|组别|大组|分组)$/i.test(h)) return "group_num";
    if (/^(小组|小组名称|组名)$/i.test(h)) return "group_name";
    if (/^(桌号|桌数|桌子|空巴桌号|晚宴桌号|dinner|table)$/i.test(h))
      return "dinner_table_num";
    if (/^(是否学员|学员|is_student)$/i.test(h)) return "is_student";
    return null;
  }
  for (var i = 0; i < headers.length; i++) {
    var m = match(headers[i]);
    if (m && map[m] === -1) map[m] = i;
  }
  return map;
}

export function findHeaderRow(rows, options) {
  options = options || {};
  var requirePhone = options.requirePhone === true;
  var best = null;
  var scanLimit = Math.min(100, rows.length);
  for (var i = 0; i < scanLimit; i++) {
    if (!Array.isArray(rows[i])) continue;
    var columns = detectColumns(rows[i]);
    if (columns.name < 0 || (requirePhone && columns.phone < 0)) continue;
    var recognized = Object.keys(columns).filter(function (key) {
      return columns[key] >= 0;
    }).length;
    if (!best || recognized > best.recognized) {
      best = { rowIndex: i, columns: columns, recognized: recognized };
    }
  }
  return best;
}

export function buildCourseImportQuality(rows) {
  rows = Array.isArray(rows) ? rows : [];
  var validPhone = 0,
    missingPhone = 0,
    invalidPhone = 0,
    duplicateMap = {};
  rows.forEach(function (item) {
    item = item || {};
    var name = String(item.name || "").trim();
    var phone = normalizeRosterPhone(item.phone);
    if (!phone) missingPhone++;
    else if (/^1\d{10}$/.test(phone)) validPhone++;
    else invalidPhone++;
    var key = name.replace(/\s+/g, "").toLowerCase() + "|" + phone;
    duplicateMap[key] = (duplicateMap[key] || 0) + 1;
  });
  var duplicateGroups = Object.keys(duplicateMap).filter(function (key) {
    return duplicateMap[key] > 1;
  });
  var repeatedSlots = duplicateGroups.reduce(function (sum, key) {
    return sum + duplicateMap[key] - 1;
  }, 0);
  return {
    member_count: rows.length,
    valid_phone_count: validPhone,
    missing_phone_count: missingPhone,
    invalid_phone_count: invalidPhone,
    duplicate_groups: duplicateGroups.length,
    repeated_slots: repeatedSlots
  };
}

export function normalizeRosterPhone(value) {
  return String(value || "")
    .trim()
    .replace(/\s/g, "")
    .replace(/-/g, "");
}

export function buildRosterQuality(rows) {
  rows = Array.isArray(rows) ? rows : [];
  var issues = [],
    validPhoneMap = {},
    validNameCount = 0,
    validPhoneCount = 0,
    missingPhoneCount = 0,
    invalidPhoneCount = 0;
  rows.forEach(function (item, index) {
    item = item || {};
    var name = String(item.name || "").trim();
    var phone = normalizeRosterPhone(item.phone);
    var issueCodes = [];
    if (name) validNameCount++;
    else issueCodes.push("MISSING_NAME");
    if (!phone) {
      missingPhoneCount++;
      // Phone is contact data only; it no longer blocks a roster import.
    } else if (!/^1\d{10}$/.test(phone)) {
      invalidPhoneCount++;
      // Keep the quality counts for an operator warning, but do not block.
    } else {
      validPhoneCount++;
      validPhoneMap[phone] = (validPhoneMap[phone] || 0) + 1;
    }
    if (issueCodes.length)
      issues.push({
        row_number: index + 1,
        name: name || "第" + (index + 1) + "行",
        issue_codes: issueCodes
      });
  });
  if (!rows.length)
    issues.push({ row_number: 0, name: "名单", issue_codes: ["EMPTY_ROSTER"] });
  var sharedPhoneGroups = Object.keys(validPhoneMap).filter(function (phone) {
    return validPhoneMap[phone] > 1;
  });
  var sharedPhoneMemberCount = sharedPhoneGroups.reduce(function (sum, phone) {
    return sum + validPhoneMap[phone];
  }, 0);
  return {
    passed: issues.length === 0,
    member_count: rows.length,
    valid_name_count: validNameCount,
    valid_phone_count: validPhoneCount,
    missing_name_count: issues.filter(function (item) {
      return item.issue_codes.indexOf("MISSING_NAME") >= 0;
    }).length,
    missing_phone_count: missingPhoneCount,
    invalid_phone_count: invalidPhoneCount,
    issue_count: issues.length,
    issues: issues,
    shared_phone_group_count: sharedPhoneGroups.length,
    shared_phone_member_count: sharedPhoneMemberCount
  };
}

export function parseAttendanceRows(arr) {
  const header = findHeaderRow(arr, { requirePhone: false });
  if (!header) throw new Error("前100行未找到包含“姓名”的表头，请检查列名");
  const dataStart = header.rowIndex + 1;
  const col = header.columns;
  let attendees;

  attendees = [];
  for (var i = dataStart; i < arr.length; i++) {
    var row = arr[i];
    if (!row) continue;
    var nameIdx = col.name,
      phoneIdx = col.phone;
    if (nameIdx < 0) break;
    var n = String(row[nameIdx] || "").trim();
    if (!n || n === "undefined" || n === "nan") continue;
    var ph = phoneIdx >= 0 ? String(row[phoneIdx] || "").trim() : "";
    ph = ph.replace(/\s/g, "").replace(/-/g, "");
    if (ph.indexOf(".") >= 0) {
      var num = parseFloat(ph);
      if (!isNaN(num)) ph = String(Math.round(num));
    }
    if (ph.length < 11 && /^\d+$/.test(ph)) ph = ph.padStart(11, "0");

    function cell(idx) {
      return idx >= 0 && row[idx] ? String(row[idx]).trim() : "";
    }
    function cellClean(idx) {
      var v = cell(idx);
      return v && v !== "nan" && v !== "undefined" ? v : "";
    }

    var gn = col.group_num >= 0 ? parseInt(cell(col.group_num)) : null;
    var dn =
      col.dinner_table_num >= 0 ? parseInt(cell(col.dinner_table_num)) : null;
    if (isNaN(gn)) gn = null;
    if (isNaN(dn)) dn = null;

    attendees.push({
      name: n,
      phone: ph,
      center: cellClean(col.center),
      class_name: cellClean(col.class_name),
      group_name: cellClean(col.group_name),
      company: cellClean(col.company),
      group_num: gn,
      dinner_table_num: dn
    });
  }

  return {
    attendees,
    headerRow: header.rowIndex + 1,
    columns: col,
    groupField:
      col.center >= 0
        ? "center"
        : col.class_name >= 0
          ? "class_name"
          : col.group_name >= 0
            ? "group_name"
            : "",
    quality: buildCourseImportQuality(attendees)
  };
}
