import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const browser = await chromium.launch({
  executablePath: "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-gpu-sandbox", "--enable-unsafe-webgpu"],
});

try {
  const context = await browser.newContext({
    viewport: { width: 1000, height: 700 },
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  await page.route(/\/assets\/patinae[^/]*\.js(?:\?.*)?$/, (route) => route.abort("failed"));
  const url = new URL(baseUrl);
  url.searchParams.set("viewer", "patinae");
  await page.goto(url.toString(), { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => document.querySelector("#status")?.textContent?.includes("Renderer initialization failed"),
  );
  await page.waitForFunction(() => document.querySelector("#status")?.textContent?.includes("Ready."));

  assert.equal(await page.locator(".example-load").first().isEnabled(), true);
  await page.locator('[data-menu="studio"] > summary').click();
  await page.locator('.example-load[data-example="ppi"]').click();
  await page.waitForFunction(
    () => document.querySelector("#structure-summary")?.textContent?.includes("Atoms"),
  );
  assert.equal(await page.locator("#validate").isEnabled(), true);

  await page.locator(".load-options > summary").click();
  await page.locator("#parse-assembly").fill("asym");
  await page.locator("#parse-hydrogens").selectOption("remove");
  await page.locator("#file-input").setInputFiles(
    fileURLToPath(new URL("../../examples/mcsa_ph2.cif", import.meta.url)),
  );
  await page.waitForFunction(
    () => document.querySelector("#structure-summary")?.textContent?.includes("mcsa_ph2"),
  );
  assert.match((await page.locator("#status").innerText()), /renderer is unavailable/);
} finally {
  await browser.close();
}
