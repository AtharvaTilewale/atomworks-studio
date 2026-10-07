import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

import { chromiumArgs, chromiumExecutable } from "../../../scripts/browser.mjs";

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const browser = await chromium.launch({
  executablePath: chromiumExecutable(),
  headless: true,
  args: chromiumArgs,
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
  assert.equal(await page.locator("#workspace").getAttribute("data-studio-tab"), "atomworks");
  await page.locator('.example-load[data-example="ppi"]').click();
  await page.waitForFunction(
    () => document.querySelector("#structure-summary")?.textContent?.includes("Atoms"),
  );
  assert.equal(await page.locator("#validate").isEnabled(), true);

  await page.locator(".load-options > summary").click();
  await page.locator("#parse-assembly").fill("asym");
  await page.locator("#parse-hydrogen_policy").selectOption("remove");
  await page.locator("#file-input").setInputFiles(
    fileURLToPath(new URL("../../../examples/mcsa_ph2.cif", import.meta.url)),
  );
  await page.waitForFunction(
    () => document.querySelector("#structure-summary")?.textContent?.includes("mcsa_ph2"),
  );
  assert.match((await page.locator("#status").textContent()), /renderer is unavailable/);
} finally {
  await browser.close();
}
