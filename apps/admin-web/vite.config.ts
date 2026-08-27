import { execFileSync } from "node:child_process";
import { getPluginsList } from "./build/plugins";
import { include, exclude } from "./build/optimize";
import {
  type ConfigEnv,
  type Plugin,
  type UserConfigExport,
  loadEnv
} from "vite";
import {
  root,
  alias,
  wrapperEnv,
  pathResolve,
  __APP_INFO__
} from "./build/utils";

const resolveBuildCommit = () => {
  const configuredCommit =
    process.env.VITE_BUILD_COMMIT_SHA || process.env.GITHUB_SHA;
  if (configuredCommit) return configuredCommit;
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8"
    }).trim();
  } catch {
    return "unknown";
  }
};

const buildInfoPlugin = (mode: string): Plugin => ({
  name: "emit-build-info",
  generateBundle() {
    const buildInfo = {
      commit_sha: resolveBuildCommit(),
      build_time:
        process.env.VITE_BUILD_TIME || new Date().toISOString(),
      version:
        process.env.VITE_RELEASE_VERSION ||
        process.env.VITE_APP_VERSION ||
        __APP_INFO__.pkg.version,
      environment: process.env.VITE_BUILD_ENV || mode
    };
    this.emitFile({
      type: "asset",
      fileName: "build-info.json",
      source: `${JSON.stringify(buildInfo, null, 2)}\n`
    });
  }
});

export default ({ mode }: ConfigEnv): UserConfigExport => {
  const { VITE_CDN, VITE_PORT, VITE_COMPRESSION, VITE_PUBLIC_PATH } =
    wrapperEnv(loadEnv(mode, root));
  return {
    base: VITE_PUBLIC_PATH,
    root,
    resolve: {
      alias
    },
    // 服务端渲染
    server: {
      // 端口号
      port: VITE_PORT,
      host: "0.0.0.0",
      // 本地跨域代理 https://cn.vitejs.dev/config/server-options.html#server-proxy
      proxy: {},
      // 预热文件以提前转换和缓存结果，降低启动期间的初始页面加载时长并防止转换瀑布
      warmup: {
        clientFiles: ["./index.html", "./src/{views,components}/*"]
      }
    },
    plugins: [
      ...getPluginsList(VITE_CDN, VITE_COMPRESSION),
      buildInfoPlugin(mode)
    ],
    // https://cn.vitejs.dev/config/dep-optimization-options.html#dep-optimization-options
    optimizeDeps: {
      include,
      exclude
    },
    build: {
      // https://cn.vitejs.dev/guide/build.html#browser-compatibility
      target: "es2015",
      sourcemap: false,
      // 消除打包大小超过500kb警告
      chunkSizeWarningLimit: 4000,
      rollupOptions: {
        input: {
          index: pathResolve("./index.html", import.meta.url)
        },
        // 静态资源分类打包
        output: {
          chunkFileNames: "static/js/[name]-[hash].js",
          entryFileNames: "static/js/[name]-[hash].js",
          assetFileNames: "static/[ext]/[name]-[hash].[ext]"
        }
      }
    },
    define: {
      __INTLIFY_PROD_DEVTOOLS__: false,
      __APP_INFO__: JSON.stringify(__APP_INFO__)
    }
  };
};
