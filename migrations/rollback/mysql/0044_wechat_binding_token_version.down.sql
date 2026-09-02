-- 仅在同时回滚使用 token_version 的应用代码前执行；这会移除会话代际字段。

ALTER TABLE wechat_member_bindings DROP COLUMN token_version;
DELETE FROM schema_migrations
WHERE version='0044_wechat_binding_token_version.sql';
