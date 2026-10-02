"""HealthBridge dashboard. Run with: streamlit run src/healthbridge/dashboard/app.py

A thin layer over the analytics marts. Everything shown is descriptive: associations are between
countries and say nothing about cause, averages are of countries rather than people, and each
value carries how far it can be trusted.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from healthbridge.dashboard import charts, data
from healthbridge.dashboard.theme import MAX_COMPARED, assign_slots, get_theme
from healthbridge.db import connect

PAGES = ["Overview", "Country profile", "Compare countries", "Relationships", "Equity gaps",
         "Pipeline and quality"]
MECHANICAL = {
    frozenset({"neonatal_mortality", "under5_mortality"}): "neonatal deaths are part of under-5 deaths",
    frozenset({"dtp3_coverage", "measles_mcv1_coverage"}): "both vaccines are delivered by the same routine programme",
}
INTRO = ("Maternal and child health indicators for the 54 African states, integrated from WHO, UNICEF "
         "and the World Bank. Every value shows how far it can be trusted. What you see is descriptive.")


@st.cache_data(ttl=600, show_spinner=False)
def run(name: str, *args):
    """Call a data-layer function with a short-lived connection; results are cached."""
    prepared = [list(a) if isinstance(a, tuple) else a for a in args]
    with connect() as conn:
        return getattr(data, name)(conn, *prepared)


def current_theme():
    try:
        return get_theme(st.context.theme.type)
    except Exception:  # noqa: BLE001 - older Streamlit or a context without theme information
        return get_theme(None)


def show(chart, theme, height: int | None = None) -> None:
    st.altair_chart(charts.style(chart, theme, height), width="stretch")


def table_view(df: pd.DataFrame, label: str = "Table view") -> None:
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch")


def pct(x, digits: int = 0) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:.{digits}f}%"


def caveat(text: str) -> None:
    st.caption(text)


# --- pages ---------------------------------------------------------------------------------

def page_overview(theme) -> None:
    info = run("snapshot_info")
    trust = run("trust")
    st.title("HealthBridge")
    st.write(INTRO)
    checkable = int((trust["share_cross_validated"] + trust["share_conflict"] > 0).sum())
    cols = st.columns(4)
    cols[0].metric("Countries", info["n_countries"])
    cols[1].metric("Indicators", len(trust))
    cols[2].metric("Country-year values", f"{info['rows']['country_indicator_year']:,}")
    cols[3].metric("Cross-checkable", f"{checkable} of {len(trust)}",
                   help="Indicators for which independent sources exist, so values can be cross-checked.")
    st.subheader("How far can each indicator be trusted?")
    st.write("A value is **cross-validated** when independent sources agree. For most indicators the sources "
             "publish one shared estimate, so their agreement is not independent confirmation: that is the "
             "**single evidence group** tier. A **conflict** means independent sources disagree by more than 10%.")
    show(charts.trust_bars(trust, theme), theme, height=300)
    table_view(trust.assign(**{c: trust[c].map(pct) for c in trust.columns if c.startswith(("share_", "grid"))})
               .rename(columns={"grid_coverage": "Grid coverage", "n_values": "Values",
                                "n_countries": "Countries"}).rename(columns={"indicator_full": "Indicator"}).drop(columns=["concept"]))
    caveat(f"Snapshot {info['run_id']}, built {info['built_at']:%Y-%m-%d}. Years {info['year_min']}-"
           f"{info['year_max']}. Grid coverage is the share of country-years with a value.")


def page_country(theme) -> None:
    st.header("Country profile")
    countries = run("countries")
    names = list(countries["country"])
    choice = st.selectbox("Country", names, index=names.index("Nigeria") if "Nigeria" in names else 0,
                          key="country_choice")
    row = countries[countries["country"] == choice].iloc[0]
    iso3, region = row["iso3"], row["un_subregion"]
    st.write(f"**{choice}** is in {region} (UN) and in WHO region **{row['who_region_code']}**.")

    latest = run("country_latest", iso3)
    trends = run("country_trends", iso3).set_index("concept")
    if latest.empty:
        st.info("No values are available for this country in the current snapshot.")
        return
    summary = latest.copy()
    summary["Quality"] = summary["quality_tier"].map(data.TIER_LABELS)
    summary["Change since 2000"] = [
        ("n/a" if c not in trends.index else
         (f"{100 * trends.loc[c, 'annual_pct_change']:+.1f}% a year" if pd.notna(trends.loc[c, "annual_pct_change"])
          else f"{trends.loc[c, 'linear_slope_per_year']:+.2f} a year")) for c in summary["concept"]]
    summary["Indicator"] = summary["concept"].map(data.short_label)
    st.dataframe(
        summary[["Indicator", "value", "latest_year", "years_behind", "Change since 2000", "Quality"]]
        .rename(columns={"value": "Latest", "latest_year": "Year", "years_behind": "Years behind"}),
        hide_index=True, width="stretch",
        column_config={"Indicator": st.column_config.TextColumn(width="large"),
                       "Latest": st.column_config.NumberColumn(format="%.1f")})
    caveat("Years behind is how much older this value is than the most recent value for that indicator in any "
           "country. Slopes are linear fits over each country's own series from 2000 and are not forecasts.")

    st.subheader("History against the sub-region")
    inds = run("indicators")
    grid = st.columns(2)
    for i, ind in enumerate(inds.itertuples()):
        df = run("panel", ind.concept, (iso3,))
        with grid[i % 2]:
            if df.empty:
                st.markdown(f"**{ind.name}**")
                st.caption("No values for this country.")
                continue
            band = run("region_band", ind.concept, region)
            show(charts.country_multiple(df, band, data.SHORT_NAMES.get(ind.concept, ind.name), ind.unit, theme),
                 theme, height=230)
    caveat(f"Blue line: {choice}. Grey band: the range across countries in {region}, with the median as a "
           "line. Ring: a flagged value (an outlier against neighbouring years, or sources of one group "
           "disagree). Triangle: independent sources conflict. Hover for details.")
    table_view(pd.concat([run("panel", c, (iso3,)).assign(indicator=n) for c, n in zip(inds.concept, inds.name)])
               [["indicator", "year", "value", "quality_tier", "outlier_flag", "source_disagreement"]])


def assign_colours(selected: list[str], theme) -> dict[str, str]:
    """Colour follows the country: a country keeps its slot while it stays selected."""
    store: dict = st.session_state.setdefault("colour_slots", {})
    slots = assign_slots(store, selected)
    store.clear()
    store.update(slots)
    return {name: theme.series[slots[name]] for name in selected}


def page_compare(theme) -> None:
    st.header("Compare countries")
    inds = run("indicators")
    countries = run("countries")
    label = {r.concept: data.SHORT_NAMES.get(r.concept, r.name) for r in inds.itertuples()}
    col1, col2 = st.columns([1, 2])
    concept = col1.selectbox("Indicator", list(label), format_func=label.get, key="compare_indicator",
                             index=list(label).index("under5_mortality"))
    names = list(countries["country"])
    default = [n for n in ("Nigeria", "Ethiopia") if n in names]
    selected = col2.multiselect(f"Countries (up to {MAX_COMPARED})", names, default=default,
                                max_selections=MAX_COMPARED, key="compare_countries")
    if not selected:
        st.info("Choose at least one country.")
        return
    iso = tuple(countries.set_index("country").loc[selected, "iso3"])
    df = run("panel", concept, iso)
    unit = data.short_label(concept)
    colours = assign_colours(selected, theme)
    show(charts.compare_lines(df, colours, unit, theme), theme, height=380)
    caveat("Colours stay with a country while it is selected. Ring: a flagged value. Triangle: independent sources "
           "conflict. Countries are shown side by side, not ranked, and levels reflect different histories "
           "and measurement.")
    table_view(df.pivot(index="year", columns="country", values="value").reset_index())


def page_relationships(theme) -> None:
    st.header("Relationships between indicators")
    st.warning("These are associations across countries, not causes. Countries that share a region, income level "
               "or history are not independent observations, so the intervals understate the uncertainty.")
    inds = run("indicators")
    label = {r.concept: data.SHORT_NAMES.get(r.concept, r.name) for r in inds.itertuples()}
    concepts = list(label)
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Indicator on the horizontal axis", concepts, format_func=label.get, key="rel_a",
                     index=concepts.index("dtp3_coverage"))
    others = [c for c in concepts if c != a]
    b = c2.selectbox("Indicator on the vertical axis", others, format_func=label.get, key="rel_b",
                     index=others.index("under5_mortality") if "under5_mortality" in others else 0)
    basis = c3.radio("Compare", list(data.BASES), format_func=data.BASES.get, key="rel_basis")
    df = run("scatter", a, b, basis)
    stat = run("association", a, b, basis)
    regions = ["None"] + sorted(df["un_subregion"].unique())
    d1, d2 = st.columns(2)
    highlight = d1.selectbox("Highlight a sub-region", regions, key="rel_region")
    selected = d2.selectbox("Mark a country", ["None", *sorted(df["country"])], key="rel_country")

    if stat:
        m = st.columns(3)
        m[0].metric("Rank correlation (Spearman)", f"{stat['rho']:+.2f}")
        m[1].metric("Approximate 95% interval", f"{stat['ci_low']:+.2f} to {stat['ci_high']:+.2f}")
        m[2].metric("Countries", stat["n"])
        st.caption(f"Alignment: {stat['reference']}. Each country contributes the observation nearest the "
                   "reference year; conflicted values are left out.")
    note = MECHANICAL.get(frozenset({a, b}))
    if note:
        st.info(f"A strong association here is expected: {note}.")
    prefix = "" if basis == "level" else "Change in "
    chart = charts.scatter_chart(df, None if highlight == "None" else highlight,
                                 None if selected == "None" else selected,
                                 prefix + data.short_label(a), prefix + data.short_label(b), theme)
    show(chart, theme, height=440)
    caveat("Each point is a country. When a sub-region is highlighted it is blue and the rest are grey. Hover a point for details.")
    table_view(df.rename(columns={"x": label[a], "y": label[b]}))


def page_equity(theme) -> None:
    st.header("Equity gaps")
    options = run("equity_options")
    label = {(r.concept, r.dimension): f"{data.SHORT_NAMES.get(r.concept, r.indicator)}: "
                                       f"{data.DIMENSIONS[r.dimension].split(' (')[0].lower()}"
             for r in options.itertuples()}
    key = st.selectbox("Indicator and group comparison", list(label), format_func=label.get, key="equity_choice")
    concept, dimension = key
    df = run("equity", concept, dimension)
    if df.empty:
        st.info("No gaps are available for this selection.")
        return
    a, b = df.iloc[0]["group_a"], df.iloc[0]["group_b"]
    m = st.columns(3)
    m[0].metric("Countries with data", len(df))
    m[1].metric(f"Median ratio ({a} / {b})", f"{df['ratio'].median():.2f}")
    m[2].metric("Countries where the ratio is above 1", pct((df["ratio"] > 1).mean()))
    show(charts.equity_strip(df, a, b, theme), theme, height=330)
    caveat("Each point is a country's most recent gap; the vertical tick is the sub-region median. A ratio of 1 "
           "means no gap. Groups are compared only within one survey or series. Modelled WHO series are preferred "
           "over survey series where both exist. Survey gaps rest on small samples.")
    table_view(df[["country", "un_subregion", "year", "value_a", "value_b", "ratio", "difference", "source"]]
               .rename(columns={"value_a": a, "value_b": b}))


def page_pipeline(theme) -> None:
    st.header("Pipeline and data quality")
    summary = run("pipeline_summary")
    info = run("snapshot_info")
    st.write("The dashboard reads tables produced by an automated pipeline: raw snapshots, typed staging, an "
             "integrated core layer, and these analytics marts. Every step is measured.")
    acc = summary["accounting"]
    if acc:
        m = st.columns(4)
        m[0].metric("Received", f"{acc['staged']:,}", help="Records in the raw snapshot after typing.")
        m[1].metric("Rejected", f"{acc['rejected']:,}", help="Stored with a reason, never dropped silently.")
        m[2].metric("Loaded", f"{acc['loaded']:,}", help="Kept with explicit dimensions (sex, wealth, residence, age).")
        m[3].metric("Analysis surface", f"{acc['selected']:,}",
                    help="One national-total value per source, country, indicator and year.")
        st.caption("Rejected records are stored with the reason. In this snapshot the rejections are empty "
                   "World Bank placeholder rows. Loaded rows that are not on the default surface are breakdowns "
                   "by sex, wealth, residence and age, and extra surveys, kept for equity analysis.")
    scores = summary["scores"]
    if scores is not None and len(scores):
        st.subheader("Before and after the pipeline")
        shown = scores.assign(before=scores["before"].map(pct), after=scores["after"].map(pct)).rename(
            columns={"dimension": "Dimension", "before": "Before (staging)", "after": "After (core)"})
        st.dataframe(shown, hide_index=True, width="stretch")
        caveat("Read with care: after the pipeline, validity, uniqueness and consistency are 100% largely by "
               "construction, because invalid rows are rejected and one row is selected per cell. They show the "
               "pipeline enforces these properties. The informative evidence is below.")
    fan = summary["fanout"]
    if fan is not None and len(fan):
        st.subheader("What a naive cross-source join does")
        st.write("Joining WHO, UNICEF and the World Bank on country and year without handling breakdowns multiplies "
                 "rows. After the pipeline every indicator joins 1:1.")
        show(charts.fanout_bars(fan, theme), theme, height=260)
        table_view(fan.rename(columns={"before": "Before", "after": "After"}))
    st.subheader("How well do the checks catch errors?")
    st.write("A seeded fault-injection experiment corrupted copies of the data (11,030 faults across 5 seeds) and "
             "measured what the real pipeline did with each. Rule-based faults were all handled as expected; "
             "value changes were detected at 0% for 2%, about 57% for 5%, 96% for 10% and at least 99.6% from "
             "25% upward. See `docs/results/fault_injection.md` for the method, the false-alarm rates and the "
             "limitations.")
    with st.expander("Build parameters for this snapshot"):
        st.json({"snapshot": info["run_id"], **info["parameters"]})


def main() -> None:
    st.set_page_config(page_title="HealthBridge", layout="wide", initial_sidebar_state="expanded")
    alt.data_transformers.disable_max_rows()
    theme = current_theme()
    with st.sidebar:
        st.markdown("### HealthBridge")
        page = st.radio("Page", PAGES, key="page", label_visibility="collapsed")
        st.caption("Descriptive analysis of public data. Not clinical or policy advice.")
        try:
            info = run("snapshot_info")
            st.caption(f"Snapshot {info['run_id']}")
        except LookupError:
            pass
    try:
        {"Overview": page_overview, "Country profile": page_country, "Compare countries": page_compare,
         "Relationships": page_relationships, "Equity gaps": page_equity,
         "Pipeline and quality": page_pipeline}[page](theme)
    except LookupError as error:
        st.error(str(error))


main()
