-- 0035: 微信小程序身份绑定（member_id 是唯一业务主身份）
-- openid 只作为指定 AppID 下的登录凭证，不写入业务记录或对外响应。

CREATE TABLE IF NOT EXISTS wechat_member_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid TEXT NOT NULL,
    openid TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'VERIFIED'
        CHECK(status IN ('VERIFIED', 'REVOKED')),
    active_slot INTEGER,
    binding_source TEXT NOT NULL DEFAULT 'MINIPROGRAM_SELF_SERVICE',
    verified_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(appid, openid),
    UNIQUE(appid, member_id, active_slot),
    CHECK((status='VERIFIED' AND active_slot=1) OR (status='REVOKED' AND active_slot IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_member
    ON wechat_member_bindings(member_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_member_bindings_status
    ON wechat_member_bindings(appid, status, updated_at);
