import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const require = createRequire(resolve("packages/web/package.json"));
const { chromium } = require("playwright-core");

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const outputDir = resolve("docs/images");
const fixture = resolve("examples/ppi_pdl1.cif");
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-gpu-sandbox", "--enable-unsafe-webgpu"],
});
let embeddedPath;

await mkdir(outputDir, { recursive: true });

async function openStudio(viewer) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
  });
  const page = await context.newPage();
  const url = new URL(baseUrl);
  url.searchParams.set("viewer", viewer);
  await page.goto(url.toString(), { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#status")?.textContent?.includes("Ready."));
  await page.locator("#file-input").setInputFiles(fixture);
  await page.locator("#download").waitFor({ state: "visible", timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector("#download")?.hasAttribute("disabled"));
  await page.waitForTimeout(750);
  return { context, page };
}

async function capture(page, filename) {
  await page.screenshot({
    path: resolve(outputDir, filename),
    animations: "disabled",
  });
}

try {
  {
    const { context, page } = await openStudio("patinae");
    await capture(page, "web-patinae-editor.png");
    await context.close();
  }

  {
    const { context, page } = await openStudio("molstar");
    const sessionId = await page.evaluate(() => sessionStorage.getItem("annotation-studio-session"));
    const session = await page.request.get(new URL(`/api/sessions/${sessionId}`, baseUrl).toString());
    ({ editor_path: embeddedPath } = await session.json());
    await capture(page, "web-molstar-editor.png");

    await page.locator("#atomworks-studio-menu > summary").click();
    await page.waitForFunction(
      () => document.querySelector("#atomworks-studio-menu")?.hasAttribute("open"),
    );
    await page.locator("#annotation-list").scrollIntoViewIfNeeded();
    await capture(page, "web-annotations.png");

    await page.locator("#rfd4-proteina-menu > summary").click();
    await page.waitForFunction(
      () => document.querySelector("#rfd4-proteina-menu")?.hasAttribute("open"),
    );
    await page.locator("#design-card").scrollIntoViewIfNeeded();
    await capture(page, "web-rfd4-panel.png");
    await context.close();
  }

  {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      deviceScaleFactor: 1,
      colorScheme: "dark",
    });
    const page = await context.newPage();
    const url = new URL(embeddedPath, baseUrl);
    url.searchParams.set("viewer", "molstar");
    await page.goto(url.toString(), { waitUntil: "networkidle" });
    await page.waitForFunction(
      () => !document.querySelector("#structure-summary")?.textContent?.includes("No structure"),
    );
    await page.waitForTimeout(750);
    await capture(page, "web-embedded.png");
    await context.close();
  }
} finally {
  await browser.close();
}

console.log(`Captured documentation screenshots in ${outputDir}`);
