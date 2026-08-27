-- 0035: 微信小程序身份绑定（member_id 是唯一业务主身份）
-- openid 只作为指定 AppID 下的登录凭证，不写入业务记录或对外响应。

CREATE TABLE IF NOT EXISTS wechat_member_bindings (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    appid VARCHAR(128) NOT NULL,
    openid VARCHAR(128) NOT NULL,
    member_id BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'VERIFIED',
    active_slot TINYINT NULL,
    binding_source VARCHAR(64) NOT NULL DEFAULT 'MINIPROGRAM_SELF_SERVICE',
    verified_at DATETIME NULL,
    revoked_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_wechat_binding_active_slot CHECK((status='VERIFIED' AND active_slot=1) OR (status='REVOKED' AND active_slot IS NULL)),
    CONSTRAINT chk_wechat_binding_status CHECK(status IN ('VERIFIED', 'REVOKED')),
    CONSTRAINT uq_wechat_binding_openid UNIQUE(appid, openid),
    CONSTRAINT uq_wechat_binding_member UNIQUE(appid, member_id, active_slot),
    CONSTRAINT fk_wechat_binding_member FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_wechat_member_bindings_member
    ON wechat_member_bindings(member_id, status);
CREATE INDEX idx_wechat_member_bindings_status
    ON wechat_member_bindings(appid, status, updated_at);
