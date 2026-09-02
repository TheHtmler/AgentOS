import assert from "node:assert/strict";
import test from "node:test";

import { displayCaseFactContent } from "./cases.ts";

test("补足 Case 事实的字段标签，但不重复已有标签", () => {
  assert.equal(
    displayCaseFactContent({
      id: "fact-1",
      key: "diagnosis_subtype",
      content: "MMUT",
      tags: ["基因诊断"],
      status: "proposed",
    }),
    "诊断分型/基因：MMUT",
  );
  assert.equal(
    displayCaseFactContent({
      id: "fact-2",
      key: "height_cm",
      content: "身高 88 cm",
      tags: ["身高"],
      status: "proposed",
    }),
    "身高 88 cm",
  );
});
