import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

test("the operations viewport preserves browser zoom", () => {
  const layout = source("app/layout.tsx");
  assert.doesNotMatch(layout, /maximumScale/);
  assert.doesNotMatch(layout, /userScalable/);
});

test("operations mobile navigation uses the shared Sheet primitive", () => {
  const shell = source("components/ops-shell.tsx");
  assert.match(shell, /@\/components\/ui\/sheet/);
  assert.doesNotMatch(shell, /ops-backdrop/);
});

test("knowledge filters and destructive actions use shared accessible primitives", () => {
  const knowledge = source("app\/(ops)\/knowledge\/page.tsx");
  assert.match(knowledge, /@\/components\/ui\/toggle-group/);
  assert.match(knowledge, /@\/components\/ui\/alert-dialog/);
  assert.doesNotMatch(knowledge, /role="tablist"/);
});

test("global styles do not restyle every native button", () => {
  const globals = source("app/globals.css");
  assert.doesNotMatch(globals, /\nbutton \{/);
  assert.doesNotMatch(globals, /\nbutton:hover/);
});
