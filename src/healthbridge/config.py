"""Static project configuration: geographic scope and the concept/source registry.

A *concept* is a harmonized idea (e.g. under-5 mortality). Each source publishes it
under its own code; the registry below records those codes. Codes were verified
against the live APIs in docs/source_profile.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# The 54 African states recognised by the United Nations (ISO 3166-1 alpha-3).
_ISO3_CODES = ["DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CPV", "CMR", "CAF", "TCD", "COM", "COG", "COD", "CIV", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH", "GAB", "GMB", "GHA", "GIN", "GNB", "KEN", "LSO", "LBR", "LBY", "MDG", "MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA", "STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO", "TUN", "UGA", "ZMB", "ZWE"]
AFRICA_ISO3: tuple[str, ...] = tuple(_ISO3_CODES)

YEAR_START = 1990


def default_years() -> tuple[int, int]:
    return (YEAR_START, datetime.now(UTC).year)


@dataclass(frozen=True)
class Concept:
    name: str
    who_code: str
    worldbank_code: str
    unicef_flow: str
    unicef_indicator: str


CONCEPTS: tuple[Concept, ...] = (
    Concept("under5_mortality", "MDG_0000000007", "SH.DYN.MORT", "CME", "CME_MRY0T4"),
    Concept("neonatal_mortality", "WHOSIS_000003", "SH.DYN.NMRT", "CME", "CME_MRM0"),
    Concept("maternal_mortality_ratio", "MDG_0000000026", "SH.STA.MMRT", "MNCH", "MNCH_MMR"),
    Concept("skilled_birth_attendance", "MDG_0000000025", "SH.STA.BRTC.ZS", "MNCH", "MNCH_SAB"),
    Concept("dtp3_coverage", "WHS4_100", "SH.IMM.IDPT", "IMMUNISATION", "IM_DTP3"),
    Concept("measles_mcv1_coverage", "WHS8_110", "SH.IMM.MEAS", "IMMUNISATION", "IM_MCV1"),
    Concept("stunting_prevalence", "NUTSTUNTINGPREV", "SH.STA.STNT.ZS", "NUTRITION", "NT_ANT_HAZ_NE2"),
)

WHO_BASE = "https://ghoapi.azureedge.net/api"
WORLDBANK_BASE = "https://api.worldbank.org/v2"
UNICEF_BASE = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest"
