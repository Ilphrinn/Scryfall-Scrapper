from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SetRequest:
    set_code: str
    language: str

    @property
    def folder_name(self) -> str:
        return f"{self.set_code.upper()}_{self.language.upper()}"


@dataclass(frozen=True)
class CardRequest:
    set_code: str
    collector_number: str
    language: str | None = None

    @property
    def folder_name(self) -> str:
        suffix = self.language.upper() if self.language else "CARD"
        return f"{self.set_code.upper()}_{suffix}"


@dataclass(frozen=True)
class CardImage:
    set_code: str
    language: str
    collector_number: str
    name: str
    image_url: str

    @property
    def base_filename(self) -> str:
        clean_number = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in self.collector_number
        )
        return f"{self.set_code.upper()}_{self.language.upper()}_{clean_number}"
