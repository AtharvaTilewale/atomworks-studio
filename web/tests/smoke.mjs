import assert from "node:assert/strict";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const viewerMode = process.env.VIEWER_MODE;
if (viewerMode !== "patinae" && viewerMode !== "molstar") {
  throw new Error("Set VIEWER_MODE=patinae or VIEWER_MODE=molstar");
}
const expectDesignEnabled = process.env.EXPECT_DESIGN_ENABLED === "1";
const browserArgs = ["--no-sandbox", "--disable-gpu-sandbox", "--enable-unsafe-webgpu"];

const browser = await chromium.launch({
  executablePath: "/usr/bin/google-chrome",
  headless: true,
  args: browserArgs,
});

try {
  const choicePage = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  await choicePage.goto(baseUrl, { waitUntil: "networkidle" });
  await choicePage.waitForFunction(() =>
    document.querySelector("#status")?.textContent?.includes("neither renderer is selected"),
  );
  assert.equal(await choicePage.locator("#viewer").getAttribute("data-backend"), null);
  assert.deepEqual(await choicePage.locator("#viewer-mode option").allTextContents(), [
    "Choose Patinae or Mol*",
    "Patinae · WebGPU",
    "Mol* · WebGL",
  ]);
  assert.equal(await choicePage.locator('[data-example="ppi"]').isEnabled(), true);
  assert.equal(await choicePage.locator("#renderer-prompt").isVisible(), true);
  await choicePage.locator('[data-example="ppi"]').click();
  assert.equal(
    await choicePage.evaluate(() => sessionStorage.getItem("annotation-studio-pending-example")),
    "ppi",
  );
  assert.match(await choicePage.locator("#status").innerText(), /Choose Patinae or Mol\* to load PPI/);
  await choicePage.evaluate(() => sessionStorage.removeItem("annotation-studio-pending-example"));
  await choicePage.close();

  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const studioUrl = new URL(baseUrl);
  studioUrl.searchParams.set("viewer", viewerMode);
  await page.goto(studioUrl.toString(), { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#status")?.textContent?.includes("Ready."));
  await page.waitForFunction(
    () => !document.querySelector("#design-availability")?.textContent?.includes("Checking"),
  );
  assert.match(
    (await page.locator("#design-availability").textContent()) ?? "",
    expectDesignEnabled ? /ready/ : /not configured/,
  );
  assert.ok(await page.locator("#viewer canvas").isVisible(), "molecular viewer canvas is visible");
  assert.equal(
    await page.locator("#viewer").getAttribute("data-backend"),
    viewerMode,
  );
  if (viewerMode === "patinae") {
    assert.match(
      (await page.locator("#viewer").getAttribute("data-picking")) ?? "",
      /^(enabled|software-disabled)$/,
    );
  }
  const manifest = await page.request.get(new URL("/manifest.webmanifest", baseUrl).toString());
  assert.equal((await manifest.json()).name, "AtomWorks Annotation Studio");
  assert.equal((await page.request.get(new URL("/sw.js", baseUrl).toString())).ok(), true);

  assert.equal(await page.locator(".example-load").count(), 3);
  assert.equal(await page.locator('[data-example="ppi"]').isEnabled(), true);
  if (viewerMode === "molstar") {
    await page.locator('[data-example="ppi"]').click();
    await page.waitForFunction(
      () => document.querySelector("#status")?.textContent?.includes("Loaded PPI"),
      undefined,
      { timeout: 30_000 },
    );
    assert.match(await page.locator("#structure-summary").innerText(), /hotspot: 6/);
    assert.equal(await page.locator(".sequence-segment").innerText(), "[50–120]");
    await page.locator(".sequence-segment").click();
    await page.waitForFunction(() => document.querySelector(".sequence-segment")?.classList.contains("realized"));
    assert.match(await page.locator(".sequence-segment").innerText(), /^\d+×X$/);
    await page.locator(".sequence-segment").click();
    await page.waitForFunction(() => !document.querySelector(".sequence-segment")?.classList.contains("realized"));
    assert.equal(await page.locator(".sequence-segment").innerText(), "[50–120]");
    const selectedMetrics = await page.locator("#design-metrics").evaluate(
      (select) => [...select.selectedOptions].map((option) => option.value),
    );
    assert.ok(
      selectedMetrics.includes("hotspot_conditioning"),
      "the PPI example selects its available hotspot metric preset",
    );
    assert.equal(await page.locator("#search-reward-tag").inputValue(), "interchain_interactions");
    assert.equal(
      await page.locator("#search-reward").inputValue(),
      "interchain_interactions.hbond_atom_pair_count_coi",
    );

    await page.locator('[data-example="mcsa"]').click();
    await page.waitForFunction(
      () => document.querySelector("#status")?.textContent?.includes("Loaded MCSA"),
      undefined,
      { timeout: 30_000 },
    );
    assert.match(await page.locator("#structure-summary").innerText(), /coordinate: 19/);
    assert.equal(await page.locator("#search-reward-tag").inputValue(), "design");
    assert.equal(await page.locator("#search-reward").inputValue(), "clash_metrics.frac_atoms_clashing_1p5A");
    assert.equal(await page.locator("#search-weight").inputValue(), "-1");

    await page.locator('[data-example="disco"]').click();
    await page.waitForFunction(
      () => document.querySelector("#status")?.textContent?.includes("Loaded DISCO"),
      undefined,
      { timeout: 30_000 },
    );
    assert.match(await page.locator("#structure-summary").innerText(), /sequence: 1/);
  }

  await page.locator("#file-input").setInputFiles(
    resolve("../../../data/benchmarks/SAM_hbonds.cif"),
  );
  await page.waitForFunction(
    () => document.querySelector("#structure-summary")?.textContent?.includes("66"),
    undefined,
    { timeout: 30_000 },
  );

  const status = await page.locator("#status").innerText();
  assert.match(status, /Native AtomWorks session ready/);
  assert.match(status, /(Patinae|Mol\*) loaded 66 atoms; lossless WASM found 9 annotation categories/);
  assert.match(await page.locator("#structure-summary").innerText(), /66/);
  assert.equal(await page.locator("#download").isEnabled(), true);
  assert.equal(await page.locator("#submit-design").isDisabled(), !expectDesignEnabled);
  assert.ok(await page.locator(".annotation-row").count(), "stored annotations are summarized");
  assert.equal(await page.locator(".annotation-field-row").count(), 11);
  await page.locator(".annotation-row").first().click();
  await page.waitForFunction(
    () =>
      !document.querySelector("#clear-annotation-view")?.hasAttribute("disabled") ||
      document.querySelector("#annotation-detail")?.textContent?.includes("Could not inspect annotation"),
  );
  assert.doesNotMatch(await page.locator("#annotation-detail").innerText(), /Could not inspect annotation/);
  assert.equal(await page.locator("#clear-annotation-view").isEnabled(), true);
  await page.waitForFunction(
    () => Number(document.querySelector("#viewer")?.getAttribute("data-annotation-atoms")) > 0,
  );
  assert.equal(await page.locator("#viewer").getAttribute("data-annotation-atoms"), "10");
  assert.ok(
    Number(await page.locator("#viewer").getAttribute("data-annotation-groups")) >= 2,
    "multi-value annotations render as separate color groups",
  );
  await page.locator("#clear-annotation-view").click();
  await page.waitForFunction(
    () => document.querySelector("#viewer")?.getAttribute("data-annotation-atoms") === "0",
  );
  assert.equal(await page.locator("#viewer").getAttribute("data-annotation-groups"), "0");

  if (expectDesignEnabled) {
    await page.locator("#design-card > summary").click();
    await page.locator("#design-count").fill("1");
    await page.locator("#submit-design").click();
    await page.waitForFunction(
      () => document.querySelector("#design-job-status")?.textContent?.includes("Completed"),
      undefined,
      { timeout: 30_000 },
    );
    assert.equal(await page.locator(".document-button").count(), 2);
    await page.waitForFunction(
      () => document.querySelector("#design-metric-summary")?.textContent?.includes("plddt coi"),
    );
    assert.match(await page.locator("#design-metric-summary").innerText(), /91.2/);
    assert.equal(await page.locator("#design-position").innerText(), "2 / 2");
    await page.locator("#previous-design").click();
    await page.waitForFunction(
      () => document.querySelector("#structure-summary")?.textContent?.includes("SAM_hbonds"),
    );
    assert.equal(await page.locator("#design-position").innerText(), "1 / 2");
    await page.locator("#next-design").click();
    await page.waitForFunction(() => document.querySelector("#design-position")?.textContent === "2 / 2");
    await page.locator("#align-design").click();
    await page.waitForFunction(
      () => document.querySelector("#design-job-status")?.textContent?.includes("Aligned"),
    );
    assert.match(await page.locator("#design-metric-summary").innerText(), /alignment · rmsd after/i);
    await page.locator("#previous-design").click();
  }

  const sessionId = await page.evaluate(() => sessionStorage.getItem("annotation-studio-session"));
  assert.ok(sessionId);
  const sessionResponse = await page.request.get(new URL(`/api/sessions/${sessionId}`, baseUrl).toString());
  assert.equal(sessionResponse.ok(), true);
  const { editor_path: editorPath } = await sessionResponse.json();
  assert.match(editorPath, /^\//);
  const embedded = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const embeddedUrl = new URL(editorPath, baseUrl);
  embeddedUrl.searchParams.set("viewer", viewerMode);
  await embedded.goto(embeddedUrl.toString(), { waitUntil: "networkidle" });
  await embedded.waitForFunction(() => document.querySelector("#structure-summary")?.textContent?.includes("66"));
  assert.equal(await embedded.locator("body").getAttribute("class"), "embedded");
  await embedded.close();
  const host = await browser.newPage();
  await host.goto(baseUrl);
  await host.setContent("<!doctype html><body></body>");
  const readyMessage = await host.evaluate(
    ({ editorPath, origin, viewerMode }) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("embedded editor did not notify its host")), 15_000);
        window.addEventListener("message", (event) => {
          if (event.data?.source !== "atomworks-annotation-studio") return;
          clearTimeout(timer);
          resolve(event.data);
        });
        const url = new URL(editorPath, origin);
        url.searchParams.set("parent_origin", origin);
        url.searchParams.set("viewer", viewerMode);
        const frame = document.createElement("iframe");
        frame.src = url.toString();
        document.body.append(frame);
      }),
    { editorPath, origin: new URL(baseUrl).origin, viewerMode },
  );
  assert.deepEqual(readyMessage, {
    source: "atomworks-annotation-studio",
    type: "ready",
    session_id: sessionId,
  });
  await host.close();

  await page.locator("#combine-input").setInputFiles(
    resolve("../../../data/benchmarks/SAM_hbonds.cif"),
  );
  await page.waitForFunction(
    () => document.querySelector("#status")?.textContent?.includes("Combine"),
    undefined,
    { timeout: 30_000 },
  );
  assert.match(await page.locator("#status").innerText(), /Combined SAM_hbonds.cif/);
  assert.match(await page.locator("#structure-summary").innerText(), /132/);
  assert.deepEqual(errors, []);
  console.log(
    `Browser smoke passed (${viewerMode}): viewer, deep-link embedding, annotations, and AtomWorks stayed in sync.`,
  );
} finally {
  await browser.close();
}
