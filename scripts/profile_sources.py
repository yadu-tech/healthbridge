"""Source profiling pass: what do WHO, World Bank and UNICEF actually give us?

Pulls a candidate set of maternal/child-health concepts for the 54 African
states from each source, then reports coverage, name variants, legacy/duplicate
codes and cross-source disagreement. Output: docs/source_profile.md.

This is exploratory (pre-pipeline); it only informs scope decisions.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "source_profile.md"

AFRICA_ISO3 = ["DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CPV", "CMR", "CAF", "TCD", "COM", "COG", "COD", "CIV", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH", "GAB", "GMB", "GHA", "GIN", "GNB", "KEN", "LSO", "LBR", "LBY", "MDG", "MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA", "STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO", "TUN", "UGA", "ZMB", "ZWE"]
assert len(AFRICA_ISO3) == 54, len(AFRICA_ISO3)

# concept -> (WHO GHO code(s), World Bank code, UNICEF (dataflow, indicator))
CONCEPTS = {
    "under5_mortality": (["MDG_0000000007", "u5mr"], "SH.DYN.MORT", ("CME", "CME_MRY0T4")),
    "neonatal_mortality": (["WHOSIS_000003", "nmr"], "SH.DYN.NMRT", ("CME", "CME_MRM0")),
    "maternal_mortality_ratio": (["MDG_0000000026"], "SH.STA.MMRT", ("MNCH", "MNCH_MMR")),
    "skilled_birth_attendance": (["MDG_0000000025"], "SH.STA.BRTC.ZS", ("MNCH", "MNCH_SAB")),
    "dtp3_coverage": (["WHS4_100"], "SH.IMM.IDPT", ("IMMUNISATION", "IM_DTP3")),
    "measles_mcv1_coverage": (["WHS8_110"], "SH.IMM.MEAS", ("IMMUNISATION", "IM_MCV1")),
    "stunting_prevalence": (["NUTSTUNTINGPREV"], "SH.STA.STNT.ZS", ("NUTRITION", "NT_ANT_HAZ_NE2")),
}

WHO = "https://ghoapi.azureedge.net/api"
WB = "https://api.worldbank.org/v2"
UNICEF = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest"


def get_who(code: str) -> pd.DataFrame:
    rows, url = [], f"{WHO}/{code}?$filter=SpatialDimType eq 'COUNTRY'"
    while url:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        j = r.json()
        rows += j["value"]
        url = j.get("@odata.nextLink")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["SpatialDim"].isin(AFRICA_ISO3)]
    df = df[df["Dim1"].isna() | (df["Dim1"] == "SEX_BTSX")]  # both sexes only
    df = df[df["Dim3"].isna() | (df["Dim3"] == "WEALTHQUINTILE_TOTL")]  # national total, not per quintile
    return pd.DataFrame(
        {"iso3": df["SpatialDim"], "year": df["TimeDim"], "value": df["NumericValue"],
         "dim1": df["Dim1"], "source_code": code}
    )


def get_wb(code: str) -> pd.DataFrame:
    url = f"{WB}/country/{';'.join(AFRICA_ISO3)}/indicator/{code}"
    rows, page = [], 1
    while True:
        r = requests.get(url, params={"format": "json", "per_page": 20000, "page": page,
                                      "date": "1990:2025"}, timeout=120)
        r.raise_for_status()
        meta, data = r.json()[0], r.json()[1] or []
        rows += data
        if page >= meta["pages"]:
            break
        page += 1
    return pd.DataFrame(
        {"iso3": [d["countryiso3code"] for d in rows], "country_name": [d["country"]["value"] for d in rows],
         "year": [int(d["date"]) for d in rows], "value": [d["value"] for d in rows],
         "source_code": code}
    )


def get_unicef(flow: str, indicator: str) -> pd.DataFrame:
    url = f"{UNICEF}/data/UNICEF,{flow},1.0/{'+'.join(AFRICA_ISO3)}.{indicator}?format=csv"
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return df


def unicef_headline(df: pd.DataFrame, indicator: str) -> pd.DataFrame:
    """Reduce UNICEF's disaggregated series to the national/total row per country-year.

    Duplicate (iso3, year) keys that remain come from multiple DATA_SOURCE values
    (e.g. several surveys for one year); they are kept so profiling can count them.
    """
    if df.empty:
        return pd.DataFrame(columns=["iso3", "year", "value", "source_code"])
    df = df.copy()
    for c in ("SEX", "AGE_AT_BIRTH", "WEALTH_QUINTILE", "RESIDENCE", "MOTHER_EDUCATION"):
        if c in df.columns:
            df = df[df[c].isin(["_T"]) | df[c].isna()]
    df["year"] = pd.to_numeric(df["TIME_PERIOD"].astype(str).str[:4], errors="coerce")
    return pd.DataFrame({"iso3": df["REF_AREA"], "year": df["year"],
                         "value": pd.to_numeric(df["OBS_VALUE"], errors="coerce"),
                         "source_code": indicator}).dropna(subset=["year"])


def coverage(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"records": 0, "countries": 0, "years": "-", "missing_pct": "-", "dup_keys": 0}
    d = df.dropna(subset=["year"])
    return {
        "records": len(d),
        "countries": d["iso3"].nunique(),
        "years": f"{int(d['year'].min())}-{int(d['year'].max())}",
        "missing_pct": round(100 * d["value"].isna().mean(), 1),
        "dup_keys": int(d.duplicated(["iso3", "year"]).sum()),
    }


def md(df: pd.DataFrame) -> str:
    """Minimal markdown table (avoids the optional `tabulate` dependency)."""
    if df.empty:
        return "_no rows_"
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(out)


def main() -> None:
    lines = ["# Source profile (auto-generated by scripts/profile_sources.py)\n"]
    cov_rows, agree_rows, name_variants = [], [], {}
    for concept, (who_codes, wb_code, (uf, ui)) in CONCEPTS.items():
        frames = {}
        for wc in who_codes:
            try:
                frames[f"WHO:{wc}"] = get_who(wc)
            except Exception as e:  # noqa: BLE001
                frames[f"WHO:{wc}"] = pd.DataFrame()
                print("WHO fail", wc, e)
        try:
            frames["WB"] = get_wb(wb_code)
        except Exception as e:  # noqa: BLE001
            frames["WB"] = pd.DataFrame(); print("WB fail", wb_code, e)
        try:
            frames["UNICEF"] = unicef_headline(get_unicef(uf, ui), ui)
        except Exception as e:  # noqa: BLE001
            frames["UNICEF"] = pd.DataFrame(); print("UNICEF fail", ui, e)

        for src, df in frames.items():
            cov_rows.append({"concept": concept, "source": src, **coverage(df)})
            if "country_name" in df.columns and not df.empty:
                name_variants.setdefault(src.split(":")[0], {}).update(
                    df.drop_duplicates("iso3").set_index("iso3")["country_name"].to_dict())

        # cross-source agreement on shared (iso3, year) keys
        clean = {s: d.dropna(subset=["value"]).drop_duplicates(["iso3", "year"])
                 .set_index(["iso3", "year"])["value"] for s, d in frames.items() if not d.empty}
        names = list(clean)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = clean[names[i]], clean[names[j]]
                m = pd.concat([a, b], axis=1, join="inner").dropna()
                if len(m) < 5:
                    continue
                rel = (m.iloc[:, 0] - m.iloc[:, 1]).abs() / m.iloc[:, 1].abs().replace(0, pd.NA)
                agree_rows.append({"concept": concept, "pair": f"{names[i]} vs {names[j]}",
                                   "shared_keys": len(m), "median_rel_diff_%": round(100 * rel.median(), 2),
                                   "share_gt_10%": round(100 * (rel > 0.10).mean(), 1),
                                   "share_exact": round(100 * (rel == 0).mean(), 1)})
        print("done", concept)

    lines += ["## Coverage per source and concept (54 African states)\n",
              md(pd.DataFrame(cov_rows)), "\n",
              "## Cross-source agreement on shared country-years\n",
              md(pd.DataFrame(agree_rows)), "\n"]

    wb_names, un_names = name_variants.get("WB", {}), name_variants.get("UNICEF", {})
    diffs = [(k, wb_names[k], un_names[k]) for k in sorted(set(wb_names) & set(un_names))
             if wb_names[k].strip().lower() != un_names[k].strip().lower()]
    lines += ["## Country-name variants: World Bank vs UNICEF (same ISO3)\n",
              md(pd.DataFrame(diffs, columns=["iso3", "world_bank", "unicef"])), "\n"]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
