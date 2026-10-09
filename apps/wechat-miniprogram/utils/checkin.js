const CHECKIN_PAGE = "/pages/checkin/index";
const PLATFORM_RESCUE_NOTICE = "平台暂时无法连接，签到系统仍可使用。请扫描现场备用签到码，或请工作人员帮助签到。";

function platformRescueNotice(error) {
  const status = Number(error && error.statusCode) || 0;
  return status >= 400 && status < 500 ? "" : PLATFORM_RESCUE_NOTICE;
}

function decode(value) {
  try { return decodeURIComponent(String(value || "")); } catch (_) { return ""; }
}

function tokenValue(value) {
  const token = decode(value);
  return /^[A-Za-z0-9_-]{16,32}$/.test(token) ? token : "";
}

function eventValue(value) {
  const id = String(value || "");
  return /^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$/.test(id) && id !== "0" ? id : "";
}

function query(value) {
  const result = {};
  String(value || "").replace(/[?&](token|scene|event_id)=([^&#]*)/gi, (_, key, item) => {
    result[key.toLowerCase()] = decode(item);
    return "";
  });
  return result;
}

function checkinTarget(options = {}) {
  let values = options;
  if (options.path || options.result) {
    if (options.result && typeof options.result === "object") {
      values = options.result;
    } else {
      const source = String(options.path || options.result || "");
      try {
        const parsed = JSON.parse(source);
        if (parsed && typeof parsed === "object") values = parsed;
      } catch (_) {
        values = { ...options, ...query(source) };
        if (!/(?:pages\/checkin\/|activity|checkin)/i.test(source)) {
          // A generic QR URL must never be treated as an activity token.
          return null;
        }
      }
    }
  }
  const scene = decode(values.scene || "");
  const token = tokenValue(values.token || (scene.startsWith("ci_") ? scene.slice(3) : scene));
  const eventId = eventValue(values.event_id);
  if (token) return { token };
  return eventId ? { event_id: eventId } : null;
}

function targetQuery(target) {
  if (!target) return "";
  return target.token ? `token=${encodeURIComponent(target.token)}` : `event_id=${encodeURIComponent(target.event_id)}`;
}

function checkinPath(target) { return `${CHECKIN_PAGE}?${targetQuery(target)}`; }
function contextPath(target) { return `/api/v1/wechat/checkin/context?${targetQuery(target)}`; }

function displayEvent(event = {}) {
  const sessionNames = { MORNING: "上午", AFTERNOON: "下午", KONPA: "晚上空巴", SINGLE: "活动签到" };
  return { ...event, session_name: event.session_name || sessionNames[event.session_code] || "活动签到" };
}

function fallbackUrl(value) {
  // Only a URL returned by the context API reaches this check. Reject protocol
  // tricks and credentials; backend configuration remains the host authority.
  const url = String(value || "");
  return /^https:\/\/[A-Za-z0-9.-]+(?::\d+)?(?:\/[^\s]*)?$/.test(url) && !/[\u0000-\u0020]/.test(url) ? url : "";
}

function engineConfirmUrl(value, apiBaseUrl, transport = {}) {
  const url = String(value || "");
  if (transport.apiTransport === "cloudrun") {
    const base = String(transport.signinEngineApiBase || "").replace(/\/$/, "");
    return /^https:\/\/[a-z0-9.-]+\/stg_signin_20\d{6}_[a-f0-9]{8}\/api$/i.test(base) &&
      url === base + "/native/v1/checkin/confirm" ? url : "";
  }
  const match = url.match(/^https:\/\/([a-z0-9.-]+)(?::443)?(?:\/api)?\/native\/v1\/checkin\/confirm$/i);
  if (!match) return "";
  const host = match[1].toLowerCase();
  const base = String(apiBaseUrl || "").match(/^https:\/\/([a-z0-9.-]+)(?::443)?(?:\/|$)/i);
  const sameHost = base && host === base[1].toLowerCase();
  const cloudbase = /^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:app|service)\.tcloudbase\.com$/.test(host) ||
    /^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:tcloudbaseapp\.com|tcb\.qcloud\.la)$/.test(host);
  return sameHost || cloudbase ? url : "";
}

function directConfirm(url, ticket, transport = {}) {
  return new Promise((resolve, reject) => {
    const success = response => {
        const result = response.data || {};
        const checkedAt = result.checked_at || (result.data && result.data.checked_at);
        const hasReceipt = result.already === true || (typeof checkedAt === "string" && checkedAt.trim());
        const fallback = result.requires_fallback === true || ["NOT_REGISTERED", "TEAM_FALLBACK"].includes(String(result.status || "").toUpperCase());
        if (response.statusCode >= 200 && response.statusCode < 300 && result.ok === true && hasReceipt && !fallback) {
          resolve(result); return;
        }
        const rejectedReceipt = response.statusCode >= 200 && response.statusCode < 300 && result.ok === true;
        const message = rejectedReceipt ? (fallback ? "当前报名需要现场确认，请使用备用签到入口或联系工作人员。" : "签到结果暂未确认，请重试。")
          : result.msg || result.detail || "签到结果暂未确认，请重试。";
        const error = new Error(message);
        error.statusCode = response.statusCode;
        error.engineTicketError = response.statusCode === 401 || response.statusCode === 403;
        reject(error);
    };
    const fail = () => reject(new Error("暂未收到现场签到结果，请重试；重复确认不会重复签到。"));
    if (transport.apiTransport === "cloudrun") {
      if (!engineConfirmUrl(url, transport.apiBaseUrl, transport) ||
          !/^checkinStg20\d{6}[a-f0-9]{8}$/.test(transport.signinEngineFunction || "") ||
          !transport.cloudbaseEnvironment || !wx.cloud || typeof wx.cloud.callFunction !== "function") {
        fail(); return;
      }
      // Direct engine calls stay independent of the platform container. Only
      // the signed, short-lived ticket is sent; no platform session is added.
      wx.cloud.callFunction({ config: { env: transport.cloudbaseEnvironment }, name: transport.signinEngineFunction,
        data: { httpMethod: "POST", path: "/native/v1/checkin/confirm",
          headers: { "content-type": "application/json" }, body: JSON.stringify({ ticket }) }
      }).then(result => {
        try { success({ statusCode: result.result.statusCode, data: JSON.parse(result.result.body) }); }
        catch (_) { fail(); }
      }, fail);
      return;
    }
    wx.request({ url, method: "POST", timeout: 15000, header: { "content-type": "application/json" },
      data: { ticket }, success, fail });
  });
}

module.exports = { checkinTarget, targetQuery, checkinPath, contextPath, displayEvent, fallbackUrl, engineConfirmUrl, directConfirm, platformRescueNotice };
