import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../src/api.ts";
import { mutateWithConfirmation, prepareCifDownload } from "../src/studio/authoring-actions.ts";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
test.afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
});

test("save checks the frozen export and cancellation creates no download payload", async () => {
  const requested: string[] = [];
  globalThis.fetch = async (input, init) => {
    requested.push(String(input));
    assert.equal(init?.method, "POST");
    return Response.json({
      cif: "data_snapshot", name: "input", revision: 2,
      issues: [{ severity: "error", check: "res_id_gap", message: "gap in group" }],
    });
  };
  globalThis.window = { confirm: (message: string) => {
    assert.match(message, /\[res_id_gap\] gap in group/);
    assert.match(message, /Save anyway/);
    return false;
  } } as Window & typeof globalThis;
  assert.equal(await prepareCifDownload("one", () => true), null);
  assert.deepEqual(requested, ["/api/v1/sessions/one/export"]);
});

test("save approval returns the validated snapshot without refetching a newer CIF", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({
      cif: "data_checked", name: "checked", revision: 3,
      issues: [{ severity: "error", check: "pipeline_error", message: "invalid" }],
    });
  };
  globalThis.window = { confirm: () => true } as Window & typeof globalThis;
  assert.equal((await prepareCifDownload("one", () => true))?.cif, "data_checked");
  assert.equal(calls, 1);
});

test("valid saves require no override; a switched session discards a pending export", async () => {
  globalThis.fetch = async () => Response.json({ cif: "data_ok", name: "valid", revision: 0, issues: [] });
  globalThis.window = { confirm: () => { throw new Error("unexpected prompt"); } } as Window & typeof globalThis;
  assert.equal((await prepareCifDownload("one", () => true))?.cif, "data_ok");
  assert.equal(await prepareCifDownload("one", () => false), null);
});

test("coordinate confirmation binds the retry to the preview revision", async () => {
  const bodies: unknown[] = [];
  globalThis.fetch = async (_input, init) => {
    bodies.push(JSON.parse(String(init?.body)));
    return bodies.length === 1
      ? Response.json({ detail: {
        code: "target_discard_confirmation_required", lost_target_count: 4, revision: 7,
      } }, { status: 409 })
      : Response.json({ structure: { revision: 8 } });
  };
  globalThis.window = { confirm: (message: string) => {
    assert.match(message, /4 atoms/);
    assert.match(message, /outside the selection/);
    return true;
  } } as Window & typeof globalThis;
  const body = { condition: "coordinate", atom_indices: [0], enabled: true };
  const result = await mutateWithConfirmation("/api/sessions/one/conditions", body, () => true);
  assert.deepEqual(result, { structure: { revision: 8 } });
  assert.deepEqual(bodies, [body, { ...body, allow_target_discard: true, expected_revision: 7 }]);
});

test("cancel and session changes never retry a destructive edit", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: {
      code: "target_discard_confirmation_required", lost_target_count: 2, revision: 0,
    } }, { status: 409 });
  };
  globalThis.window = { confirm: () => false } as Window & typeof globalThis;
  assert.equal(await mutateWithConfirmation("/api/edit", {}, () => true), null);
  assert.equal(calls, 1);
  globalThis.window = { confirm: () => { throw new Error("unexpected prompt"); } } as Window & typeof globalThis;
  assert.equal(await mutateWithConfirmation("/api/edit", {}, () => false), null);
  assert.equal(calls, 2);
});

test("stale confirmation fails instead of silently retrying against newer data", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: calls === 1
      ? { code: "target_discard_confirmation_required", lost_target_count: 2, revision: 0 }
      : { code: "revision_conflict", revision: 1 } }, { status: 409 });
  };
  globalThis.window = { confirm: () => true } as Window & typeof globalThis;
  await assert.rejects(() => mutateWithConfirmation("/api/edit", {}, () => true), ApiError);
  assert.equal(calls, 2);
});
