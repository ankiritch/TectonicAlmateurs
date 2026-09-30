"""Places a document can be labeled for: a country, a continent, or worldwide."""

from __future__ import annotations

WORLDWIDE = "Worldwide"

CONTINENTS: tuple[str, ...] = (
    "Africa",
    "Antarctica",
    "Asia",
    "Europe",
    "North America",
    "Oceania",
    "South America",
)

# Country -> continent. Enough of a list for the prototype dropdown.
COUNTRIES: dict[str, str] = {
    "Algeria": "Africa",
    "Argentina": "South America",
    "Australia": "Oceania",
    "Brazil": "South America",
    "Canada": "North America",
    "China": "Asia",
    "Egypt": "Africa",
    "France": "Europe",
    "Germany": "Europe",
    "India": "Asia",
    "Indonesia": "Asia",
    "Italy": "Europe",
    "Japan": "Asia",
    "Kenya": "Africa",
    "Mexico": "North America",
    "Morocco": "Africa",
    "Netherlands": "Europe",
    "New Zealand": "Oceania",
    "Nigeria": "Africa",
    "Norway": "Europe",
    "South Africa": "Africa",
    "South Korea": "Asia",
    "Spain": "Europe",
    "Sweden": "Europe",
    "Switzerland": "Europe",
    "United Kingdom": "Europe",
    "United States": "North America",
}


def grouped_regions() -> list[tuple[str, list[str]]]:
    return [
        ("Worldwide", [WORLDWIDE]),
        ("Continents", list(CONTINENTS)),
        ("Countries", sorted(COUNTRIES)),
    ]


def _index() -> dict[str, str]:
    labels = [WORLDWIDE, *CONTINENTS, *COUNTRIES]
    return {label.casefold(): label for label in labels}


def canonical_region(label: str | None) -> str | None:
    if not label:
        return None
    return _index().get(label.strip().casefold())


def continent_of(label: str) -> str | None:
    canonical = canonical_region(label)
    if canonical is None or canonical == WORLDWIDE:
        return None
    if canonical in CONTINENTS:
        return canonical
    return COUNTRIES.get(canonical)


def region_match(document_label: str, wanted: str) -> float:
    """1.0 exact (or a country inside a requested continent), else partial or 0."""
    doc = canonical_region(document_label)
    want = canonical_region(wanted)
    if doc is None or want is None:
        return 0.0
    if doc == want:
        return 1.0
    if doc == WORLDWIDE:
        return 0.5
    if want == WORLDWIDE:
        return 0.0
    doc_continent = continent_of(doc)
    if want in CONTINENTS and doc_continent == want:
        return 1.0
    if doc in CONTINENTS and continent_of(want) == doc:
        return 0.5
    return 0.0
