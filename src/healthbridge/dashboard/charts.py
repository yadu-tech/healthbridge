"""Altair chart builders. Pure functions: a DataFrame and a Theme in, a chart out.

Rules applied throughout (from the data-visualisation guidance): thin marks; hairline solid
grid; no dual axes (different indicators get separate small multiples); a legend whenever there
are two or more series, with a few direct labels; hover targets larger than the marks; a 2px
surface ring where marks overlap; a table view for every chart is provided by the app; and
colour follows the entity, never its rank.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from healthbridge.dashboard.data import SHORT_NAMES, TIER_LABELS
from healthbridge.dashboard.theme import TIER_ORDER, Theme, tier_colors

HIT_AREA = 700   # point size whose diameter (~26px) gives a comfortable hover target


def style(chart, theme: Theme, height: int | None = None):
    """Apply the chart chrome: surface, hairline grid, recessive axes, system sans."""
    chart = chart.properties(autosize=alt.AutoSizeParams(type="fit", contains="padding"))
    if height is not None:
        chart = chart.properties(height=height)
    return (chart.configure(background=theme.surface, font='system-ui, "Segoe UI", sans-serif')
            .configure_view(stroke=None)
            .configure_axis(gridColor=theme.grid, domainColor=theme.axis, tickColor=theme.axis,
                            labelColor=theme.muted, titleColor=theme.text2, labelFontSize=11,
                            titleFontSize=11, titleFontWeight="normal", gridDash=[])
            .configure_legend(labelColor=theme.text2, titleColor=theme.text2, labelFontSize=11,
                              titleFontSize=11, orient="top", symbolStrokeWidth=0)
            .configure_title(color=theme.text, fontSize=13, fontWeight="normal", anchor="start"))


# --- data trust ----------------------------------------------------------------------------

def trust_bars(trust: pd.DataFrame, theme: Theme):
    """Share of each indicator's values in each quality tier, as 100% stacked horizontal bars."""
    trust = trust.assign(indicator=trust["concept"].map(SHORT_NAMES))
    long = trust.melt(id_vars=["indicator"], value_vars=["share_single_evidence", "share_cross_validated",
                                                         "share_conflict"], var_name="tier", value_name="share")
    long["tier"] = long["tier"].map({"share_single_evidence": "single_evidence_group",
                                     "share_cross_validated": "cross_validated",
                                     "share_conflict": "conflict"})
    long["label"] = long["tier"].map(TIER_LABELS)
    long["order"] = long["tier"].map({t: i for i, t in enumerate(TIER_ORDER)})
    labels = [TIER_LABELS[t] for t in TIER_ORDER]
    colour = alt.Color("label:N", title=None, sort=labels,
                       scale=alt.Scale(domain=labels, range=tier_colors(theme)))
    base = alt.Chart(long).encode(
        y=alt.Y("indicator:N", title=None, sort=alt.SortField("indicator"), axis=alt.Axis(labelLimit=260)),
        x=alt.X("share:Q", stack="normalize", axis=alt.Axis(format="%", title="Share of values")),
        order=alt.Order("order:Q"),
    )
    bars = base.mark_bar(size=18, stroke=theme.surface, strokeWidth=2).encode(
        color=colour,
        tooltip=[alt.Tooltip("indicator:N"), alt.Tooltip("label:N", title="Tier"),
                 alt.Tooltip("share:Q", format=".1%")])
    # a label is drawn inside a segment only where it fits
    text = (base.transform_filter("datum.share >= 0.14")
            .mark_text(fontSize=11, dx=0)
            # white on the blue segment, near-black on aqua: white on light-mode aqua is only ~2.7:1
            .encode(text=alt.Text("share:Q", format=".0%"), detail="label:N",
                    color=alt.condition("datum.tier == 'single_evidence_group'", alt.value("#ffffff"),
                                        alt.value("#0b0b0b")),
                    x=alt.X("share:Q", stack="normalize", bandPosition=0.5)))
    return bars + text


# --- time series ---------------------------------------------------------------------------

def _flag_layers(df: pd.DataFrame, x, y, theme: Theme):
    """Hollow ring for flagged points; triangle for conflict between independent sources."""
    flagged = df[(df["outlier_flag"]) | (df["source_disagreement"])]
    conflict = df[df["quality_tier"] == "conflict"]
    layers = []
    if len(flagged):
        layers.append(alt.Chart(flagged).mark_point(shape="circle", size=110, filled=False, strokeWidth=2,
                                                    color=theme.text).encode(x=x, y=y))
    if len(conflict):
        layers.append(alt.Chart(conflict).mark_point(shape="triangle-up", size=110, filled=True,
                                                     color=theme.series[1], stroke=theme.surface,
                                                     strokeWidth=2).encode(x=x, y=y))
    return layers


def country_multiple(country: pd.DataFrame, band: pd.DataFrame, title: str, unit: str, theme: Theme):
    """One small multiple: the country's line against its sub-region's median and range."""
    x = alt.X("year:Q", title=None, axis=alt.Axis(format="d", tickCount=6))
    y = alt.Y("value:Q", title=unit, scale=alt.Scale(zero=True), axis=alt.Axis(titleAngle=0, titleAlign="left",
                                                                           titleY=-8, titleX=0))
    layers = []
    if len(band):
        b = band.rename(columns={"median_value": "median", "min_value": "low", "max_value": "high"})
        layers.append(alt.Chart(b).mark_area(color=theme.context, opacity=0.22).encode(
            x=x, y=alt.Y("low:Q", scale=alt.Scale(zero=True)), y2="high:Q"))
        layers.append(alt.Chart(b).mark_line(color=theme.context, strokeWidth=1.5).encode(
            x=x, y=alt.Y("median:Q")))
    line = alt.Chart(country).mark_line(color=theme.series[0], strokeWidth=2).encode(x=x, y=y)
    layers.append(line)
    layers += _flag_layers(country, x, y, theme)
    tip = [alt.Tooltip("year:Q", format="d"), alt.Tooltip("value:Q", format=",.1f", title=unit),
           alt.Tooltip("quality_tier:N", title="Quality tier"),
           alt.Tooltip("n_sources:Q", title="Sources"),
           alt.Tooltip("outlier_flag:N", title="Outlier flag"),
           alt.Tooltip("source_disagreement:N", title="Source disagreement")]
    layers.append(alt.Chart(country).mark_point(size=HIT_AREA, opacity=0).encode(x=x, y=y, tooltip=tip))
    return alt.layer(*layers).properties(title=title, width="container")


def compare_lines(df: pd.DataFrame, colour_of: dict[str, str], unit: str, theme: Theme):
    """Up to four countries on one axis; each keeps the colour it was first given."""
    names = list(colour_of)
    scale = alt.Scale(domain=names, range=[colour_of[n] for n in names])
    x = alt.X("year:Q", title=None, axis=alt.Axis(format="d", tickCount=8))
    y = alt.Y("value:Q", title=unit, scale=alt.Scale(zero=True))
    colour = alt.Color("country:N", scale=scale, legend=alt.Legend(title=None, symbolType="stroke",
                                                                   symbolStrokeWidth=3))
    line = alt.Chart(df).mark_line(strokeWidth=2).encode(x=x, y=y, color=colour)
    ends = df.sort_values("year").groupby("country", as_index=False).tail(1)
    label = alt.Chart(ends).mark_text(align="left", dx=6, fontSize=11, color=theme.text2).encode(
        x=x, y=y, text="country:N")
    layers = [line, label] + _flag_layers(df, x, y, theme)
    tip = [alt.Tooltip("country:N"), alt.Tooltip("year:Q", format="d"),
           alt.Tooltip("value:Q", format=",.1f", title=unit), alt.Tooltip("quality_tier:N", title="Quality tier")]
    layers.append(alt.Chart(df).mark_point(size=HIT_AREA, opacity=0).encode(x=x, y=y, color=colour, tooltip=tip))
    return alt.layer(*layers).properties(width="container")


# --- relationships -------------------------------------------------------------------------

def scatter_chart(df: pd.DataFrame, highlight: str | None, selected: str | None, x_title: str, y_title: str,
                  theme: Theme):
    """Countries as points. Emphasis, not a rainbow: one sub-region is coloured, the rest are grey."""
    d = df.copy()
    x = alt.X("x:Q", title=x_title, scale=alt.Scale(zero=False))
    y = alt.Y("y:Q", title=y_title, scale=alt.Scale(zero=False))
    tip = [alt.Tooltip("country:N"), alt.Tooltip("un_subregion:N", title="Sub-region"),
           alt.Tooltip("x:Q", format=",.1f", title=x_title), alt.Tooltip("y:Q", format=",.1f", title=y_title)]
    if highlight:
        # emphasis: the chosen sub-region in colour, everything else as grey context
        in_focus = d[d["un_subregion"] == highlight]
        rest = d[d["un_subregion"] != highlight]
    else:
        # nothing is singled out, so every country is simply data and gets the primary colour
        in_focus, rest = d, d.iloc[0:0]
    layers = []
    if len(rest):
        layers.append(alt.Chart(rest).mark_point(filled=True, size=70, color=theme.context, opacity=0.85,
                                                 stroke=theme.surface, strokeWidth=2).encode(x=x, y=y))
    if len(in_focus):
        layers.append(alt.Chart(in_focus).mark_point(filled=True, size=90, color=theme.series[0],
                                                     stroke=theme.surface, strokeWidth=2).encode(x=x, y=y))
    chosen = d[d["country"] == selected] if selected else d.iloc[0:0]
    if len(chosen):
        layers.append(alt.Chart(chosen).mark_point(filled=False, size=220, strokeWidth=2.5,
                                                   color=theme.series[1]).encode(x=x, y=y))
        layers.append(alt.Chart(chosen).mark_text(align="left", dx=12, fontSize=12, color=theme.text).encode(
            x=x, y=y, text="country:N"))
    layers.append(alt.Chart(d).mark_point(size=HIT_AREA, opacity=0).encode(x=x, y=y, tooltip=tip))
    return alt.layer(*layers).properties(width="container")


# --- equity --------------------------------------------------------------------------------

def _jitter(iso3: str) -> float:
    """Deterministic vertical offset in [-0.3, 0.3] so a strip plot reads the same every render."""
    return ((sum(ord(c) * (i + 3) for i, c in enumerate(iso3)) % 61) - 30) / 100


def equity_strip(df: pd.DataFrame, group_a: str, group_b: str, theme: Theme):
    """Each country's latest gap ratio, grouped by sub-region, against parity at 1."""
    d = df.copy()
    d["jitter"] = d["iso3"].map(_jitter)
    d["ratio_label"] = d["ratio"].round(2)
    lo, hi = float(d["ratio"].min()), float(d["ratio"].max())
    scale = alt.Scale(type="log", domain=[min(0.5, lo * 0.9), max(2, hi * 1.1)], nice=False)
    x = alt.X("ratio:Q", scale=scale, title=f"{group_a} / {group_b} (log scale, 1 = parity)",
              axis=alt.Axis(values=[0.1, 0.2, 0.5, 1, 2, 3, 5, 10], format="~g"))
    y = alt.Y("un_subregion:N", title=None, sort=alt.SortField("un_subregion"))
    tip = [alt.Tooltip("country:N"), alt.Tooltip("year:Q", format="d"),
           alt.Tooltip("value_a:Q", format=",.1f", title=group_a), alt.Tooltip("value_b:Q", format=",.1f", title=group_b),
           alt.Tooltip("ratio:Q", format=".2f", title="Ratio"), alt.Tooltip("source:N")]
    points = alt.Chart(d).mark_point(filled=True, size=90, color=theme.series[0], opacity=0.9,
                                     stroke=theme.surface, strokeWidth=2).encode(
        x=x, y=y, yOffset=alt.YOffset("jitter:Q", scale=alt.Scale(domain=[-1, 1])), tooltip=tip)
    parity = alt.Chart(pd.DataFrame({"r": [1.0]})).mark_rule(color=theme.muted, strokeWidth=1).encode(x="r:Q")
    medians = (alt.Chart(d).transform_aggregate(m="median(ratio)", groupby=["un_subregion"])
               .mark_tick(color=theme.text, thickness=2.5, size=26).encode(x="m:Q", y=y))
    return (parity + points + medians).properties(width="container")


# --- pipeline ------------------------------------------------------------------------------

def fanout_bars(fan: pd.DataFrame, theme: Theme):
    """Rows produced per country-year by a naive cross-source join, before the pipeline."""
    d = fan.sort_values("before", ascending=False).assign(
        indicator=lambda t: t["concept"].map(SHORT_NAMES))
    base = alt.Chart(d).encode(y=alt.Y("indicator:N", title=None, sort=list(d["indicator"]),
                                       axis=alt.Axis(labelLimit=260)),
                               x=alt.X("before:Q", title="Joined rows per country-year (1 = a clean 1:1 join)"))
    bars = base.mark_bar(size=16, color=theme.series[0],
                         cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        tooltip=[alt.Tooltip("indicator:N"), alt.Tooltip("before:Q", format=",.1f", title="Rows per country-year")])
    labels = base.mark_text(align="left", dx=6, color=theme.text2, fontSize=11).encode(
        text=alt.Text("before:Q", format=",.1f"))
    return (bars + labels).properties(width="container")
