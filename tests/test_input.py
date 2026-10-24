import pytest

from finology_scraper.input import InvalidCompanyInput, parse_company_input


@pytest.mark.parametrize("value,identifier,mode", [
    ("TCS", "TCS", None),
    (" SCRIP-132540 ", "SCRIP-132540", None),
    ("https://ticker.finology.in/company/TCS", "TCS", None),
    ("https://ticker.finology.in/company/TCS/?mode=C#balance", "TCS", "C"),
])
def test_supported_targets(value, identifier, mode):
    target = parse_company_input(value)
    assert (target.identifier, target.url_mode) == (identifier, mode)


@pytest.mark.parametrize("value", [
    "", " ", "532540", "Tata Consultancy Services", "../TCS", "TCS/foo",
    "http://ticker.finology.in/company/TCS",
    "https://ticker.finology.in.evil.example/company/TCS",
    "https://evil.example/company/TCS",
    "https://user:secret@ticker.finology.in/company/TCS",
    "https://ticker.finology.in:8080/company/TCS",
    "https://ticker.finology.in/company/TCS%2F..",
    "https://ticker.finology.in/company/TCS?mode=C&mode=S",
    "https://ticker.finology.in/company/TCS?mode=unknown",
    "https://ticker.finology.in/company/TCS?mode=",
    "https://ticker.finology.in/company/TCS?other=1",
])
def test_invalid_targets_are_rejected_before_network_access(value):
    with pytest.raises(InvalidCompanyInput):
        parse_company_input(value)
