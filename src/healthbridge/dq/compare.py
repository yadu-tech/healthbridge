"""Before (staging) vs after (core) comparison for one snapshot, from stored metrics only."""
from __future__ import annotations

import psycopg

from healthbridge.dq.report import _fetch, _pct


def _val(metrics: dict, scope: str, source: str, concept: str, dim: str, metric: str):
    return metrics.get((scope, source, concept, dim, metric), {}).get("value")


def render_comparison(conn: psycopg.Connection, before_id: int, after_id: int) -> str:
    runs = {i: conn.execute(
        "SELECT snapshot_run_id FROM dq.run WHERE dq_run_id = %s", (i,)
    ).fetchone() for i in (before_id, after_id)}
    if None in runs.values():
        raise ValueError("unknown dq run id")
    if runs[before_id][0] != runs[after_id][0]:
        raise ValueError("the two runs measure different snapshots")
    snapshot = runs[before_id][0]
    b, a = _fetch(conn, before_id), _fetch(conn, after_id)

    sources = sorted({k[1] for k in a if k[0] == "source" and k[3] == "score"})
    concepts = sorted({k[2] for k in a if k[0] == "concept"})
    series = sorted({(k[1], k[2]) for k in a if k[0] == "series"})

    out = [
        f"# Before vs after: staging layer vs core layer, snapshot `{snapshot}`",
        "",
        (f"Both layers were measured with the same engine (dq runs {before_id} and {after_id}). "
        "*Before* is the staged data as an analyst would first query it; *after* is the default "
        "analyst surface of the core layer: one selected national-total row per source, country, "
        "indicator and year (`core.v_headline_observation`)."),
        "",
        "## 1. Pipeline accounting",
        "",
        ("Nothing is dropped silently. Rows that are not loaded are listed with a reason in "
        "`core.rejected_record`; rows that are loaded but not on the default surface (sex, "
        "wealth-quintile, residence and age breakdowns, and non-selected surveys) stay in "
        "`core.fact_observation` with explicit dimensions."),
        "",
        "| Source | Staged | Rejected | Reasons | Loaded | On default surface |",
        "|---|---|---|---|---|---|",
    ]
    for source in sources:
        keys = [k for k in series if k[0] == source]
        staged = sum(_val(a, "series", s, c, "lineage", "records_staged") or 0 for s, c in keys)
        rejected = sum((a[("series", s, c, "lineage", "records_rejected")]["num"] or 0) for s, c in keys)
        loaded = sum(_val(a, "series", s, c, "lineage", "records_loaded") or 0 for s, c in keys)
        selected = sum(_val(a, "series", s, c, "lineage", "records_selected") or 0 for s, c in keys)
        reasons: dict[str, int] = {}
        for s, c in keys:
            details = a[("series", s, c, "lineage", "records_rejected")]["details"] or {}
            for reason, n in details.items():
                reasons[reason] = reasons.get(reason, 0) + n
        reason_text = ", ".join(f"{k}: {v:,}" for k, v in reasons.items()) or "none"
        out.append(f"| {source} | {int(staged):,} | {int(rejected):,} | {reason_text} | "
                   f"{int(loaded):,} | {int(selected):,} |")

    out += [
        "",
        "## 2. Scores before and after",
        "",
        ("**Read with care.** After the pipeline, validity, uniqueness and consistency are 100% "
        "largely *by construction*: invalid rows are rejected and exactly one row is selected per "
        "cell. They show that the pipeline enforces these properties, not that the data improved "
        "in a way that was not enforced. Completeness rises mainly because the World Bank empty "
        "placeholder rows are rejected (section 4 shows coverage, which cleaning cannot improve)."),
        "",
        "| Scope | Dimension | Before | After |",
        "|---|---|---|---|",
    ]
    for source in [*sources, ""]:
        scope = "source" if source else "overall"
        for dim in ("completeness", "validity", "uniqueness", "consistency", "composite"):
            out.append(f"| {source or 'all sources'} | {dim} | "
                       f"{_pct(_val(b, scope, source, '', 'score', dim))} | "
                       f"{_pct(_val(a, scope, source, '', 'score', dim))} |")

    out += [
        "",
        "## 3. Cross-source join, before and after",
        "",
        ("*Fan-out* is joined rows per country-year (1.0x is a clean 1:1 join). *Conflicting* is the "
        "share of joined rows where WHO, World Bank and UNICEF differ by more than 10% of their "
        "mean. Before, much of that is breakdowns compared with totals; after, what remains is "
        "genuine disagreement between sources."),
        "",
        "| Concept | Fan-out before | Fan-out after | Conflicting before | Conflicting after |",
        "|---|---|---|---|---|",
    ]
    for concept in concepts:
        fan_b = _val(b, "concept", "", concept, "integration", "naive_join_fanout")
        if fan_b is None:
            continue
        fan_a = _val(a, "concept", "", concept, "integration", "naive_join_fanout")
        con_b = _val(b, "concept", "", concept, "integration", "naive_join_conflict_rate")
        con_a = _val(a, "concept", "", concept, "integration", "naive_join_conflict_rate")
        out.append(f"| {concept} | {fan_b:.1f}x | {fan_a:.1f}x | {_pct(con_b)} | {_pct(con_a)} |")

    out += [
        "",
        "## 4. Coverage on the default surface",
        "",
        ("Grid coverage is the share of the (54 countries x years) grid with a value. Cleaning "
        "cannot add data, so it should not rise; it can fall where a country-year exists only as "
        "a breakdown (for example only for adolescent women) and so has no headline row. Those "
        "rows remain in `core.fact_observation`."),
        "",
        "| Source | Concept | Coverage before | Coverage after |",
        "|---|---|---|---|",
    ]
    for source, concept in series:
        out.append(f"| {source} | {concept} | "
                   f"{_pct(_val(b, 'series', source, concept, 'completeness', 'grid_coverage'))} | "
                   f"{_pct(_val(a, 'series', source, concept, 'completeness', 'grid_coverage'))} |")

    out += [
        "",
        "## 5. Source independence and reconciliation",
        "",
        ("Two sources are treated as *dependent* (publishing the same underlying estimate) when at "
        "least 90% of their shared country-years agree within 1%, with at least 30 shared "
        "country-years. Dependent sources form one *evidence group*; agreement inside a group is "
        "not independent confirmation. The reconciled value is never an average: it is the value "
        "of the highest-priority source of the highest-priority group (priority order WHO, UNICEF, "
        "World Bank; fixed and arbitrary). A conflict is flagged when independent groups differ by "
        "more than 10%; for those cells use `group_values`, not the single reconciled value."),
        "",
        "| Concept | Evidence groups | Reconciled country-years | Cross-validated (2+ groups) | Conflicts among cross-validated |",
        "|---|---|---|---|---|",
    ]
    for concept in concepts:
        cells = _val(a, "concept", "", concept, "integration", "cells_reconciled")
        if cells is None:
            continue
        groups = (a.get(("concept", "", concept, "integration", "independent_evidence_groups"), {})
                  .get("details") or [])
        cross = a[("concept", "", concept, "integration", "cross_validated_cell_rate")]
        conf = a[("concept", "", concept, "integration", "conflict_rate_among_cross_validated")]
        conf_text = f"{conf['num']} of {conf['den']} ({_pct(conf['value'])})" if conf["den"] else "n/a"
        out.append(f"| {concept} | {', '.join(groups)} | {int(cells):,} | "
                   f"{cross['num']:,} ({_pct(cross['value'])}) | {conf_text} |")

    out += [
        "",
        ("Section 3 and this table measure conflict slightly differently: section 3 compares the "
        "values of all three sources, while this table compares one representative per evidence "
        "group, so the stunting figures differ a little (for example 16.0% vs 15.6%)."),
        "",
        "## 6. Limitations",
        "",
        ("- The comparison is for one snapshot and one set of judgements (plausible ranges, headline "
        "definitions, tolerances); all are recorded in `reference/` and `core.build_log`."),
        ("- Dependence is inferred from agreement of values, not from documentation, so two sources "
        "could agree by coincidence or differ for benign reasons such as rounding or estimate vintage."),
        ("- Section 2 measures properties the pipeline enforces; independent evidence of benefit comes "
        "from sections 1, 3, 4 and 5 and, later, the fault-injection experiment."),
        "",
    ]
    return "\n".join(out)
