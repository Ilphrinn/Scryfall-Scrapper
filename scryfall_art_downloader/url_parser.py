from __future__ import annotations

from typing import TypeAlias
from urllib.parse import urlparse

from .models import CardRequest, SetRequest


ScryfallRequest: TypeAlias = SetRequest | CardRequest


def parse_set_url(value: str) -> SetRequest:
    """Parse a Scryfall set URL such as https://scryfall.com/sets/fin/fr."""
    request = parse_scryfall_url(value)
    if not isinstance(request, SetRequest):
        raise ValueError("Format attendu: https://scryfall.com/sets/fin/fr")
    return request


def parse_scryfall_url(value: str) -> ScryfallRequest:
    """Parse a Scryfall set URL or a single-card URL."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Veuillez saisir un lien Scryfall.")

    parsed = urlparse(stripped)
    if parsed.netloc.lower() not in {"scryfall.com", "www.scryfall.com"}:
        raise ValueError("Le lien doit pointer vers scryfall.com.")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0].lower() == "sets":
        return _parse_set_parts(parts)
    if len(parts) >= 3 and parts[0].lower() == "card":
        return _parse_card_parts(parts)

    raise ValueError(
        "Format attendu: https://scryfall.com/sets/fin/fr "
        "ou https://scryfall.com/card/fic/7/yshtola-nights-blessed"
    )


def _parse_set_parts(parts: list[str]) -> SetRequest:
    if len(parts) != 3:
        raise ValueError("Format attendu: https://scryfall.com/sets/fin/fr")

    set_code = parts[1].strip().lower()
    language = parts[2].strip().lower()
    if not set_code or not language:
        raise ValueError("Le code du set et la langue sont obligatoires.")

    return SetRequest(set_code=set_code, language=language)


def _parse_card_parts(parts: list[str]) -> CardRequest:
    set_code = parts[1].strip().lower()
    collector_number = parts[2].strip().lower()
    language = None

    if len(parts) >= 5 and len(parts[3]) in {2, 3}:
        language = parts[3].strip().lower()

    if not set_code or not collector_number:
        raise ValueError("Le code du set et le numéro collector sont obligatoires.")

    return CardRequest(set_code=set_code, collector_number=collector_number, language=language)
