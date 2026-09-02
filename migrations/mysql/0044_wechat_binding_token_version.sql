-- 0044: 微信绑定 session 代际
-- 每次撤销或从 REVOKED 重新绑定都递增，防止旧 token 在记录复用后恢复访问。

ALTER TABLE wechat_member_bindings
    ADD COLUMN token_version INT NOT NULL DEFAULT 1;
