from __future__ import annotations

import json
import time
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import CardImage, CardRequest, SetRequest


SCRYFALL_API = "https://api.scryfall.com"
USER_AGENT = "ScryfallArtDownloader/1.0"


class ScryfallClient:
    def __init__(self, pause_seconds: float = 0.1) -> None:
        self.pause_seconds = pause_seconds

    def iter_card_images(
        self,
        set_request: SetRequest,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
        on_total: Callable[[int], None] | None = None,
    ) -> Iterable[CardImage]:
        query = f"set:{set_request.set_code} lang:{set_request.language}"
        params = urlencode(
            {
                "q": query,
                "unique": "prints",
                "order": "set",
                "include_multilingual": "true",
            }
        )
        next_url: str | None = f"{SCRYFALL_API}/cards/search?{params}"

        while next_url:
            if on_status:
                on_status(f"Lecture Scryfall: {next_url}")
            payload = self._get_json(next_url)
            if on_total and payload.get("total_cards"):
                on_total(int(payload["total_cards"]))
                on_total = None
            for raw_card in payload.get("data", []):
                image_url = self._extract_image_url(raw_card, image_size)
                if not image_url:
                    continue
                yield CardImage(
                    set_code=set_request.set_code,
                    language=set_request.language,
                    collector_number=str(raw_card.get("collector_number", "")),
                    name=str(raw_card.get("name", "")),
                    image_url=image_url,
                )

            next_url = payload.get("next_page") if payload.get("has_more") else None
            if next_url:
                time.sleep(self.pause_seconds)

    def get_card_image(
        self,
        card_request: CardRequest,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
    ) -> CardImage:
        url = self._card_url(card_request)
        if on_status:
            on_status(f"Lecture Scryfall: {url}")

        raw_card = self._get_json(url)
        image_url = self._extract_image_url(raw_card, image_size)
        if not image_url:
            raise RuntimeError(f"Aucune image '{image_size}' trouvée pour cette carte.")

        return CardImage(
            set_code=str(raw_card.get("set", card_request.set_code)),
            language=str(raw_card.get("lang", card_request.language or "")),
            collector_number=str(raw_card.get("collector_number", card_request.collector_number)),
            name=str(raw_card.get("name", "")),
            image_url=image_url,
        )

    @staticmethod
    def _card_url(card_request: CardRequest) -> str:
        set_code = quote(card_request.set_code, safe="")
        collector_number = quote(card_request.collector_number, safe="")
        if card_request.language:
            language = quote(card_request.language, safe="")
            return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}/{language}"
        return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"

    def _get_json(self, url: str) -> dict:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Erreur Scryfall HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Impossible de joindre Scryfall: {error.reason}") from error

    @staticmethod
    def _extract_image_url(card: dict, image_size: str) -> str | None:
        image_uris = card.get("image_uris")
        if isinstance(image_uris, dict) and image_uris.get(image_size):
            return str(image_uris[image_size])

        card_faces = card.get("card_faces")
        if isinstance(card_faces, list):
            for face in card_faces:
                face_uris = face.get("image_uris") if isinstance(face, dict) else None
                if isinstance(face_uris, dict) and face_uris.get(image_size):
                    return str(face_uris[image_size])

        return None
