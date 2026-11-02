"""Per-language build / diagnostics / test runners.

Three stages per language, kept deliberately parallel:
  build(ws)       -> (ok, output)  what the language checks before it can run anything
                                   (syntax for Python/TS, full compile for Rust/Go/Java/C++)
  diagnostics(ws) -> output        everything a static checker says: compiler errors AND warnings,
                                   plus pyright (Python) and tsc --strict (TypeScript)
  test(ws)        -> (ok, output)  run the track's full test suite

The experiment's two feedback conditions differ only in what the model is shown when build()
fails and whether diagnostics() output is included; see run.py.
"""
import glob
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / "env"
BUILD_TIMEOUT = 240
TEST_TIMEOUT = 90


class Outcome:
    PASS, TEST_FAIL, BUILD_FAIL, TIMEOUT = "pass", "test_fail", "build_fail", "timeout"


def sh(cmd, cwd, timeout, env=None):
    """Run a command; return (returncode, combined output). returncode None means timeout."""
    full_env = {**os.environ, **(env or {})}
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=full_env, shell=isinstance(cmd, str), errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.replace(str(cwd) + "/", "").replace(str(cwd), ".")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
        return None, out + f"\n[timed out after {timeout}s]"


def touch_all(ws):
    """Give every workspace file a fresh mtime so incremental builds (cargo) never reuse a stale binary."""
    for p in Path(ws).rglob("*"):
        if p.is_file() and "node_modules" not in p.parts and "out" not in p.parts:
            os.utime(p, None)


def files_with(ws, files, suffix):
    return [f for f in files if f.endswith(suffix) and (Path(ws) / f).exists()]


class Python:
    name = "python"

    def build(self, ws, task, worker):
        srcs = files_with(ws, task["solution_files"], ".py")
        rc, out = sh(["python3", "-m", "py_compile", *srcs], ws, BUILD_TIMEOUT)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        srcs = files_with(ws, task["solution_files"], ".py")
        _, out = sh(["pyright", *srcs], ws, BUILD_TIMEOUT)
        return out

    def test(self, ws, task, worker):
        rc, out = sh(["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", *task["test_files"]], ws, TEST_TIMEOUT)
        return rc, out


class Rust:
    name = "rust"

    def _env(self, worker):
        return {"CARGO_TARGET_DIR": str(ENV / "rust" / f"target-{worker}"), "CARGO_TERM_COLOR": "never"}

    def build(self, ws, task, worker):
        rc, out = sh(["cargo", "test", "--no-run", "--quiet", "--message-format=short"], ws, BUILD_TIMEOUT, self._env(worker))
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        # full human-readable diagnostics (errors + warnings), the thing rustc is famous for
        _, out = sh(["cargo", "test", "--no-run", "--quiet"], ws, BUILD_TIMEOUT, self._env(worker))
        return out

    def test(self, ws, task, worker):
        return sh(["cargo", "test", "--quiet", "--", "--include-ignored"], ws, TEST_TIMEOUT, self._env(worker))


class Go:
    name = "go"
    ENVV = {"GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod"}
    _local = None

    def _pin(self, ws):
        """Track go.mod files ask for the newest Go; run on the installed one instead of downloading."""
        if Go._local is None:
            v = subprocess.run(["go", "env", "GOVERSION"], capture_output=True, text=True).stdout.strip()
            Go._local = ".".join(v.removeprefix("go").split(".")[:2])
        mod = Path(ws) / "go.mod"
        txt = mod.read_text()
        lines = [f"go {Go._local}" if l.startswith("go ") else l for l in txt.splitlines()]
        mod.write_text("\n".join(l for l in lines if not l.startswith("toolchain ")) + "\n")

    def build(self, ws, task, worker):
        self._pin(ws)
        rc, out = sh(["go", "test", "-c", "-o", os.devnull, "."], ws, BUILD_TIMEOUT, self.ENVV)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        self._pin(ws)
        _, b = sh(["go", "test", "-c", "-o", os.devnull, "."], ws, BUILD_TIMEOUT, self.ENVV)
        _, v = sh(["go", "vet", "."], ws, BUILD_TIMEOUT, self.ENVV)
        return (b + "\n" + v).strip()

    def test(self, ws, task, worker):
        return sh(["go", "test", "-count=1", "."], ws, TEST_TIMEOUT, self.ENVV)


class Java:
    name = "java"

    @staticmethod
    def jars():
        js = sorted(glob.glob(str(ENV / "java" / "*.jar")))
        if not any("console-standalone" in j for j in js):
            raise RuntimeError("JUnit jars missing: run ./setup_env.sh")
        return js

    def _javac(self, ws, extra=()):
        srcs = [str(p.relative_to(ws)) for p in Path(ws).joinpath("src").rglob("*.java")]
        cp = os.pathsep.join(self.jars())
        shutil.rmtree(Path(ws) / "out", ignore_errors=True)
        return sh(["javac", "-nowarn", *extra, "-d", "out", "-cp", cp, *srcs], ws, BUILD_TIMEOUT)

    def build(self, ws, task, worker):
        rc, out = self._javac(ws)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        _, out = self._javac(ws, ["-Xlint:all"])
        return out

    def test(self, ws, task, worker):
        jars = self.jars()
        console = next(j for j in jars if "console-standalone" in j)
        cp = os.pathsep.join(["out", *[j for j in jars if j != console]])
        return sh(["java", "-jar", console, "execute", "--disable-banner", "--details=tree",
                   "--class-path", cp, "--scan-class-path"], ws, TEST_TIMEOUT)


class TypeScript:
    name = "typescript"
    NM = ENV / "ts" / "node_modules"

    def _link(self, ws):
        nm = Path(ws) / "node_modules"
        if not nm.exists():
            nm.symlink_to(self.NM)

    def build(self, ws, task, worker):
        self._link(ws)
        srcs = files_with(ws, task["solution_files"], ".ts")
        rc, out = sh(["node", str(ENV / "ts" / "syntax_check.mjs"), *srcs], ws, BUILD_TIMEOUT)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        self._link(ws)
        _, out = sh([str(self.NM / ".bin" / "tsc"), "--noEmit", "-p", "."], ws, BUILD_TIMEOUT)
        return out

    def test(self, ws, task, worker):
        self._link(ws)
        return sh([str(self.NM / ".bin" / "jest"), "--ci", "--no-cache", "--rootDir", "."], ws, TEST_TIMEOUT,
                  {"NODE_OPTIONS": "--no-warnings"})


class Cpp:
    name = "cpp"
    FLAGS = ["-std=c++17", "-DEXERCISM_RUN_ALL_TESTS", "-I.", "-Itest"]

    def _catch_main(self, ws):
        """Catch's main() takes ~20s to compile; build it once per catch.hpp version."""
        hdr = Path(ws) / "test" / "catch.hpp"
        key = hashlib.sha1(hdr.read_bytes()).hexdigest()[:12]
        obj = ENV / "cpp" / f"catch-main-{key}.o"
        if not obj.exists():
            obj.parent.mkdir(parents=True, exist_ok=True)
            tmp = obj.with_suffix(f".{os.getpid()}.tmp.o")
            rc, out = sh(["g++", "-std=c++17", "-c", "test/tests-main.cpp", "-o", str(tmp)], ws, BUILD_TIMEOUT)
            if rc != 0:
                raise RuntimeError("could not build Catch main: " + out[-500:])
            os.replace(tmp, obj)
        return str(obj)

    def _compile(self, ws, warn):
        srcs = sorted(str(p.relative_to(ws)) for p in Path(ws).glob("*.cpp"))
        flags = self.FLAGS + (["-Wall", "-Wextra"] if warn else [])
        return sh(["g++", *flags, "-o", "prog", *srcs, self._catch_main(ws)], ws, BUILD_TIMEOUT)

    def build(self, ws, task, worker):
        rc, out = self._compile(ws, warn=False)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        _, out = self._compile(ws, warn=True)
        return out

    def test(self, ws, task, worker):
        if not (Path(ws) / "prog").exists():
            self._compile(ws, warn=False)
        return sh(["./prog"], ws, TEST_TIMEOUT)


RUNNERS = {r.name: r for r in [Python(), Rust(), Go(), Java(), TypeScript(), Cpp()]}


def evaluate(lang, ws, task, worker=0):
    """Build then test. Returns dict(outcome, build_output, test_output)."""
    r = RUNNERS[lang]
    touch_all(ws)
    ok, bout = r.build(ws, task, worker)
    if not ok:
        return {"outcome": Outcome.BUILD_FAIL, "build_output": bout, "test_output": ""}
    rc, tout = r.test(ws, task, worker)
    if rc is None:
        return {"outcome": Outcome.TIMEOUT, "build_output": bout, "test_output": tout}
    return {"outcome": Outcome.PASS if rc == 0 else Outcome.TEST_FAIL, "build_output": bout, "test_output": tout}
