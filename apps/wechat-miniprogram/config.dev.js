// Developer Tools only.  Keep the test API separate from config.js so the
// production gateway remains the default for trial/release builds.
module.exports = {
  apiBaseUrl: "http://127.0.0.1:8000",
  environment: "TEST",
};
