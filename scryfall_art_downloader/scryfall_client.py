from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .models import CardImage, CardPrint, CardRequest, SetRequest


SCRYFALL_API = "https://api.scryfall.com"
USER_AGENT = "ScryfallArtDownloader/1.0"
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.11
SLOW_ENDPOINT_INTERVAL_SECONDS = 0.55
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_DELAY_SECONDS = 30
PRINT_SEARCH_CACHE_SECONDS = 24 * 60 * 60
PRINT_SEARCH_CACHE_DIR = Path.home() / ".scryfall_art_downloader" / "api_cache" / "prints"
SLOW_ENDPOINTS = (
    "/cards/search",
    "/cards/named",
    "/cards/random",
    "/cards/collection",
)


class ScryfallClient:
    _request_lock = threading.Lock()
    _last_request_at_by_bucket: dict[str, float] = {}

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
            payload = self._get_json(next_url, on_status)
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

        raw_card = self._get_json(url, on_status)
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

    def search_card_prints(
        self,
        name: str,
        language: str,
        image_size: str = "large",
        on_status: Callable[[str], None] | None = None,
    ) -> list[CardPrint]:
        cached_prints = self._read_print_search_cache(name, language, image_size)
        if cached_prints is not None:
            if on_status:
                on_status(f"Cache Scryfall: {name} ({language.upper()})")
            return cached_prints

        oracle_id = self._oracle_id_for_name(name, on_status)
        if oracle_id:
            query = f"oracleid:{oracle_id} lang:any"
        else:
            query = f'!"{self._escape_search_text(name)}" lang:any'

        params = urlencode(
            {
                "q": query,
                "unique": "prints",
                "order": "released",
                "dir": "desc",
                "include_multilingual": "true",
            }
        )
        next_url: str | None = f"{SCRYFALL_API}/cards/search?{params}"
        prints: list[CardPrint] = []

        while next_url:
            if on_status:
                on_status(f"Recherche editions: {name} (ANY)")
            try:
                payload = self._get_json(next_url, on_status)
            except RuntimeError as error:
                if "HTTP 404" in str(error):
                    break
                raise
            for raw_card in payload.get("data", []):
                card_print = self._raw_card_to_print(raw_card, image_size)
                if card_print is not None:
                    prints.append(card_print)

            next_url = payload.get("next_page") if payload.get("has_more") else None
            if next_url:
                time.sleep(self.pause_seconds)

        # Sort so that target language prints come first, then sort by released_at DESC
        prints.sort(key=lambda p: (p.language == language, p.released_at), reverse=True)

        self._write_print_search_cache(name, language, image_size, prints)
        return prints

    def _oracle_id_for_name(self, name: str, on_status: Callable[[str], None] | None = None) -> str | None:
        params = urlencode({"exact": name})
        url = f"{SCRYFALL_API}/cards/named?{params}"
        if on_status:
            on_status(f"Resolution du nom: {name}")
        try:
            raw_card = self._get_json(url, on_status)
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return None
            raise
        oracle_id = raw_card.get("oracle_id")
        return str(oracle_id) if oracle_id else None

    @staticmethod
    def _card_url(card_request: CardRequest) -> str:
        set_code = quote(card_request.set_code, safe="")
        collector_number = quote(card_request.collector_number, safe="")
        if card_request.language:
            language = quote(card_request.language, safe="")
            return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}/{language}"
        return f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"

    def _get_json(self, url: str, on_status: Callable[[str], None] | None = None) -> dict:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._wait_for_rate_limit(url)
            try:
                with urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                if error.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                    delay = self._rate_limit_delay(detail)
                    if on_status:
                        on_status(f"Limite Scryfall atteinte, pause {delay} secondes avant reprise.")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Erreur Scryfall HTTP {error.code}: {detail}") from error
            except URLError as error:
                raise RuntimeError(f"Impossible de joindre Scryfall: {error.reason}") from error

        raise RuntimeError("Erreur Scryfall: nombre maximum de reprises atteint.")

    @classmethod
    def _wait_for_rate_limit(cls, url: str = "") -> None:
        bucket, interval = cls._rate_limit_bucket(url)
        with cls._request_lock:
            now = time.monotonic()
            elapsed = now - cls._last_request_at_by_bucket.get(bucket, 0.0)
            if elapsed < interval:
                time.sleep(interval - elapsed)
            cls._last_request_at_by_bucket[bucket] = time.monotonic()

    @staticmethod
    def _rate_limit_bucket(url: str) -> tuple[str, float]:
        path = urlparse(url).path if url else ""
        for endpoint in SLOW_ENDPOINTS:
            if path.startswith(endpoint):
                return endpoint, SLOW_ENDPOINT_INTERVAL_SECONDS
        return "default", DEFAULT_REQUEST_INTERVAL_SECONDS

    @staticmethod
    def _rate_limit_delay(detail: str) -> int:
        match = re.search(r"after\s+(\d+)\s+seconds?", detail, flags=re.IGNORECASE)
        if match:
            return max(1, min(int(match.group(1)), MAX_RATE_LIMIT_DELAY_SECONDS))
        return MAX_RATE_LIMIT_DELAY_SECONDS

    def _read_print_search_cache(self, name: str, language: str, image_size: str) -> list[CardPrint] | None:
        path = self._print_search_cache_path(name, language, image_size)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as source:
                payload = json.load(source)
            if time.time() - float(payload.get("saved_at", 0)) > PRINT_SEARCH_CACHE_SECONDS:
                return None
            return [CardPrint(**raw_print) for raw_print in payload.get("prints", [])]
        except Exception:
            return None

    def _write_print_search_cache(self, name: str, language: str, image_size: str, prints: list[CardPrint]) -> None:
        try:
            PRINT_SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),
                "name": name,
                "language": language,
                "image_size": image_size,
                "prints": [asdict(card_print) for card_print in prints],
            }
            with self._print_search_cache_path(name, language, image_size).open("w", encoding="utf-8") as target:
                json.dump(payload, target, ensure_ascii=False)
        except Exception:
            return

    @staticmethod
    def _print_search_cache_path(name: str, language: str, image_size: str) -> Path:
        cache_key = json.dumps(
            {
                "name": name.casefold().strip(),
                "language": language.lower().strip(),
                "image_size": image_size,
                "version": 2,
            },
            sort_keys=True,
        )
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        return PRINT_SEARCH_CACHE_DIR / f"{digest}.json"

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

    def _raw_card_to_print(self, card: dict, image_size: str) -> CardPrint | None:
        image_urls = self._extract_image_urls(card)
        image_url = image_urls.get(image_size) or image_urls.get("large") or ""
        if not image_url:
            return None

        return CardPrint(
            id=str(card.get("id", "")),
            set_code=str(card.get("set", "")),
            set_name=str(card.get("set_name", "")),
            collector_number=str(card.get("collector_number", "")),
            language=str(card.get("lang", "")),
            name=str(card.get("name", "")),
            image_url=image_url,
            released_at=str(card.get("released_at", "")),
            preview_url=image_urls.get("small") or image_urls.get("normal") or image_url,
            image_urls=image_urls,
            highres_image=bool(card.get("highres_image", True)),
        )

    @staticmethod
    def _extract_image_urls(card: dict) -> dict[str, str]:
        return {
            image_size: image_url
            for image_size in ("small", "normal", "large", "png", "art_crop", "border_crop")
            if (image_url := ScryfallClient._extract_image_url(card, image_size))
        }

    @staticmethod
    def _escape_search_text(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
