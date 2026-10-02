"""Chart theme: the validated reference palette as named roles, for light and dark surfaces.

The categorical slots were checked with the palette validator (adjacent-pair colour-blind
separation, normal-vision floor, lightness band, chroma floor, contrast) for both modes. In light
mode the aqua and yellow slots sit below 3:1 against the surface, so every chart ships with a
table view and visible labels, as the validator requires. Series counts are capped at the
palette's validated ladder; beyond that a chart highlights one group against grey context.
"""
from __future__ import annotations

from dataclasses import dataclass

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Theme:
    mode: str
    surface: str      # chart surface
    text: str         # primary ink
    text2: str        # secondary ink
    muted: str        # axis labels
    grid: str         # hairline gridlines
    axis: str         # baseline and axis rules
    context: str      # de-emphasised grey for context marks
    series: tuple[str, ...]   # categorical slots 1-4 in fixed order


LIGHT = Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#b4b3ac",
              ("#2a78d6", "#eb6834", "#1baf7a", "#eda100"))
DARK = Theme("dark", "#1a1a19", "#ffffff", "#c3c2b7", "#898781", "#2c2c2a", "#383835", "#77766f",
             ("#3987e5", "#d95926", "#199e70", "#c98500"))

# Quality tiers are identity (categorical), assigned to fixed slots. Orange for conflict is
# deliberate; the status palette is reserved for good/bad meanings and is not used here.
TIER_ORDER = ("single_evidence_group", "cross_validated", "conflict")
TIER_SLOT = {"single_evidence_group": 0, "conflict": 1, "cross_validated": 2}
MAX_COMPARED = 4   # series ladder: four lines is the most that stays gate-safe with direct labels


def get_theme(mode: str | None) -> Theme:
    return DARK if mode == "dark" else LIGHT


def tier_colors(theme: Theme) -> list[str]:
    """Colours in TIER_ORDER."""
    return [theme.series[TIER_SLOT[t]] for t in TIER_ORDER]


def assign_slots(existing: dict[str, int], selected: list[str], max_slots: int = MAX_COMPARED) -> dict[str, int]:
    """Give each selected entity a colour slot that it keeps for as long as it stays selected.

    A new entity takes the lowest free slot; entities that leave free theirs. Colour therefore
    follows the entity, never its position in the list, so changing the selection does not
    repaint the survivors.
    """
    if len(selected) > max_slots:
        raise ValueError(f"at most {max_slots} entities can be shown with distinct colours")
    slots = {name: slot for name, slot in existing.items() if name in selected}
    for name in selected:
        if name not in slots:
            slots[name] = min(set(range(max_slots)) - set(slots.values()))
    return slots
