# =============================================================================
#  MODÈLES DE DONNÉES — models.py
# =============================================================================
# Ce fichier définit les "structures de données" utilisées dans tout le programme.
#
# Une structure de données, c'est comme un formulaire avec des cases à remplir.
# Par exemple, un "SetRequest" c'est un formulaire avec :
#   - le code du set  (ex: "FIN")
#   - la langue        (ex: "fr")
#
# On utilise des "dataclasses" Python qui génèrent automatiquement les méthodes
# de base (__init__, __repr__, __eq__) sans qu'on ait à les écrire à la main.
#
# Le paramètre frozen=True rend les objets IMMUABLES (on ne peut pas modifier
# leurs valeurs après création), ce qui évite des bugs difficiles à détecter.
# =============================================================================

from __future__ import annotations   # Permet d'utiliser les annotations de type modernes

from dataclasses import dataclass    # Outil Python pour créer des structures de données facilement


# ---------------------------------------------------------------------------
#  SetRequest — Demande de téléchargement d'un set complet
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetRequest:
    """
    Représente une demande de téléchargement d'un set entier depuis Scryfall.

    Un "set" Magic: The Gathering est un ensemble de cartes sorties en même temps
    (ex: "Foundations", "Duskmourn", etc.).

    Attributs :
        set_code  (str) : Code court du set, ex: "FIN", "DSK", "BLB"
        language  (str) : Code de langue, ex: "fr", "en", "ja"

    Exemple d'utilisation :
        req = SetRequest(set_code="fin", language="fr")
        print(req.folder_name)  # Affiche : "FIN_FR"
    """

    set_code: str   # Code court du set Scryfall (ex: "fin" pour Foundations)
    language: str   # Langue souhaitée pour les cartes (ex: "fr", "en", "ja")

    @property
    def folder_name(self) -> str:
        """
        Génère le nom du dossier de sortie pour ce set.

        La propriété @property permet d'appeler folder_name comme un attribut
        (sans parenthèses) plutôt que comme une méthode.

        Retourne :
            str : Nom du dossier, ex: "FIN_FR" pour le set "fin" en français.
        """
        return f"{self.set_code.upper()}_{self.language.upper()}"


# ---------------------------------------------------------------------------
#  CardRequest — Demande de téléchargement d'une seule carte
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardRequest:
    """
    Représente une demande de téléchargement d'une carte spécifique.

    Pour identifier une carte précise sur Scryfall, on a besoin du code set,
    du numéro de collection, et optionnellement de la langue.

    Attributs :
        set_code          (str)       : Code court du set, ex: "FIN"
        collector_number  (str)       : Numéro de la carte dans le set, ex: "101"
        language          (str|None)  : Langue souhaitée. None = pas de préférence.

    Exemple :
        req = CardRequest(set_code="fin", collector_number="101", language="fr")
        print(req.folder_name)  # Affiche : "FIN_FR"
    """

    set_code: str                   # Code court du set (ex: "fin")
    collector_number: str           # Numéro de la carte dans le set (ex: "101")
    language: str | None = None     # Langue voulue (optionnelle)

    @property
    def folder_name(self) -> str:
        """
        Génère le nom du dossier de sortie pour cette carte.

        Retourne :
            str : "SET_LANGUE" si langue précisée, sinon "SET_CARD".
                  Exemples : "FIN_FR", "DSK_EN", "BLB_CARD"
        """
        # Si une langue est précisée on l'utilise, sinon on met "CARD" par défaut
        suffix = self.language.upper() if self.language else "CARD"
        return f"{self.set_code.upper()}_{suffix}"


# ---------------------------------------------------------------------------
#  CardImage — Image d'une carte téléchargée
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardImage:
    """
    Représente une image de carte prête à être téléchargée.

    C'est la structure utilisée lors du téléchargement d'un set complet.
    Elle contient tout ce qu'il faut pour nommer et sauvegarder le fichier.

    Attributs :
        set_code         (str) : Code du set, ex: "FIN"
        language         (str) : Langue de la carte, ex: "fr"
        collector_number (str) : Numéro dans le set, ex: "101"
        name             (str) : Nom de la carte, ex: "Lightning Bolt"
        image_url        (str) : URL de l'image à télécharger
    """

    set_code: str           # Code du set
    language: str           # Langue de la carte
    collector_number: str   # Numéro dans le set
    name: str               # Nom de la carte
    image_url: str          # URL de l'image sur Scryfall

    @property
    def base_filename(self) -> str:
        """
        Génère un nom de fichier sûr pour cette image.

        Les numéros de collection peuvent contenir des caractères spéciaux
        (ex: "101a", "★1"). On les nettoie pour éviter des erreurs de fichier.

        Retourne :
            str : Nom de fichier sans extension, ex: "FIN_FR_101a"
        """
        # Remplace tous les caractères non-alphanumériques du numéro par "_"
        # On garde les tirets (-) et underscores (_) car ils sont sûrs
        clean_number = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in self.collector_number
        )
        return f"{self.set_code.upper()}_{self.language.upper()}_{clean_number}"


# ---------------------------------------------------------------------------
#  DecklistEntry — Ligne d'une liste de cartes (decklist)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecklistEntry:
    """
    Représente une ligne d'une liste de cartes (decklist).

    Une decklist c'est une liste du type :
        4 Lightning Bolt
        2 Forest
        1 Black Lotus

    Chaque ligne devient un DecklistEntry avec la quantité et le nom.

    Attributs :
        quantity  (int) : Nombre d'exemplaires de cette carte (ex: 4)
        name      (str) : Nom de la carte (ex: "Lightning Bolt")
    """

    quantity: int   # Nombre d'exemplaires dans la liste (ex: 4 pour "4 Lightning Bolt")
    name: str       # Nom de la carte (ex: "Lightning Bolt")


# ---------------------------------------------------------------------------
#  CardPrint — Impression spécifique d'une carte
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardPrint:
    """
    Représente une édition/impression spécifique d'une carte Magic.

    Une même carte (ex: "Forest") peut avoir été imprimée dans des dizaines
    de sets différents, dans plusieurs langues, avec des illustrations différentes.
    CardPrint représente UNE version précise de cette carte.

    Attributs obligatoires :
        id               (str)  : Identifiant unique Scryfall (UUID)
        set_code         (str)  : Code du set, ex: "FIN"
        set_name         (str)  : Nom complet du set, ex: "Foundations"
        collector_number (str)  : Numéro dans le set, ex: "101"
        language         (str)  : Langue de l'impression, ex: "fr"
        name             (str)  : Nom de la carte
        image_url        (str)  : URL principale de l'image
        released_at      (str)  : Date de sortie du set, ex: "2024-11-15"

    Attributs optionnels :
        preview_url       (str)       : URL de la vignette (petite image de prévisualisation)
        image_urls        (dict|None) : Dictionnaire des URLs pour chaque taille d'image
        highres_image     (bool)      : True si l'image est haute résolution
        custom_file_path  (str)       : Chemin vers une image locale personnalisée
        is_custom         (bool)      : True si c'est une image importée par l'utilisateur
    """

    # Champs obligatoires (doivent toujours être fournis)
    id: str                           # ID unique Scryfall (format UUID)
    set_code: str                     # Code court du set (ex: "fin")
    set_name: str                     # Nom complet du set (ex: "Foundations")
    collector_number: str             # Numéro de collecteur (ex: "101", "101a")
    language: str                     # Langue de l'impression (ex: "fr", "en")
    name: str                         # Nom de la carte
    image_url: str                    # URL de l'image principale
    released_at: str                  # Date de sortie (ex: "2024-11-15")

    # Champs optionnels (ont une valeur par défaut si non fournis)
    preview_url: str = ""             # URL de la petite vignette (pour aperçu rapide)
    image_urls: dict[str, str] | None = None   # Toutes les tailles d'images disponibles
    highres_image: bool = True        # Image en haute résolution ? (False = attention qualité)
    custom_file_path: str = ""        # Chemin vers un fichier image local personnalisé
    is_custom: bool = False           # Vrai si l'utilisateur a fourni sa propre image

    @property
    def label(self) -> str:
        """
        Génère l'étiquette lisible affichée dans le tableau des cartes.

        Pour une impression normale :
            "FIN #101 - Foundations - 2024-11-15 (FR)"
        Pour une impression basse résolution :
            "FIN #101 - Foundations - 2024-11-15 (FR) ⚠️[LowRes]"
        Pour une image personnalisée :
            "CUSTOM - Lightning Bolt"

        Retourne :
            str : Texte affiché dans la colonne "Edition" du tableau.
        """
        if self.is_custom:
            # Image fournie par l'utilisateur — on affiche juste "CUSTOM"
            return f"CUSTOM - {self.name}"

        set_label = self.set_code.upper()
        date_label = self.released_at or "date inconnue"
        # Avertissement visuel si l'image est basse résolution
        highres_label = "" if self.highres_image else " ⚠️[LowRes]"
        return f"{set_label} #{self.collector_number} - {self.set_name} - {date_label} ({self.language.upper()}){highres_label}"

    @property
    def base_filename(self) -> str:
        """
        Génère un nom de fichier de base propre pour sauvegarder l'image.

        Exemple : pour une Forest de Foundations en français numéro 101 :
            "FIN_FR_101_Forest"

        Retourne :
            str : Nom de fichier sans extension, sans caractères problématiques.
        """
        clean_name = _clean_filename_part(self.name)
        clean_number = _clean_filename_part(self.collector_number)
        return f"{self.set_code.upper()}_{self.language.upper()}_{clean_number}_{clean_name}"

    def for_image_size(self, image_size: str) -> CardPrint:
        """
        Retourne une copie de cette impression avec l'URL adaptée à la taille voulue.

        Scryfall propose plusieurs tailles d'images :
            "small"        : 146 × 204 px  — vignette
            "normal"       : 488 × 680 px  — affichage web
            "large"        : 672 × 936 px  — haute qualité (recommandé)
            "png"          : 745 × 1040 px — meilleure qualité, fichier plus lourd
            "art_crop"     : Recadrage sur l'illustration uniquement
            "border_crop"  : Carte sans bords blancs

        Si la taille demandée n'est pas disponible, on utilise "large" comme repli,
        et si "large" n'est pas disponible non plus, on utilise l'URL principale.

        Arguments :
            image_size (str) : Taille souhaitée (ex: "large", "png")

        Retourne :
            CardPrint : Même impression mais avec l'URL ajustée pour la taille.
        """
        image_urls = self.image_urls or {}

        # Chercher l'URL dans cet ordre : taille voulue → large → url par défaut
        image_url = image_urls.get(image_size) or image_urls.get("large") or self.image_url

        # URL de prévisualisation : petite vignette pour l'aperçu rapide dans l'interface
        preview_url = self.preview_url or image_urls.get("small") or image_urls.get("normal") or image_url

        # On retourne une copie identique mais avec les URLs mises à jour
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
            custom_file_path=self.custom_file_path,
            is_custom=self.is_custom,
        )


# ---------------------------------------------------------------------------
#  Fonctions utilitaires
# ---------------------------------------------------------------------------

def _clean_filename_part(value: str) -> str:
    """
    Nettoie une chaîne de caractères pour qu'elle soit utilisable dans un nom de fichier.

    Les noms de fichiers Windows ne peuvent pas contenir certains caractères
    comme : / \\ : * ? " < > |
    Cette fonction remplace tous les caractères non sûrs par des underscores (_).

    Exemples :
        "Lightning Bolt"  →  "Lightning_Bolt"
        "101a"            →  "101a"
        "Fire/Ice"        →  "Fire_Ice"
        "___test___"      →  "test"  (underscores doubles et en bords supprimés)

    Arguments :
        value (str) : Texte à nettoyer.

    Retourne :
        str : Texte nettoyé, ou "card" si le résultat est vide.
    """
    # Remplace tout caractère qui n'est ni alphanumérique, ni tiret, ni underscore
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)

    # Réduit les suites de plusieurs underscores consécutifs en un seul
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")

    # Supprime les underscores en début et fin, retourne "card" si tout est vide
    return cleaned.strip("_") or "card"
