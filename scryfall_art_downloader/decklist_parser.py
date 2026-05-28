from __future__ import annotations

import re

from .models import DecklistEntry


DECKLIST_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def parse_decklist(value: str) -> tuple[list[DecklistEntry], list[str]]:
    entries: list[DecklistEntry] = []
    skipped: list[str] = []

    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = DECKLIST_LINE.match(line)
        if not match:
            skipped.append(raw_line)
            continue

        quantity = int(match.group(1))
        name = match.group(2).strip()
        if quantity <= 0 or not name:
            skipped.append(raw_line)
            continue

        entries.append(DecklistEntry(quantity=quantity, name=name))

    return entries, skipped
