import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compiled = ts.transpileModule(
  readFileSync(
    new URL("../src/utils/section-loader.ts", import.meta.url),
    "utf8"
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022
    }
  }
).outputText;
const { createSectionLoader } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("a fast section renders without waiting for slow sections", async () => {
  let finish,
    visible = "";
  const run = createSectionLoader().begin();
  const slow = run(
    () =>
      new Promise(resolve => {
        finish = resolve;
      }),
    () => {},
    () => {}
  );
  await run(
    async () => "today",
    value => {
      visible = value;
    },
    () => {}
  );
  assert.equal(visible, "today");
  finish();
  await slow;
});

test("a superseded month cannot overwrite data, errors or loading for the new month", async () => {
  const loader = createSectionLoader();
  let finishOld,
    visible,
    failed = false,
    settled = false;
  const old = loader.begin()(
    () =>
      new Promise(resolve => {
        finishOld = resolve;
      }),
    value => {
      visible = value;
    },
    () => {
      failed = true;
    },
    () => {
      settled = true;
    }
  );
  await loader.begin()(
    async () => "September",
    value => {
      visible = value;
    },
    () => {}
  );
  finishOld("August");
  await old;
  assert.equal(visible, "September");
  assert.equal(failed, false);
  assert.equal(settled, false);
});

test("section failures stay local and settle their own loading state", async () => {
  const run = createSectionLoader().begin();
  let visible = null,
    error = false,
    loading = true;
  await run(
    async () => {
      throw new Error("unavailable");
    },
    value => {
      visible = value;
    },
    () => {
      error = true;
    },
    () => {
      loading = false;
    }
  );
  assert.equal(visible, null);
  assert.equal(error, true);
  assert.equal(loading, false);
});
