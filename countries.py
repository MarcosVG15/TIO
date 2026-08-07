"""Country name to ISO 3166-1 alpha-2.

Nuitee's hotel search requires `countryCode` and refuses to answer without it,
while TIO stores country *names* throughout - the corpus, the trip, the plan.
Without this bridge every hotel lookup returned nothing, silently: the provider
was configured, the key worked, and accommodation simply never appeared.

A table rather than a library because the input is not arbitrary. It is the
`country` column of the locations corpus, which is OSM/Wikidata English names,
so the mapping is small, exact and auditable. Unknown names return None and the
caller degrades to an estimate rather than guessing a code and pricing hotels in
the wrong country.
"""
from __future__ import annotations

from typing import Optional

_ISO2: dict[str, str] = {
    # Europe
    "albania": "AL", "andorra": "AD", "austria": "AT", "belarus": "BY",
    "belgium": "BE", "bosnia and herzegovina": "BA", "bulgaria": "BG",
    "croatia": "HR", "cyprus": "CY", "czechia": "CZ", "czech republic": "CZ",
    "denmark": "DK", "estonia": "EE", "finland": "FI", "france": "FR",
    "germany": "DE", "greece": "GR", "hungary": "HU", "iceland": "IS",
    "ireland": "IE", "italy": "IT", "kosovo": "XK", "latvia": "LV",
    "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU", "malta": "MT",
    "moldova": "MD", "monaco": "MC", "montenegro": "ME", "netherlands": "NL",
    "north macedonia": "MK", "norway": "NO", "poland": "PL", "portugal": "PT",
    "romania": "RO", "russia": "RU", "san marino": "SM", "serbia": "RS",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
    "switzerland": "CH", "ukraine": "UA", "united kingdom": "GB",
    "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "vatican city": "VA", "holy see": "VA",
    # Americas
    "argentina": "AR", "bahamas": "BS", "barbados": "BB", "belize": "BZ",
    "bolivia": "BO", "brazil": "BR", "canada": "CA", "chile": "CL",
    "colombia": "CO", "costa rica": "CR", "cuba": "CU", "dominican republic": "DO",
    "ecuador": "EC", "el salvador": "SV", "guatemala": "GT", "honduras": "HN",
    "jamaica": "JM", "mexico": "MX", "nicaragua": "NI", "panama": "PA",
    "paraguay": "PY", "peru": "PE", "puerto rico": "PR", "uruguay": "UY",
    "united states": "US", "united states of america": "US", "usa": "US",
    "venezuela": "VE",
    # Asia
    "armenia": "AM", "azerbaijan": "AZ", "bahrain": "BH", "bangladesh": "BD",
    "cambodia": "KH", "china": "CN", "georgia": "GE", "hong kong": "HK",
    "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ", "israel": "IL",
    "japan": "JP", "jordan": "JO", "kazakhstan": "KZ", "kuwait": "KW",
    "laos": "LA", "lebanon": "LB", "malaysia": "MY", "maldives": "MV",
    "mongolia": "MN", "myanmar": "MM", "nepal": "NP", "oman": "OM",
    "pakistan": "PK", "philippines": "PH", "qatar": "QA", "saudi arabia": "SA",
    "singapore": "SG", "south korea": "KR", "korea": "KR", "sri lanka": "LK",
    "taiwan": "TW", "thailand": "TH", "turkey": "TR", "türkiye": "TR",
    "united arab emirates": "AE", "uzbekistan": "UZ", "vietnam": "VN",
    # Africa
    "algeria": "DZ", "botswana": "BW", "egypt": "EG", "ethiopia": "ET",
    "ghana": "GH", "kenya": "KE", "madagascar": "MG", "mauritius": "MU",
    "morocco": "MA", "mozambique": "MZ", "namibia": "NA", "nigeria": "NG",
    "rwanda": "RW", "senegal": "SN", "seychelles": "SC", "south africa": "ZA",
    "tanzania": "TZ", "tunisia": "TN", "uganda": "UG", "zambia": "ZM",
    "zimbabwe": "ZW",
    # Oceania
    "australia": "AU", "fiji": "FJ", "new zealand": "NZ",
    "papua new guinea": "PG",
}


def iso2(country: Optional[str]) -> Optional[str]:
    """The two-letter code for a country name, or None if it is not known.

    A name already in code form ("IT") is passed through, so callers holding
    either shape can use this without checking first.
    """
    if not country:
        return None
    text = str(country).strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _ISO2.get(text.casefold())
