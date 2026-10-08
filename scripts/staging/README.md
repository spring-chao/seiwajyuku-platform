# 隔离签到验收与小程序预览准备

`run_signin_acceptance.py` 启动127.0.0.1:8765/8766两个实际 HTTP handlers，使用独立 SQLite 和合成事务数据。它不连接生产 CloudBase，也不代替手机、真实云持久化或 MySQL CI 验收。每次需要新的输出目录；`--keep-running` 保持服务，Ctrl+C只清理该次启动的子进程。

`prepare_signin_preview.mjs` 只创建小程序预览副本，不上传，不改仓库生产配置，不改变微信安全设置。已有隔离 HTTPS 平台可用时执行：

```powershell
node scripts/staging/prepare_signin_preview.mjs --api-base '<实际隔离HTTPS平台API基础地址>' --output '<仓库外全新输出目录>'
```

基础地址应与小程序请求规则一致，例如网关带`/platform`时保留该前缀。脚本拒绝已知生产平台地址、本机/IP/占位域名、URL凭据/查询、重定向及已有目录；先只读检查公共`/api/v1/system/environment`，再检查`/health`。运行环境必须为dev/test/staging且production及production_mutations_allowed为false，deployment_read_only为false，真实微信绑定、身份授权、签到管理和会员签到均启用，wechat_local_test_mode必须为false。TLS验证保持开启，不发送授权凭据或查询业务数据。

生成目录内`wechat-miniprogram`可导入开发者工具。副本固定`environment=STAGING`及`urlCheck=true`；不带测试目录、隐藏文件、私人开发工具配置。`preview-manifest.json`记录源配置/文件SHA-256、公开运行环境检查和`PREPARED_NOT_UPLOADED`，不会宣称已上传或已完成手机验收。失败副本保留`PREPARATION_FAILED`；不能拿它上传，重试必须换新目录。

公共环境检查无法证明后端数据库或CloudBase连接独立。上传前仍需核验用户指定的独立CloudBase测试env、独立数据库、引擎与反向名册同步地址、两个HTTPS主机的微信request合法域名，以及真实绑定/签到/故障补传。脚本没有跳过检查、关闭域名校验、写入密钥或上传选项。所有服务密钥只在隔离后端私下注入。

无真实隔离地址时，脚本的测试使用临时合成文件和注入的只读transport；这属于准备逻辑测试，不会生成可声称手机可用的现场预览。回归已纳入：

```powershell
node scripts/run_node_tests.mjs miniprogram
node scripts/check_wechat_miniprogram.mjs
```
