# 签到管理前端

运营菜单入口为「活动与学习 → 签到现场管理」，路由 `/operations/checkin-management`。现有 `/operations/activities` 继续展示同步后的活动统计、签到明细、真实迟到与学分；有 `attendance:view` 权限的账号可以从统计页进入管理工作区。

## 操作流程

1. 创建普通活动时填写名称、日期、类型、签到时间，并选择明确的所属组织 ID。先创建空名单活动，再在开始签到前通过 Excel 追加报名名额。班级学习会从可信班级名单创建上午、下午、晚上空巴三个独立场次；小组学习会根据选定班级和小组 ID 由引擎再次读取可信名单。
2. 在活动列表选择活动，在现场工作区切换具体场次。每场都有独立 `event_id`，上午签到不能替代下午或空巴签到。
3. 通过工作区确认举办、退回草稿、取消活动，或开放/关闭当前场次签到。取消活动作用于同一活动组的全部场次。开放仍受引擎的活动状态和时间窗口限制。
4. 现场分别查看全部报名、已签到、未签到、预计迟到、请假、外班学长和团队名额。原报名联系人与实际到场人分列展示。本班签到率与现场真实人数分别展示；外班参加保留原班级。
5. 对未签到记录可标记预计迟到、请假或恢复待签到。晚上空巴不设置预计迟到。人工签到只适用于已确认且开放的场次；需要确认实际参加人的团队位不能以联系人直接人工签到。已签到记录不可删除。
6. 临时报名使用原签到引擎兼容流程，不修改学员组织关系。正式班级、小组名单来自运营平台组织 ID，Excel 不替代可信班级/小组名单。班级名单对账保留创建快照；增补同步仅作用于本场及后续未开始场次，不删除或覆盖已有报名与签到事实。
7. 为当前具体场次生成小程序码。默认开发版用于开发成员验收，可切换体验版或正式版。正式版需要签到页面已审核发布。每个场次分别生成、预览和下载，二维码只携带服务端短 token，不包含姓名或手机号。
8. 用「导出 Excel」导出当前场次明细。导出同时保留实际参加人、原报名人、身份和签到时间；不导出跟进备注，联系方式仅使用服务端已脱敏字段。

## 权限与 API

客户端只连接运营平台的明确管理操作，不保存或使用旧签到管理员密码、引擎密钥。所有权限和组织范围仍由平台 API 和签到引擎再次校验。

| 权限 | 管理能力 |
| --- | --- |
| `attendance:view` | 活动列表、详情、现场统计、组织选项、名单对账 |
| `attendance:create` | 创建普通活动、三场班级学习会 |
| `attendance:update` | 活动信息修改、确认举办、退回草稿 |
| `attendance:manage` | 开放/关闭、取消、临时报名、删除未签到报名、人工签到 |
| `attendance:status` | 预计迟到、请假、恢复待签到 |
| `attendance:import` | 读取名单、组织名单增补、Excel 追加预览与确认 |
| `attendance:export` | 当前场次 Excel 导出 |
| `attendance:code` | 生成和下载当前场次小程序码 |

管理操作请求为 `POST /api/v1/attendance/manage/{operation}`，请求体使用现有签到引擎参数，响应使用引擎原形 `{ok, ...}`，不额外包 `data`。二维码为 `POST /api/v1/attendance/manage/events/{event_id}/code`，响应 `{success, data: {image_base64, mime_type, event_id, token, page, env_version}}`。

`event_detail.rows` 每个报名位一行，使用 `registration_id`、`registered_name`、`actual_attendee_name`、`checked`、`checked_at`、`attendance_status`、`attendance_role`、`is_team`。客户端不通过姓名合并记录、不推断组织关系、不定义第二套签到事实规则。

Excel 追加使用 `import_preview → import_apply`。预览绑定活动和名单当前状态；页面也记录名单指纹，名单变化后不能继续使用旧预览提交。`added` 是本次追加名额，`new_total` 是追加后总报名名额。

## 复用来源

`src/utils/attendanceImportParser.mjs` 直接迁入 `spring-chao/signin/public/admin.html` 已验证的 `detectColumns`、`findHeaderRow`、手机号清理、名单质量统计和行解析算法，仅改为模块导出并适配 Vue 文件读取。表头从前 100 行寻找，列顺序不限；手机号缺失或异常只作提示，重复报名保留独立名额。

SheetJS 使用与旧管理页一致的 `0.20.2`，从原官方 CDN tarball 锁定版本及 SHA-512 完整性，作为按需加载的本地构建 chunk，现场导入和导出不再依赖浏览器临时访问 CDN。

## 验证

在 `apps/admin-web` 执行：

```text
pnpm typecheck
pnpm test
pnpm build:staging
```

2026-10-08 最终源码检查：类型检查通过；8 个管理端测试文件、26 项测试全部通过，其中本次新增 10 项行为测试；staging 构建通过，2147 个模块、59.02 秒、产物约 3.51 MB。

行为测试实际执行页面处理函数和 API 客户端，覆盖 Excel 表头与重复位、可信组织失败关闭、各现场筛选、北京时间、活动快速切换丢弃旧响应、名单变化阻止复用预览、缺组织不提交创建、姓名编辑不更改既有组织/时间以及二维码绑定具体场次。

证据摘要见 `verification/signin-management-20261008.json`。本地视觉验收必须通过 `VITE_API_BASE_URL` 显式连接隔离服务；仓库预置 `.env.staging` 当前不是本次新建的专用 staging 地址，不用该配置启动浏览器验收生产数据。本次源码和构建操作不发布应用、不投放线上二维码、不关闭旧入口。
