import assert from "node:assert/strict";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const viewerMode = process.env.VIEWER_MODE;
if (viewerMode !== "patinae" && viewerMode !== "molstar") {
  throw new Error("Set VIEWER_MODE=patinae or VIEWER_MODE=molstar");
}
const expectDesignEnabled = process.env.EXPECT_DESIGN_ENABLED === "1";
const runDesignJob = expectDesignEnabled && process.env.RUN_DESIGN_JOB !== "0";
const browserArgs = ["--no-sandbox", "--disable-gpu-sandbox", "--enable-unsafe-webgpu"];

const browser = await chromium.launch({
  executablePath: "/usr/bin/google-chrome",
  headless: true,
  args: browserArgs,
});

try {
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
  assert.deepEqual(await page.locator(".viewer-menu > summary").allTextContents(), [
    "Style", "Select", "Analyze", "AtomWorks Studio", "View", "•••",
  ]);
  assert.deepEqual(await page.locator("#selection-syntax option").allTextContents(), [
    "Patinae", "AtomWorks", "Path",
  ]);
  assert.equal(await page.locator(".drawer-stack > details").count(), 3);
  if (viewerMode === "patinae") {
    assert.match(
      (await page.locator("#viewer").getAttribute("data-picking")) ?? "",
      /^(enabled|software-disabled)$/,
    );
  }
  const manifest = await page.request.get(new URL("/manifest.webmanifest", baseUrl).toString());
  assert.equal((await manifest.json()).name, "AtomWorks Annotation Studio");
  assert.equal((await page.request.get(new URL("/sw.js", baseUrl).toString())).ok(), true);
  assert.equal((await page.request.get(new URL("/atomworks_logo_dark.svg", baseUrl).toString())).ok(), true);
  assert.equal(await page.locator(".eyebrow").first().innerText(), "RFD4-PROTEINA");

  await page.locator("#atomworks-studio-menu > summary").click();
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
  await page.locator("#atomworks-studio-menu > summary").click();
  assert.deepEqual(await page.locator("#pick-scope option").allTextContents(), ["Atom", "Residue", "Chain"]);
  assert.ok(await page.locator(".sequence-dock .sequence-token").count(), "top sequence dock has residues");
  await page.locator(".sequence-dock .sequence-token[data-atoms]").first().click();
  assert.equal(await page.locator(".sequence-dock .sequence-token.selected").count(), 1);
  const selectedBeforeMutation = await page.locator("#selection-count").innerText();
  await page.locator("#atomworks-studio-menu > summary").click();
  assert.equal(await page.locator("#atomworks-studio-menu").getAttribute("open"), "");
  assert.ok(await page.locator("#annotation-list").isVisible(), "AtomWorks annotations are in the Studio menu");
  assert.ok(await page.locator("#design-card").isVisible(), "model submission is in the Studio menu");
  assert.equal(await page.locator("#design-card #submit-design").count(), 1);
  await page.locator("#apply-condition").click();
  await page.waitForFunction(
    () => document.querySelector("#status")?.textContent?.includes("state updated"),
  );
  assert.equal(
    await page.locator("#selection-count").innerText(),
    selectedBeforeMutation,
    "non-structural mutations preserve the active selection",
  );
  await page.locator("#atomworks-studio-menu > summary").click();
  const alternateViewer = viewerMode === "patinae" ? "molstar" : "patinae";
  await page.locator("#viewer-mode").selectOption(alternateViewer);
  await page.waitForFunction(
    (backend) => document.querySelector("#viewer")?.getAttribute("data-backend") === backend,
    alternateViewer,
    { timeout: 30_000 },
  );
  assert.equal(await page.locator("#selection-count").innerText(), selectedBeforeMutation);
  await page.locator("#viewer-mode").selectOption(viewerMode);
  await page.waitForFunction(
    (backend) => document.querySelector("#viewer")?.getAttribute("data-backend") === backend,
    viewerMode,
    { timeout: 30_000 },
  );
  await page.locator("#selection-syntax").selectOption("expression");
  await page.locator("#viewer-command").fill("(atom_id == 1) | (atom_id == 2)");
  await page.locator("#run-viewer-command").click();
  await page.waitForFunction(() => document.querySelector("#selection-count")?.textContent?.startsWith("2 atoms"));
  await page.locator(".viewer-menu > summary").nth(2).click();
  assert.equal(await page.locator("#add-measurement").isEnabled(), true);
  await page.locator("#add-measurement").click();
  await page.waitForFunction(
    () => !document.querySelector("#measurement-list")?.textContent?.includes("No measurements"),
  );
  assert.notEqual((await page.locator("#measurement-list").innerText()).trim(), "");
  await page.locator("#atomworks-studio-menu > summary").click();
  for (const [kind, expression, count] of [
    ["angle", "(atom_id == 1) | (atom_id == 2) | (atom_id == 3)", 3],
    ["dihedral", "(atom_id == 1) | (atom_id == 2) | (atom_id == 3) | (atom_id == 4)", 4],
  ]) {
    await page.locator("#selection-syntax").selectOption("expression");
    await page.locator("#viewer-command").fill(expression);
    await page.locator("#run-viewer-command").click();
    await page.waitForFunction(
      (expected) => document.querySelector("#selection-count")?.textContent?.startsWith(`${expected} atoms`),
      count,
    );
    await page.locator(".viewer-menu > summary").nth(2).click();
    await page.locator("#measurement-kind").selectOption(kind);
    assert.equal(await page.locator("#add-measurement").isEnabled(), true);
    await page.locator("#add-measurement").click();
    await page.waitForFunction(
      (measurementKind) => document.querySelector("#status")?.textContent?.includes(`${measurementKind} measurement added`),
      kind,
    );
    await page.locator("#atomworks-studio-menu > summary").click();
  }
  await page.locator(".viewer-menu > summary").nth(2).click();
  await page.locator('input[name="interaction-type"]').evaluateAll((inputs) => {
    for (const input of inputs) input.checked = input.value === "hbond";
  });
  await page.locator("#inter-chain-only").uncheck();
  await page.locator("#run-analysis").click();
  await page.waitForFunction(
    () => document.querySelector("#status")?.textContent?.includes("Interaction analysis completed"),
    undefined,
    { timeout: 30_000 },
  );
  assert.equal(await page.locator(".interaction-result").count(), 1);
  await page.locator(".interaction-result").click();
  assert.match(await page.locator("#selection-count").innerText(), /^2 atoms/);
  await page.locator("#atomworks-studio-menu > summary").click();
  await page.locator(".viewer-menu > summary").nth(4).click();
  await page.locator('[data-camera="reset"]').click();
  await page.locator(".viewer-menu > summary").nth(4).click();
  await page.locator('[data-background="black"]').click();
  if (viewerMode === "patinae") {
    await page.locator(".viewer-menu > summary").nth(0).click();
    assert.equal(await page.locator('[data-representation="ribbon"]').isEnabled(), true);
    await page.locator('[data-representation="ribbon"]').click();
    await page.locator(".viewer-menu > summary").nth(0).click();
    await page.locator('[data-color="chain"]').click();
    assert.equal(await page.locator("#viewer-command").isEnabled(), true);
    await page.locator("#selection-syntax").selectOption("expression");
    await page.locator("#viewer-command").fill("has_bonds()");
    await page.locator("#run-viewer-command").click();
    await page.waitForFunction(
      () => document.querySelector("#status")?.textContent?.includes("AtomWorks expression query selected"),
    );
    assert.doesNotMatch(await page.locator("#selection-count").innerText(), /^0 atoms/);
    await page.locator("#selection-syntax").selectOption("patinae");
    await page.locator("#viewer-command").fill("show spheres, sele");
    await page.locator("#run-viewer-command").click();
    await page.waitForFunction(() => document.querySelector("#viewer-command")?.value === "");
    assert.doesNotMatch(await page.locator("#status").innerText(), /Command failed/);
  } else {
    assert.equal(await page.locator("#style-menu").isHidden(), true);
    assert.equal(await page.locator("#selection-syntax option").first().isDisabled(), true);
    assert.equal(await page.locator("#viewer-command").isEnabled(), true);
  }
  await page.locator("#atomworks-studio-menu > summary").click();
  assert.ok(await page.locator(".annotation-row").count(), "stored annotations are summarized");
  assert.ok(await page.locator(".annotation-field-row").count() >= 11);
  const selectionBeforeOverlay = await page.locator("#selection-count").innerText();
  await page.locator(".annotation-row").first().click();
  await page.waitForFunction(
    () =>
      !document.querySelector("#clear-annotation-view")?.hasAttribute("disabled") ||
      document.querySelector("#annotation-detail")?.textContent?.includes("Could not inspect annotation"),
  );
  assert.doesNotMatch(await page.locator("#annotation-detail").innerText(), /Could not inspect annotation/);
  assert.equal(await page.locator("#clear-annotation-view").isEnabled(), true);
  assert.equal(
    await page.locator("#selection-count").innerText(),
    selectionBeforeOverlay,
    "annotation overlay does not replace the active selection",
  );
  await page.waitForFunction(
    () => Number(document.querySelector("#viewer")?.getAttribute("data-annotation-atoms")) > 0,
  );
  assert.ok(Number(await page.locator("#viewer").getAttribute("data-annotation-atoms")) > 0);
  assert.ok(
    Number(await page.locator("#viewer").getAttribute("data-annotation-groups")) >= 1,
    "annotation groups render independently from active selection",
  );
  await page.locator("#clear-annotation-view").click();
  await page.waitForFunction(
    () => document.querySelector("#viewer")?.getAttribute("data-annotation-atoms") === "0",
  );
  assert.equal(await page.locator("#viewer").getAttribute("data-annotation-groups"), "0");

  if (runDesignJob) {
    if (!(await page.locator("#atomworks-studio-menu").evaluate((menu) => menu.hasAttribute("open")))) {
      await page.locator("#atomworks-studio-menu > summary").click();
    }
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
  assert.equal(await embedded.locator(".header-logo").isVisible(), true);
  assert.equal(await embedded.locator(".eyebrow").first().innerText(), "RFD4-PROTEINA");
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
