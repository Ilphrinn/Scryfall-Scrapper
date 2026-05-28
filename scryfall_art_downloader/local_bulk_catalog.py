from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterable

from .models import CardPrint, DecklistEntry


LOCAL_BULK_INDEX_DIR = Path.home() / ".scryfall_art_downloader" / "local_bulk_index"
INSERT_BATCH_SIZE = 2000


class LocalBulkCatalog:
    def __init__(
        self,
        bulk_file: Path,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.bulk_file = bulk_file
        self.on_status = on_status
        self.on_progress = on_progress
        self.should_cancel = should_cancel
        self.index_file = self._index_dir() / f"{self.bulk_file.stem}.sqlite3"

    def search_deck_prints(
        self,
        entries: list[DecklistEntry],
        language: str,
        image_size: str = "large",
    ) -> dict[int, list[CardPrint]]:
        self._ensure_index()
        if self.on_status:
            self.on_status("Bulk local: recherche dans l'index local...")
        if self.on_progress:
            self.on_progress(0, max(len(entries), 1))

        result: dict[int, list[CardPrint]] = {}
        with closing(sqlite3.connect(self.index_file)) as connection:
            for index, entry in enumerate(entries):
                self._raise_if_cancelled()
                result[index] = self._lookup_prints(connection, entry.name, language.lower(), image_size)
                if self.on_progress:
                    self.on_progress(index + 1, max(len(entries), 1))
        return result

    def _ensure_index(self) -> None:
        bulk_stat = self.bulk_file.stat()
        if self._index_is_current(bulk_stat.st_size, int(bulk_stat.st_mtime)):
            return

        if self.on_status:
            self.on_status(f"Bulk local: indexation unique de {self.bulk_file.name}...")
        temp_index = self.index_file.with_suffix(".sqlite3.tmp")
        if temp_index.exists():
            temp_index.unlink()

        with closing(sqlite3.connect(temp_index)) as connection:
            self._prepare_connection(connection)
            self._create_schema(connection)
            self._populate_index(connection, bulk_stat.st_size)
            if self.on_status:
                self.on_status("Bulk local: finalisation de l'index...")
            self._create_indexes(connection)
            connection.execute("INSERT INTO meta(key, value) VALUES('bulk_size', ?)", (str(bulk_stat.st_size),))
            connection.execute("INSERT INTO meta(key, value) VALUES('bulk_mtime', ?)", (str(int(bulk_stat.st_mtime)),))
            connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
            connection.commit()

        if self.index_file.exists():
            try:
                self.index_file.unlink()
            except Exception:
                pass
        temp_index.replace(self.index_file)
        if self.on_status:
            self.on_status("Bulk local: index prêt.")

    def _index_is_current(self, bulk_size: int, bulk_mtime: int) -> bool:
        if not self.index_file.exists():
            return False
        try:
            with closing(sqlite3.connect(self.index_file)) as connection:
                rows = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            return (
                rows.get("bulk_size") == str(bulk_size)
                and rows.get("bulk_mtime") == str(bulk_mtime)
                and rows.get("schema_version") == "2"
            )
        except sqlite3.Error:
            return False

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE aliases (
                name TEXT NOT NULL,
                oracle_id TEXT NOT NULL
            );
            CREATE TABLE prints (
                oracle_id TEXT NOT NULL,
                language TEXT NOT NULL,
                card_id TEXT PRIMARY KEY,
                set_code TEXT NOT NULL,
                set_name TEXT NOT NULL,
                collector_number TEXT NOT NULL,
                card_name TEXT NOT NULL,
                released_at TEXT NOT NULL,
                image_urls TEXT NOT NULL,
                preview_url TEXT NOT NULL,
                highres_image INTEGER NOT NULL
            );
            """
        )

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE INDEX idx_aliases_name ON aliases(name);
            CREATE INDEX idx_prints_oracle_lang ON prints(oracle_id, language, released_at DESC);
            """
        )

    @staticmethod
    def _prepare_connection(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = MEMORY;
            """
        )

    def _populate_index(self, connection: sqlite3.Connection, total_size: int) -> None:
        alias_rows: list[tuple[str, str]] = []
        print_rows: list[tuple[str, str, str, str, str, str, str, str, str, str, int]] = []
        last_progress = 0

        for card, bytes_read in self._iter_bulk_cards():
            self._raise_if_cancelled()
            oracle_id = str(card.get("oracle_id") or "")
            if not oracle_id:
                continue

            for value in (card.get("name"), card.get("printed_name")):
                if value:
                    alias_rows.append((_normalize_name(str(value)), oracle_id))

            print_row = _print_row_from_card(card)
            if print_row is not None:
                print_rows.append(print_row)

            if len(alias_rows) >= INSERT_BATCH_SIZE or len(print_rows) >= INSERT_BATCH_SIZE:
                self._flush_rows(connection, alias_rows, print_rows)
                alias_rows.clear()
                print_rows.clear()

            if self.on_progress and bytes_read - last_progress >= 20 * 1024 * 1024:
                last_progress = bytes_read
                self.on_progress(min(bytes_read, total_size), max(total_size, 1))

        self._flush_rows(connection, alias_rows, print_rows)
        if self.on_progress:
            self.on_progress(max(total_size, 1), max(total_size, 1))

    @staticmethod
    def _flush_rows(
        connection: sqlite3.Connection,
        alias_rows: list[tuple[str, str]],
        print_rows: list[tuple[str, str, str, str, str, str, str, str, str, str, int]],
    ) -> None:
        if alias_rows:
            connection.executemany("INSERT INTO aliases(name, oracle_id) VALUES(?, ?)", alias_rows)
        if print_rows:
            connection.executemany(
                """
                INSERT OR REPLACE INTO prints(
                    oracle_id,
                    language,
                    card_id,
                    set_code,
                    set_name,
                    collector_number,
                    card_name,
                    released_at,
                    image_urls,
                    preview_url,
                    highres_image
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                print_rows,
            )

    def _lookup_prints(
        self,
        connection: sqlite3.Connection,
        name: str,
        language: str,
        image_size: str,
    ) -> list[CardPrint]:
        oracle_rows = connection.execute(
            "SELECT DISTINCT oracle_id FROM aliases WHERE name = ?",
            (_normalize_name(name),),
        ).fetchall()
        if not oracle_rows:
            return []

        seen: set[str] = set()
        prints: list[CardPrint] = []
        for (oracle_id,) in oracle_rows:
            rows = connection.execute(
                """
                SELECT card_id, set_code, set_name, collector_number, card_name, released_at, image_urls, preview_url, language, highres_image
                FROM prints
                WHERE oracle_id = ?
                ORDER BY released_at DESC
                """,
                (oracle_id,),
            ).fetchall()
            for row in rows:
                card_id, set_code, set_name, collector_number, card_name, released_at, image_urls_json, preview_url, card_lang, highres_image = row
                if card_id in seen:
                    continue
                seen.add(card_id)
                image_urls = json.loads(image_urls_json)
                image_url = image_urls.get(image_size) or image_urls.get("large") or next(iter(image_urls.values()), "")
                if not image_url:
                    continue
                prints.append(
                    CardPrint(
                        id=card_id,
                        set_code=set_code,
                        set_name=set_name,
                        collector_number=collector_number,
                        language=card_lang,
                        name=card_name,
                        image_url=image_url,
                        released_at=released_at,
                        preview_url=preview_url or image_urls.get("small", "") or image_urls.get("normal", "") or image_url,
                        image_urls=image_urls,
                        highres_image=bool(highres_image),
                    )
                )

        # Sort so that prints with the target language come first, then release date desc
        prints.sort(key=lambda p: (p.language == language, p.released_at), reverse=True)
        return prints

    def _index_dir(self) -> Path:
        try:
            LOCAL_BULK_INDEX_DIR.mkdir(parents=True, exist_ok=True)
            return LOCAL_BULK_INDEX_DIR
        except OSError:
            fallback = self.bulk_file.parent / ".scryfall_local_bulk_index"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _iter_bulk_cards(self) -> Iterable[tuple[dict, int]]:
        decoder = json.JSONDecoder()
        buffer = ""
        bytes_read = 0
        array_started = False
        eof = False

        with self.bulk_file.open("r", encoding="utf-8") as source:
            while True:
                if not eof:
                    chunk = source.read(1024 * 1024)
                    if chunk:
                        buffer += chunk
                        bytes_read += len(chunk.encode("utf-8", errors="ignore"))
                    else:
                        eof = True

                while True:
                    buffer = buffer.lstrip()
                    if not buffer:
                        break

                    if not array_started:
                        if buffer[0] != "[":
                            raise RuntimeError("Le fichier all-cards local ne contient pas un tableau JSON.")
                        buffer = buffer[1:]
                        array_started = True
                        continue

                    if buffer[0] == ",":
                        buffer = buffer[1:]
                        continue
                    if buffer[0] == "]":
                        return

                    try:
                        card, end_index = decoder.raw_decode(buffer)
                    except json.JSONDecodeError:
                        if eof:
                            raise
                        break

                    if isinstance(card, dict):
                        yield card, bytes_read
                    buffer = buffer[end_index:]

                if eof:
                    if buffer.strip() in {"", "]"}:
                        return
                    raise RuntimeError("Lecture du fichier all-cards local incomplète.")

    def _raise_if_cancelled(self) -> None:
        if self.should_cancel and self.should_cancel():
            raise RuntimeError("Indexation du bulk local annulée.")


def find_local_bulk_file(root: Path) -> Path | None:
    candidates = []
    for pattern in ("all-cards-*.json", "oracle-cards-*.json"):
        candidates.extend(root.glob(pattern))
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _print_row_from_card(card: dict) -> tuple[str, str, str, str, str, str, str, str, str, str, int] | None:
    image_urls = {
        image_size: image_url
        for image_size in ("small", "normal", "large", "png", "art_crop", "border_crop")
        if (image_url := _extract_image_url(card, image_size))
    }
    if not image_urls:
        return None

    oracle_id = str(card.get("oracle_id") or "")
    if not oracle_id:
        return None

    preview_url = image_urls.get("small") or image_urls.get("normal") or image_urls.get("large") or next(iter(image_urls.values()))
    highres = 1 if card.get("highres_image") else 0
    return (
        oracle_id,
        str(card.get("lang", "")),
        str(card.get("id", "")),
        str(card.get("set", "")),
        str(card.get("set_name", "")),
        str(card.get("collector_number", "")),
        str(card.get("name", "")),
        str(card.get("released_at", "")),
        json.dumps(image_urls, ensure_ascii=False),
        preview_url,
        highres,
    )


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


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())
