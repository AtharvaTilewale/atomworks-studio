import { defineConfig } from "vite";
import type { Plugin } from "vite";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

function patinaeWasmAsset(): Plugin {
  return {
    name: "patinae-wasm-asset",
    enforce: "pre",
    transform(code) {
      if (!code.includes("patinae_web_bg.wasm")) return null;
      return {
        code: code.replace(
          /new\s+URL\(\s*["']patinae_web_bg\.wasm["']\s*,\s*import\.meta\.url\s*\)/g,
          "new URL('patinae_web_bg.wasm', (value => value)(import.meta.url))",
        ),
        map: null,
      };
    },
    closeBundle() {
      copyFileSync(
        resolve("node_modules/@patinae/viewer/dist/patinae_web_bg.wasm"),
        resolve("dist/assets/patinae_web_bg.wasm"),
      );
    },
  };
}

function versionServiceWorker(): Plugin {
  const rawVersion =
    process.env.ANNOTATION_STUDIO_REVISION ?? process.env.ANNOTATION_STUDIO_VERSION ?? "development";
  const version = rawVersion.replace(/[^A-Za-z0-9_.-]/g, "-");
  return {
    name: "version-service-worker",
    closeBundle() {
      const path = resolve("dist/sw.js");
      const source = readFileSync(path, "utf-8");
      if (!source.includes("__BUILD_VERSION__")) {
        throw new Error("service worker build-version placeholder is missing");
      }
      writeFileSync(path, source.replaceAll("__BUILD_VERSION__", version));
    },
  };
}

export default defineConfig({
  plugins: [patinaeWasmAsset(), versionServiceWorker()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
  build: {
    target: "es2022",
  },
});
