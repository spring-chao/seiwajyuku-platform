// 发布前请把 API 域名加入微信公众平台的“request 合法域名”。
// AppID、AppSecret 只配置在微信平台和后端环境变量中，不写入小程序代码。
module.exports = {
  apiBaseUrl:
    "https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com",
  environment: "TEST",
  sessionStorageKey: "seiwajyuku_member_session",
  homePage: "pages/home/index",
  serviceSubjectName: "学长服务助手",
  privacyContractTitle: "用户隐私保护指引"
};
