"""Build the matched task set: Exercism practice exercises present in every track.

For each (exercise, language) this writes tasks/<slug>/<lang>/ containing:
  workspace/   the exercise as a student gets it (stubs + tests), tests un-skipped
  reference/   the track's reference solution, laid out at the solution paths
  task.json    instructions, solution file list, test file list

Usage: python prepare_tasks.py --tracks tracks --out tasks
"""
import argparse
import json
import re
import shutil
from pathlib import Path

LANGS = ["python", "rust", "go", "java", "typescript", "cpp"]
SKIP_DIRS = {".meta", ".approaches", ".articles", ".docs", ".vscode", "node_modules", "gradle"}
SKIP_FILES = {"gradlew", "gradlew.bat", ".yarnrc.yml", "yarn.lock", "eslint.config.mjs", "test-runner.mjs", ".gitignore"}


def unskip(lang: str, text: str) -> str:
    """Exercism skips all but the first test so students go one at a time. Run them all."""
    if lang == "java":
        text = re.sub(r"^\s*@Disabled\(.*?\)\s*$\n?", "", text, flags=re.M)
    elif lang == "typescript":
        text = re.sub(r"\bxit\(", "it(", text)
        text = re.sub(r"\bxdescribe\(", "describe(", text)
        text = re.sub(r"\b(it|test|describe)\.skip\(", r"\1(", text)
    # rust: handled with --include-ignored; cpp: handled with -DEXERCISM_RUN_ALL_TESTS
    return text


def map_examples(solution: list[str], example: list[str], src: Path) -> dict[str, str]:
    """Map each reference file to the path it replaces: same basename first, then same extension."""
    out, used = {}, set()
    for ex in example:
        name, ext = Path(ex).name, Path(ex).suffix
        by_name = [s for s in solution if Path(s).name == name and s not in used]
        by_ext = [s for s in solution if Path(s).suffix == ext and s not in used
                  and not any(Path(e).name == Path(s).name for e in example if e != ex)]
        if by_name or by_ext:
            slot = (by_name or by_ext)[0]
        else:
            # helper file shipped with the exercise (e.g. a Java enum); replace it in place
            hits = [p for p in src.rglob(name) if ".meta" not in p.parts]
            if hits:
                slot = str(hits[0].relative_to(src))
            else:
                # extra file the reference adds: put it next to the main solution file
                anchor = next(s for s in solution if Path(s).suffix == ext)
                slot = str(Path(anchor).parent / name)
        out[ex] = slot
        used.add(slot)
    return out


def instructions(ex_dir: Path) -> str:
    parts = []
    for name in ["introduction.md", "instructions.md", "instructions.append.md"]:
        p = ex_dir / ".docs" / name
        if p.exists():
            parts.append(p.read_text())
    return "\n\n".join(parts).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="tracks")
    ap.add_argument("--out", default="tasks")
    args = ap.parse_args()
    tracks, out = Path(args.tracks), Path(args.out)

    slug_sets = [{p.name for p in (tracks / l / "exercises" / "practice").iterdir() if p.is_dir()} for l in LANGS]
    common = sorted(set.intersection(*slug_sets))
    print(f"{len(common)} exercises shared by all {len(LANGS)} tracks")

    manifest = []
    for slug in common:
        for lang in LANGS:
            src = tracks / lang / "exercises" / "practice" / slug
            cfg = json.loads((src / ".meta" / "config.json").read_text())["files"]
            sol, tests, ex = cfg["solution"], cfg["test"], cfg["example"]
            dst = out / slug / lang
            if dst.exists():
                shutil.rmtree(dst)
            ws = dst / "workspace"
            for f in src.rglob("*"):
                rel = f.relative_to(src)
                if f.is_dir() or rel.parts[0] in SKIP_DIRS or rel.name in SKIP_FILES:
                    continue
                target = ws / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if str(rel) in tests:
                    target.write_text(unskip(lang, f.read_text()))
                else:
                    shutil.copy2(f, target)
            ref = dst / "reference"
            for ex_path, sol_path in map_examples(sol, ex, src).items():
                (ref / sol_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / ex_path, ref / sol_path)
            meta = {
                "slug": slug,
                "lang": lang,
                "solution_files": sol,
                "test_files": tests,
                "reference_files": sorted(str(p.relative_to(ref)) for p in ref.rglob("*") if p.is_file()),
                "instructions": instructions(src),
            }
            (dst / "task.json").write_text(json.dumps(meta, indent=2))
            manifest.append({"slug": slug, "lang": lang})
    (out / "manifest.json").write_text(json.dumps({"langs": LANGS, "slugs": common}, indent=2))
    print(f"wrote {len(manifest)} task instances to {out}/")


if __name__ == "__main__":
    main()
