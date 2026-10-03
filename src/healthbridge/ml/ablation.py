"""The data-quality ablation: how much do corrupted data degrade forecasts, and what does the
pipeline prevent?  Protocol: docs/ml_decision.md ("Ablation protocol"), fixed before this was built.

Three ways of preparing the same data:
  clean     the pipeline on the uncorrupted snapshot (flagged values dropped): the reference
  naive     the corrupted WHO rows taken as published, with no validation
  pipeline  the corrupted snapshot through the pipeline (flagged values dropped)

Models are trained on a variant's own data, as an analyst would have it, and scored against the
clean truth on test forecasts that exist in every variant.
"""
from __future__ import annotations

import gzip
import json
import random
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from healthbridge.core.build import build_core
from healthbridge.experiments import faults as fx
from healthbridge.experiments.run import _git_version, purge_run
from healthbridge.ingest.snapshot import MANIFEST_NAME
from healthbridge.marts.build import build_marts
from healthbridge.ml.backtest import bucket_of, cluster_bootstrap_median
from healthbridge.ml.dataset import CONCEPTS, build_instances
from healthbridge.ml.models import design_matrix, make_gbm, to_values
from healthbridge.ml.stage2 import CUTOFFS, MIN_TRAIN, split
from healthbridge.reference import indicator_ranges, read_csv
from healthbridge.staging import parse
from healthbridge.staging.load import load_snapshot

RATES = (0.01, 0.03, 0.10)
SEEDS = (1, 2, 3)
GBM_SEEDS = (0, 1, 2)
VARIANTS = ("clean", "naive", "pipeline")
# Fixed from the stage-2 results, before any ablation run: the better simple baseline per indicator.
BASELINE_FOR = {"under5_mortality": "linear_trend_5", "maternal_mortality_ratio": "linear_trend_5",
                "dtp3_coverage": "last_value", "measles_mcv1_coverage": "last_value"}
DECISIVE_BUCKET = "4-5"
MARTS_TABLES = ("marts.country_indicator_year", "marts.country_latest", "marts.trend", "marts.region_year",
                "marts.indicator_association", "marts.equity_gap", "marts.data_trust", "marts.build_log")


# --- preparing the data three ways ---------------------------------------------------------

def load_panel_run(conn: psycopg.Connection, run_id: str, concepts: tuple[str, ...] = CONCEPTS,
                   drop_flagged: bool = True) -> pd.DataFrame:
    """The reconciled panel of one run. ``drop_flagged`` removes outlier-flagged and source-disagreeing values."""
    cur = conn.execute(
        "SELECT i.concept, trim(c.iso3) AS iso3, c.un_subregion, p.year, p.value, p.outlier_flag,"
        " p.source_disagreement FROM marts.country_indicator_year p JOIN core.dim_country c USING (country_key)"
        " JOIN core.dim_indicator i USING (indicator_key)"
        " WHERE p.run_id = %s AND i.concept = ANY(%s) AND p.quality_tier <> 'conflict'",
        (run_id, list(concepts)))
    panel = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    if drop_flagged:
        panel = panel[~(panel["outlier_flag"] | panel["source_disagreement"])]
    return panel[["concept", "iso3", "un_subregion", "year", "value"]].reset_index(drop=True)


def naive_panel(snapshot_dir: Path, regions: dict[str, str], concepts: tuple[str, ...] = CONCEPTS) -> pd.DataFrame:
    """The WHO rows as published, with no validation.

    Keep rows whose sex and wealth codes are exactly the totals, join on the exact ISO3 code, drop nulls and let a
    later duplicate overwrite an earlier one. No range, year or outlier check: wrong values flow through, and rows
    that fail these steps are lost.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    canonical = {r["iso3"] for r in read_csv("countries.csv")}
    latest: dict[tuple[str, str, int], float] = {}
    for entry in manifest["files"]:
        if entry["source"] != "who" or entry["concept"] not in concepts:
            continue
        for row in parse.parse_who((snapshot_dir / entry["path"]).read_bytes()):
            if row["dim1"] not in (None, "SEX_BTSX") or row["dim3"] not in (None, "WEALTHQUINTILE_TOTL"):
                continue
            iso3, year, value = row["spatial_dim"], row["time_dim"], row["numeric_value"]
            if iso3 not in canonical or year is None or value is None:
                continue
            latest[(entry["concept"], iso3, year)] = value
    rows = [{"concept": c, "iso3": i, "un_subregion": regions.get(i, ""), "year": y, "value": v}
            for (c, i, y), v in latest.items()]
    return pd.DataFrame(rows, columns=["concept", "iso3", "un_subregion", "year", "value"])


# --- scoring a variant ---------------------------------------------------------------------

def evaluate_variant(instances: pd.DataFrame, truth: dict[tuple[str, str, int], float],
                     clean_keys: set[tuple], ranges: dict[str, tuple[float, float]],
                     cutoffs: tuple[int, ...] = CUTOFFS, gbm_seeds: tuple[int, ...] = GBM_SEEDS) -> pd.DataFrame:
    """Train on the variant's own instances; score each forecast against the clean truth.

    Only forecasts whose (indicator, country, origin, horizon) also exist in the clean data and whose target has a
    clean truth value are scored.
    """
    frames = []
    for cutoff in cutoffs:
        train, test = split(instances, cutoff)
        if len(train) < MIN_TRAIN or test.empty:
            continue
        x_train, x_test = design_matrix(train), design_matrix(test)
        logs = [make_gbm(s).fit(x_train, train["y"]).predict(x_test) for s in gbm_seeds]
        test = test.copy()
        test["pred_gbm"] = to_values(test, np.mean(logs, axis=0), ranges)
        test["pred_baseline"] = [row[f"pred_{BASELINE_FOR[row['concept']]}"] for _, row in test.iterrows()]
        test["truth"] = [truth.get((c, i, y)) for c, i, y in zip(test["concept"], test["iso3"], test["target_year"],
                                                                 strict=True)]
        keys = list(zip(test["concept"], test["iso3"], test["origin_year"], test["horizon"], strict=True))
        keep = test["truth"].notna().to_numpy() & np.array([k in clean_keys for k in keys])
        scored = test[keep]
        frames.append(pd.DataFrame({
            "concept": scored["concept"].to_numpy(), "iso3": scored["iso3"].to_numpy(),
            "origin_year": scored["origin_year"].to_numpy(), "horizon": scored["horizon"].to_numpy(),
            "cutoff": cutoff,
            "ape_baseline": (scored["pred_baseline"] - scored["truth"]).abs().to_numpy() / scored["truth"].to_numpy(),
            "ape_gbm": (scored["pred_gbm"] - scored["truth"]).abs().to_numpy() / scored["truth"].to_numpy()}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# --- running it ----------------------------------------------------------------------------

def _save(frame: pd.DataFrame, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        frame.to_csv(handle, index=False)


def _load(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return pd.read_csv(handle)


def purge_ablation_run(conn: psycopg.Connection, run_id: str) -> None:
    with conn.transaction():
        for table in MARTS_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE run_id = %s", (run_id,))
    purge_run(conn, run_id)


def run_ablation(conn: psycopg.Connection, clean_run_dir: Path, out_dir: Path, rates: tuple[float, ...] = RATES,
                 seeds: tuple[int, ...] = SEEDS, resume: bool = True, keep: bool = False) -> Path:
    """Corrupt, process three ways, score. Finished (rate, seed) pairs are kept, so a run can resume."""
    out_dir = Path(out_dir)
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    ranges = indicator_ranges()
    store = fx.RawStore(clean_run_dir)
    clean_id = store.manifest["run_id"]
    pool = [p for p in fx.build_pool(conn, clean_id) if p.concept in CONCEPTS]
    n_universe = len(fx.clean_universe(pool))

    truth_panel = load_panel_run(conn, clean_id, drop_flagged=False)
    truth = {(r.concept, r.iso3, r.year): r.value for r in truth_panel.itertuples()}
    regions = truth_panel.drop_duplicates("iso3").set_index("iso3")["un_subregion"].to_dict()
    clean_instances = build_instances(truth_panel, ranges)
    clean_keys = {(r.concept, r.iso3, r.origin_year, r.horizon)
                  for r in clean_instances[clean_instances["origin_year"].isin(CUTOFFS)].itertuples()}

    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if resume and meta_path.exists() else {"runs": {}}
    meta.update({"code_version": _git_version(), "clean_run_id": clean_id, "rates": list(rates), "seeds": list(seeds),
                 "gbm_seeds": list(GBM_SEEDS), "n_clean_keys": len(clean_keys), "n_modelled_rows": n_universe})

    clean_path = out_dir / "frames" / "clean.csv.gz"
    if not (resume and clean_path.exists()):
        panel_a = load_panel_run(conn, clean_id, drop_flagged=True)
        meta["clean_dropped_share"] = 1 - len(panel_a) / len(truth_panel)
        _save(evaluate_variant(build_instances(panel_a, ranges), truth, clean_keys, ranges), clean_path)
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    for rate in rates:
        for seed in seeds:
            tag = f"r{round(rate * 100):02d}_s{seed}"
            if resume and (out_dir / "frames" / f"{tag}_pipeline.csv.gz").exists():
                continue
            run_id = f"ab_{tag}"
            rng = random.Random(f"ablation-{rate}-{seed}")
            parsed = store.working_copy()
            faults = fx.inject_mixed(store, parsed, pool, rng, rate)
            snapshot = fx.write_snapshot(store, parsed, run_id, out_dir / "snapshots")
            try:
                load_snapshot(conn, snapshot)
                build_core(conn, snapshot)
                build_marts(conn, snapshot)
                panel_c = load_panel_run(conn, run_id, drop_flagged=True)
                dropped = 1 - len(panel_c) / max(1, len(load_panel_run(conn, run_id, drop_flagged=False)))
                panel_b = naive_panel(snapshot, regions)
            finally:
                purge_ablation_run(conn, run_id)
                if not keep:
                    shutil.rmtree(snapshot, ignore_errors=True)
            frame_b = evaluate_variant(build_instances(panel_b, ranges), truth, clean_keys, ranges)
            frame_c = evaluate_variant(build_instances(panel_c, ranges), truth, clean_keys, ranges)
            _save(frame_b, out_dir / "frames" / f"{tag}_naive.csv.gz")
            _save(frame_c, out_dir / "frames" / f"{tag}_pipeline.csv.gz")       # written last: marks the pair as done
            meta["runs"][tag] = {"rate": rate, "seed": seed, "n_faults": len(faults),
                                 "naive_rows": len(panel_b), "pipeline_rows": len(panel_c),
                                 "pipeline_dropped_share": dropped}
            meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta_path


# --- the paired comparison -----------------------------------------------------------------

def paired_frames(out_dir: Path, rate: float, seed: int) -> pd.DataFrame | None:
    """Forecasts present in all three variants for one corruption, side by side."""
    tag = f"r{round(rate * 100):02d}_s{seed}"
    paths = {"clean": out_dir / "frames" / "clean.csv.gz", "naive": out_dir / "frames" / f"{tag}_naive.csv.gz",
             "pipeline": out_dir / "frames" / f"{tag}_pipeline.csv.gz"}
    if not all(p.exists() for p in paths.values()):
        return None
    key = ["concept", "iso3", "origin_year", "horizon"]
    merged = None
    for name, path in paths.items():
        frame = _load(path)[key + ["cutoff", "ape_baseline", "ape_gbm"]].rename(
            columns={"ape_baseline": f"baseline_{name}", "ape_gbm": f"gbm_{name}"})
        merged = frame if merged is None else merged.merge(frame.drop(columns="cutoff"), on=key, how="inner")
    merged["rate"], merged["seed"] = rate, seed
    return merged


def degradation(frame: pd.DataFrame, model: str, resamples: int = 500) -> dict:
    """Median paired degradation relative to clean input, for naive and pipeline, and their difference."""
    out = {"n": len(frame), "n_countries": int(frame["iso3"].nunique())}
    delta = {v: frame[f"{model}_{v}"] - frame[f"{model}_clean"] for v in ("naive", "pipeline")}
    delta["naive_minus_pipeline"] = frame[f"{model}_naive"] - frame[f"{model}_pipeline"]
    for name, series in delta.items():
        groups = [g.to_numpy() for _, g in series.groupby(frame["iso3"])]
        low, high = cluster_bootstrap_median(groups, resamples)
        out[name] = {"median": float(series.median()), "low": low, "high": high}
    for v in VARIANTS:
        out[f"ape_{v}"] = float(frame[f"{model}_{v}"].median())
    return out


def build_summary(out_dir: Path, resamples: int = 500) -> dict:
    """Aggregate every finished corruption into the quantities the protocol names."""
    out_dir = Path(out_dir)
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    rows, seed_spread, coverage = [], [], []
    for rate in meta["rates"]:
        frames = [f for seed in meta["seeds"] if (f := paired_frames(out_dir, rate, seed)) is not None]
        if not frames:
            continue
        pooled = pd.concat(frames, ignore_index=True)
        pooled = pooled[pooled["horizon"].map(bucket_of) == DECISIVE_BUCKET]
        for model in ("gbm", "baseline"):
            rows.append({"rate": rate, "model": model, "concept": "all", **degradation(pooled, model, resamples)})
            for concept, g in pooled.groupby("concept"):
                rows.append({"rate": rate, "model": model, "concept": concept, **degradation(g, model, resamples)})
        for f in frames:
            f = f[f["horizon"].map(bucket_of) == DECISIVE_BUCKET]
            seed_spread.append({"rate": rate, "seed": int(f["seed"].iloc[0]), "gbm_naive": float(f["gbm_naive"].median()),
                                "gbm_pipeline": float(f["gbm_pipeline"].median()),
                                "gbm_clean": float(f["gbm_clean"].median())})
        for seed in meta["seeds"]:
            tag = f"r{round(rate * 100):02d}_s{seed}"
            for variant in ("naive", "pipeline"):
                path = out_dir / "frames" / f"{tag}_{variant}.csv.gz"
                if path.exists():
                    coverage.append({"rate": rate, "variant": variant,
                                     "coverage": len(_load(path)) / meta["n_clean_keys"]})
    return {"meta": meta, "rows": pd.DataFrame(rows), "seed_spread": pd.DataFrame(seed_spread),
            "coverage": pd.DataFrame(coverage)}


def render_ablation_report(summary: dict) -> str:
    """The comparison the protocol names, with the primary outcome stated as found."""
    meta, rows, spread, coverage = summary["meta"], summary["rows"], summary["seed_spread"], summary["coverage"]

    def pp(d):
        return f"{100 * d['median']:+.2f} pp ({100 * d['low']:+.2f} to {100 * d['high']:+.2f})"

    def table(headers, body):
        return (["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                + ["| " + " | ".join(str(c) for c in row) + " |" for row in body])

    lines = [
        "# Data-quality ablation: forecasts from corrupted data",
        "",
        ("Generated by `python -m healthbridge.ml ablation report`. The protocol was fixed in "
        "[../ml_decision.md](../ml_decision.md) before this was built. Corrupted copies of the raw snapshot were "
        "prepared three ways (clean through the pipeline, corrupted naively, corrupted through the pipeline) and "
        "forecasts were scored against the clean truth on forecasts present in all three. Degradation is the median "
        "paired difference in absolute percentage error relative to the clean variant, in percentage points "
        "(positive means worse than clean); the interval resamples countries."),
        "",
        (f"Code version `{meta.get('code_version', 'unknown')}`; clean snapshot `{meta['clean_run_id']}`; "
        f"{len(meta['runs'])} corrupted snapshots ({len(meta['rates'])} rates x {len(meta['seeds'])} seeds). "
        f"The pipeline-processed clean data drops {100 * meta.get('clean_dropped_share', float('nan')):.1f}% of values as "
        "flagged, so the reference already carries that cost."),
        "",
        "## 1. Primary outcome: gradient boosting at 4-5 years, all four indicators pooled",
        "",
        ("The pipeline protects the forecast if variant (c) degrades *less* than variant (b), meaning the interval of "
        "the paired difference (naive minus pipeline) lies entirely above zero."),
        "",
    ]
    main = rows[(rows["concept"] == "all") & (rows["model"] == "gbm")]
    lines += table(["Fault rate", "Forecasts", "Naive degradation", "Pipeline degradation",
                    "Naive minus pipeline (95% interval)", "Pipeline degrades less"],
                   [[f"{100 * r.rate:.0f}%", f"{r.n:,}", pp(r.naive), pp(r.pipeline), pp(r.naive_minus_pipeline),
                     "**yes**" if r.naive_minus_pipeline["low"] > 0
                     else ("**worse**" if r.naive_minus_pipeline["high"] < 0 else "not shown")]
                    for r in main.itertuples()])
    lines += ["", "## 2. The baseline model", "",
              "The same comparison for the simple baseline chosen per indicator in advance.", ""]
    base = rows[(rows["concept"] == "all") & (rows["model"] == "baseline")]
    lines += table(["Fault rate", "Naive degradation", "Pipeline degradation", "Naive minus pipeline (95% interval)"],
                   [[f"{100 * r.rate:.0f}%", pp(r.naive), pp(r.pipeline), pp(r.naive_minus_pipeline)]
                    for r in base.itertuples()])
    lines += ["", "## 3. By indicator (gradient boosting, 4-5 years)", ""]
    per = rows[(rows["concept"] != "all") & (rows["model"] == "gbm")]
    lines += table(["Indicator", "Fault rate", "Median error: clean", "naive", "pipeline",
                    "Naive minus pipeline (95% interval)"],
                   [[r.concept, f"{100 * r.rate:.0f}%", f"{100 * r.ape_clean:.1f}%", f"{100 * r.ape_naive:.1f}%",
                     f"{100 * r.ape_pipeline:.1f}%", pp(r.naive_minus_pipeline)] for r in per.itertuples()])
    lines += ["", "## 4. Reproducibility: spread across corruption seeds", "",
              ("Median gradient-boosting error at 4-5 years for each corruption seed. A wide range means the result "
              "depends on which rows happened to be corrupted."), ""]
    body = []
    for rate, g in spread.groupby("rate"):
        body.append([f"{100 * rate:.0f}%"] + [
            f"{100 * g[c].mean():.1f}% (range {100 * g[c].min():.1f}-{100 * g[c].max():.1f})"
            for c in ("gbm_clean", "gbm_naive", "gbm_pipeline")])
    lines += table(["Fault rate", "Clean", "Naive", "Pipeline"], body)
    lines += ["", "## 5. Coverage: how many forecasts can each variant produce?", "",
              ("Share of the clean data's forecasts that a variant can make at all (rows lost to bad codes, unparseable "
              "values or dropped flagged values reduce it). The comparison above uses only forecasts present in all "
              "three variants, so coverage is an extra cost the degradation figures do not show."), ""]
    body = [[f"{100 * rate:.0f}%", variant, f"{100 * g['coverage'].mean():.1f}%"]
            for (rate, variant), g in coverage.groupby(["rate", "variant"])]
    lines += table(["Fault rate", "Variant", "Mean coverage"], body)
    lines += ["", "## 6. Limits", "",
              "- Injected faults are synthetic and the mixture is a choice; real errors may differ in kind and frequency.",
              ("- The naive variant is deliberately minimal. Variants (b) and (c) share most WHO values, so the comparison "
              "is mainly about validation, repair and cross-source checking."),
              ("- The pipeline variant also drops flagged values, including real events, a cost visible in the clean "
              "reference. Three corruption seeds per rate is a small number of corruption patterns."),
              ("- Results are pseudo out-of-sample on final-vintage modelled estimates (see ml_decision.md), and describe "
              "these four indicators only."), ""]
    return "\n".join(lines)
