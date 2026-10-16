// Usage: node run.mjs job.json > results.json
// job: { cql: "<CQL text>", rules: [names], cases: [ {param: value|null, ...}, ... ] }
// result: { translator_errors: [...], results: [ {rule: true|false|null, ...}, ... ] }
import fs from "node:fs";
import { createRequire } from "node:module";
import { CqlTranslator, LibraryManager, ModelManager, createModelInfoProvider, stringAsSource } from "@cqframework/cql/cql-to-elm";
import cql from "cql-execution";

const require = createRequire(import.meta.url);
const job = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

// The translator ships the System model info as an embedded string; register it for the library manager.
const pkgDir = require.resolve("@cqframework/cql/cql").replace(/[^/]*$/, "");
const bundle = fs.readFileSync(pkgDir + "cql.mjs", "utf8");
const m = bundle.match(/systemModelInfoXml = '((?:[^'\\]|\\.)*)';/);
if (!m) throw new Error("could not locate the System model info in @cqframework/cql");
const systemXml = new Function(`return '${m[1]}'`)();

const mm = new ModelManager();
mm.modelInfoLoader.registerModelInfoProvider(createModelInfoProvider((n) => (n === "System" ? stringAsSource(systemXml) : null)), true);
const tr = CqlTranslator.fromText(job.cql, new LibraryManager(mm));
const errs = [...tr.errors.toArray()].map(String);
if (errs.length) {
  console.log(JSON.stringify({ translator_errors: errs, results: [] }));
  process.exit(0);
}
const lib = new cql.Library(JSON.parse(tr.toJson()));
const codes = new cql.CodeService({});
const results = [];
for (const params of job.cases) {
  const r = await new cql.Executor(lib, codes, params).exec(new cql.PatientSource([]));
  const out = {};
  for (const name of job.rules) out[name] = r.unfilteredResults[name] === undefined ? "undefined" : r.unfilteredResults[name];
  results.push(out);
}
console.log(JSON.stringify({ translator_errors: [], translator_version: "@cqframework/cql 5.4.0", engine: "cql-execution 3.3.2", results }));
