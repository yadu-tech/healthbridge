# Dashboard

A Streamlit app over the analytics marts. It reads only the marts and the pipeline and data-quality log tables, never raw or staging data, so anything on screen can be traced to a documented table ([analytics.md](analytics.md)).

```bash
pip install -e ".[dashboard]"
docker compose up -d                              # the database
python -m healthbridge.marts build                # if the marts are not built yet
streamlit run src/healthbridge/dashboard/app.py
```

## Pages

| Page | What it answers |
|---|---|
| Overview | How many countries, indicators and values there are, and **how far each indicator can be trusted** (the share of values that are cross-validated, from a single evidence group, or in conflict) |
| Country profile | One country: latest values with their age and quality tier, and each indicator's history against its sub-region's median and range, as small multiples |
| Compare countries | Up to four countries on one indicator. Colours stay with a country while it is selected |
| Relationships | Two indicators across countries, with the rank correlation, its approximate interval and the number of countries, behind a prominent "associations, not causes" notice |
| Equity gaps | The most recent poorest-vs-richest, female-vs-male or rural-vs-urban gap per country, grouped by sub-region against parity |
| Pipeline and quality | Records received, rejected and loaded; before and after the pipeline; what a naive join does; a summary of the fault-injection results |

Every chart has a **table view** beneath it with the same values, so no number is available only by hovering.

## Design decisions

- **Trust is part of the interface.** Quality tiers appear on the overview, in country tables and as markers on every time series (a ring for a flagged value, a triangle where independent sources conflict). The dashboard does not present uncertain values as settled.
- **Descriptive only.** The relationships page carries its warning above the controls, not in a footnote. Countries are shown side by side and are never ranked; the equity page groups by sub-region and marks parity instead of sorting.
- **Emphasis instead of rainbows.** Where a chart would need more colours than are safe, it colours one group and greys the rest. The scatter is a single colour until a sub-region is highlighted.
- **No dual axes.** Indicators with different units are separate small multiples.
- **Colour follows the entity.** A country keeps its colour while it is selected, however the other selections change.

## Colour and accessibility

The categorical palette is the reference palette from the data-visualisation guidance, checked with its validator for both modes. First four slots, adjacent pairs:

| Mode | Lightness band | Chroma | Colour-blind separation (worst pair) | Normal-vision floor | Contrast vs surface |
|---|---|---|---|---|---|
| Light | pass | pass | pass (ΔE 9.1) | pass (22.9) | **warning**: aqua and yellow are below 3:1 |
| Dark | pass | pass | pass (ΔE 8.4) | pass (19.8) | pass |

The light-mode warning requires visible labels or a table view; both are provided. Segment labels are dark on aqua and white on blue for the same reason. A four-colour comparison is the maximum (the series ladder for line charts, with direct labels); scatter charts use emphasis because all-pairs forms are only validated to three colours. The app follows the viewer's light or dark theme. Text uses text colours, never a series colour. Hover targets are larger than the marks, and filters sit in one row above the charts they control.

## Verification

- **Data layer** (SQL to DataFrames, no Streamlit) is tested against a database: the scatter's points are exactly the countries behind the reported correlation, trend slopes and equity pairs match known values, and the pipeline summary degrades gracefully when no quality runs exist.
- **Charts** are built for both themes and validated against the Vega-Lite schema; the palette hex values are pinned by a test so an accidental edit is caught.
- **Pages** are each run headlessly with Streamlit's `AppTest`, checking for exceptions and key figures.
- **Visual review** in a browser, in both colour schemes, found problems that no automated test would: a chart overflowing its container, clipped axis labels, a nearly invisible grey in dark mode, and truncated tile labels. All were fixed.

## Limitations

- It reads the most recently built snapshot and has no authentication or deployment setup.
- No map: country geometry is not part of the data, and a choropleth would also invite ranking.
- The fault-injection summary on the last page is a short static text; the report in `docs/results/fault_injection.md` is the source.
