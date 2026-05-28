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


@dataclass(frozen=True)
class DecklistEntry:
    quantity: int
    name: str


@dataclass(frozen=True)
class CardPrint:
    id: str
    set_code: str
    set_name: str
    collector_number: str
    language: str
    name: str
    image_url: str
    released_at: str
    preview_url: str = ""
    image_urls: dict[str, str] | None = None
    highres_image: bool = True

    @property
    def label(self) -> str:
        set_label = self.set_code.upper()
        date_label = self.released_at or "date inconnue"
        highres_label = "" if self.highres_image else " ⚠️[LowRes]"
        return f"{set_label} #{self.collector_number} - {self.set_name} - {date_label} ({self.language.upper()}){highres_label}"

    @property
    def base_filename(self) -> str:
        clean_name = _clean_filename_part(self.name)
        clean_number = _clean_filename_part(self.collector_number)
        return f"{self.set_code.upper()}_{self.language.upper()}_{clean_number}_{clean_name}"

    def for_image_size(self, image_size: str) -> CardPrint:
        image_urls = self.image_urls or {}
        image_url = image_urls.get(image_size) or image_urls.get("large") or self.image_url
        preview_url = self.preview_url or image_urls.get("small") or image_urls.get("normal") or image_url
        return CardPrint(
            id=self.id,
            set_code=self.set_code,
            set_name=self.set_name,
            collector_number=self.collector_number,
            language=self.language,
            name=self.name,
            image_url=image_url,
            released_at=self.released_at,
            preview_url=preview_url,
            image_urls=image_urls or None,
            highres_image=self.highres_image,
        )


def _clean_filename_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "card"
