import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../src/style.css", import.meta.url), "utf8");
const markup = readFileSync(new URL("../index.html", import.meta.url), "utf8");

test("product, infrastructure, and scientific colors remain separate", () => {
  assert.match(styles, /--accent: #9787f7/);
  assert.match(styles, /--institutional: #e2b85c/);
  assert.match(styles, /--gpu: #76b900/);
  assert.match(styles, /\.gpu-status\[data-state="ready"\]/);
  assert.match(styles, /--condition-sequence: #56b4e9/);
  assert.match(styles, /--condition-coordinate: #e69f00/);
  assert.match(styles, /--condition-hotspot: #d55e00/);
  assert.match(styles, /--condition-index: #cc79a7/);
  assert.match(styles, /\.sequence-token\.condition-index/);
  assert.match(markup, /class="muted gpu-status"/);
});

test("VS Code host modes map to semantic tokens", () => {
  assert.match(styles, /body\.host-vscode:is\(\[data-host-theme="light"\]/);
  assert.match(styles, /\[data-host-theme="high-contrast-light"\]/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
});
