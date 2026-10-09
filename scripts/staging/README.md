# 隔离签到验收与小程序预览准备

`run_signin_acceptance.py` 启动127.0.0.1:8765/8766两个实际 HTTP handlers，使用独立 SQLite 和合成事务数据。它不连接生产 CloudBase，也不代替手机、真实云持久化或 MySQL CI 验收。每次需要新的输出目录；`--keep-running` 保持服务，Ctrl+C只清理该次启动的子进程。

`prepare_signin_preview.mjs` 只创建小程序预览副本，不上传，不改仓库生产配置，不改变微信安全设置。已有隔离 HTTPS 平台可用时执行：

```powershell
node scripts/staging/prepare_signin_preview.mjs --api-base '<实际隔离HTTPS平台API基础地址>' --output '<仓库外全新输出目录>'
```

基础地址应与小程序请求规则一致，例如网关带`/platform`时保留该前缀。脚本拒绝已知生产平台地址、本机/IP/占位域名、URL凭据/查询、重定向及已有目录；先只读检查公共`/api/v1/system/environment`，再检查`/health`。运行环境必须为dev/test/staging且production及production_mutations_allowed为false，deployment_read_only为false，真实微信绑定、身份授权、签到管理和会员签到均启用，wechat_local_test_mode必须为false。TLS验证保持开启，不发送授权凭据或查询业务数据。

生成目录内`wechat-miniprogram`可导入开发者工具。副本固定`environment=STAGING`及`urlCheck=true`；不带测试目录、隐藏文件、私人开发工具配置。`preview-manifest.json`记录源配置/文件SHA-256、公开运行环境检查和`PREPARED_NOT_UPLOADED`，不会宣称已上传或已完成手机验收。失败副本保留`PREPARATION_FAILED`；不能拿它上传，重试必须换新目录。

公共环境检查无法证明后端数据库或CloudBase连接隔离。上传前仍需核验测试数据库、专用集合命名空间、引擎与反向名册同步地址，以及真实绑定/签到/故障补传。共享标准版环境使用专用测试服务、测试库和集合前缀，仍共享资源配额。脚本没有跳过检查、关闭域名校验、写入密钥或上传选项。所有服务密钥只在测试后端私下注入。

自有域名尚未就绪时，可使用 CloudBase 官方云调用。另传 `--cloudbase-env <已关联的环境>`、`--cloudrun-service sj-signin-stg-YYYYMMDD-xxxxxxxx`、`--engine-function checkinStgYYYYMMDDxxxxxxxx` 和 `--engine-api-base https://<环境网关>/stg_signin_YYYYMMDD_xxxxxxxx/api`。脚本要求服务、函数、网关命名空间匹配，且仍核验专用 HTTPS 平台的真实运行状态。平台请求走 `wx.cloud.callContainer`，预加载的签名签到票据直接走独立引擎 `wx.cloud.callFunction`；后者不发送平台会话。失败不会降级到生产接口或关闭域名校验。预览副本使用独立会话缓存，开发工具和手机连接同一测试服务。正式配置继续使用原请求方式。

无真实隔离地址时，脚本的测试使用临时合成文件和注入的只读transport；这属于准备逻辑测试，不会生成可声称手机可用的现场预览。回归已纳入：

```powershell
node scripts/run_node_tests.mjs miniprogram
node scripts/check_wechat_miniprogram.mjs
```
