// Syntax-only check for TypeScript files (the TS analogue of `python -m py_compile`).
// Type errors are NOT reported here; those come from tsc in the diagnostics condition.
import ts from "typescript";
import { readFileSync } from "node:fs";

let bad = false;
for (const f of process.argv.slice(2)) {
  const out = ts.transpileModule(readFileSync(f, "utf8"), {
    fileName: f,
    reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  });
  for (const d of out.diagnostics ?? []) {
    bad = true;
    const pos = d.file && d.start != null ? d.file.getLineAndCharacterOfPosition(d.start) : null;
    const loc = pos ? `${f}(${pos.line + 1},${pos.character + 1})` : f;
    console.log(`${loc}: ${ts.flattenDiagnosticMessageText(d.messageText, "\n")}`);
  }
}
process.exit(bad ? 1 : 0);
