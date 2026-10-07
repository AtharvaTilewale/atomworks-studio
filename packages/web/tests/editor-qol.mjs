import assert from "node:assert/strict";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { chromiumArgs, chromiumExecutable } from "../../../scripts/browser.mjs";

const baseUrl = process.env.ANNOTATION_STUDIO_URL ?? "http://127.0.0.1:8000/";
const backend = process.env.VIEWER_MODE;
assert.ok(["molstar", "patinae"].includes(backend));
const browser = await chromium.launch({ executablePath: chromiumExecutable(), args: chromiumArgs, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${baseUrl}?viewer=${backend}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#status")?.textContent?.includes("Ready."));
  const upload = page.waitForResponse((r) => r.url().endsWith("/sessions") && r.request().method() === "POST");
  await page.locator("#file-input").setInputFiles(fileURLToPath(new URL("../../../tests/fixtures/SAM_hbonds.cif", import.meta.url)));
  const loaded = await (await upload).json();
  const session = loaded.session_id;
  await page.waitForFunction(() => document.querySelector("#structure-summary")?.textContent?.includes("66"));
  assert.equal(await page.locator("#workspace").getAttribute("data-studio-tab"), "atomworks");

  async function select(expression, count) {
    await page.locator("#selection-syntax").selectOption("expression");
    await page.locator("#viewer-command").fill(expression);
    await page.locator("#run-viewer-command").click();
    await page.waitForFunction((count) => document.querySelector("#selection-count")?.textContent?.startsWith(`${count} atom`), count);
  }
  async function edit(button, endpoint) {
    const response = page.waitForResponse((r) => r.url().endsWith(endpoint) && r.request().method() === "POST");
    await page.locator(button).click();
    const result = await response;
    assert.equal(result.status(), 200, await result.text());
    await page.waitForFunction(() => !document.querySelector("#viewer-mode")?.disabled);
    return result.json();
  }
  async function summary() {
    return (await (await page.request.get(`${baseUrl}api/v1/sessions/${session}`)).json()).structure;
  }

  await page.locator("#editor-tab-annotations").click();
  await page.locator("#condition").selectOption("index_group");
  await select("atom_id == 1", 1);
  const created = await edit("#group-new", "/groups");
  const group = created.edit_result.group_id;
  assert.match(await page.locator("#selection-count").innerText(), /^0 atoms/);
  await page.locator("#condition-group").selectOption(String(group));
  assert.match(await page.locator("#condition-group option:checked").innerText(), /1 atoms/);
  await select("atom_id == 2", 1);
  await edit("#group-add", "/groups");
  assert.match(await page.locator("#condition-group option:checked").innerText(), /2 atoms/);
  await page.locator("#annotation-list > .annotation-row").filter({ hasText: "Index Group" }).click();
  await page.waitForFunction(() => Number(document.querySelector("#viewer")?.dataset.annotationAtoms) > 0);
  await select("atom_id == 3", 1);
  await edit("#group-add", "/groups");
  assert.match(await page.locator("#selection-count").innerText(), /^0 atoms/);
  const inspected = (await (await page.request.get(`${baseUrl}api/v1/sessions/${session}/annotations/index_group`)).json()).annotation;
  assert.equal(Number(await page.locator("#viewer").getAttribute("data-annotation-atoms")),
    new Set(inspected.values.flatMap((value) => value.atom_indices)).size);
  await edit("#group-delete", "/groups");
  assert.ok(!(await summary()).condition_editors.groups.index_group.some((entry) => entry.id === group));
  await edit("#undo", "/undo");
  assert.ok((await summary()).condition_editors.groups.index_group.some((entry) => entry.id === group));

  await page.locator("#condition").selectOption("chain_centroid");
  const centroids = (await summary()).condition_editors.centroids.chain_centroid;
  const chain = centroids.find((entry) => entry.current)?.chain;
  assert.ok(chain);
  await page.locator("#centroid-chain").selectOption(chain);
  await page.locator("#centroid-fill").click();
  const values = await Promise.all(["x", "y", "z"].map((axis) => page.locator(`#condition-value-${axis}`).inputValue()));
  assert.ok(values.every((value) => Number.isFinite(Number(value))));
  await edit("#centroid-set", "/centroids");
  assert.equal(await page.locator("#viewer").getAttribute("data-centroid-markers"), "1");
  assert.deepEqual((await summary()).condition_editors.centroids.chain_centroid.find((entry) => entry.chain === chain).target
    .map((value) => value.toFixed(3)), values);
  if (process.env.ANNOTATION_SCREENSHOT_DIR) {
    await page.screenshot({ path: resolve(process.env.ANNOTATION_SCREENSHOT_DIR, `${backend}-centroid.png`) });
  }
  await page.locator("#centroid-markers").uncheck();
  await page.waitForFunction(() => document.querySelector("#viewer")?.dataset.centroidMarkers === "0");
  await page.locator("#centroid-markers").check();
  await page.waitForFunction(() => document.querySelector("#viewer")?.dataset.centroidMarkers === "1");
  const field = (await summary()).stored_fields.find((entry) => /centroid/i.test(entry.name));
  assert.ok(field);
  await page.locator("#annotation-list details > summary").click();
  await page.locator(".annotation-field-row").filter({ hasText: field.label }).click();
  await page.waitForFunction(() => document.querySelector("#annotation-detail")?.textContent?.includes("Raw field preview"));
  await edit("#undo", "/undo");
  assert.doesNotMatch(await page.locator("#status").textContent(), /Error:/);
  assert.equal(await page.locator("#viewer").getAttribute("data-centroid-markers"), "0");
  await edit("#centroid-set", "/centroids");
  await edit("#centroid-clear", "/centroids");
  assert.equal(await page.locator("#viewer").getAttribute("data-centroid-markers"), "0");

  await page.locator("#condition").selectOption("distance");
  await select("(atom_id == 1) | (atom_id == 2)", 2);
  await page.locator("#pair-use-selection").click();
  await page.waitForFunction(() => document.querySelector("#pair-atom-0")?.textContent?.includes("/"));
  const distanceResponse = page.waitForResponse((r) => r.url().includes("/atom-distance?"));
  await page.locator("#pair-actual").click();
  const distance = (await (await distanceResponse).json()).distance;
  await page.waitForFunction((distance) => document.querySelector("#pair-distance")?.value === distance.toFixed(2), distance);
  await page.locator("#pair-add").click();
  assert.equal(await page.locator("#pair-table tbody tr").count(), 1);
  assert.equal((await summary()).condition_editors.restraints.length, 0, "pair editing is staged until Apply");
  let releaseApply;
  let applyReceived;
  const applying = new Promise((resolve) => { applyReceived = resolve; });
  const release = new Promise((resolve) => { releaseApply = resolve; });
  await page.route("**/distance-restraints/replace", async (route) => {
    const response = await route.fetch(); applyReceived(); await release;
    await route.fulfill({ response });
  });
  const applied = edit("#pair-apply", "/distance-restraints/replace");
  await applying;
  for (const id of ["pair-add", "pair-clear", "pair-reload", "pair-apply"]) {
    assert.equal(await page.locator(`#${id}`).isDisabled(), true, `${id} is disabled while Apply is pending`);
  }
  releaseApply(); await applied;
  await page.unroute("**/distance-restraints/replace");
  assert.equal((await summary()).condition_editors.restraints.length, 1);
  await page.locator("#pair-table tbody button").first().click();
  assert.match(await page.locator("#selection-count").innerText(), /^2 atoms/);
  await page.locator("#pair-delete").click();
  await edit("#pair-apply", "/distance-restraints/replace");
  assert.equal((await summary()).condition_editors.restraints.length, 0);
  await edit("#undo", "/undo");
  assert.equal(await page.locator("#pair-table tbody tr").count(), 1);
  await page.locator("#pair-table tbody button").first().click();
  await page.locator("#pair-delete").click();
  await edit("#undo", "/undo");
  assert.match(await page.locator("#pair-draft-status").innerText(), /structure changed/);
  assert.equal(await page.locator("#pair-apply").isDisabled(), true);
  await page.locator("#pair-reload").click();
  assert.equal(await page.locator("#pair-apply").isDisabled(), false);

  await page.locator(".load-options > summary").click();
  await page.locator("#parse-preset").selectOption("rcsb");
  assert.equal(await page.locator("#parse-add_missing_atoms").isChecked(), true);
  await page.locator("#parse-preset").selectOption("minimal");
  assert.equal(await page.locator("#parse-add_missing_atoms").isChecked(), false);
  assert.equal(await page.locator("#parse-load_standard_annotations").isChecked(), true);
  await page.locator(".load-options > summary").click();
  assert.deepEqual(errors, []);
  if (process.env.ANNOTATION_SCREENSHOT_DIR) {
    await page.screenshot({ path: resolve(process.env.ANNOTATION_SCREENSHOT_DIR, `${backend}-qol.png`) });
  }
  console.log(`Editor QoL passed (${backend}): group actions, persistent colors, centroids/markers, pair staging, loading presets.`);
} finally { await browser.close(); }
