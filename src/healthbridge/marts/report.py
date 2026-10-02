"""Render an analytics report from the marts of one snapshot (every number is read from the database)."""
from __future__ import annotations

import psycopg

# Pairs whose relationship is partly built in, so a strong association is not informative.
MECHANICAL = {
    frozenset({"neonatal_mortality", "under5_mortality"}):
        "neonatal deaths are a subset of under-5 deaths",
    frozenset({"dtp3_coverage", "measles_mcv1_coverage"}):
        "delivered together through the same routine immunization programme",
}


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def _f(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def render_marts_report(conn: psycopg.Connection, run_id: str | None = None) -> str:
    run_id = run_id or conn.execute("SELECT run_id FROM marts.latest_run").fetchone()[0]
    built = conn.execute("SELECT built_at, parameters FROM marts.build_log WHERE run_id = %s",
                         (run_id,)).fetchone()
    if built is None:
        raise ValueError(f"marts for {run_id} have not been built")
    params = built[1]

    def rows(sql, *args):
        return conn.execute(sql, (run_id, *args)).fetchall()

    out = [
        f"# Analytics report: snapshot `{run_id}`",
        "",
        (f"Generated from the marts built {built[0]:%Y-%m-%d %H:%M} UTC with `python -m healthbridge.marts "
        "report`. Definitions, grains and caveats are in [../analytics.md](../analytics.md). "
        f"Parameters: trend window from {params['trend_start']}; cross-country comparisons use the "
        f"observation nearest {params['ref_year']} within +-{params['tolerance']} years "
        f"(changes: {params['base_year']} to {params['ref_year']})."),
        "",
        ("**How to read this.** Everything here is descriptive. Associations are between countries "
        "(ecological), cannot show cause, and are partly shaped by shared geography, income and "
        "history. Averages are of countries, not people. Many series are modelled estimates, and "
        "most indicators cannot be cross-checked because the sources republish one estimate."),
        "",
        "## 1. How far each indicator can be trusted",
        "",
    ]
    trust = rows("SELECT i.concept, t.n_countries, t.year_min, t.year_max, t.grid_coverage,"
                 " t.share_single_evidence, t.share_cross_validated, t.share_conflict,"
                 " t.share_outlier_flag, t.share_source_disagreement FROM marts.data_trust t"
                 " JOIN core.dim_indicator i USING (indicator_key) WHERE t.run_id = %s ORDER BY 1")
    out += _table(["Indicator", "Countries", "Years", "Grid coverage", "Single evidence group",
                   "Cross-validated", "Conflict", "Outlier-flagged", "Source disagreement"],
                  [[c, n, f"{a}-{b}", _pct(cov), _pct(s), _pct(x), _pct(k), _pct(o, 2), _pct(d, 2)]
                   for c, n, a, b, cov, s, x, k, o, d in trust])
    out += ["", ("*Single evidence group* means every source republishes one estimate, so agreement "
            "between them is not independent confirmation. Only stunting has independent evidence "
            "(model-based WHO against survey-based UNICEF and World Bank values)."), ""]

    out += ["## 2. Regional spread: countries within a region differ widely", "",
            ("Median and range across countries in each UN sub-region, for the indicators with "
            "annual series. The range matters as much as the median: sub-regions are not "
            "homogeneous."), ""]
    for concept, label in (("under5_mortality", "Under-5 mortality (deaths per 1000 live births)"),
                           ("maternal_mortality_ratio", "Maternal mortality ratio (per 100000 live births)"),
                           ("dtp3_coverage", "DTP3 coverage (% of one-year-olds)")):
        data = rows("SELECT r.un_subregion, r.year, r.n_countries, r.n_possible, r.median_value,"
                    " r.min_value, r.max_value FROM marts.region_year r JOIN core.dim_indicator i"
                    " USING (indicator_key) WHERE r.run_id = %s AND i.concept = %s AND r.year IN (2000, 2015)"
                    " ORDER BY 1, 2", concept)
        by_region: dict[str, dict] = {}
        for region, year, n, possible, med, lo, hi in data:
            by_region.setdefault(region, {})[year] = (n, possible, med, lo, hi)
        table = []
        for region, years in sorted(by_region.items()):
            cells = [region]
            for year in (2000, 2015):
                if year in years:
                    n, possible, med, lo, hi = years[year]
                    cells.append(f"{_f(med)} ({_f(lo)}-{_f(hi)}), n={n}/{possible}")
                else:
                    cells.append("n/a")
            table.append(cells)
        out += [f"**{label}**", ""] + _table(["Sub-region", "2000: median (range)", "2015: median (range)"], table) + [""]

    out += ["## 3. Direction and pace of change since 2000", "",
            ("Each country's slope over its own series (at least 4 observations spanning 8 years or more). "
            "Linear slope is in the indicator's units per year; the annual percentage change is "
            "log-linear and defined only for positive series."), ""]
    trend = rows("SELECT i.concept, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY t.linear_slope_per_year),"
                 " percentile_cont(0.5) WITHIN GROUP (ORDER BY t.annual_pct_change),"
                 " avg((t.linear_slope_per_year < 0)::int), avg((t.linear_slope_per_year > 0)::int)"
                 " FROM marts.trend t JOIN core.dim_indicator i USING (indicator_key) WHERE t.run_id = %s"
                 " GROUP BY 1 ORDER BY 1")
    out += _table(["Indicator", "Countries", "Median slope per year", "Median annual % change",
                   "Countries falling", "Countries rising"],
                  [[c, n, _f(s, 2), _pct(p, 2), _pct(fall, 0), _pct(rise, 0)] for c, n, s, p, fall, rise in trend])
    out += [""]

    out += ["## 3b. Reversals: under-5 mortality that rose over four years", "",
            ("Countries whose reconciled under-5 mortality was more than 10% higher than four years "
             "earlier at some point since 2000 (the largest such rise per country). Two patterns "
             "appear. A **sustained** rise over several years is not flagged by the temporal outlier "
             "check, which compares each year with its neighbours. A **single-year spike** is flagged; "
             "the last column counts flagged years inside the span so the two can be told apart. "
             "Flagged rows stay in the data. All sources agree because they republish one estimate, "
             "which says nothing about whether the values are right. **These values have not been "
             "verified against the source documentation** and should be checked before any is used "
             "in an analysis; they also account for extreme values in the regional ranges above."), ""]
    reversals = conn.execute(
        "WITH w AS (SELECT p.country_key, p.year, p.value, "
        " lag(p.value, 4) OVER (PARTITION BY p.country_key ORDER BY p.year) AS v_prev, "
        " lag(p.year, 4) OVER (PARTITION BY p.country_key ORDER BY p.year) AS y_prev "
        " FROM marts.country_indicator_year p JOIN core.dim_indicator i USING (indicator_key) "
        " WHERE p.run_id = %(r)s AND i.concept = 'under5_mortality' AND p.year >= 2000), "
        "r AS (SELECT DISTINCT ON (country_key) country_key, y_prev, v_prev, year, value, "
        " value / v_prev - 1 AS rise FROM w WHERE y_prev = year - 4 AND v_prev > 0 "
        " ORDER BY country_key, value / v_prev DESC) "
        "SELECT c.name, r.y_prev, r.v_prev, r.year, r.value, r.rise, "
        " (SELECT count(*) FROM marts.country_indicator_year q "
        "  JOIN core.dim_indicator qi USING (indicator_key) "
        "  WHERE q.run_id = %(r)s AND qi.concept = 'under5_mortality' AND q.country_key = r.country_key "
        "  AND q.year BETWEEN r.y_prev AND r.year AND q.outlier_flag) "
        "FROM r JOIN core.dim_country c USING (country_key) WHERE r.rise > 0.10 "
        "ORDER BY r.rise DESC LIMIT 8", {"r": run_id}).fetchall()
    if reversals:
        out += _table(["Country", "From", "To", "Rise", "Outlier flags in span"],
                      [[n, f"{_f(vp)} ({yp})", f"{_f(v)} ({y})", _pct(rise, 0), flags]
                       for n, yp, vp, y, v, rise, flags in reversals])
    else:
        out += ["No country shows a rise above 10% over four years in this snapshot."]
    out += [""]

    out += ["## 4. Associations between indicators across countries", "",
            ("Spearman rank correlation with an approximate 95% interval (Fisher z). **Associations "
            "only.** Levels compare countries at one time; changes compare how countries moved. "
            "Pairs marked * are partly built in."), ""]
    for basis, title in (("level", "Levels (nearest observation to the reference year)"),
                         ("change", "Changes (reference year minus base year)")):
        assoc = rows("SELECT a.concept, b.concept, x.n_countries, x.spearman_rho, x.rho_ci_low, x.rho_ci_high"
                     " FROM marts.indicator_association x JOIN core.dim_indicator a ON a.indicator_key = x.indicator_a"
                     " JOIN core.dim_indicator b ON b.indicator_key = x.indicator_b"
                     " WHERE x.run_id = %s AND x.basis = %s ORDER BY abs(x.spearman_rho) DESC", basis)
        table = []
        for a, b, n, rho, lo, hi in assoc:
            mark = "*" if frozenset({a, b}) in MECHANICAL else ""
            table.append([f"{a} / {b}{mark}", n, _f(rho, 2), f"{_f(lo, 2)} to {_f(hi, 2)}"])
        out += [f"**{title}**", ""] + _table(["Pair", "Countries", "Spearman rho", "95% interval"], table) + [""]
    for pair, reason in MECHANICAL.items():
        out.append(f"- \\* {' and '.join(sorted(pair))}: {reason}.")
    out += [""]

    out += ["## 5. Equity gaps", "",
            ("Gaps use only groups from the same survey or series, with each country's most recent "
            "available gap. *Ratio* is the first group over the second. Modelled WHO series are "
            "preferred over survey series where both exist (a documented source-priority rule)."), ""]
    equity = rows("SELECT g.dimension, i.concept, min(g.group_a), min(g.group_b), count(*),"
                  " percentile_cont(0.5) WITHIN GROUP (ORDER BY g.ratio), min(g.ratio), max(g.ratio),"
                  " avg((g.ratio > 1)::int) FROM (SELECT DISTINCT ON (country_key, indicator_key, dimension) *"
                  " FROM marts.equity_gap WHERE run_id = %s ORDER BY country_key, indicator_key, dimension, year DESC) g"
                  " JOIN core.dim_indicator i USING (indicator_key) GROUP BY 1, 2 ORDER BY 1, 2")
    out += _table(["Dimension", "Indicator", "Ratio of", "Countries", "Median ratio", "Min", "Max",
                   "Share above 1"],
                  [[d, c, f"{a} / {b}", n, _f(med, 2), _f(lo, 2), _f(hi, 2), _pct(sh, 0)]
                   for d, c, a, b, n, med, lo, hi, sh in equity])
    out += [""]

    out += ["## 6. How current is the latest value?", "",
            ("Years between each country's latest value and the most recent year available for that "
            "indicator in any country. Survey-based series lag."), ""]
    stale = rows("SELECT i.concept, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY l.years_behind),"
                 " max(l.years_behind), avg((l.years_behind >= 5)::int) FROM marts.country_latest l"
                 " JOIN core.dim_indicator i USING (indicator_key) WHERE l.run_id = %s GROUP BY 1 ORDER BY 1")
    out += _table(["Indicator", "Countries", "Median years behind", "Max years behind", "5+ years behind"],
                  [[c, n, _f(med, 0), mx, _pct(sh, 0)] for c, n, med, mx, sh in stale])
    out += ["", "## 7. Limitations", "",
            ("- Cross-country associations are ecological: they describe countries, not individuals or "
            "mechanisms, and share geography and history, so the effective sample is smaller than the "
            "country count suggests."),
            ("- Most series are modelled estimates. Where WHO, UNICEF and the World Bank agree they are "
            "usually one estimate, not three."),
            ("- Regional averages are unweighted (the average country, not the average person); no "
            "population data is included in this release."),
            ("- Equity gaps mix modelled (WHO) and survey (UNICEF) series by a priority rule, and survey "
            "gaps rest on small samples."),
            ("- Nearest-observation alignment within +-3 years treats values from different years as "
            "contemporaneous; survey indicators are the least current."),
            ""]
    return "\n".join(out)
