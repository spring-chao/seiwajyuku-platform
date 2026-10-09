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

test("a failed volunteer catalog does not erase loaded appointments, and refresh failure preserves them", async () => {
  const loader = createSectionLoader();
  const run = loader.begin();
  let appointments = [],
    catalogError = false,
    appointmentError = false;
  await Promise.all([
    run(
      async () => {
        throw new Error("catalog unavailable");
      },
      () => {},
      () => {
        catalogError = true;
      }
    ),
    run(
      async () => [{ id: 1, status: "ACTIVE" }],
      value => {
        appointments = value;
      },
      () => {
        appointmentError = true;
      }
    )
  ]);
  assert.equal(catalogError, true);
  assert.equal(appointmentError, false);
  assert.deepEqual(appointments, [{ id: 1, status: "ACTIVE" }]);
  await loader.begin()(
    async () => {
      throw new Error("refresh unavailable");
    },
    value => {
      appointments = value;
    },
    () => {
      appointmentError = true;
    }
  );
  assert.equal(appointmentError, true);
  assert.deepEqual(appointments, [{ id: 1, status: "ACTIVE" }]);
});

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
  const requestError = Object.assign(new Error("unavailable"), {
    code: "ECONNABORTED"
  });
  let visible = null,
    error = false,
    loading = true;
  await run(
    async () => {
      throw requestError;
    },
    value => {
      visible = value;
    },
    failure => {
      assert.equal(failure, requestError);
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
