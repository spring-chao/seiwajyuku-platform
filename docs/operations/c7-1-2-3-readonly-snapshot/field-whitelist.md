# C7.1.2.3｜权威快照字段白名单

仅导出下列字段。`members` 的 `phone_masked`、`phone_last4` 只能在同名同组候选需要总部脱敏账号消歧时加入；不得导出完整电话号码或哈希。

| CSV | 允许字段 |
| --- | --- |
| `class_candidates.csv` | `id, name, unit_type, parent_id, active_from, active_until, is_active` |
| `org_units.csv` | `id, name, unit_type, parent_id, active_from, active_until, is_active` |
| `members.csv` | `id, name, member_code, status`；可选 `phone_masked, phone_last4` |
| `member_org_relations.csv` | `id, member_id, org_unit_id, relation_type, is_primary, valid_from, valid_until, source_type` |
| `class_learning_bindings.csv` | `id, class_org_unit_id, plan_version_id, cohort_month, started_at, ended_at, status, learning_round, transition_type, credit_rule_version_id, course_credit_rule_version_id` |
| `class_learning_cycles.csv` | `id, binding_id, class_org_unit_id, learning_cycle_index, plan_cycle_id, opened_at, actual_class_meeting_at, cycle_status, closed_at` |
| `learning_plan_versions.csv` | `id, plan_key, plan_name, version_label, duration_cycles, status` |
| `schema_migrations.csv` | `version, applied_at` |
| 可选 `learning_credit_rule_versions.csv` | `id, rule_set_key, version_label, status` |
| 可选 `learning_credit_rules.csv` | `id, rule_version_id, rule_key, credit_category, credit_type, settlement_model, status` |
| 可选 `learning_business_calendar_versions.csv` | `id, calendar_key, calendar_year, version_label, timezone, status` |
| 可选 `learning_business_calendar_days.csv` | `calendar_version_id, business_date, day_type, note` |

明确禁止字段包括但不限于：

```text
phone_ciphertext
phone_hash
完整手机号 / mobile / phone
身份证号
关怀备注、跟进备注
企业财务密文
password_hash
token_hash
数据库连接串、密码、密钥、令牌
learning_credit_entries 的任何字段
```

范围也必须最小化：只含吴越三班、其直属 1–5 组与精进组、班级直属父组织参考、窗口内关联成员和关系、该班级的历史 binding/cycle，以及 binding 引用的计划版本。空小组没有平台组节点时不能被手工补造成正常小组。
