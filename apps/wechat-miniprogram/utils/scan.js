function textOf(value) {
  return typeof value === "string" ? value : value ? JSON.stringify(value) : "";
}

function queryValue(value, key) {
  const source = textOf(value);
  const match = source.match(new RegExp(`[?&]${key}=([^&#]+)`, "i"));
  if (!match) return "";
  try { return decodeURIComponent(match[1]); } catch (_) { return match[1]; }
}

function classifyScanResult(result) {
  const values = [result && result.path, result && result.result, result && result.scene]
    .map(textOf)
    .filter(Boolean);
  const joined = values.join(" ");
  let payload = null;
  for (const value of values) {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object") { payload = parsed; break; }
    } catch (_) {
      // QR content is usually a URL or a route; JSON is only an optional
      // compatibility format for internal test codes.
    }
  }
  const kind = String(payload && (payload.type || payload.scene || payload.kind) || "").toLowerCase();
  if (/(study[_-]?meeting|group[_-]?meeting|学习会|小组学习)/i.test(`${kind} ${joined}`)) return "study-meeting";
  if (/(enrollment|enroll|入塾|新学长)/i.test(`${kind} ${joined}`)) return "enrollment";
  if (/(activity|checkin|签到|活动)/i.test(`${kind} ${joined}`)) return "activity";
  return "unknown";
}

function enrollmentToken(result) {
  const payload = result && result.result;
  if (payload && typeof payload === "object" && payload.token) return String(payload.token);
  return queryValue(result && result.path, "token") || queryValue(payload, "token") || queryValue(result && result.scene, "token");
}

module.exports = { classifyScanResult, enrollmentToken };
