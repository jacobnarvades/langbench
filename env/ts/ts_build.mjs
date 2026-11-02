// Transpile one TypeScript file to CommonJS JavaScript (out/<name>.js) for stdin/stdout tasks.
// Like syntax_check.mjs this reports syntax errors only; type errors come from tsc in the diag condition.
import ts from "typescript";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { basename } from "node:path";

const f = process.argv[2];
const out = ts.transpileModule(readFileSync(f, "utf8"), {
  fileName: f,
  reportDiagnostics: true,
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
});
for (const d of out.diagnostics ?? []) {
  const pos = d.file && d.start != null ? d.file.getLineAndCharacterOfPosition(d.start) : null;
  console.log(`${pos ? `${f}(${pos.line + 1},${pos.character + 1})` : f}: ${ts.flattenDiagnosticMessageText(d.messageText, "\n")}`);
}
if ((out.diagnostics ?? []).length) process.exit(1);
mkdirSync("out", { recursive: true });
writeFileSync(`out/${basename(f, ".ts")}.js`, out.outputText);
