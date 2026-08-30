import { existsSync } from "node:fs";

export const chromiumArgs = [
  "--no-sandbox",
  "--disable-gpu-sandbox",
  "--enable-unsafe-webgpu",
];

export function chromiumExecutable() {
  const candidates = [
    process.env.CHROME_PATH,
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ].filter(Boolean);
  const executable = candidates.find((candidate) => existsSync(candidate));
  if (!executable) {
    throw new Error("Chrome or Chromium was not found; set CHROME_PATH to its executable.");
  }
  return executable;
}
