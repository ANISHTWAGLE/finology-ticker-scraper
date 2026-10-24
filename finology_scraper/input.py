"""Validate company targets before any network request is made."""

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, urlsplit


class InvalidCompanyInput(ValueError):
    """The company input is not a supported Finology page or identifier."""


@dataclass(frozen=True)
class CompanyTarget:
    identifier: str
    url_mode: str | None


def parse_company_input(value: str) -> CompanyTarget:
    """Accept a Finology page URL or its company slug, not arbitrary websites.

    URL slugs include NSE-like symbols (TCS) and Finology SCRIP- identifiers.
    A bare BSE code, ISIN or company name is not silently resolved to a slug.
    This validates input shape only; existence must be checked against the source.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidCompanyInput("Provide a Finology company page URL or company-page identifier.")
    value = value.strip()
    if len(value) > 2048:
        raise InvalidCompanyInput("Company input is too long.")
    mode = None
    if "://" in value:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise InvalidCompanyInput("Invalid company URL.") from exc
        if (parsed.scheme != "https" or parsed.hostname != "ticker.finology.in"
                or parsed.username is not None or parsed.password is not None
                or port not in {None, 443}):
            raise InvalidCompanyInput("Use an HTTPS company page on ticker.finology.in.")
        match = re.fullmatch(r"/company/([A-Za-z0-9_-]+)/?", parsed.path)
        if not match:
            raise InvalidCompanyInput("Expected URL path /company/<identifier>.")
        identifier = match.group(1)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {"mode"}:
            raise InvalidCompanyInput("Only the optional mode=S or mode=C URL parameter is supported.")
        if "mode" in query:
            if len(query["mode"]) != 1 or query["mode"][0] not in {"S", "C"}:
                raise InvalidCompanyInput("URL mode must be exactly S or C.")
            mode = query["mode"][0]
    else:
        identifier = value
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier)
            or identifier.isdigit()):
        raise InvalidCompanyInput(
            "Use the Finology company-page identifier, such as TCS or SCRIP-132540; "
            "bare exchange codes, company names and ISIN lookup are not implemented."
        )
    return CompanyTarget(identifier=identifier, url_mode=mode)
