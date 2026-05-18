from __future__ import annotations

from pathlib import Path
from shutil import copyfileobj
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import CardImage, CardRequest, SetRequest
from .scryfall_client import ScryfallClient, USER_AGENT


ProgressCallback = Callable[[str], None]
ProgressCountCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


class ArtDownloader:
    def __init__(self, output_root: Path | str = "ART") -> None:
        self.output_root = Path(output_root)
        self.client = ScryfallClient()

    def download(
        self,
        request: SetRequest | CardRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        if isinstance(request, CardRequest):
            return self.download_card(request, image_size, overwrite, on_status, on_progress, should_cancel)
        return self.download_set(request, image_size, overwrite, on_status, on_progress, should_cancel)

    def download_set(
        self,
        set_request: SetRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        target_dir = self.output_root / set_request.folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        if on_status:
            on_status(f"Dossier cible: {target_dir}")

        count = 0
        total = 0

        def set_total(value: int) -> None:
            nonlocal total
            total = value
            if on_status:
                on_status(f"Total cartes: {total}")
            if on_progress:
                on_progress(count, total)

        for card in self.client.iter_card_images(set_request, image_size, on_status, set_total):
            if should_cancel and should_cancel():
                if on_status:
                    on_status("Annulé.")
                break

            target = self._target_path(target_dir, card)
            if target.exists() and not overwrite:
                count += 1
                if on_status:
                    on_status(f"Déjà présent: {target.name}")
                if on_progress:
                    on_progress(count, total)
                continue

            if on_status:
                on_status(f"Téléchargement: {target.name}")
            self._download_file(card.image_url, target)
            count += 1
            if on_progress:
                on_progress(count, total)

        return count, target_dir

    def download_card(
        self,
        card_request: CardRequest,
        image_size: str = "large",
        overwrite: bool = False,
        on_status: ProgressCallback | None = None,
        on_progress: ProgressCountCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[int, Path]:
        target_dir = self.output_root / card_request.folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        if on_status:
            on_status(f"Dossier cible: {target_dir}")
            on_status("Total cartes: 1")
        if on_progress:
            on_progress(0, 1)

        if should_cancel and should_cancel():
            if on_status:
                on_status("Annulé.")
            return 0, target_dir

        card = self.client.get_card_image(card_request, image_size, on_status)
        target = self._target_path(target_dir, card)
        if target.exists() and not overwrite:
            if on_status:
                on_status(f"Déjà présent: {target.name}")
            if on_progress:
                on_progress(1, 1)
            return 1, target_dir

        if on_status:
            on_status(f"Téléchargement: {target.name}")
        self._download_file(card.image_url, target)
        if on_progress:
            on_progress(1, 1)
        return 1, target_dir

    @staticmethod
    def _target_path(target_dir: Path, card: CardImage) -> Path:
        extension = Path(urlparse(card.image_url).path).suffix or ".jpg"
        return target_dir / f"{card.base_filename}{extension}"

    @staticmethod
    def _download_file(url: str, target: Path) -> None:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=60) as response:
                with target.open("wb") as output:
                    copyfileobj(response, output)
        except HTTPError as error:
            raise RuntimeError(f"Erreur image HTTP {error.code}: {url}") from error
        except URLError as error:
            raise RuntimeError(f"Impossible de telecharger l'image: {error.reason}") from error
