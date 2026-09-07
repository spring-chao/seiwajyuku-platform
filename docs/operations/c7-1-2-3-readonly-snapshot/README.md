# C7.1.2.3｜吴越三班权威只读快照包

本包只用于把受授权的当前平台历史快照复制到本地隔离验证环境。它不包含真实学员数据、数据库连接串、密码、令牌，也不会由 Codex 连接或查询生产数据库。

固定核验范围：

```text
class_name   = 吴越三班
window_start = 2026-08-31
window_end   = 2026-09-04
```

## 交付给数据所有者 / DBA 的文件

| 文件 | 用途 |
| --- | --- |
| [readonly-snapshot.mysql.sql](readonly-snapshot.mysql.sql) | 参数化的只读预检与导出查询 |
| [field-whitelist.md](field-whitelist.md) | 每个 CSV 的允许字段与禁止字段 |
| [manifest.template.json](manifest.template.json) | 私有快照清单模板 |
| [credit-scope.template.json](credit-scope.template.json) | 安全身份确认后才填写的最小人员范围模板 |
| `apps/platform-api/scripts/validate_c7_authoritative_snapshot.py` | 离线完整性和历史覆盖校验工具 |

## 执行边界

执行者必须是当前平台的数据所有者或受授权 DBA，并使用仅具备本次表集合 `SELECT` 权限的账号。禁止向本包、Git、聊天或验证报告写入：连接串、密码、令牌、完整手机号、电话哈希、关怀备注或不相关班级数据。

在受控 SQL 客户端中以原生参数绑定方式提供 `:class_name`、`:window_start`、`:window_end`。第 1 个候选预检返回且只返回一条有效 `CLASS` 后，人工抄录该条 `id` 为 `:class_org_unit_id`，再运行后续查询。不得因同名候选而自行挑选 ID。

所有查询必须在同一个一致性只读事务中运行：

```sql
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SET SESSION TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
```

导出完成后执行：

```sql
ROLLBACK;
```

SQL 文件不使用 `INTO OUTFILE`，避免要求生产数据库的 `FILE` 权限。请由受控客户端把每个结果集导出为 UTF-8 CSV；列名必须与字段白名单完全一致。

## 私有文件放置与完整性检查

在数据所有者受控的本地目录保存如下文件，不能放入 `outputs/` 或 Git 跟踪目录：

```text
.codex-tmp/c7-real-inputs/platform-snapshot-吴越三班-2026-08-31_09-04/
  manifest.json
  manifest.sha256
  class_candidates.csv
  org_units.csv
  members.csv
  member_org_relations.csv
  class_learning_bindings.csv
  class_learning_cycles.csv
  learning_plan_versions.csv
  schema_migrations.csv
```

隔离库没有冻结规则或 2026 `CHINA_MAINLAND` 日历时，才追加 SQL 中标记为“可选依赖”的 CSV。不要导出 `learning_credit_entries`。

填写 `manifest.json` 后，使用本地 PowerShell 生成独立清单校验文件：

```powershell
$snapshotDir = 'E:\盛和塾运营系统\seiwajyuku-platform\.codex-tmp\c7-real-inputs\platform-snapshot-吴越三班-2026-08-31_09-04'
$hash = (Get-FileHash -Algorithm SHA256 (Join-Path $snapshotDir 'manifest.json')).Hash.ToLower()
Set-Content -Encoding utf8 (Join-Path $snapshotDir 'manifest.sha256') "$hash  manifest.json"
```

再执行离线校验：

```powershell
Set-Location 'E:\盛和塾运营系统\seiwajyuku-platform\apps\platform-api'
python scripts/validate_c7_authoritative_snapshot.py `
  --snapshot-dir 'E:\盛和塾运营系统\seiwajyuku-platform\.codex-tmp\c7-real-inputs\platform-snapshot-吴越三班-2026-08-31_09-04' `
  --output 'E:\盛和塾运营系统\seiwajyuku-platform\.codex-tmp\c7-real-inputs\platform-snapshot-吴越三班-2026-08-31_09-04\snapshot-preflight.json'
```

该工具没有数据库驱动，不读取环境变量中的数据库配置，且不会生成来源事实、DRY-RUN 或账本记录。`snapshot_preflight_status=PASS` 只表示快照包的来源声明、文件完整性、组织/轮次结构通过；它不是 `REAL_FACT_RECONCILIATION=PASS`。

## 收到快照后的固定流程

1. 先运行上述离线校验；任何 `BLOCKED` 都登记为 `PLATFORM_MASTER_DATA_MISSING` 或相应完整性原因，不能在隔离库补造主档。
2. 在新鲜隔离数据库应用至 `0050`，并导入经过验证的最小快照。
3. 使用同一份吴越三班原始总部 Excel，生成 230 条 observation 和 46 个 source identity。
4. 正常 1–5 组及精进组人员仅在班级、小组、姓名、脱敏账号和已确认绑定均能安全解释时确认身份。姓名本身不是确认依据。
5. 身份确认后，私有生成 `credit-scope.json`（仅 `member_id` 与预期小组 ID），再次运行校验工具的 `--credit-scope-file`，验证每个事实日期都有唯一有效的 `STUDY_CLASS` / `STUDY_GROUP`。
6. 空小组 5 人继续保留观察和身份审计，但其个人积分、班级率分子、班级率分母均为 `false`，不得阻断正常组或精进组。
7. 保持 `LEARNING_CREDIT_SETTLEMENT_ENABLED=false`，执行 C7 DRY-RUN；比较 `learning_credit_entries` 前后数量，必须为零增量。
8. 人工核对正常 1–5 组五天的基准 `39 / 39 = 100%`。精进组不进入班级率；本批精进组未提交不产生完成 DAILY_READING；空小组不产生个人读书学分。

只有身份、历史组织关系、学习轮次、日历、冻结规则、班级率、DRY-RUN 与零账本写入同时通过，才可把 `REAL_FACT_RECONCILIATION` 改为 `PASS`。本包不授权 C7.2、生产迁移、生产部署、补历史学分或开启正式结算。
