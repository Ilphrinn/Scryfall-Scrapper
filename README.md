# Scryfall Artwork Downloader

Petite application pour telecharger les images d'un set Scryfall ou d'une carte unique dans un dossier organise.

Exemples acceptes dans les champs dedies:

```text
https://scryfall.com/sets/fin/fr
https://scryfall.com/card/fic/7/yshtola-nights-blessed
```

Ce lien cree le dossier:

```text
ART/FIN_FR
```

Les fichiers sont nommes avec le format:

```text
FIN_FR_1.jpg
FIN_FR_2.jpg
```

## Lancer l'application

Depuis ce dossier:

```powershell
py -3 main.py
```

Sur Linux, utilise plutot `python3 main.py` ou le lanceur `lancer-scryfall-downloader.sh`.

## Creer un executable Windows

Il faut le lanceur `py` avec Python 3.11+ installe. Ensuite:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows-exe.ps1
```

Le fichier sera cree ici:

```text
dist/ScryfallArtworkDownloader.exe
```

Pour limiter les blocages Windows/antivirus, le build Windows embarque des informations de version, un manifeste `asInvoker`, et n'utilise pas UPX. Le script affiche aussi le SHA256 de l'executable pour verifier que le fichier copie sur un autre ordinateur est identique.

Si Windows SmartScreen affiche encore un avertissement sur un autre ordinateur, c'est normal pour un executable non signe et peu connu. La correction definitive est de signer `dist/ScryfallArtworkDownloader.exe` avec un certificat de signature de code. Sans certificat, il peut rester necessaire de choisir "Informations complementaires" puis "Executer quand meme".

Si Windows affiche encore une ancienne icone pour l'executable, vide le cache d'icones:

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh-windows-icon-cache.ps1
```

## Creer un executable Linux

Sur Linux:

```bash
chmod +x build-linux-executable.sh lancer-scryfall-downloader.sh
./build-linux-executable.sh
```

Le fichier sera cree ici:

```text
dist/ScryfallArtworkDownloader
```

Le lanceur Linux `lancer-scryfall-downloader.sh` lance le binaire si present, sinon tente `python3 main.py`.

## Logo et theme

Le logo source est dans:

```text
assets/logo.png
```

Les variantes utilisees par l'interface et l'executable sont:

```text
assets/logo32.png
assets/logo.ico
```

L'icone de `dist/ScryfallArtworkDownloader.exe` est injectee par PyInstaller depuis `assets/logo.ico`.

L'interface utilise une teinte gris fonce avec une transparence legere, en accord avec le logo.
La barre Windows native est remplacee par une barre personnalisee avec boutons reduire et fermer.
La taille de la fenetre est fixe.
Le logo est utilise comme identite visuelle de l'application et comme icone de fenetre/barre des taches quand Tkinter le permet.

## Fonctionnement

L'application utilise l'API officielle Scryfall:

```text
https://api.scryfall.com/cards/search?q=set:fin lang:fr&include_multilingual=true
```

Par defaut, elle telecharge `image_uris.large`, comme dans ton exemple. Le menu "Taille image" permet aussi de choisir `art_crop`, `png`, `normal`, etc.
Le dossier choisi sert de racine: l'application cree toujours un sous-dossier `SET_LANGUE`, par exemple `FIN_FR`.
Pour une carte unique sans langue dans le lien, l'application cree un sous-dossier `SET_CARD`, par exemple `FIC_CARD`.
Si le champ "Lien de carte" est rempli, il est utilise en priorite. Sinon, l'application utilise le champ "Lien du set".
Le bouton `Annuler` interrompt le telechargement entre deux images.

## DPI Upscaler

## Decklist Downloader

Le deuxieme onglet permet de coller une decklist brute au format:

```text
1 Arcane Signet
10 Island
```

Colle la liste avec `Ctrl+V`, choisis la langue et la taille d'image, puis lance `Analyser`. Si un fichier `all-cards-*.json` est present dans le dossier de l'application, il est utilise en priorite pour trouver les editions disponibles sans appeler `/cards/search` carte par carte.
Au premier lancement avec ce fichier, l'application construit un index SQLite local dans `%USERPROFILE%\.scryfall_art_downloader\local_bulk_index`. Cette premiere indexation peut prendre du temps avec le fichier complet de Scryfall, mais les analyses suivantes interrogent directement l'index local et doivent etre beaucoup plus rapides.
Sinon l'application cherche les impressions carte par carte avec l'API Scryfall, en limitant `/cards/search` et `/cards/named` a 2 requetes par seconde.
Les recherches d'impressions sont mises en cache pendant 24 heures dans `%USERPROFILE%\.scryfall_art_downloader\api_cache\prints`.
Apres une analyse, changer la taille d'image ne relance plus de recherche Scryfall: les editions gardent les URLs disponibles et la selection est mise a jour localement.
Pour changer d'edition, double-clique sur une ligne de carte ou selectionne-la puis utilise `Changer edition`. Si une carte existe en plusieurs exemplaires, la fenetre permet d'appliquer l'edition a la copie choisie ou a toutes les copies de cette carte. Une preview est chargee depuis un cache local, sans nouvel appel API Scryfall.
Le bouton `Telecharger` cree un dossier `DECKLIST_LANGUE` et genere autant de fichiers que la quantite indiquee dans la decklist.

## DPI Upscaler

Le troisieme onglet permet de selectionner un dossier source et un dossier de sortie. Si le dossier de sortie est vide, l'application cree dans le dossier source:

```text
DPI_Upscale
```

Chaque image compatible est normalisee en `3193x4457` puis enregistree a `1200` DPI.
Formats pris en charge: JPG, PNG, TIFF, BMP et WEBP.

## Margin Creator

Le quatrieme onglet permet de selectionner un dossier source et un dossier de sortie. Si le dossier de sortie est vide, l'application cree dans le dossier source:

```text
Margin_Creator
```

Chaque image compatible est normalisee en `3193x4457`, puis copiee avec une marge harmonisee de `144` pixels par cote. Le fichier final mesure `3481x4745` et est enregistre a `1200` DPI.

## Ratio Cropper

Le cinquieme onglet permet de selectionner une image puis de la recadrer au ratio `0.714:1`.
La selection est centree par defaut et peut etre deplacee ou redimensionnee avec les poignees blanches.
Le ratio reste verrouille et le fichier final est obtenu uniquement par decoupe de l'image source, sans ajout de contenu.

## Structure

- `main.py`: point d'entree de l'application.
- `assets/logo.png`: logo source de l'application.
- `assets/logo32.png`: logo optimise pour les petites tailles.
- `assets/logo.ico`: icone Windows de l'executable.
- `build-windows-exe.ps1`: generation de l'executable Windows.
- `refresh-windows-icon-cache.ps1`: rafraichissement optionnel du cache d'icones Windows.
- `build-linux-executable.sh`: generation de l'executable Linux.
- `lancer-scryfall-downloader.sh`: lanceur Linux.
- `scryfall_art_downloader/app.py`: interface graphique.
- `scryfall_art_downloader/decklist_parser.py`: lecture des listes au format quantite + nom.
- `scryfall_art_downloader/local_bulk_catalog.py`: index SQLite local d'un fichier `all-cards-*.json`.
- `scryfall_art_downloader/aspect_cropper.py`: recadrage des images au ratio 0.714:1.
- `scryfall_art_downloader/dpi_upscaler.py`: copie des images avec DPI 1200.
- `scryfall_art_downloader/margin_creator.py`: creation de cadres noirs autour des images.
- `scryfall_art_downloader/url_parser.py`: lecture des liens Scryfall.
- `scryfall_art_downloader/scryfall_client.py`: appels API Scryfall.
- `scryfall_art_downloader/downloader.py`: creation des dossiers et telechargement des images.
