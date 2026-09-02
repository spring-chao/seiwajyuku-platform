-- 仅在同时回滚生命周期应用代码前执行；先确认没有新建的历史 binding。

ALTER TABLE class_learning_bindings
    DROP FOREIGN KEY fk_class_learning_binding_previous,
    DROP CHECK chk_class_learning_binding_round,
    DROP CHECK chk_class_learning_binding_start_cycle,
    DROP CHECK chk_class_learning_binding_transition,
    DROP INDEX idx_class_learning_bindings_previous,
    DROP INDEX idx_class_learning_bindings_lifecycle,
    DROP COLUMN transition_type,
    DROP COLUMN previous_binding_id,
    DROP COLUMN ended_reason,
    DROP COLUMN ended_at,
    DROP COLUMN start_cycle_index,
    DROP COLUMN learning_round;

DELETE FROM schema_migrations
WHERE version='0045_class_learning_plan_lifecycle.sql';
