"""Analyze a run: language effects with task difficulty held fixed.

Outputs (in results/<run>/):
  report.md   tables below, with bootstrap 95% CIs (resampling exercises, so CIs respect pairing)
  by_lang.csv the per-language numbers

Questions answered:
  1. Python-normalized index, single shot (turn 1, no feedback)          -> "is the model better at X than Python?"
  2. Python-normalized index after repair, per feedback condition
  3. Compiler effect: final pass rate with diagnostics minus without, per language
     (the hypothesis predicts this is larger for languages with stronger static checkers)
  4. How turn-1 failures split into build vs test failures
  5. Mixed-effects logistic regression: pass ~ language * condition + (1 | exercise)

Usage: python analyze.py results/<run_name> [--boot 2000]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

LANG_ORDER = ["python", "rust", "go", "java", "typescript", "cpp"]


def load(run_dir):
    rows = [json.loads(l) for l in (run_dir / "episodes.jsonl").read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    df["pass_final"] = (df["final"] == "pass").astype(float)
    df["pass_t1"] = (df["pass_turn"] == 1).astype(float)
    df["t1_build_fail"] = (df["first_outcome"] == "build_fail").astype(float)
    return df


def boot(per_slug: pd.DataFrame, stat, n, seed=0):
    """per_slug: index=slug, columns=whatever stat needs. Resample slugs; return (point, lo, hi)."""
    rng = np.random.default_rng(seed)
    point = stat(per_slug)
    idx = per_slug.index.to_numpy()
    draws = np.array([stat(per_slug.loc[rng.choice(idx, len(idx))]) for _ in range(n)])
    lo, hi = np.nanpercentile(draws, [2.5, 97.5], axis=0)
    return point, lo, hi


def fmt(p, lo, hi, pct=False):
    if pct:
        return f"{100*p:.1f}% [{100*lo:.1f}, {100*hi:.1f}]"
    return f"{p:.2f} [{lo:.2f}, {hi:.2f}]"


def index_table(df, col, langs, n_boot):
    """Per-language rate / Python rate, using exercise-level means so every language is scored on the same set."""
    per = df.groupby(["slug", "lang"])[col].mean().unstack("lang")[langs].dropna()
    out = {}
    for l in langs:
        p, lo, hi = boot(per, lambda d, l=l: d[l].mean() / d["python"].mean(), n_boot)
        r, rlo, rhi = boot(per, lambda d, l=l: d[l].mean(), n_boot)
        out[l] = {"rate": fmt(r, rlo, rhi, pct=True), "index_vs_python": fmt(p, lo, hi), "_index": p}
    return out, len(per)


def mixed_model(df):
    try:
        from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
    except ImportError:
        return "statsmodels not installed"
    d = df.copy()
    d["lang"] = pd.Categorical(d["lang"], categories=[l for l in LANG_ORDER if l in set(d["lang"])])
    d["condition"] = pd.Categorical(d["condition"], categories=["tests", "diag"])
    try:
        m = BinomialBayesMixedGLM.from_formula(
            "pass_final ~ C(lang, Treatment('python')) * C(condition, Treatment('tests'))",
            {"exercise": "0 + C(slug)"}, d).fit_vb()
    except Exception as e:  # degenerate data (e.g. everything passes) breaks the fit
        return f"model did not fit: {e!r}"
    s = m.summary().tables[0]
    return s.to_string() if hasattr(s, "to_string") else str(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    df = load(run_dir)
    langs = [l for l in LANG_ORDER if l in set(df["lang"])]
    if "python" not in langs:
        raise SystemExit("need Python episodes as the baseline")

    lines = [f"# Matched-task language benchmark: `{run_dir.name}`", "",
             f"{df['slug'].nunique()} exercises x {len(langs)} languages x "
             f"{df['condition'].nunique()} conditions x {df['sample'].nunique()} samples = {len(df)} episodes.",
             "Every language is scored on the same exercises. Brackets are 95% bootstrap CIs over exercises.", ""]

    # 1. single shot (turn 1 pooled over conditions: the first prompt is identical)
    t1, n = index_table(df, "pass_t1", langs, args.boot)
    lines += ["## 1. Single shot (no feedback), Python = 1.00", "",
              "| Language | Pass rate | Index vs Python |", "|---|---|---|"]
    lines += [f"| {l} | {t1[l]['rate']} | {t1[l]['index_vs_python']} |" for l in langs]

    # 2. after repair, per condition
    rows_csv = []
    lines += ["", "## 2. After up to N turns of feedback, Python = 1.00", ""]
    finals = {}
    for c in ["tests", "diag"]:
        sub = df[df["condition"] == c]
        if sub.empty:
            continue
        finals[c], _ = index_table(sub, "pass_final", langs, args.boot)
    lines += ["| Language | Tests-only: pass | Tests-only: index | Full diagnostics: pass | Full diagnostics: index |",
              "|---|---|---|---|---|"]
    for l in langs:
        a, b = finals.get("tests", {}).get(l, {}), finals.get("diag", {}).get(l, {})
        lines.append(f"| {l} | {a.get('rate','')} | {a.get('index_vs_python','')} | {b.get('rate','')} | {b.get('index_vs_python','')} |")

    # 3. compiler effect, paired by exercise
    lines += ["", "## 3. Value of diagnostics: final pass rate (diag) minus (tests-only)", "",
              "Positive = seeing compiler / type-checker messages helped. The hypothesis is that this is larger "
              "for languages whose static checker catches more (Rust, Java, Go, C++, TypeScript) than for Python.", "",
              "| Language | Diag - tests (pp) | Turns used, tests-only | Turns used, diag |", "|---|---|---|---|"]
    if set(finals) == {"tests", "diag"}:
        per = df.groupby(["slug", "lang", "condition"])["pass_final"].mean().unstack(["lang", "condition"])
        for l in langs:
            sub = per[l].dropna()
            p, lo, hi = boot(sub, lambda d: d["diag"].mean() - d["tests"].mean(), args.boot)
            tu = df[(df.lang == l)].groupby("condition")["n_turns"].mean()
            lines.append(f"| {l} | {100*p:+.1f} [{100*lo:+.1f}, {100*hi:+.1f}] | {tu.get('tests', np.nan):.2f} | {tu.get('diag', np.nan):.2f} |")
            rows_csv.append({"lang": l, "diag_minus_tests": p, "lo": lo, "hi": hi})

    # 4. how turn-1 failures look
    lines += ["", "## 4. Turn-1 outcomes", "", "| Language | pass | test_fail | build_fail | timeout | no_files |",
              "|---|---|---|---|---|---|"]
    oc = df.groupby("lang")["first_outcome"].value_counts(normalize=True).unstack().reindex(langs).fillna(0)
    for l in langs:
        r = oc.loc[l]
        lines.append("| " + l + " | " + " | ".join(f"{100*r.get(k, 0):.0f}%" for k in
                                                  ["pass", "test_fail", "build_fail", "timeout", "no_files"]) + " |")

    # 5. mixed model
    lines += ["", "## 5. Mixed-effects logistic regression (final pass)", "",
              "`pass ~ language * condition + (1 | exercise)`, Python and tests-only as baselines. "
              "The language:condition interaction terms are the test of the compiler-feedback hypothesis.", "",
              "```", mixed_model(df), "```"]

    for l in langs:
        rec = {"lang": l, "single_shot_index": t1[l]["_index"]}
        for c in finals:
            rec[f"final_index_{c}"] = finals[c][l]["_index"]
        rows_csv.append(rec)
    pd.DataFrame(rows_csv).groupby("lang").first().reindex(langs).to_csv(run_dir / "by_lang.csv")
    (run_dir / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
