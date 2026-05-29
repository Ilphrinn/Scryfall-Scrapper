# =============================================================================
#  INTERFACE GRAPHIQUE PRINCIPALE — app.py
# =============================================================================
# Ce fichier est le plus grand et le plus complexe du programme.
# Il contient toute l'interface graphique (fenêtres, boutons, tableaux, etc.)
# et orchestre les opérations entre les différents modules.
#
# TECHNOLOGIE UTILISÉE : Tkinter
# Tkinter est la bibliothèque graphique intégrée à Python. Elle permet de
# créer des fenêtres, boutons, listes, barres de progression, etc.
# Elle est disponible sur Windows, Mac et Linux sans installation.
#
# ARCHITECTURE GÉNÉRALE :
# L'application est organisée en 6 onglets dans une fenêtre à onglets (Notebook) :
#   1. Scryfall Downloader — télécharge un set complet ou une carte individuelle
#   2. Decklist            — analyse une liste de cartes et télécharge les images
#   3. XML Generator       — génère un fichier XML pour l'impression
#   4. DPI Upscaler        — normalise les images à 1200 DPI
#   5. Margin Creator      — ajoute des marges aux cartes pour l'impression
#   6. Ratio Cropper       — recadre les images au ratio exact d'une carte Magic
#
# THREADING (fils d'exécution) :
# Les opérations longues (téléchargement, analyse) sont exécutées dans des
# "threads" séparés pour ne pas bloquer l'interface graphique.
# La communication entre les threads et l'interface se fait via une file
# de messages (queue.Queue) consultée régulièrement (_poll_messages).
#
# FENÊTRE SANS BORDS :
# La fenêtre utilise overrideredirect(True) pour supprimer la barre de titre
# du système. On crée notre propre barre de titre personnalisée avec des
# boutons déplacer/réduire/plein écran/fermer.
# =============================================================================

from __future__ import annotations

import json          # Lecture/écriture JSON (sauvegarde des états, cache)
import queue         # File de messages thread-safe (communication worker → UI)
import hashlib       # Génération d'empreintes SHA1 (noms de fichiers cache)
import sys           # Informations système (platform, chemin d'exécution)
import threading     # Création de threads pour les opérations longues
import tkinter as tk  # Bibliothèque graphique principale
from pathlib import Path              # Manipulation de chemins de fichiers
from shutil import copyfileobj, rmtree  # Copie de fichiers et suppression de dossiers
from tkinter import filedialog, messagebox, ttk  # Dialogues et widgets avancés Tkinter
from urllib.parse import urlparse     # Analyse d'URLs
from urllib.request import Request, urlopen   # Requêtes HTTP pour les prévisualisations

from .aspect_cropper import TARGET_ASPECT_RATIO, centered_crop_rect, crop_image_to_ratio
from .decklist_parser import parse_decklist
from .downloader import ArtDownloader
from .dpi_upscaler import upscale_folder_dpi
from .local_bulk_catalog import LOCAL_BULK_INDEX_DIR, LocalBulkCatalog, find_local_bulk_file
from .margin_creator import create_black_margins
from .models import CardPrint, CardRequest, DecklistEntry, SetRequest
from .scryfall_client import PRINT_SEARCH_CACHE_DIR, ScryfallClient, USER_AGENT
from .url_parser import parse_scryfall_url


# =============================================================================
#  CLASSE PRINCIPALE DE L'APPLICATION
# =============================================================================

class ScryfallArtApp(tk.Tk):
    """
    Classe principale de l'application Scryfall Artwork Downloader.

    Hérite de tk.Tk, ce qui fait de cette classe la fenêtre racine Tkinter.
    Toute l'interface graphique et la logique métier se trouvent ici.

    HÉRITAGE :
    En Python, "hériter" signifie qu'une classe reprend toutes les fonctionnalités
    d'une autre classe. ScryfallArtApp hérite de tk.Tk, donc elle EST une fenêtre
    Tkinter avec en plus toutes les fonctionnalités qu'on y ajoute.

    STRUCTURE :
    - __init__        : Initialisation, création des variables, construction de l'UI
    - _build_ui()     : Construction de l'interface graphique
    - _build_*_tab()  : Construction de chaque onglet
    - _start_*/run_*  : Lancement et exécution des opérations longues (en thread)
    - _poll_messages  : Traitement des messages des threads de travail
    """

    # Textes d'exemple affichés dans les champs URL avant que l'utilisateur saisisse
    URL_PLACEHOLDER = "https://scryfall.com/sets/SET/LANGUE"
    CARD_URL_PLACEHOLDER = "https://scryfall.com/card/SET/NUMERO/nom"

    def __init__(self) -> None:
        """
        Initialise la fenêtre principale et tous les composants de l'application.

        Étapes :
        1. Initialisation de la fenêtre Tkinter (super().__init__())
        2. Configuration de la fenêtre (taille, style, sans-bordure)
        3. Chargement des logos
        4. Initialisation des variables d'état
        5. Création des variables Tkinter (liées aux widgets UI)
        6. Construction de l'interface graphique
        7. Configuration des événements (fermeture, raccourcis clavier)
        8. Démarrage du polling de messages
        """
        super().__init__()   # Appel du constructeur de tk.Tk (obligatoire)
        self.withdraw()      # Cache la fenêtre immédiatement pour éviter le flash à (0,0)

        # --- Configuration de la fenêtre ---
        self.title("Scryfall Artwork Downloader")
        self.geometry("820x540")       # Taille initiale en pixels
        self.minsize(820, 540)         # Taille minimale (impossible de réduire en dessous)
        self.resizable(True, True)     # Redimensionnable en largeur ET hauteur
        self.configure(bg="#202020")   # Couleur de fond (gris très foncé)

        # Suppression de la barre de titre Windows standard
        # On crée notre propre barre de titre pour un style personnalisé
        self.overrideredirect(True)

        # Légère transparence (96% d'opacité) pour un effet visuel
        try:
            self.attributes("-alpha", 0.96)
        except tk.TclError:
            pass   # Sur certains systèmes cette option n'est pas disponible → on l'ignore

        # --- Chargement des logos pour chaque onglet ---
        # On charge plusieurs tailles car différents endroits de l'UI utilisent
        # des tailles différentes (barre de titre 26px, en-têtes d'onglets 54px...)
        self.taskbar_logo = self._load_logo_image(64)                              # Icône dans la barre des tâches Windows
        self.titlebar_logo = self._load_logo_image(26, prefer_small=True)          # Logo dans la barre de titre personnalisée
        self.header_logo = self._load_logo_image(54)                               # En-tête onglet Scryfall Downloader
        self.decklist_header_logo = self._load_logo_image(54, names=("logo_decklist.ico", "logo.png"))   # En-tête onglet Decklist
        self.xml_header_logo = self._load_logo_image(54, names=("logo_XML.ico", "logo.png"))             # En-tête onglet XML
        self.upscale_header_logo = self._load_logo_image(54, names=("logo_upscale.ico", "logo.png"))     # En-tête onglet DPI
        self.margin_header_logo = self._load_logo_image(54, names=("logo_margin.ico", "logo.png"))       # En-tête onglet Margin
        self.trim_header_logo = self._load_logo_image(54, names=("logo_trim.ico", "logo.png"))           # En-tête onglet Crop
        self.iconphoto(True, self.taskbar_logo)   # Icône de la fenêtre dans la barre des tâches

        # --- File de messages (communication entre threads) ---
        # Les threads de travail mettent des messages dans cette file.
        # Le thread principal (UI) les lit via _poll_messages() toutes les ~50ms.
        # Format des messages : (type_message, contenu) ex: ("log", "Téléchargement...")
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()

        # --- Threads de travail et événements d'annulation ---
        # Chaque opération longue a son propre thread et son propre événement d'annulation.
        # threading.Event : flag thread-safe. set() = annuler, is_set() = est-ce annulé ?
        self.worker: threading.Thread | None = None               # Thread pour Scryfall Downloader
        self.cancel_event = threading.Event()                     # Annulation du downloader
        self.decklist_worker: threading.Thread | None = None      # Thread pour l'analyse/téléchargement Decklist
        self.decklist_cancel_event = threading.Event()            # Annulation de la decklist
        self.upscale_worker: threading.Thread | None = None       # Thread pour le DPI Upscaler
        self.upscale_cancel_event = threading.Event()             # Annulation de l'upscaler
        self.margin_worker: threading.Thread | None = None        # Thread pour le Margin Creator
        self.margin_cancel_event = threading.Event()              # Annulation du margin creator
        self.xml_worker: threading.Thread | None = None           # Thread pour la génération XML

        # --- Variables d'état pour l'onglet Ratio Cropper ---
        self.crop_source_image = None             # Image Pillow chargée pour le recadrage
        self.crop_preview_image = None            # Image Tkinter pour l'aperçu (doit rester en mémoire)
        self.crop_rect: tuple[float, float, float, float] | None = None  # Rectangle de recadrage (left,top,right,bottom)
        self.crop_scale = 1.0                     # Facteur d'échelle entre l'image réelle et l'aperçu
        self.crop_offset = (0, 0)                 # Décalage (x,y) de l'image dans le canvas d'aperçu
        self.crop_drag_mode: str | None = None    # Mode de drag en cours ("move", "resize", ou None)
        self.crop_drag_start = (0.0, 0.0)         # Position de départ du drag
        self.crop_drag_rect: tuple[float, float, float, float] | None = None  # État du rectangle au début du drag

        # --- Variables d'état pour l'onglet Decklist ---
        self.decklist_entries: list[DecklistEntry] = []   # Cartes analysées (une par nom unique)
        self.decklist_rows: list[dict[str, object]] = []  # Lignes du tableau (une par exemplaire)
        self.decklist_prints_by_index: dict[int, list[CardPrint]] = {}    # Toutes les impressions disponibles, par index de carte
        self.decklist_selected_prints: dict[int, CardPrint] = {}          # Impression sélectionnée pour chaque ligne
        self.decklist_locked_rows: set[int] = set()       # Indices des lignes verrouillées
        self.decklist_analyzed_language = ""              # Langue utilisée lors de la dernière analyse
        self.decklist_analyzed_image_size = ""            # Taille d'image lors de la dernière analyse
        self._pending_state_restore: dict | None = None   # Sauvegarde à restaurer après analyse (chargement automatique)
        self.decklist_preview_images: dict[str, object] = {}   # Cache des images de prévisualisation (card_id → PhotoImage)
        self.decklist_preview_lock = threading.Lock()     # Verrou pour accès thread-safe au cache de prévisualisations

        # --- Variables d'état pour la fenêtre ---
        self.is_fullscreen = False                # En mode plein écran ?
        self.normal_geometry = "820x540"          # Géométrie mémorisée pour sortir du plein écran
        self.fullscreen_button: tk.Button | None = None   # Référence au bouton plein écran (pour mettre à jour son texte)
        self._drag_start_x = 0                    # Position X de début de déplacement de la fenêtre
        self._drag_start_y = 0                    # Position Y de début de déplacement
        self._resize_start_x = 0                  # Position X de début de redimensionnement
        self._resize_start_y = 0                  # Position Y de début de redimensionnement
        self._resize_start_width = 820            # Largeur de la fenêtre au début du redimensionnement
        self._resize_start_height = 540           # Hauteur de la fenêtre au début du redimensionnement

        # --- Variables Tkinter (liées aux widgets de l'interface) ---
        # Un StringVar/BooleanVar est une variable observable par Tkinter :
        # quand sa valeur change, les widgets liés se mettent à jour automatiquement.

        # Onglet Scryfall Downloader
        self.url_var = tk.StringVar(value=self.URL_PLACEHOLDER)          # Champ "Lien du set"
        self.card_url_var = tk.StringVar(value=self.CARD_URL_PLACEHOLDER) # Champ "Lien de carte"
        self.output_var = tk.StringVar(value="")                          # Champ "Dossier de sortie"
        self.image_size_var = tk.StringVar(value="large")                 # Sélecteur "Taille image"
        self.overwrite_var = tk.BooleanVar(value=False)                   # Case "Remplacer les fichiers"

        # Onglet Decklist
        self.decklist_language_var = tk.StringVar(value="all")           # Sélecteur de langue
        self.decklist_output_var = tk.StringVar(value="")                # Dossier de destination
        self.decklist_image_size_var = tk.StringVar(value="large")       # Taille d'image
        self.decklist_overwrite_var = tk.BooleanVar(value=False)         # Case "Remplacer"
        self.decklist_show_lowres_var = tk.BooleanVar(value=True)        # Afficher les images basse résolution

        # Onglet XML Generator
        self.xml_source_var = tk.StringVar(value="")                     # Dossier images recto
        self.xml_output_var = tk.StringVar(value="")                     # Dossier de sortie de l'archive
        self.xml_name_var = tk.StringVar(value="order")                  # Nom du projet (→ archive.zip + fichier.xml)
        self.xml_stock_var = tk.StringVar(value="(S30) Standard Smooth") # Stock papier sélectionné
        self.xml_foil_var = tk.BooleanVar(value=False)                   # Option foil (brillant)

        # Onglet DPI Upscaler
        self.upscale_folder_var = tk.StringVar(value="")                 # Dossier source
        self.upscale_output_var = tk.StringVar(value="")                 # Dossier de sortie

        # Onglet Margin Creator
        self.margin_folder_var = tk.StringVar(value="")                  # Dossier source
        self.margin_output_var = tk.StringVar(value="")                  # Dossier de sortie

        # Onglet Ratio Cropper
        self.crop_image_var = tk.StringVar(value="")                     # Chemin de l'image source
        self.crop_output_var = tk.StringVar(value="")                    # Chemin du fichier de sortie
        self.crop_status_var = tk.StringVar(value="Choisis une image pour préparer le recadrage.")  # Message de statut

        # --- Construction et finalisation ---
        self._build_ui()           # Construction de tous les widgets (barre de titre, onglets...)
        self._center_window()      # Centrage de la fenêtre sur l'écran
        self.protocol("WM_DELETE_WINDOW", self._close_application)   # Action sur fermeture de fenêtre
        self.bind("<Map>", self._restore_borderless)    # Restaure le mode sans-bords après minimisation
        self.bind("<Escape>", self._exit_fullscreen)    # Échap = sortie du plein écran
        self.after(100, self._poll_messages)   # Démarre le polling des messages (toutes les ~50ms)
        self.after(0, self._initial_show)      # Affichage initial centré, sans flash

    def _center_window(self) -> None:
        self.update_idletasks()
        width = 820
        height = 540
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    # ==========================================================================
    #  CHARGEMENT DES IMAGES / LOGOS
    # ==========================================================================

    def _load_logo_image(
        self,
        size: int,
        prefer_small: bool = False,
        names: tuple[str, ...] | None = None,
    ) -> tk.PhotoImage:
        """
        Charge un logo depuis le dossier assets/ et le redimensionne.

        Cherche le logo dans plusieurs emplacements possibles (dossier PyInstaller,
        dossier du script, répertoire courant) pour fonctionner dans tous les contextes.
        Si aucun logo n'est trouvé, retourne une image transparente vide.

        Arguments :
            size        (int)           : Taille souhaitée en pixels (carré).
            prefer_small (bool)         : Préférer logo32.png (petite version) si True.
            names       (tuple|None)    : Noms de fichiers à chercher, dans l'ordre de préférence.

        Retourne :
            tk.PhotoImage : Image chargée et redimensionnée (ou vide si introuvable).
        """
        # sys._MEIPASS = répertoire temporaire créé par PyInstaller (fichier .exe)
        # Si on n'est pas dans un exe compilé, on utilise le dossier parent du script
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        if names is None:
            names = ("logo32.png", "logo.png") if prefer_small else ("logo.png", "logo32.png")
        # Liste de tous les chemins possibles où chercher le logo
        candidates = [
            *(bundle_root / "assets" / name for name in names),
            *(Path(__file__).resolve().parent.parent / "assets" / name for name in names),
            *(Path.cwd() / "assets" / name for name in names),
        ]
        for candidate in candidates:
            if candidate.exists():
                image = self._open_logo_image(candidate, size)
                if image is not None:
                    return image

        return tk.PhotoImage(width=size, height=size)   # Image vide de secours

    def _open_logo_image(self, path: Path, size: int) -> tk.PhotoImage | None:
        """
        Ouvre et redimensionne un fichier image logo pour Tkinter.

        Utilise Pillow si disponible pour un redimensionnement de meilleure qualité.
        Sinon, utilise le redimensionnement natif Tkinter (qualité inférieure).

        Arguments :
            path (Path) : Chemin du fichier image.
            size (int)  : Taille cible en pixels (carré).

        Retourne :
            tk.PhotoImage : Image redimensionnée, ou None en cas d'erreur.
        """
        try:
            from PIL import Image, ImageTk
        except ImportError:
            # Pillow pas installé → on essaie avec Tkinter natif
            try:
                return self._fit_image(tk.PhotoImage(file=str(path)), size)
            except tk.TclError:
                return None

        try:
            with Image.open(path) as source:
                source = source.convert("RGBA")
                source = source.resize((size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(source)
        except Exception:
            try:
                return self._fit_image(tk.PhotoImage(file=str(path)), size)
            except tk.TclError:
                return None

    @staticmethod
    def _fit_image(image: tk.PhotoImage, size: int) -> tk.PhotoImage:
        """
        Redimensionne une PhotoImage Tkinter à la taille cible.

        Tkinter ne supporte pas le redimensionnement libre des PhotoImage.
        On utilise subsample() pour réduire par un facteur entier, puis
        on copie l'image centrée dans un nouveau canvas vide.

        Arguments :
            image (tk.PhotoImage) : Image source.
            size  (int)           : Taille cible en pixels.

        Retourne :
            tk.PhotoImage : Image de taille (size × size) centrée.
        """
        # Calcul du facteur de réduction (arrondi vers le haut pour subsample)
        # -(-a//b) = division avec arrondi vers le haut (équivalent à ceil(a/b))
        factor = max(1, -(-max(image.width(), image.height()) // size))
        if factor > 1:
            image = image.subsample(factor, factor)   # Réduction par facteur entier
        if image.width() == size and image.height() == size:
            return image

        # Création d'une image vide de la bonne taille, avec l'image copiée au centre
        fitted = tk.PhotoImage(width=size, height=size)
        x = max(0, (size - image.width()) // 2)   # Décalage pour centrage horizontal
        y = max(0, (size - image.height()) // 2)  # Décalage pour centrage vertical
        fitted.tk.call(fitted, "copy", image, "-to", x, y)
        return fitted

    # ==========================================================================
    #  WIDGETS UTILITAIRES
    # ==========================================================================

    def _bind_combobox_dropdown_top(self, combobox: ttk.Combobox) -> None:
        """
        Configure une liste déroulante pour toujours s'ouvrir au début de la liste.

        Par défaut, Tkinter se souvient de la position de défilement de la dernière
        ouverture. Ce comportement est contre-intuitif : on préfère toujours
        commencer en haut. Ce bind force le retour en haut à chaque ouverture.

        Arguments :
            combobox (ttk.Combobox) : La liste déroulante à configurer.
        """
        def scroll_to_top(_event: tk.Event | None = None) -> None:
            # after_idle : exécute la fonction quand Tkinter est disponible
            # (nécessaire car la liste doit d'abord s'ouvrir avant qu'on la fasse défiler)
            self.after_idle(lambda: self._scroll_combobox_dropdown_top(combobox))

        combobox.bind("<Button-1>", scroll_to_top, add="+")    # Clic gauche
        combobox.bind("<Down>", scroll_to_top, add="+")        # Touche flèche bas
        combobox.bind("<Alt-Down>", scroll_to_top, add="+")    # Alt + flèche bas (ouvre la liste)

    @staticmethod
    def _scroll_combobox_dropdown_top(combobox: ttk.Combobox) -> None:
        """
        Fait défiler la liste déroulante jusqu'en haut.

        Utilise des commandes Tcl internes de Tkinter pour accéder à la listbox
        du popup de la combobox. Ce n'est pas une API officielle mais c'est la
        seule façon d'accéder à la liste interne d'une ttk.Combobox.

        Arguments :
            combobox (ttk.Combobox) : La liste déroulante à faire défiler.
        """
        try:
            popdown = combobox.tk.eval(f"ttk::combobox::PopdownWindow {combobox}")
            listbox = f"{popdown}.f.l"
            combobox.tk.call(listbox, "yview", "moveto", 0)   # Défile à la position 0 (début)
        except tk.TclError:
            return   # La liste n'est pas encore ouverte → on ignore

    def _present_custom_dialog(
        self,
        dialog: tk.Toplevel,
        *,
        min_width: int = 0,
        min_height: int = 0,
        focus_widget: tk.Widget | None = None,
    ) -> None:
        """
        Affiche une boîte de dialogue personnalisée centrée sur la fenêtre principale.

        S'assure que la boîte de dialogue :
        - A une taille minimale
        - Est centrée sur la fenêtre principale
        - Passe temporairement au premier plan
        - Bloque l'interaction avec les autres fenêtres (modal)

        Arguments :
            dialog       (tk.Toplevel)    : La fenêtre de dialogue à afficher.
            min_width    (int)            : Largeur minimale en pixels.
            min_height   (int)            : Hauteur minimale en pixels.
            focus_widget (tk.Widget|None) : Widget qui doit recevoir le focus initial.
        """
        dialog.update_idletasks()   # Force le recalcul des dimensions avant de les lire
        width = max(min_width, dialog.winfo_reqwidth())
        height = max(min_height, dialog.winfo_reqheight())
        # Position centrée par rapport à la fenêtre principale
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_width = self.winfo_width()
        parent_height = self.winfo_height()
        x = parent_x + max(0, (parent_width - width) // 2)
        y = parent_y + max(0, (parent_height - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()          # Rend visible (au cas où il était caché)
        dialog.lift(self)           # Passe la boîte de dialogue au premier plan
        try:
            dialog.attributes("-topmost", True)
            # On remet topmost à False après 50ms pour ne pas rester bloqué devant toutes les fenêtres
            dialog.after(50, lambda: dialog.winfo_exists() and dialog.attributes("-topmost", False))
        except tk.TclError:
            pass
        dialog.grab_set()           # Mode modal : bloque les clics sur la fenêtre principale
        if focus_widget is not None:
            # focus_force() via after() : plus fiable que focus_set() sur Windows
            # avec overrideredirect(True). Le délai laisse Tkinter finir de positionner
            # la fenêtre avant de forcer le focus.
            dialog.after(10, lambda: focus_widget.focus_force() if dialog.winfo_exists() else None)

    def _make_window_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        """
        Crée un bouton de barre de titre (fermer, plein écran, réduire).

        Ces boutons ont un style épuré (sans bordure, fond très sombre) avec
        un effet de survol (hover) qui assombrit encore le fond.

        Arguments :
            parent  (tk.Widget) : Widget parent (la barre de titre).
            text    (str)       : Texte du bouton (ex: "X", "□", "-").
            command (callable)  : Fonction appelée au clic.

        Retourne :
            tk.Button : Bouton stylisé prêt à être affiché.
        """
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg="#181818",           # Fond gris très sombre
            fg="#f1f1f1",           # Texte blanc cassé
            activebackground="#000000",   # Fond noir au clic
            activeforeground="#ffffff",   # Texte blanc au clic
            bd=0,                   # Pas de bordure
            relief=tk.FLAT,         # Style plat (sans relief 3D)
            width=4,
            height=1,
            font=("Segoe UI", 13, "bold"),
        )
        # Effets de survol (hover) : assombrit le fond quand la souris est dessus
        button.bind("<Enter>", lambda event: button.configure(bg="#000000"))
        button.bind("<Leave>", lambda event: button.configure(bg="#181818"))
        return button

    # ==========================================================================
    #  GESTION DE LA FENÊTRE (déplacement, redimensionnement, plein écran)
    # ==========================================================================

    def _initial_show(self) -> None:
        """
        Premier affichage de la fenêtre : configure le style taskbar puis révèle la fenêtre.

        On configure le style Win32 (WS_EX_APPWINDOW) AVANT de montrer la fenêtre pour
        la première fois. Ainsi Windows intègre directement la fenêtre dans la barre des
        tâches sans avoir besoin d'un cycle withdraw/deiconify, ce qui élimine le flash.
        """
        self._ensure_windows_appwindow(refresh=False)
        self.deiconify()

    def _show_in_windows_taskbar(self) -> None:
        """
        Force l'affichage de l'icône de l'application dans la barre des tâches Windows.

        Nécessaire car overrideredirect(True) cache normalement l'application de la barre.
        Utilisé uniquement après une restauration depuis la barre des tâches.
        """
        self._ensure_windows_appwindow(refresh=True)

    def _ensure_windows_appwindow(self, refresh: bool = False) -> None:
        """
        Configure les styles de fenêtre Windows pour apparaître dans la barre des tâches.

        Utilise l'API Windows (ctypes) pour :
        1. Retirer le style WS_EX_TOOLWINDOW (fenêtre outil = invisible dans la barre)
        2. Ajouter le style WS_EX_APPWINDOW (fenêtre application = visible dans la barre)

        Cette manipulation est nécessaire uniquement sous Windows. Sur Mac/Linux, on retourne
        immédiatement sans rien faire.

        Arguments :
            refresh (bool) : Si True, effectue un cycle caché/visible pour forcer la mise à jour.
        """
        if sys.platform != "win32":
            return   # Pas sous Windows → rien à faire

        try:
            import ctypes   # Module Python pour appeler des DLL Windows directement

            # Obtenir le handle (identifiant interne Windows) de notre fenêtre
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()

            # Constantes des styles de fenêtre Windows (valeurs hexadécimales définies par l'API Win32)
            gwl_exstyle = -20               # Index pour lire/écrire le style étendu
            ws_ex_appwindow = 0x00040000    # Fenêtre application (visible dans la barre des tâches)
            ws_ex_toolwindow = 0x00000080   # Fenêtre outil (invisible dans la barre des tâches)
            swp_nosize = 0x0001             # Ne pas changer la taille lors de SetWindowPos
            swp_nomove = 0x0002             # Ne pas changer la position
            swp_nozorder = 0x0004           # Ne pas changer l'ordre Z (avant/derrière)
            swp_noactivate = 0x0010         # Ne pas activer la fenêtre
            swp_framechanged = 0x0020       # Recalculer le cadre de la fenêtre

            # Lecture du style actuel
            style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
            # Calcul du style voulu : enlève WS_EX_TOOLWINDOW, ajoute WS_EX_APPWINDOW
            desired_style = (style & ~ws_ex_toolwindow) | ws_ex_appwindow
            if style != desired_style:
                ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, desired_style)
            # Notification à Windows que le style a changé
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                swp_nosize | swp_nomove | swp_nozorder | swp_noactivate | swp_framechanged,
            )
            if refresh and self.state() == "normal":
                # Cycle caché/visible pour forcer l'apparition dans la barre des tâches
                self.withdraw()
                self.after(10, self.deiconify)
        except Exception:
            return   # Si l'API Windows échoue → on l'ignore silencieusement

    def _restore_borderless(self, event: tk.Event | None = None) -> None:
        """
        Restaure le mode sans-bords après que la fenêtre a été dépliée (depuis la barre des tâches).

        Quand on minimise puis restaure la fenêtre, Tkinter réapplique parfois les bords
        de fenêtre standard. Cet événement (<Map>) est déclenché quand la fenêtre
        redevient visible, et on réapplique overrideredirect(True).

        Arguments :
            event (tk.Event|None) : Événement Tkinter (non utilisé directement).
        """
        if event and event.widget is not self:
            return   # L'événement concerne un widget enfant → on l'ignore
        if self.state() == "normal":
            self.overrideredirect(True)
            self.after(0, self._ensure_windows_appwindow)

    def _start_move(self, event: tk.Event) -> None:
        """
        Mémorise la position initiale pour le déplacement de la fenêtre par drag.

        Appelé au clic souris sur la barre de titre (MouseButtonPress).
        On mémorise le point de clic par rapport au coin de la fenêtre.

        Arguments :
            event (tk.Event) : Événement de clic (contient les coordonnées relatives).
        """
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _move_window(self, event: tk.Event) -> None:
        """
        Déplace la fenêtre en suivant le curseur lors d'un drag sur la barre de titre.

        Appelé à chaque mouvement de souris pendant le drag (B1-Motion).
        On calcule la nouvelle position en décalant de la même distance que le drag.

        Arguments :
            event (tk.Event) : Événement de mouvement (non utilisé, on prend winfo_pointer).
        """
        if self.is_fullscreen:
            return   # Pas de déplacement en plein écran
        # Position absolue du curseur sur l'écran, moins le décalage initial
        x = self.winfo_pointerx() - self._drag_start_x
        y = self.winfo_pointery() - self._drag_start_y
        self.geometry(f"+{x}+{y}")   # "+x+y" = position sans changer la taille

    def _start_resize(self, event: tk.Event) -> None:
        """
        Mémorise l'état initial pour le redimensionnement de la fenêtre.

        Appelé au clic sur le coin de redimensionnement (grip en bas à droite).

        Arguments :
            event (tk.Event) : Événement de clic (non utilisé directement).
        """
        if self.is_fullscreen:
            return
        self._resize_start_x = self.winfo_pointerx()
        self._resize_start_y = self.winfo_pointery()
        self._resize_start_width = self.winfo_width()
        self._resize_start_height = self.winfo_height()

    def _resize_window(self, event: tk.Event) -> None:
        """
        Redimensionne la fenêtre en suivant le déplacement du grip.

        Calcule la nouvelle taille en ajoutant le déplacement du curseur.
        Respect de la taille minimale (820×540).

        Arguments :
            event (tk.Event) : Événement de mouvement souris.
        """
        if self.is_fullscreen:
            return
        width = max(820, self._resize_start_width + self.winfo_pointerx() - self._resize_start_x)
        height = max(540, self._resize_start_height + self.winfo_pointery() - self._resize_start_y)
        self.geometry(f"{width}x{height}")

    def _toggle_fullscreen(self) -> None:
        """Bascule entre le mode plein écran et le mode fenêtré."""
        if self.is_fullscreen:
            self._set_fullscreen(False)
        else:
            self.normal_geometry = self.geometry()   # Mémorise la géométrie actuelle pour pouvoir revenir
            self._set_fullscreen(True)

    def _exit_fullscreen(self, event: tk.Event | None = None) -> None:
        """
        Sort du mode plein écran (déclenché par la touche Échap).

        Arguments :
            event (tk.Event|None) : Événement clavier (non utilisé).
        """
        if self.is_fullscreen:
            self._set_fullscreen(False)

    def _set_fullscreen(self, enabled: bool) -> None:
        """
        Active ou désactive le mode plein écran.

        Essaie d'abord l'attribut -fullscreen natif. Si non supporté (certaines
        versions Tkinter), utilise la taille de l'écran directement.
        Met à jour le texte du bouton plein écran.

        Arguments :
            enabled (bool) : True pour activer, False pour désactiver.
        """
        self.is_fullscreen = enabled
        try:
            self.attributes("-fullscreen", enabled)
        except tk.TclError:
            # -fullscreen non supporté → simulation manuelle avec la taille de l'écran
            if enabled:
                self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
            else:
                self.geometry(self.normal_geometry)
        if not enabled:
            self.geometry(self.normal_geometry)   # Restaure la taille mémorisée
        if self.fullscreen_button is not None:
            self.fullscreen_button.configure(text="▢" if enabled else "□")

    def _minimize_window(self) -> None:
        """
        Réduit la fenêtre dans la barre des tâches.

        On doit d'abord réactiver les bordures (overrideredirect=False) pour
        que la minimisation fonctionne correctement, puis on les supprime
        à nouveau quand la fenêtre est restaurée (via _restore_borderless).
        """
        if self.is_fullscreen:
            self._set_fullscreen(False)
        self.overrideredirect(False)   # Réactive les bords (nécessaire pour iconify)
        self.iconify()                 # Minimise dans la barre des tâches
        self.after(150, self._ensure_windows_appwindow)   # Maintient l'icône dans la barre

    def _configure_style(self) -> None:
        """
        Configure le thème visuel sombre de toute l'interface.

        Utilise ttk.Style pour définir les couleurs et polices de tous les widgets ttk.
        Le thème "clam" est utilisé comme base car il est le plus personnalisable.

        Palette de couleurs :
            #202020 = fond principal (gris très foncé)
            #242424 = fond des en-têtes
            #2d2d2d = fond des champs de saisie
            #3b3b3b = bordures
            #e8e8e8 = texte principal
            #000000 = onglet actif (noir)
        """
        style = ttk.Style(self)
        style.theme_use("clam")   # Thème de base : le plus compatible avec la personnalisation
        # Style global : s'applique à tous les widgets ttk par défaut
        style.configure(".", background="#202020", foreground="#e8e8e8", fieldbackground="#2d2d2d")
        style.configure("TFrame", background="#202020")
        style.configure("TNotebook", background="#202020", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2d2d2d", foreground="#e8e8e8", padding=(10, 5), font=("Segoe UI", 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#000000"), ("active", "#000000")],     # Onglet sélectionné/survolé = noir
            foreground=[("selected", "#ffffff"), ("active", "#ffffff")],      # Texte blanc pour l'onglet actif
            padding=[("selected", (22, 10)), ("!selected", (10, 5))],         # Onglet actif = padding plus grand
            font=[("selected", ("Segoe UI", 10, "bold")), ("!selected", ("Segoe UI", 9))],
        )
        style.configure("Header.TFrame", background="#242424")
        style.configure("TLabel", background="#202020", foreground="#e8e8e8")
        style.configure("HeaderTitle.TLabel", background="#242424", foreground="#ffffff", font=("Segoe UI", 15, "bold"))
        style.configure("HeaderSub.TLabel", background="#242424", foreground="#bdbdbd")
        style.configure("TEntry", fieldbackground="#2d2d2d", foreground="#ffffff", insertcolor="#ffffff")
        style.configure(
            "TCombobox",
            fieldbackground="#2d2d2d",
            foreground="#ffffff",
            arrowcolor="#e8e8e8",
            selectbackground="#000000",
            selectforeground="#ffffff",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#2d2d2d"), ("hover", "#000000")],
            background=[("active", "#000000"), ("pressed", "#000000")],
            selectbackground=[("readonly", "#000000")],
            selectforeground=[("readonly", "#ffffff")],
        )
        style.configure("TCheckbutton", background="#202020", foreground="#e8e8e8")
        style.map(
            "TCheckbutton",
            background=[("active", "#000000"), ("pressed", "#000000")],
            foreground=[("active", "#ffffff")],
        )
        style.configure("TButton", background="#3b3b3b", foreground="#ffffff", borderwidth=1, focusthickness=0, padding=(12, 6))
        style.map("TButton", background=[("active", "#000000"), ("pressed", "#000000")])
        style.configure(
            "LowResVisible.TButton",
            background="#7d5f00",
            foreground="#ffffff",
            borderwidth=1,
            focusthickness=0,
            padding=(12, 6),
        )
        style.map(
            "LowResVisible.TButton",
            background=[("active", "#5f4800"), ("pressed", "#5f4800")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure(
            "LowResHidden.TButton",
            background="#1f7a45",
            foreground="#ffffff",
            borderwidth=1,
            focusthickness=0,
            padding=(12, 6),
        )
        style.map(
            "LowResHidden.TButton",
            background=[("active", "#185f36"), ("pressed", "#185f36")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure("Horizontal.TProgressbar", troughcolor="#2d2d2d", background="#8a8a8a", bordercolor="#202020")
        style.configure("FoilOn.TButton", background="#7a6000", foreground="#ffffff", borderwidth=1, focusthickness=0, padding=(12, 6))
        style.map("FoilOn.TButton", background=[("active", "#5a4600"), ("pressed", "#5a4600")], foreground=[("active", "#ffffff")])
        style.configure(
            "Treeview",
            background="#181818",
            fieldbackground="#181818",
            foreground="#e8e8e8",
            rowheight=24,
            bordercolor="#3b3b3b",
        )
        style.map("Treeview", background=[("selected", "#000000")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#2d2d2d", foreground="#ffffff", relief=tk.FLAT)
        style.map("Treeview.Heading", background=[("active", "#000000")])
        self.option_add("*TCombobox*Listbox.background", "#181818")
        self.option_add("*TCombobox*Listbox.foreground", "#ffffff")
        self.option_add("*TCombobox*Listbox.selectBackground", "#000000")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ==========================================================================
    #  CONSTRUCTION DE L'INTERFACE GRAPHIQUE
    # ==========================================================================

    def _build_ui(self) -> None:
        """
        Construit l'intégralité de l'interface graphique.

        Crée dans l'ordre :
        1. La barre de titre personnalisée (avec logo, titre, boutons)
        2. Le widget Notebook (onglets) qui occupe le reste de la fenêtre
        3. Le grip de redimensionnement (coin bas-droit)
        4. Les 6 onglets (délègue à _build_*_tab)
        """
        self._configure_style()

        # --- Barre de titre personnalisée ---
        titlebar = tk.Frame(self, bg="#181818", height=42)
        titlebar.pack(fill=tk.X)
        # Binding pour déplacer la fenêtre en maintenant le clic sur la barre de titre
        titlebar.bind("<ButtonPress-1>", self._start_move)
        titlebar.bind("<B1-Motion>", self._move_window)

        title_logo = tk.Label(titlebar, image=self.titlebar_logo, bg="#181818")
        title_logo.pack(side=tk.LEFT, padx=(10, 8))
        title_logo.bind("<ButtonPress-1>", self._start_move)
        title_logo.bind("<B1-Motion>", self._move_window)

        title = tk.Label(
            titlebar,
            text="Scryfall Artwork Downloader",
            bg="#181818",
            fg="#f1f1f1",
            font=("Segoe UI", 10, "bold"),
        )
        title.pack(side=tk.LEFT)
        title.bind("<ButtonPress-1>", self._start_move)
        title.bind("<B1-Motion>", self._move_window)

        self._make_window_button(titlebar, "X", self._close_application).pack(side=tk.RIGHT)
        self.fullscreen_button = self._make_window_button(titlebar, "□", self._toggle_fullscreen)
        self.fullscreen_button.pack(side=tk.RIGHT)
        self._make_window_button(titlebar, "-", self._minimize_window).pack(side=tk.RIGHT)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        resize_grip = ttk.Sizegrip(self)
        resize_grip.place(relx=1.0, rely=1.0, anchor="se")
        resize_grip.bind("<ButtonPress-1>", self._start_resize)
        resize_grip.bind("<B1-Motion>", self._resize_window)

        scraper_tab = ttk.Frame(notebook)
        decklist_tab = ttk.Frame(notebook)
        upscaler_tab = ttk.Frame(notebook)
        margin_tab = ttk.Frame(notebook)
        crop_tab = ttk.Frame(notebook)
        xml_tab = ttk.Frame(notebook)
        notebook.add(scraper_tab, text="Scryfall Downloader")
        notebook.add(decklist_tab, text="Decklist")
        notebook.add(upscaler_tab, text="DPI Upscaler")
        notebook.add(margin_tab, text="Margin Creator")
        notebook.add(crop_tab, text="Ratio Cropper")
        notebook.add(xml_tab, text="XML Generator")

        self._build_scraper_tab(scraper_tab)
        self._build_decklist_tab(decklist_tab)
        self._build_upscaler_tab(upscaler_tab)
        self._build_margin_tab(margin_tab)
        self._build_crop_tab(crop_tab)
        self._build_xml_tab(xml_tab)

    def _build_scraper_tab(self, root: ttk.Frame) -> None:
        """
        Construit l'onglet "Scryfall Downloader" (onglet 1).

        Cet onglet permet de :
        - Télécharger toutes les cartes d'un set en collant l'URL Scryfall du set
        - Télécharger une carte individuelle en collant son URL Scryfall
        - Choisir le dossier de sortie, la taille d'image et si on remplace les fichiers

        Layout (grille) :
            Ligne 0 : En-tête avec logo et titre
            Ligne 1 : Champ "Lien du set"
            Ligne 2 : Champ "Lien de carte"
            Ligne 3 : Champ "Dossier de sortie" + bouton Parcourir
            Ligne 4 : Sélecteur "Taille image"
            Ligne 5 : Case "Remplacer les fichiers"
            Ligne 6 : Boutons Télécharger + Annuler
            Ligne 7 : Barre de progression + label de progression
            Ligne 8 : Zone de log (s'étire avec la fenêtre)

        Arguments :
            root (ttk.Frame) : Conteneur de l'onglet.
        """
        root.columnconfigure(1, weight=1)
        root.rowconfigure(8, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="Scryfall Artwork Downloader", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Artwork par set, dossier par langue", style="HeaderSub.TLabel").grid(row=1, column=1, sticky="w")

        ttk.Label(root, text="Lien du set").grid(row=1, column=0, sticky="w")
        self.url_entry = ttk.Entry(root, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(12, 0))
        self.url_entry.configure(foreground="#9a9a9a")
        self.url_entry.bind("<FocusIn>", self._clear_url_placeholder)
        self.url_entry.bind("<FocusOut>", self._restore_url_placeholder)

        ttk.Label(root, text="Lien de carte").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.card_url_entry = ttk.Entry(root, textvariable=self.card_url_var)
        self.card_url_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(12, 0))
        self.card_url_entry.configure(foreground="#9a9a9a")
        self.card_url_entry.bind("<FocusIn>", self._clear_card_url_placeholder)
        self.card_url_entry.bind("<FocusOut>", self._restore_card_url_placeholder)

        ttk.Label(root, text="Dossier de sortie").grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.output_var).grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(root, text="Parcourir", command=self._choose_output).grid(row=3, column=2, sticky="ew", pady=(12, 0))

        ttk.Label(root, text="Taille image").grid(row=4, column=0, sticky="w", pady=(12, 0))
        image_size_combo = ttk.Combobox(
            root,
            textvariable=self.image_size_var,
            values=("small", "normal", "large", "png", "art_crop", "border_crop"),
            state="readonly",
            width=16,
        )
        image_size_combo.grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(12, 0))
        self._bind_combobox_dropdown_top(image_size_combo)

        ttk.Checkbutton(root, text="Remplacer les fichiers déjà présents", variable=self.overwrite_var).grid(
            row=5, column=1, sticky="w", padx=(12, 0), pady=(12, 0)
        )

        scraper_btn_frame = ttk.Frame(root)
        scraper_btn_frame.grid(row=6, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(16, 0))
        self.start_button = ttk.Button(scraper_btn_frame, text="Télécharger", command=self._start_download)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.cancel_button = ttk.Button(scraper_btn_frame, text="Annuler", command=self._cancel_download, state="disabled")
        self.cancel_button.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 8))

        self.progress_label = ttk.Label(root, text="En attente", anchor="e")
        self.progress_label.grid(row=7, column=2, sticky="ew", padx=(12, 0), pady=(16, 8))

        self.log = self._make_log_widget(root)
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew")

    def _build_decklist_tab(self, root: ttk.Frame) -> None:
        """
        Construit l'onglet "Decklist" (onglet 2).

        C'est l'onglet le plus complexe. Il permet de :
        1. Coller une liste de cartes en texte brut (ex: "4 Lightning Bolt")
        2. Lancer l'analyse → recherche toutes les éditions disponibles pour chaque carte
        3. Choisir l'édition souhaitée pour chaque carte (double-clic)
        4. Verrouiller des sélections pour les protéger
        5. Télécharger toutes les images sélectionnées
        6. Sauvegarder/charger l'état complet (avec les sélections d'éditions)

        Layout :
            Ligne 0 : En-tête + boutons Sauvegarde/Chargement
            Ligne 1 : Langue + Dossier de destination
            Ligne 2 : Taille image + Options (remplacer, verrouiller)
            Ligne 3 : Zone de texte "Decklist brute" (s'étire)
            Ligne 4 : Boutons d'action (Analyser, Télécharger, Corriger LowRes, Annuler)
            Ligne 5 : En-tête du tableau
            Ligne 6 : Tableau des cartes (s'étire)
            Ligne 7 : Barre de progression
            Ligne 8 : Zone de log

        Arguments :
            root (ttk.Frame) : Conteneur de l'onglet.
        """
        for row in range(9):
            root.rowconfigure(row, weight=0)
        root.columnconfigure(1, weight=1)
        root.columnconfigure(3, weight=1)
        root.rowconfigure(3, weight=3, minsize=120)
        root.rowconfigure(6, weight=2, minsize=96)
        root.rowconfigure(8, weight=1, minsize=48)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.decklist_header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="Decklist", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(
            header,
            text="Filtre la langue choisie, avec fallback anglais seulement si aucun highres n'existe.",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=1, sticky="w")

        header_actions = ttk.Frame(header, style="Header.TFrame")
        header_actions.grid(row=0, column=2, rowspan=2, sticky="e")
        self.decklist_save_state_header_button = ttk.Button(
            header_actions,
            text="Sauvegarde",
            command=self._save_decklist_state,
        )
        self.decklist_save_state_header_button.pack(side=tk.LEFT, padx=(0, 8))
        self.decklist_load_state_header_button = ttk.Button(
            header_actions,
            text="Chargement",
            command=self._load_decklist_state,
        )
        self.decklist_load_state_header_button.pack(side=tk.LEFT)

        ttk.Label(root, text="Langue").grid(row=1, column=0, sticky="w")
        decklist_language_combo = ttk.Combobox(
            root,
            textvariable=self.decklist_language_var,
            values=("all", "en", "fr", "de", "es", "it", "pt", "ja", "ko", "ru", "zhs", "zht"),
            state="readonly",
            width=8,
        )
        decklist_language_combo.grid(row=1, column=1, sticky="w", padx=(12, 0))
        self._bind_combobox_dropdown_top(decklist_language_combo)

        ttk.Label(root, text="Dossier de destination").grid(row=1, column=2, sticky="e", padx=(12, 0))
        ttk.Entry(root, textvariable=self.decklist_output_var).grid(row=1, column=3, sticky="ew", padx=(12, 8))
        ttk.Button(root, text="Parcourir", command=self._choose_decklist_output).grid(row=1, column=4, sticky="ew")

        ttk.Label(root, text="Taille image").grid(row=2, column=0, sticky="w", pady=(8, 0))
        decklist_image_size_combo = ttk.Combobox(
            root,
            textvariable=self.decklist_image_size_var,
            values=("small", "normal", "large", "png", "art_crop", "border_crop"),
            state="readonly",
            width=16,
        )
        decklist_image_size_combo.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        decklist_image_size_combo.bind("<<ComboboxSelected>>", self._on_decklist_image_size_changed)
        self._bind_combobox_dropdown_top(decklist_image_size_combo)

        options_frame = ttk.Frame(root)
        options_frame.grid(row=2, column=2, columnspan=3, sticky="ew", padx=(12, 0), pady=(8, 0))
        ttk.Checkbutton(options_frame, text="Remplacer les fichiers déjà présents", variable=self.decklist_overwrite_var).pack(
            side=tk.LEFT
        )
        ttk.Label(root, text="Decklist brute").grid(row=3, column=0, sticky="nw", pady=(10, 0))

        self.decklist_text = tk.Text(
            root,
            height=7,
            wrap="word",
            bg="#181818",
            fg="#e8e8e8",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#666666",
        )
        self.decklist_text.grid(row=3, column=1, columnspan=3, sticky="nsew", padx=(12, 0), pady=(10, 0))
        decklist_scroll = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.decklist_text.yview)
        decklist_scroll.grid(row=3, column=4, sticky="ns", pady=(10, 0))
        self.decklist_text.configure(yscrollcommand=decklist_scroll.set)
        self.decklist_text.bind("<Control-v>", self._paste_decklist_text)
        self.decklist_text.bind("<Control-V>", self._paste_decklist_text)
        self.decklist_text.bind("<Control-a>", self._select_all_decklist_text)
        self.decklist_text.bind("<Control-A>", self._select_all_decklist_text)

        decklist_btn_frame = ttk.Frame(root)
        decklist_btn_frame.grid(row=4, column=1, columnspan=4, sticky="w", padx=(12, 0), pady=(8, 0))
        self.decklist_analyze_button = ttk.Button(decklist_btn_frame, text="Analyser", command=self._start_decklist_analysis)
        self.decklist_analyze_button.pack(side=tk.LEFT, padx=(0, 8))
        self.decklist_download_button = ttk.Button(
            decklist_btn_frame, text="Télécharger", command=self._start_decklist_download, state="disabled"
        )
        self.decklist_download_button.pack(side=tk.LEFT, padx=(0, 8))
        self.decklist_fix_lowres_button = ttk.Button(
            decklist_btn_frame,
            text="Corriger les LowRes",
            command=self._fix_all_lowres_decklist,
            state="disabled",
        )
        self.decklist_fix_lowres_button.pack(side=tk.LEFT, padx=(0, 8))
        self.decklist_cancel_button = ttk.Button(decklist_btn_frame, text="Annuler", command=self._cancel_decklist, state="disabled")
        self.decklist_cancel_button.pack(side=tk.LEFT)

        ttk.Label(root, text="Cartes").grid(row=5, column=0, sticky="w", pady=(8, 4))
        ttk.Label(
            root,
            text="Double-clic sur une édition pour la changer. Double-clic sur la colonne Copie pour modifier la quantité.",
        ).grid(row=5, column=1, columnspan=4, sticky="e", pady=(8, 4))

        columns = ("qty", "lock", "name", "edition")
        self.decklist_tree = ttk.Treeview(root, columns=columns, show="headings", height=7, selectmode="browse")
        self.decklist_tree.tag_configure("lowres", foreground="#ff9800")
        self.decklist_tree.heading("qty", text="Copie")
        self.decklist_tree.heading("lock", text="🔒")
        self.decklist_tree.heading("name", text="Carte")
        self.decklist_tree.heading("edition", text="Edition")
        self.decklist_tree.column("qty", width=60, anchor="center", stretch=False)
        self.decklist_tree.column("lock", width=72, anchor="center", stretch=False)
        self.decklist_tree.column("name", width=210, stretch=True)
        self.decklist_tree.column("edition", width=330, stretch=True)
        self.decklist_tree.grid(row=6, column=0, columnspan=4, sticky="nsew")
        tree_scroll = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.decklist_tree.yview)
        tree_scroll.grid(row=6, column=4, sticky="ns")
        self.decklist_tree.configure(yscrollcommand=tree_scroll.set)
        self.decklist_tree.bind("<<TreeviewSelect>>", self._on_decklist_row_selected)
        self.decklist_tree.bind("<Double-1>", self._choose_decklist_edition)

        self.decklist_progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.decklist_progress.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        self.decklist_progress_label = ttk.Label(root, text="En attente", anchor="e")
        self.decklist_progress_label.grid(row=7, column=4, sticky="ew", padx=(12, 0), pady=(8, 4))

        self.decklist_log = self._make_log_widget(root)
        self.decklist_log.configure(height=4)
        self.decklist_log.grid(row=8, column=0, columnspan=5, sticky="nsew")

    def _build_xml_tab(self, root: ttk.Frame) -> None:
        """
        Construit l'onglet "XML Generator" (onglet 6, dernier).

        L'utilisateur fournit un dossier d'images et obtient une seule archive ZIP
        contenant les images renommées (underscores → espaces) + Cardback.jpg + order.xml.

        Arguments :
            root (ttk.Frame) : Conteneur de l'onglet.
        """
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.xml_header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="XML Generator", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(
            header,
            text="Dossier d'images → archive ZIP (images renommées + Cardback.jpg + <nom>.xml).",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=1, sticky="w")

        ttk.Label(root, text="Dossier images").grid(row=1, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.xml_source_var).grid(row=1, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(root, text="Parcourir", command=self._choose_xml_source).grid(row=1, column=2, sticky="ew")

        ttk.Label(root, text="Dossier de sortie").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.xml_output_var).grid(row=2, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(root, text="Parcourir", command=self._choose_xml_output).grid(row=2, column=2, sticky="ew", pady=(12, 0))

        ttk.Label(root, text="Nom du projet").grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.xml_name_var).grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))

        ttk.Label(root, text="Stock").grid(row=4, column=0, sticky="w", pady=(12, 0))
        xml_options_frame = ttk.Frame(root)
        xml_options_frame.grid(row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(12, 0))

        xml_stock_combo = ttk.Combobox(
            xml_options_frame,
            textvariable=self.xml_stock_var,
            values=(
                "(S27) Smooth",
                "(S30) Standard Smooth",
                "(S33) Superior Smooth",
                "(M31) Linen",
                "(P10) Plastic",
            ),
            state="readonly",
            width=26,
        )
        xml_stock_combo.pack(side=tk.LEFT, padx=(0, 16))
        self._bind_combobox_dropdown_top(xml_stock_combo)

        self.xml_foil_button = ttk.Button(
            xml_options_frame,
            text="Foil : Non",
            style="TButton",
            command=self._toggle_xml_foil,
        )
        self.xml_foil_button.pack(side=tk.LEFT)

        xml_gen_frame = ttk.Frame(root)
        xml_gen_frame.grid(row=5, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(16, 0))
        self.xml_generate_button = ttk.Button(xml_gen_frame, text="Générer XML", command=self._start_xml_generation)
        self.xml_generate_button.pack(side=tk.LEFT)

        self.xml_progress = ttk.Progressbar(root, mode="indeterminate")
        self.xml_progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 4))

        self.xml_log = self._make_log_widget(root)
        self.xml_log.configure(height=6)
        self.xml_log.grid(row=7, column=0, columnspan=3, sticky="nsew")

    def _build_upscaler_tab(self, root: ttk.Frame) -> None:
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.upscale_header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="DPI Upscaler", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Copie les images avec un DPI minimum de 1200", style="HeaderSub.TLabel").grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(root, text="Dossier source").grid(row=1, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.upscale_folder_var).grid(row=1, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(root, text="Parcourir", command=self._choose_upscale_folder).grid(row=1, column=2, sticky="ew")

        ttk.Label(root, text="Dossier de sortie").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.upscale_output_var).grid(row=2, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(root, text="Parcourir", command=self._choose_upscale_output).grid(row=2, column=2, sticky="ew", pady=(12, 0))

        upscale_btn_frame = ttk.Frame(root)
        upscale_btn_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(16, 0))
        self.upscale_start_button = ttk.Button(upscale_btn_frame, text="Upscaler DPI", command=self._start_upscale)
        self.upscale_start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.upscale_cancel_button = ttk.Button(upscale_btn_frame, text="Annuler", command=self._cancel_upscale, state="disabled")
        self.upscale_cancel_button.pack(side=tk.LEFT)

        self.upscale_progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.upscale_progress.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 8))

        self.upscale_progress_label = ttk.Label(root, text="En attente", anchor="e")
        self.upscale_progress_label.grid(row=4, column=2, sticky="ew", padx=(12, 0), pady=(16, 8))

        self.upscale_log = self._make_log_widget(root)
        self.upscale_log.grid(row=6, column=0, columnspan=3, sticky="nsew")

    def _build_margin_tab(self, root: ttk.Frame) -> None:
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.margin_header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="Margin Creator", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Ajoute une marge colorée selon les DPI et le bord de l'image", style="HeaderSub.TLabel").grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(root, text="Dossier source").grid(row=1, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.margin_folder_var).grid(row=1, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(root, text="Parcourir", command=self._choose_margin_folder).grid(row=1, column=2, sticky="ew")

        ttk.Label(root, text="Dossier de sortie").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.margin_output_var).grid(row=2, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(root, text="Parcourir", command=self._choose_margin_output).grid(row=2, column=2, sticky="ew", pady=(12, 0))

        margin_btn_frame = ttk.Frame(root)
        margin_btn_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(16, 0))
        self.margin_start_button = ttk.Button(margin_btn_frame, text="Créer les marges", command=self._start_margin)
        self.margin_start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.margin_cancel_button = ttk.Button(margin_btn_frame, text="Annuler", command=self._cancel_margin, state="disabled")
        self.margin_cancel_button.pack(side=tk.LEFT)

        self.margin_progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.margin_progress.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 8))

        self.margin_progress_label = ttk.Label(root, text="En attente", anchor="e")
        self.margin_progress_label.grid(row=4, column=2, sticky="ew", padx=(12, 0), pady=(16, 8))

        self.margin_log = self._make_log_widget(root)
        self.margin_log.grid(row=6, column=0, columnspan=3, sticky="nsew")

    def _build_crop_tab(self, root: ttk.Frame) -> None:
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=12)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        logo = tk.Label(header, image=self.trim_header_logo, bg="#242424")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        ttk.Label(header, text="Ratio Cropper", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Recadre au ratio 0.714:1 sans générer de contenu", style="HeaderSub.TLabel").grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(root, text="Image source").grid(row=1, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.crop_image_var).grid(row=1, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(root, text="Parcourir", command=self._choose_crop_image, width=12).grid(row=1, column=2, sticky="ew")

        ttk.Label(root, text="Image de sortie").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(root, textvariable=self.crop_output_var).grid(row=2, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(root, text="Parcourir", command=self._choose_crop_output, width=12).grid(
            row=2, column=2, sticky="ew", pady=(12, 0)
        )

        crop_btn_frame = ttk.Frame(root)
        crop_btn_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(16, 0))
        ttk.Button(crop_btn_frame, text="Enregistrer le crop", command=self._save_crop).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(crop_btn_frame, text="Centrer", command=self._reset_crop_rect).pack(side=tk.LEFT)
        ttk.Label(root, text="Sélection").grid(row=4, column=0, sticky="w", pady=(16, 8))
        ttk.Label(root, textvariable=self.crop_status_var, anchor="e").grid(
            row=4, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(16, 8)
        )

        self.crop_canvas = tk.Canvas(
            root,
            width=500,
            height=260,
            bg="#181818",
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#666666",
            cursor="crosshair",
        )
        self.crop_canvas.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(0, 10))
        self.crop_canvas.bind("<ButtonPress-1>", self._crop_press)
        self.crop_canvas.bind("<B1-Motion>", self._crop_drag)
        self.crop_canvas.bind("<ButtonRelease-1>", self._crop_release)
        self.crop_canvas.bind("<Configure>", lambda event: self._draw_crop_canvas())

    # ==========================================================================
    #  WIDGETS UTILITAIRES PARTAGÉS
    # ==========================================================================

    @staticmethod
    def _make_log_widget(parent: tk.Widget) -> tk.Text:
        """
        Crée une zone de texte stylisée pour afficher les messages de log.

        La zone est en lecture seule (state="disabled") pour que l'utilisateur
        ne puisse pas la modifier. On l'active temporairement pour écrire,
        puis on la remet en disabled (voir _append_log).

        Arguments :
            parent (tk.Widget) : Widget parent dans lequel créer la zone de log.

        Retourne :
            tk.Text : Zone de log prête à l'emploi.
        """
        return tk.Text(
            parent,
            height=12,
            wrap="word",        # Retour à la ligne automatique sur les mots
            state="disabled",   # Lecture seule (l'utilisateur ne peut pas modifier)
            bg="#181818",
            fg="#e8e8e8",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#666666",
        )

    # ==========================================================================
    #  GESTION DES CHAMPS AVEC PLACEHOLDER (texte exemple en grisé)
    # ==========================================================================
    # Les champs URL affichent un texte d'exemple en gris quand ils sont vides.
    # Quand l'utilisateur clique, le texte disparaît. Quand le champ reprend sa valeur
    # vide, le texte exemple réapparaît.

    def _clear_url_placeholder(self, event: tk.Event | None = None) -> None:
        """Efface le texte d'exemple du champ 'Lien du set' au clic."""
        if self.url_var.get() == self.URL_PLACEHOLDER:
            self.url_var.set("")
            self.url_entry.configure(foreground="#ffffff")

    def _restore_url_placeholder(self, event: tk.Event | None = None) -> None:
        """Restaure le texte d'exemple du champ 'Lien du set' quand il est vide."""
        if not self.url_var.get().strip():
            self.url_var.set(self.URL_PLACEHOLDER)
            self.url_entry.configure(foreground="#9a9a9a")   # Gris pour indiquer que c'est un exemple
        else:
            self.url_entry.configure(foreground="#ffffff")

    def _clear_card_url_placeholder(self, event: tk.Event | None = None) -> None:
        """Efface le texte d'exemple du champ 'Lien de carte' au clic."""
        if self.card_url_var.get() == self.CARD_URL_PLACEHOLDER:
            self.card_url_var.set("")
            self.card_url_entry.configure(foreground="#ffffff")

    def _restore_card_url_placeholder(self, event: tk.Event | None = None) -> None:
        """Restaure le texte d'exemple du champ 'Lien de carte' quand il est vide."""
        if not self.card_url_var.get().strip():
            self.card_url_var.set(self.CARD_URL_PLACEHOLDER)
            self.card_url_entry.configure(foreground="#9a9a9a")
        else:
            self.card_url_entry.configure(foreground="#ffffff")

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or ".")
        if folder:
            self.output_var.set(folder)

    def _choose_decklist_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.decklist_output_var.get() or ".")
        if folder:
            self.decklist_output_var.set(folder)

    def _choose_xml_source(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.xml_source_var.get() or ".")
        if folder:
            self.xml_source_var.set(folder)

    def _choose_xml_output(self) -> None:
        initial = self.xml_output_var.get().strip() or "."
        folder = filedialog.askdirectory(initialdir=initial)
        if folder:
            self.xml_output_var.set(folder)

    def _toggle_xml_foil(self) -> None:
        """Bascule l'option Foil et met à jour le style du bouton."""
        new_val = not self.xml_foil_var.get()
        self.xml_foil_var.set(new_val)
        if hasattr(self, "xml_foil_button"):
            if new_val:
                self.xml_foil_button.configure(text="Foil : Oui", style="FoilOn.TButton")
            else:
                self.xml_foil_button.configure(text="Foil : Non", style="TButton")

    def _find_proxy_back(self) -> Path | None:
        """
        Cherche ProxyBack.jpg dans tous les emplacements assets/ possibles.

        Couvre quatre scénarios :
        1. Exe PyInstaller compilé   → sys._MEIPASS / assets /
        2. Script Python (dev)       → dossier parent de app.py / assets /
        3. Lancement depuis un IDE   → Path.cwd() / assets /
        4. Exe lancé depuis son dossier → dossier de sys.executable / assets /
        """
        roots = []

        # 1. PyInstaller : dossier temporaire d'extraction (_MEIPASS)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))

        # 2. Dossier racine du projet (parent du dossier contenant app.py)
        try:
            roots.append(Path(__file__).resolve().parent.parent)
        except Exception:
            pass

        # 3. Répertoire courant au moment de la génération
        roots.append(Path.cwd())

        # 4. Dossier de l'exécutable (pour un exe lancé hors de la racine projet)
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except Exception:
            pass

        for root in roots:
            candidate = root / "assets" / "Cardback.jpg"
            if candidate.exists():
                return candidate

        return None

    def _start_xml_generation(self) -> None:
        """Valide les champs et lance la génération de l'archive ZIP dans un thread."""
        if self.xml_worker and self.xml_worker.is_alive():
            return

        source = self.xml_source_var.get().strip()
        output_dir = self.xml_output_var.get().strip()
        name = self.xml_name_var.get().strip() or "order"
        if not source:
            messagebox.showerror("Dossier manquant", "Sélectionne le dossier contenant les images.")
            return
        if not output_dir:
            messagebox.showerror("Dossier manquant", "Indique le dossier de sortie de l'archive ZIP.")
            return

        stock = self.xml_stock_var.get().strip()
        foil = self.xml_foil_var.get()

        self.xml_generate_button.configure(state="disabled")
        self.xml_progress.configure(mode="indeterminate")
        self.xml_progress.start(12)

        self.xml_worker = threading.Thread(
            target=self._run_xml_generation,
            args=(source, output_dir, name, stock, foil),
            daemon=True,
        )
        self.xml_worker.start()

    def _run_xml_generation(self, source_folder: str, output_dir: str, name: str, stock: str, foil: bool) -> None:
        """
        Thread de travail : génère une archive ZIP contenant les images renommées + order.xml.

        L'utilisateur fournit un dossier d'images → il reçoit UNE SEULE archive ZIP,
        prête à être uploadée sur MakePlayingCards. Aucun fichier intermédiaire sur le disque.

        Contenu de l'archive :
            order.xml                          — XML MakePlayingCards (généré en mémoire)
            <Nom Propre>.jpg / .png            — chaque image unique (doublons exclus)
            Cardback.jpg                       — dos de carte depuis assets/

        Nommage dans l'archive :
            "Ancient_Brass_Dragon_1200DPI_Marged.jpg" → "Ancient Brass Dragon 1200DPI Marged.jpg"
            (underscores remplacés par des espaces)

        Regroupement des doublons :
            Si "Swamp 1 1200DPI Marged.jpg" et "Swamp 2 1200DPI Marged.jpg" sont identiques
            (même hash SHA1), une seule entrée est créée dans le XML avec les deux slots,
            et un seul fichier est mis dans l'archive.

        Arguments :
            source_folder (str)  : Dossier contenant les images de cartes.
            output_zip    (str)  : Chemin de l'archive ZIP à créer.
            stock         (str)  : Type de stock papier (ex: "(S30) Standard Smooth").
            foil          (bool) : True si impression brillante (foil).
        """
        import hashlib
        import zipfile
        from xml.etree.ElementTree import Element, SubElement, indent, tostring

        try:
            EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
            source = Path(source_folder)
            output = Path(output_dir) / f"{name}.zip"

            # --- Scan des fichiers image (Cardback.jpg exclu pour éviter le doublon) ---
            image_files = sorted(
                f for f in source.iterdir()
                if f.is_file() and f.suffix.lower() in EXTENSIONS
                and f.name.lower() != "cardback.jpg"
            )
            if not image_files:
                self.messages.put(("xml_error", "Aucune image trouvée dans le dossier source."))
                return

            self.messages.put(("xml_log", f"{len(image_files)} image(s) trouvée(s). Calcul des hash…"))

            # --- Hash SHA1 de chaque fichier pour détecter les doublons ---
            file_hashes: dict[Path, str] = {}
            for f in image_files:
                file_hashes[f] = hashlib.sha1(f.read_bytes()).hexdigest()

            # --- Regroupement : hash → (fichier canonique, liste des slots) ---
            hash_canonical: dict[str, Path] = {}
            hash_slots: dict[str, list[int]] = {}
            for slot, f in enumerate(image_files):
                h = file_hashes[f]
                if h not in hash_canonical:
                    hash_canonical[h] = f   # Premier fichier avec ce hash = représentant du groupe
                hash_slots.setdefault(h, []).append(slot)

            total_cards = len(image_files)

            # --- Localisation de Cardback.jpg ---
            cardback = self._find_proxy_back()
            if cardback is None:
                checked = [
                    str(r / "assets" / "Cardback.jpg")
                    for r in [
                        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)),
                        Path(__file__).resolve().parent.parent,
                        Path.cwd(),
                        Path(sys.executable).resolve().parent,
                    ]
                ]
                self.messages.put(("xml_error",
                    "Cardback.jpg introuvable. Chemins vérifiés :\n" + "\n".join(f"  {p}" for p in checked)))
                return

            # --- Construction du XML en mémoire ---
            order = Element("order")

            details = SubElement(order, "details")
            SubElement(details, "quantity").text = str(total_cards)
            SubElement(details, "stock").text = stock
            SubElement(details, "foil").text = "true" if foil else "false"

            fronts_el = SubElement(order, "fronts")
            seen_hashes: set[str] = set()
            for f in image_files:
                h = file_hashes[f]
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                canonical  = hash_canonical[h]
                clean_id   = self._xml_file_in_zip(canonical)   # ex: "Sol Ring 1200DPI Marged.jpg"
                clean_name = self._xml_clean_name(canonical)     # idem mais .jpeg si .jpg
                slots_str  = ",".join(str(s) for s in hash_slots[h])
                group_size = len(hash_slots[h])

                card = SubElement(fronts_el, "card")
                SubElement(card, "id").text = f"./Artwork/{clean_id}"
                SubElement(card, "sourceType").text = "Local File"
                SubElement(card, "slots").text = slots_str
                SubElement(card, "name").text = clean_name
                SubElement(card, "query").text = self._xml_query_from_filename(canonical)

                if group_size > 1:
                    self.messages.put(("xml_log", f"Groupé ×{group_size} : {clean_id}"))

            all_slots = ",".join(str(i) for i in range(total_cards))
            backs_el = SubElement(order, "backs")
            back_el = SubElement(backs_el, "card")
            SubElement(back_el, "id").text = f"./Artwork/{self._xml_file_in_zip(cardback)}"
            SubElement(back_el, "sourceType").text = "Local File"
            SubElement(back_el, "slots").text = all_slots
            SubElement(back_el, "name").text = self._xml_clean_name(cardback)
            SubElement(back_el, "query").text = "cardback"

            SubElement(order, "cardback")

            indent(order, space="    ")
            xml_bytes = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                + tostring(order, encoding="unicode")
            ).encode("utf-8")

            # --- Création de l'archive ZIP (tout en mémoire, rien sur le disque sauf le .zip final) ---
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as zf:
                # 1. XML à la racine de l'archive
                zf.writestr(f"{name}.xml", xml_bytes)

                # 2. Images recto dans le sous-dossier Artwork/
                seen_in_zip: set[str] = set()
                for f in image_files:
                    h = file_hashes[f]
                    if h in seen_in_zip:
                        continue
                    seen_in_zip.add(h)
                    zf.write(hash_canonical[h], arcname=f"Artwork/{self._xml_file_in_zip(hash_canonical[h])}")

                # 3. Dos : Cardback.jpg dans Artwork/
                zf.write(cardback, arcname=f"Artwork/{self._xml_file_in_zip(cardback)}")

            unique_images = len(seen_hashes)
            grouped = total_cards - unique_images
            summary = f"{total_cards} carte(s), {unique_images} image(s) unique(s)"
            if grouped:
                summary += f", {grouped} doublon(s) regroupé(s)"
            self.messages.put(("xml_done", f"Archive prête : {summary} → {output.name}"))

        except Exception as error:
            self.messages.put(("xml_error", self._format_error(error)))

    @staticmethod
    def _xml_clean_name(f: Path) -> str:
        """
        Génère le nom propre d'un fichier image pour le XML et l'archive ZIP.

        Deux transformations appliquées :
        1. Underscores → espaces dans le nom (ex: "Sol_Ring_1200DPI" → "Sol Ring 1200DPI")
        2. Extension .jpg → .jpeg dans le champ <name> (requis par MakePlayingCards)

        Le champ <id> utilise l'extension originale, le champ <name> utilise .jpeg.
        Le fichier physique dans le ZIP garde l'extension originale (.jpg ou .png).

        Arguments :
            f (Path) : Fichier source.

        Retourne :
            str : Nom propre avec extension .jpeg si .jpg, sinon extension inchangée.
        """
        clean_stem = f.stem.replace("_", " ").replace("'", " ")
        suffix = ".jpeg" if f.suffix.lower() == ".jpg" else f.suffix
        return clean_stem + suffix

    @staticmethod
    def _xml_file_in_zip(f: Path) -> str:
        """
        Génère le nom du fichier tel qu'il sera stocké dans l'archive ZIP.

        Identique à _xml_clean_name mais conserve l'extension originale (.jpg reste .jpg).
        C'est le nom référencé dans le champ <id> du XML.
        """
        return f.stem.replace("_", " ").replace("'", " ") + f.suffix

    @staticmethod
    def _xml_query_from_filename(f: Path) -> str:
        """
        Génère le champ <query> XML (nom de la carte simplifié) depuis un nom de fichier.

        Exemples :
            "Swamp_1.jpg"         → "swamp"
            "Lightning Bolt.jpg"  → "lightning bolt"
            "Sol Ring_3.png"      → "sol ring"
        """
        import re
        stem = f.stem
        # Supprime le suffixe _N généré par le Decklist downloader (ex: "_1", "_10")
        stem = re.sub(r"_\d+$", "", stem)
        # Minuscules et remplacement des caractères non-alphabétiques par des espaces
        query = re.sub(r"[^a-z0-9]+", " ", stem.lower())
        return " ".join(query.split())

    def _paste_decklist_text(self, event: tk.Event | None = None) -> str:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"

        try:
            if self.decklist_text.tag_ranges(tk.SEL):
                self.decklist_text.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass

        self.decklist_text.insert(tk.INSERT, text)
        self.decklist_text.focus_set()
        return "break"

    def _select_all_decklist_text(self, event: tk.Event | None = None) -> str:
        self.decklist_text.tag_add(tk.SEL, "1.0", tk.END)
        self.decklist_text.mark_set(tk.INSERT, "1.0")
        self.decklist_text.see(tk.INSERT)
        return "break"


    def _rebuild_decklist_rows(self) -> None:
        selected_by_entry: dict[int, list[CardPrint]] = {}
        locked_positions: set[tuple[int, int]] = set()
        for row_index, row in enumerate(self.decklist_rows):
            entry_index = int(row["entry_index"])
            card_print = self.decklist_selected_prints.get(row_index)
            if card_print is not None:
                selected_by_entry.setdefault(entry_index, []).append(card_print)
            if row_index in self.decklist_locked_rows:
                locked_positions.add((entry_index, int(row["copy_number"])))

        self.decklist_rows.clear()
        self.decklist_selected_prints.clear()
        self.decklist_locked_rows.clear()
        self.decklist_tree.delete(*self.decklist_tree.get_children())

        for entry_index, entry in enumerate(self.decklist_entries):
            saved_prints = selected_by_entry.get(entry_index, [])
            fallback_print = saved_prints[0] if saved_prints else None
            for copy_number in range(1, entry.quantity + 1):
                row_index = len(self.decklist_rows)
                copy_label = f"{copy_number}/{entry.quantity}" if entry.quantity > 1 else "1"
                self.decklist_rows.append(
                    {
                        "entry_index": entry_index,
                        "copy_number": copy_number,
                        "entry": entry,
                    }
                )
                self.decklist_tree.insert("", tk.END, iid=str(row_index), values=(copy_label, "", entry.name, "Recherche..."))
                if (entry_index, copy_number) in locked_positions:
                    self.decklist_locked_rows.add(row_index)

                selected_print = saved_prints[copy_number - 1] if copy_number - 1 < len(saved_prints) else fallback_print
                if selected_print is not None:
                    self._set_decklist_row_print(row_index, selected_print.for_image_size(self.decklist_image_size_var.get().strip()))
                else:
                    prints = self.decklist_prints_by_index.get(entry_index, [])
                    if prints:
                        self._set_decklist_row_print(row_index, self._default_print_for_entry(prints))
                    elif entry_index in self.decklist_prints_by_index:
                        self.decklist_tree.set(str(row_index), "edition", "Introuvable")
                self._refresh_decklist_row_state(row_index)
        self._update_decklist_fix_button_state()
        self._update_decklist_lock_button_state()

    def _set_decklist_row_print(self, row_index: int, card_print: CardPrint) -> None:
        self.decklist_selected_prints[row_index] = card_print
        edition_label = card_print.label
        if not card_print.highres_image:
            edition_label = "⚠️ " + edition_label
        self.decklist_tree.set(str(row_index), "edition", edition_label)
        self.decklist_tree.item(str(row_index), tags=("lowres",) if not card_print.highres_image else ())
        self._refresh_decklist_row_state(row_index)

    def _refresh_decklist_row_state(self, row_index: int) -> None:
        self.decklist_tree.set(str(row_index), "lock", "🔒" if row_index in self.decklist_locked_rows else "")

    def _decklist_row_group_indexes(self, row_index: int) -> list[int]:
        if row_index >= len(self.decklist_rows):
            return []
        entry_index = int(self.decklist_rows[row_index]["entry_index"])
        return [
            candidate_index
            for candidate_index, row in enumerate(self.decklist_rows)
            if int(row["entry_index"]) == entry_index
        ]

    def _set_decklist_row_lock(self, row_index: int, locked: bool, batch: bool = True) -> None:
        target_indexes = self._decklist_row_group_indexes(row_index) if batch else [row_index]
        for target_index in target_indexes:
            if locked:
                self.decklist_locked_rows.add(target_index)
            else:
                self.decklist_locked_rows.discard(target_index)
            self._refresh_decklist_row_state(target_index)
        self._update_decklist_lock_button_state()

    def _toggle_selected_decklist_lock(self) -> None:
        selection = self.decklist_tree.selection()
        if not selection:
            return
        row_index = int(selection[0])
        locked = row_index not in self.decklist_locked_rows
        self._set_decklist_row_lock(row_index, locked, batch=True)
        row = self.decklist_rows[row_index]
        entry = row.get("entry")
        if isinstance(entry, DecklistEntry):
            self._decklist_log(f"{'Verrouillé' if locked else 'Déverrouillé'}: {entry.name}")

    def _update_decklist_lock_button_state(self) -> None:
        selection = self.decklist_tree.selection()
        if not selection:
            if hasattr(self, "decklist_lock_button"):
                self.decklist_lock_button.configure(text="Verrouiller", state="disabled")
            return
        row_index = int(selection[0])
        locked = row_index in self.decklist_locked_rows
        if hasattr(self, "decklist_lock_button"):
            self.decklist_lock_button.configure(text="Déverrouiller" if locked else "Verrouiller", state="normal")

    def _decklist_analysis_ready(self) -> bool:
        return bool(self.decklist_entries) and len(self.decklist_prints_by_index) == len(self.decklist_entries) and not (
            self.decklist_worker and self.decklist_worker.is_alive()
        )

    def _update_decklist_state_buttons(self) -> None:
        save_state = "normal" if self.decklist_selected_prints else "disabled"
        can_load = not (self.decklist_worker and self.decklist_worker.is_alive())
        load_state = "normal" if can_load else "disabled"
        if hasattr(self, "decklist_save_state_header_button"):
            self.decklist_save_state_header_button.configure(state=save_state)
        if hasattr(self, "decklist_load_state_header_button"):
            self.decklist_load_state_header_button.configure(state=load_state)

    def _save_decklist_state(self) -> None:
        if not self.decklist_rows or not self.decklist_selected_prints:
            messagebox.showerror("Sauvegarde impossible", "Aucune sélection d'édition à sauvegarder.")
            return

        payload = {
            "raw_text": self.decklist_text.get("1.0", tk.END).strip(),
            "entries": [{"name": entry.name, "quantity": entry.quantity} for entry in self.decklist_entries],
            "language": self.decklist_analyzed_language,
            "image_size": self.decklist_analyzed_image_size,
            "rows": [],
        }
        for row_index, row in enumerate(self.decklist_rows):
            card_print = self.decklist_selected_prints.get(row_index)
            if card_print is None:
                continue
            entry = row.get("entry")
            if not isinstance(entry, DecklistEntry):
                continue
            payload["rows"].append(
                {
                    "entry_name": entry.name,
                    "copy_number": int(row["copy_number"]),
                    "print_id": card_print.id,
                    "custom_file_path": card_print.custom_file_path,
                    "locked": row_index in self.decklist_locked_rows,
                }
            )

        filename = filedialog.asksaveasfilename(
            parent=self,
            initialfile="decklist_state.txt",
            defaultextension=".txt",
            filetypes=(("Fichier texte", "*.txt"), ("Fichier JSON", "*.json"), ("Tous les fichiers", "*.*")),
        )
        if not filename:
            return

        path = Path(filename)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._decklist_log(f"Etat Decklist sauvegardé: {path}")
        self._update_decklist_state_buttons()

    def _load_decklist_state(self) -> None:
        if self.decklist_worker and self.decklist_worker.is_alive():
            messagebox.showerror("Analyse en cours", "Attends la fin de l'analyse avant de charger une sauvegarde.")
            return

        filename = filedialog.askopenfilename(
            parent=self,
            filetypes=(("Fichier texte", "*.txt"), ("Fichier JSON", "*.json"), ("Tous les fichiers", "*.*")),
        )
        if not filename:
            return
        path = Path(filename)
        if not path.exists():
            messagebox.showerror("Fichier introuvable", "Le fichier sélectionné est introuvable.")
            return

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            messagebox.showerror("Fichier invalide", f"Impossible de lire l'état sauvegardé: {error}")
            return

        saved_entries = payload.get("entries", [])
        if not saved_entries:
            messagebox.showerror("Fichier invalide", "Le fichier ne contient pas de decklist valide.")
            return

        raw_text = payload.get("raw_text") or "\n".join(
            f"{entry.get('quantity', 1)} {entry.get('name', '')}" for entry in saved_entries
        )
        language = str(payload.get("language", "all")).strip() or "all"
        image_size = str(payload.get("image_size", "large")).strip() or "large"

        self.decklist_text.delete("1.0", tk.END)
        self.decklist_text.insert("1.0", raw_text)
        self.decklist_language_var.set(language)
        self.decklist_image_size_var.set(image_size)

        self._pending_state_restore = payload
        self._decklist_log("Sauvegarde chargée — lancement de l'analyse pour restaurer les éditions…")
        self._start_decklist_analysis()

    def _apply_pending_state_restore(self) -> None:
        payload = self._pending_state_restore
        self._pending_state_restore = None
        if payload is None:
            return

        saved_rows = {
            (str(row.get("entry_name", "")), int(row.get("copy_number", 0))): row
            for row in payload.get("rows", [])
        }
        restored = 0
        missing = 0

        for row_index, row in enumerate(self.decklist_rows):
            entry = row.get("entry")
            if not isinstance(entry, DecklistEntry):
                continue
            saved_row = saved_rows.get((entry.name, int(row["copy_number"])))
            if not saved_row:
                continue

            saved_print: CardPrint | None = None
            custom_file_path = str(saved_row.get("custom_file_path", "")).strip()
            if custom_file_path:
                custom_path = Path(custom_file_path)
                if custom_path.exists():
                    saved_print = self._build_custom_decklist_print(entry, custom_path)
            else:
                print_id = str(saved_row.get("print_id", ""))
                for card_print in self.decklist_prints_by_index.get(int(row["entry_index"]), []):
                    if card_print.id == print_id:
                        saved_print = card_print
                        break

            if saved_print is None:
                missing += 1
                continue

            self._apply_decklist_print(row_index, saved_print, batch=False)
            self._set_decklist_row_lock(row_index, bool(saved_row.get("locked")), batch=False)
            restored += 1

        self._update_decklist_fix_button_state()
        self._update_decklist_lock_button_state()
        self._update_decklist_state_buttons()
        self._decklist_log(f"Etat Decklist restauré: {restored} éditions, {missing} introuvables.")

    def _decklist_visible_prints(self, prints: list[CardPrint], show_lowres: bool | None = None) -> list[CardPrint]:
        language = (self.decklist_analyzed_language or self.decklist_language_var.get().strip()).lower()
        if language == "all":
            visible_prints = prints
        else:
            same_language = [card_print for card_print in prints if card_print.language.lower() == language]
            if language == "en":
                visible_prints = same_language
            else:
                english = [card_print for card_print in prints if card_print.language.lower() == "en"]
                if any(card_print.highres_image for card_print in same_language):
                    visible_prints = same_language
                elif same_language:
                    visible_prints = same_language + english
                else:
                    visible_prints = english

        if show_lowres is None:
            show_lowres = self.decklist_show_lowres_var.get()
        if show_lowres:
            return visible_prints
        return [card_print for card_print in visible_prints if card_print.highres_image]

    def _default_print_for_entry(self, prints: list[CardPrint]) -> CardPrint:
        language = (self.decklist_analyzed_language or self.decklist_language_var.get().strip()).lower()
        if language == "all":
            return prints[0]

        same_language = [card_print for card_print in prints if card_print.language.lower() == language]
        if same_language:
            return same_language[0]

        english = [card_print for card_print in prints if card_print.language.lower() == "en"]
        if english:
            return english[0]

        visible_prints = self._decklist_visible_prints(prints)
        return visible_prints[0] if visible_prints else prints[0]

    def _preferred_highres_replacement(self, prints: list[CardPrint]) -> CardPrint | None:
        language = (self.decklist_analyzed_language or self.decklist_language_var.get().strip()).lower()
        if language == "all":
            for card_print in prints:
                if card_print.highres_image:
                    return card_print
            return None

        for card_print in prints:
            if card_print.language.lower() == language and card_print.highres_image:
                return card_print
        for card_print in prints:
            if card_print.language.lower() == "en" and card_print.highres_image:
                return card_print
        return None

    def _update_decklist_fix_button_state(self) -> None:
        has_fix = False
        for row_index, row in enumerate(self.decklist_rows):
            if row_index in self.decklist_locked_rows:
                continue
            current = self.decklist_selected_prints.get(row_index)
            if current is None or current.highres_image:
                continue
            entry_index = int(row["entry_index"])
            replacement = self._preferred_highres_replacement(self.decklist_prints_by_index.get(entry_index, []))
            if replacement is not None and replacement.id != current.id:
                has_fix = True
                break
        self.decklist_fix_lowres_button.configure(state="normal" if has_fix else "disabled")

    def _choose_upscale_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.upscale_folder_var.get() or ".")
        if folder:
            self.upscale_folder_var.set(folder)
            if not self.upscale_output_var.get().strip():
                self.upscale_output_var.set(str(Path(folder) / "DPI_Upscale"))

    def _choose_upscale_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.upscale_output_var.get() or self.upscale_folder_var.get() or ".")
        if folder:
            self.upscale_output_var.set(folder)

    def _choose_margin_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.margin_folder_var.get() or ".")
        if folder:
            self.margin_folder_var.set(folder)
            if not self.margin_output_var.get().strip():
                self.margin_output_var.set(str(Path(folder) / "Margin_Creator"))

    def _choose_margin_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.margin_output_var.get() or self.margin_folder_var.get() or ".")
        if folder:
            self.margin_output_var.set(folder)

    def _choose_crop_image(self) -> None:
        filetypes = (
            ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
            ("Tous les fichiers", "*.*"),
        )
        filename = filedialog.askopenfilename(initialdir=".", filetypes=filetypes)
        if not filename:
            return

        self.crop_image_var.set(filename)
        source = Path(filename)
        if not self.crop_output_var.get().strip():
            self.crop_output_var.set(str(source.with_name(f"{source.stem}_ratio_0714{source.suffix}")))
        self._load_crop_image(source)

    def _choose_crop_output(self) -> None:
        initial = self.crop_output_var.get().strip() or self.crop_image_var.get().strip() or "."
        source_suffix = Path(self.crop_image_var.get()).suffix or ".png"
        filename = filedialog.asksaveasfilename(
            initialfile=Path(initial).name or "image_ratio_0714.png",
            initialdir=str(Path(initial).parent) if Path(initial).parent else ".",
            defaultextension=source_suffix,
            filetypes=(
                ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
                ("Tous les fichiers", "*.*"),
            ),
        )
        if filename:
            self.crop_output_var.set(filename)

    def _load_crop_image(self, path: Path) -> None:
        try:
            from PIL import Image
        except ImportError:
            messagebox.showerror(
                "Pillow manquant",
                "Pillow n'est pas installé. Lance la compilation ou installe Pillow avec: py -3 -m pip install Pillow",
            )
            return

        try:
            self.crop_source_image = Image.open(path).copy()
        except Exception as error:
            messagebox.showerror("Image invalide", str(error))
            return

        self.crop_rect = tuple(float(value) for value in centered_crop_rect(self.crop_source_image.width, self.crop_source_image.height))
        self._draw_crop_canvas()

    def _reset_crop_rect(self) -> None:
        if self.crop_source_image is None:
            return
        if self.crop_rect is None:
            self.crop_rect = tuple(
                float(value) for value in centered_crop_rect(self.crop_source_image.width, self.crop_source_image.height)
            )
        else:
            left, top, right, bottom = self.crop_rect
            width = right - left
            max_width = min(float(self.crop_source_image.width), float(self.crop_source_image.height) * TARGET_ASPECT_RATIO)
            width = max(1.0, min(width, max_width))
            height = width / TARGET_ASPECT_RATIO
            center_x = self.crop_source_image.width / 2
            center_y = self.crop_source_image.height / 2
            self.crop_rect = (
                center_x - width / 2,
                center_y - height / 2,
                center_x + width / 2,
                center_y + height / 2,
            )
        self._draw_crop_canvas()

    def _save_crop(self) -> None:
        source = self.crop_image_var.get().strip()
        output = self.crop_output_var.get().strip()
        if not source:
            messagebox.showerror("Image invalide", "Veuillez sélectionner une image source.")
            return
        if not output:
            messagebox.showerror("Fichier invalide", "Veuillez sélectionner un fichier de sortie.")
            return
        if self.crop_rect is None:
            self._load_crop_image(Path(source))
        if self.crop_rect is None:
            messagebox.showerror("Selection invalide", "Veuillez sélectionner une zone de recadrage.")
            return

        try:
            target = crop_image_to_ratio(source, output, tuple(int(round(value)) for value in self.crop_rect))
        except Exception as error:
            messagebox.showerror("Erreur", str(error))
            return

        self.crop_status_var.set(f"Enregistré: {target.name}")

    def _draw_crop_canvas(self) -> None:
        self.crop_canvas.delete("all")
        canvas_width = max(1, self.crop_canvas.winfo_width() or 500)
        canvas_height = max(1, self.crop_canvas.winfo_height() or 260)
        if self.crop_source_image is None:
            self.crop_canvas.create_text(
                canvas_width / 2,
                canvas_height / 2,
                text="Aucune image",
                fill="#9a9a9a",
                font=("Segoe UI", 13, "bold"),
            )
            return

        try:
            from PIL import Image, ImageTk
        except ImportError:
            return

        top_preview_margin = 12
        bottom_preview_margin = 12
        available_height = max(1, canvas_height - top_preview_margin - bottom_preview_margin)
        source_width, source_height = self.crop_source_image.size
        self.crop_scale = min(canvas_width / source_width, available_height / source_height)
        preview_size = (max(1, round(source_width * self.crop_scale)), max(1, round(source_height * self.crop_scale)))
        preview = self.crop_source_image.resize(preview_size, Image.Resampling.LANCZOS)
        self.crop_preview_image = ImageTk.PhotoImage(preview)
        offset_x = (canvas_width - preview.width) // 2
        offset_y = top_preview_margin + max(0, (available_height - preview.height) // 2)
        self.crop_offset = (offset_x, offset_y)

        self.crop_canvas.create_image(offset_x, offset_y, image=self.crop_preview_image, anchor="nw")
        if self.crop_rect is not None:
            self._draw_crop_overlay()
            left, top, right, bottom = tuple(round(value) for value in self.crop_rect)
            self.crop_status_var.set(f"Zone: {right - left}x{bottom - top}px")

    def _draw_crop_overlay(self) -> None:
        if self.crop_rect is None:
            return

        left, top, right, bottom = self._source_rect_to_canvas(self.crop_rect)
        image_left, image_top = self.crop_offset
        image_right = image_left + round(self.crop_source_image.width * self.crop_scale)
        image_bottom = image_top + round(self.crop_source_image.height * self.crop_scale)

        self.crop_canvas.create_rectangle(image_left, image_top, image_right, top, fill="#000000", stipple="gray50", outline="")
        self.crop_canvas.create_rectangle(image_left, bottom, image_right, image_bottom, fill="#000000", stipple="gray50", outline="")
        self.crop_canvas.create_rectangle(image_left, top, left, bottom, fill="#000000", stipple="gray50", outline="")
        self.crop_canvas.create_rectangle(right, top, image_right, bottom, fill="#000000", stipple="gray50", outline="")
        self.crop_canvas.create_rectangle(left, top, right, bottom, outline="#ffffff", width=2)

        for x, y in ((left, top), (right, top), (left, bottom), (right, bottom)):
            self.crop_canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5, fill="#ffffff", outline="#181818")

    def _source_rect_to_canvas(self, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        offset_x, offset_y = self.crop_offset
        return tuple(
            (
                offset_x + rect[0] * self.crop_scale,
                offset_y + rect[1] * self.crop_scale,
                offset_x + rect[2] * self.crop_scale,
                offset_y + rect[3] * self.crop_scale,
            )
        )

    def _canvas_to_source(self, x: float, y: float) -> tuple[float, float]:
        offset_x, offset_y = self.crop_offset
        if self.crop_source_image is None:
            return (0.0, 0.0)
        source_x = (x - offset_x) / self.crop_scale
        source_y = (y - offset_y) / self.crop_scale
        return (
            max(0.0, min(float(self.crop_source_image.width), source_x)),
            max(0.0, min(float(self.crop_source_image.height), source_y)),
        )

    def _crop_press(self, event: tk.Event) -> None:
        if self.crop_source_image is None or self.crop_rect is None:
            return

        self.crop_drag_mode = self._crop_hit_test(event.x, event.y)
        self.crop_drag_start = self._canvas_to_source(event.x, event.y)
        self.crop_drag_rect = self.crop_rect

    def _crop_drag(self, event: tk.Event) -> None:
        if self.crop_source_image is None or self.crop_rect is None or self.crop_drag_rect is None or not self.crop_drag_mode:
            return

        source_x, source_y = self._canvas_to_source(event.x, event.y)
        if self.crop_drag_mode == "move":
            start_x, start_y = self.crop_drag_start
            dx = source_x - start_x
            dy = source_y - start_y
            self.crop_rect = self._move_crop_rect(self.crop_drag_rect, dx, dy)
        elif self.crop_drag_mode in {"nw", "ne", "sw", "se"}:
            self.crop_rect = self._resize_crop_rect(self.crop_drag_rect, self.crop_drag_mode, source_x, source_y)
        self._draw_crop_canvas()

    def _crop_release(self, event: tk.Event) -> None:
        self.crop_drag_mode = None
        self.crop_drag_rect = None

    def _crop_hit_test(self, x: float, y: float) -> str | None:
        if self.crop_rect is None:
            return None

        left, top, right, bottom = self._source_rect_to_canvas(self.crop_rect)
        handles = {
            "nw": (left, top),
            "ne": (right, top),
            "sw": (left, bottom),
            "se": (right, bottom),
        }
        for name, (handle_x, handle_y) in handles.items():
            if abs(x - handle_x) <= 10 and abs(y - handle_y) <= 10:
                return name
        if left <= x <= right and top <= y <= bottom:
            return "move"
        return None

    def _move_crop_rect(self, rect: tuple[float, float, float, float], dx: float, dy: float) -> tuple[float, float, float, float]:
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        left = max(0.0, min(float(self.crop_source_image.width) - width, left + dx))
        top = max(0.0, min(float(self.crop_source_image.height) - height, top + dy))
        return (left, top, left + width, top + height)

    def _resize_crop_rect(
        self,
        rect: tuple[float, float, float, float],
        handle: str,
        pointer_x: float,
        pointer_y: float,
    ) -> tuple[float, float, float, float]:
        left, top, right, bottom = rect
        anchor_x = right if "w" in handle else left
        anchor_y = bottom if "n" in handle else top
        sign_x = -1 if "w" in handle else 1
        sign_y = -1 if "n" in handle else 1
        max_width = anchor_x if sign_x < 0 else self.crop_source_image.width - anchor_x
        max_height = anchor_y if sign_y < 0 else self.crop_source_image.height - anchor_y
        width_from_x = abs(pointer_x - anchor_x)
        width_from_y = abs(pointer_y - anchor_y) * TARGET_ASPECT_RATIO
        width = max(width_from_x, width_from_y, 24.0)
        width = min(width, max_width, max_height * TARGET_ASPECT_RATIO)
        height = width / TARGET_ASPECT_RATIO

        new_left = anchor_x if sign_x > 0 else anchor_x - width
        new_right = anchor_x + width if sign_x > 0 else anchor_x
        new_top = anchor_y if sign_y > 0 else anchor_y - height
        new_bottom = anchor_y + height if sign_y > 0 else anchor_y
        return (new_left, new_top, new_right, new_bottom)

    # ==========================================================================
    #  OPÉRATIONS DE LA DECKLIST (analyse, téléchargement)
    # ==========================================================================

    def _start_decklist_analysis(self) -> None:
        """
        Lance l'analyse de la decklist dans un thread de travail séparé.

        Cette méthode est appelée quand l'utilisateur clique "Analyser" ou
        quand un chargement de sauvegarde déclenche l'analyse automatique.

        Étapes :
        1. Vérifie qu'aucune analyse n'est déjà en cours
        2. Parse le texte de la decklist
        3. Réinitialise l'état de la decklist
        4. Démarre le thread d'analyse (_run_decklist_analysis)
        5. Désactive les boutons pendant l'analyse

        Le thread communique avec l'UI via la file de messages (self.messages).
        """
        if self.decklist_worker and self.decklist_worker.is_alive():
            return   # Une analyse est déjà en cours → on ignore le clic

        raw_decklist = self.decklist_text.get("1.0", tk.END)
        entries, skipped = parse_decklist(raw_decklist)
        if not entries:
            messagebox.showerror("Decklist invalide", "Colle au moins une ligne au format: 1 Nom de carte")
            return

        self.decklist_entries = entries
        self.decklist_prints_by_index.clear()
        self.decklist_selected_prints.clear()
        self.decklist_locked_rows.clear()
        self.decklist_analyzed_language = self.decklist_language_var.get().strip().lower()
        self.decklist_analyzed_image_size = self.decklist_image_size_var.get().strip()
        self._rebuild_decklist_rows()

        self._clear_decklist_log()
        if skipped:
            self._decklist_log(f"Lignes ignorees: {len(skipped)}")
            for line in skipped[:5]:
                self._decklist_log(f"  {line}")
            if len(skipped) > 5:
                self._decklist_log("  ...")

        self.decklist_analyze_button.configure(state="disabled")
        self.decklist_download_button.configure(state="disabled")
        self._update_decklist_state_buttons()
        self.decklist_cancel_button.configure(state="normal")
        self.decklist_cancel_event.clear()
        self.decklist_progress.configure(maximum=100, value=0)
        self.decklist_progress_label.configure(text="Démarrage...")

        self.decklist_worker = threading.Thread(
            target=self._run_decklist_analysis,
            args=(entries, self.decklist_analyzed_language, self.decklist_analyzed_image_size),
            daemon=True,
        )
        self.decklist_worker.start()

    def _run_decklist_analysis(self, entries: list[DecklistEntry], language: str, image_size: str) -> None:
        """
        Thread de travail pour l'analyse de la decklist.

        Exécuté dans un thread séparé pour ne pas bloquer l'interface graphique.
        Communique avec l'UI via la file de messages self.messages.

        DEUX MODES DE RECHERCHE :
        1. Local (prioritaire) : Si un fichier "all-cards-*.json" est présent
           dans le dossier du programme, on utilise LocalBulkCatalog pour
           chercher sans connexion internet.
        2. Distant (repli) : Sinon, on utilise ScryfallClient pour rechercher
           carte par carte via l'API Scryfall (nécessite une connexion).

        Messages envoyés à l'UI :
            "decklist_log"   : Message texte pour le log
            "decklist_prints" : (index, liste_impressions) pour une carte
            "decklist_progress" : "current/total" pour la barre de progression
            "decklist_analysis_done" : Fin d'analyse réussie
            "decklist_cancelled" : Annulation par l'utilisateur
            "decklist_error" : Erreur inattendue

        Arguments :
            entries    (list[DecklistEntry]) : Cartes à analyser.
            language   (str)                 : Langue cible (ex: "fr").
            image_size (str)                 : Taille d'image (ex: "large").
        """
        try:
            local_bulk_file = self._find_local_bulk_file()
            client: ScryfallClient | None = None
            if local_bulk_file is None:
                self.messages.put(("decklist_log", "Aucun fichier bulk local 'all-cards' trouvé."))
                self.messages.put(("decklist_log", "Recherche distante via Scryfall, sans téléchargement de bulk."))
                client = ScryfallClient()  # Mode distant : utilise l'API Scryfall

            prints_by_index: dict[int, list[CardPrint]] | None = None
            if local_bulk_file is not None:
                self.messages.put(("decklist_log", f"Bulk local trouvé: {local_bulk_file.name}"))
                catalog = LocalBulkCatalog(
                    bulk_file=local_bulk_file,
                    on_status=lambda message: self.messages.put(("decklist_log", message)),
                    on_progress=lambda current, total: self.messages.put(("decklist_local_bulk_progress", f"{current}/{total}")),
                    should_cancel=self.decklist_cancel_event.is_set,
                )
                prints_by_index = catalog.search_deck_prints(entries, language, image_size)

            missing: list[str] = []
            for index, entry in enumerate(entries):
                if self.decklist_cancel_event.is_set():
                    self.messages.put(("decklist_cancelled", "Analyse annulée."))
                    return

                self.messages.put(("decklist_log", f"Recherche: {entry.name}"))
                if prints_by_index is not None:
                    prints = prints_by_index.get(index, [])
                else:
                    prints = client.search_card_prints(
                        entry.name,
                        language,
                        image_size,
                        on_status=lambda message: self.messages.put(("decklist_log", message)),
                    )
                if not prints:
                    missing.append(entry.name)
                self.messages.put(("decklist_prints", (index, prints)))
                self.messages.put(("decklist_progress", f"{index + 1}/{len(entries)}"))

            if missing:
                self.messages.put(("decklist_log", f"Introuvables en {language.upper()}: {', '.join(missing)}"))
            self.messages.put(("decklist_analysis_done", "Analyse terminée."))
        except Exception as error:
            self.messages.put(("decklist_error", self._format_error(error)))

    def _find_local_bulk_file(self) -> Path | None:
        roots = [
            Path.cwd(),
            Path(__file__).resolve().parent.parent,
        ]
        if getattr(sys, "frozen", False):
            roots.append(Path(sys.executable).resolve().parent)
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            roots.append(Path(bundle_root))

        seen: set[Path] = set()
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            local_bulk_file = find_local_bulk_file(resolved)
            if local_bulk_file is not None:
                return local_bulk_file
        return None

    def _cancel_decklist(self) -> None:
        """
        Demande l'annulation de l'analyse ou du téléchargement en cours.

        Positionne le drapeau d'annulation (threading.Event). Le thread
        de travail vérifie ce drapeau régulièrement et s'arrête proprement.
        """
        if self.decklist_worker and self.decklist_worker.is_alive():
            self.decklist_cancel_event.set()   # Signal d'annulation au thread
            self.decklist_cancel_button.configure(state="disabled")
            self.decklist_progress_label.configure(text="Annulation...")
            self._decklist_log("Annulation demandée...")

    def _receive_decklist_prints(self, index: int, prints: list[CardPrint]) -> None:
        image_size = self.decklist_image_size_var.get().strip()
        prints = [card_print.for_image_size(image_size) for card_print in prints]
        self.decklist_prints_by_index[index] = prints

        row_indexes = [
            row_index
            for row_index, row in enumerate(self.decklist_rows)
            if row.get("entry_index") == index
        ]
        if prints:
            selected = self._default_print_for_entry(prints)
            for row_index in row_indexes:
                self._set_decklist_row_print(row_index, selected)
        else:
            for row_index in row_indexes:
                self.decklist_selected_prints.pop(row_index, None)
                self.decklist_tree.set(str(row_index), "edition", "Introuvable")
                self.decklist_tree.item(str(row_index), tags=())

        selection = self.decklist_tree.selection()
        if selection:
            self._on_decklist_row_selected()
        self._update_decklist_fix_button_state()
        self._update_decklist_state_buttons()

    def _on_decklist_image_size_changed(self, event: tk.Event | None = None) -> None:
        if not self.decklist_prints_by_index:
            return
        self._refresh_decklist_image_size()
        self.decklist_analyzed_image_size = self.decklist_image_size_var.get().strip()
        self._decklist_log(f"Taille image mise à jour localement: {self.decklist_analyzed_image_size}")

    def _refresh_decklist_image_size(self) -> None:
        image_size = self.decklist_image_size_var.get().strip()
        self.decklist_prints_by_index = {
            index: [card_print.for_image_size(image_size) for card_print in prints]
            for index, prints in self.decklist_prints_by_index.items()
        }
        self.decklist_selected_prints = {
            row_index: card_print.for_image_size(image_size)
            for row_index, card_print in self.decklist_selected_prints.items()
        }
        for row_index, card_print in self.decklist_selected_prints.items():
            self._set_decklist_row_print(row_index, card_print)

    def _on_decklist_row_selected(self, event: tk.Event | None = None) -> None:
        self._update_decklist_fix_button_state()
        self._update_decklist_lock_button_state()

    def _fix_all_lowres_decklist(self) -> None:
        updated = 0
        for row_index, row in enumerate(self.decklist_rows):
            if row_index in self.decklist_locked_rows:
                continue
            current = self.decklist_selected_prints.get(row_index)
            if current is None or current.highres_image:
                continue
            entry_index = int(row["entry_index"])
            replacement = self._preferred_highres_replacement(self.decklist_prints_by_index.get(entry_index, []))
            if replacement is None or replacement.id == current.id:
                continue
            self._apply_decklist_print(row_index, replacement, batch=False)
            updated += 1

        self._update_decklist_fix_button_state()
        if updated:
            self._decklist_log(f"Corrections lowres appliquées: {updated}")
        else:
            self._decklist_log("Aucune correction lowres disponible.")

    def _edit_decklist_quantity(self, row_index: int) -> None:
        if row_index >= len(self.decklist_rows):
            return
        if row_index in self.decklist_locked_rows:
            row = self.decklist_rows[row_index]
            entry = row.get("entry")
            if isinstance(entry, DecklistEntry):
                self._decklist_log(f"Carte verrouillée: quantité inchangée pour {entry.name}.")
            return

        row = self.decklist_rows[row_index]
        entry_index = int(row["entry_index"])
        entry = row["entry"]
        if not isinstance(entry, DecklistEntry):
            return

        new_quantity = self._ask_decklist_quantity(entry.name, entry.quantity)
        if new_quantity is None or new_quantity == entry.quantity:
            return

        self.decklist_entries[entry_index] = DecklistEntry(quantity=new_quantity, name=entry.name)
        self._rebuild_decklist_rows()

        for candidate_index, candidate_row in enumerate(self.decklist_rows):
            if int(candidate_row["entry_index"]) == entry_index:
                self.decklist_tree.selection_set(str(candidate_index))
                self.decklist_tree.focus(str(candidate_index))
                break

        if len(self.decklist_selected_prints) == len(self.decklist_rows) and self.decklist_rows:
            self.decklist_download_button.configure(state="normal")
        else:
            self.decklist_download_button.configure(state="disabled")
        self._decklist_log(f"Copies mises à jour: {entry.name} x{new_quantity}")

    def _ask_decklist_quantity(self, card_name: str, initial_quantity: int) -> int | None:
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.configure(bg="#202020")
        dialog.overrideredirect(True)
        dialog.transient(self)
        dialog.resizable(False, False)

        drag_state = {"x": 0, "y": 0}

        def start_dialog_move(event: tk.Event) -> None:
            drag_state["x"] = event.x_root - dialog.winfo_x()
            drag_state["y"] = event.y_root - dialog.winfo_y()

        def move_dialog(event: tk.Event) -> None:
            x = event.x_root - drag_state["x"]
            y = event.y_root - drag_state["y"]
            dialog.geometry(f"+{x}+{y}")

        container = tk.Frame(dialog, bg="#3b3b3b", bd=0, highlightthickness=1, highlightbackground="#4a4a4a")
        container.pack(fill=tk.BOTH, expand=True)

        titlebar = tk.Frame(container, bg="#181818", height=38)
        titlebar.pack(fill=tk.X)
        titlebar.bind("<ButtonPress-1>", start_dialog_move)
        titlebar.bind("<B1-Motion>", move_dialog)

        title_logo = tk.Label(titlebar, image=self.titlebar_logo, bg="#181818")
        title_logo.pack(side=tk.LEFT, padx=(10, 8))
        title_logo.bind("<ButtonPress-1>", start_dialog_move)
        title_logo.bind("<B1-Motion>", move_dialog)

        title = tk.Label(
            titlebar,
            text="Nombre de copies",
            bg="#181818",
            fg="#f1f1f1",
            font=("Segoe UI", 10, "bold"),
        )
        title.pack(side=tk.LEFT)
        title.bind("<ButtonPress-1>", start_dialog_move)
        title.bind("<B1-Motion>", move_dialog)

        self._make_window_button(titlebar, "X", dialog.destroy).pack(side=tk.RIGHT)

        frame = ttk.Frame(container, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Nombre de copies", style="HeaderTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=card_name).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 12))
        ttk.Label(frame, text="Quantité").grid(row=2, column=0, sticky="w")

        quantity_var = tk.StringVar(value=str(initial_quantity))
        spinbox = ttk.Spinbox(frame, from_=1, to=99, textvariable=quantity_var, width=8)
        spinbox.grid(row=2, column=1, sticky="e", padx=(12, 0))

        result = {"value": None}

        def validate_and_close() -> None:
            try:
                value = int(quantity_var.get().strip())
            except ValueError:
                messagebox.showerror("Quantité invalide", "Veuillez saisir un nombre entier entre 1 et 99.", parent=dialog)
                return
            if not 1 <= value <= 99:
                messagebox.showerror("Quantité invalide", "Veuillez saisir un nombre entier entre 1 et 99.", parent=dialog)
                return
            result["value"] = value
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Valider", command=validate_and_close).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=tk.RIGHT)

        dialog.bind("<Return>", lambda _event: validate_and_close())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self._present_custom_dialog(dialog, focus_widget=spinbox)
        spinbox.selection_range(0, tk.END)
        dialog.wait_window()
        return result["value"]

    def _build_custom_decklist_print(self, entry: DecklistEntry, source_path: Path) -> CardPrint:
        return CardPrint(
            id=f"custom:{source_path.resolve()}",
            set_code="custom",
            set_name="Image personnalisée",
            collector_number="custom",
            language="custom",
            name=entry.name,
            image_url=str(source_path),
            released_at="",
            preview_url=str(source_path),
            image_urls=None,
            highres_image=True,
            custom_file_path=str(source_path),
            is_custom=True,
        )

    def _choose_decklist_edition(self, event: tk.Event | None = None) -> str | None:
        if event is not None and getattr(event, "widget", None) is self.decklist_tree:
            row_id = self.decklist_tree.identify_row(event.y)
            if row_id:
                self.decklist_tree.selection_set(row_id)
                self.decklist_tree.focus(row_id)
            else:
                return "break"
            column_id = self.decklist_tree.identify_column(event.x)
            if column_id == "#1":
                self._edit_decklist_quantity(int(row_id))
                return "break"
            if column_id == "#2":
                self._set_decklist_row_lock(int(row_id), int(row_id) not in self.decklist_locked_rows, batch=True)
                return "break"

        selection = self.decklist_tree.selection()
        if not selection:
            return None

        row_index = int(selection[0])
        if row_index >= len(self.decklist_rows):
            return None

        row = self.decklist_rows[row_index]
        if row_index in self.decklist_locked_rows:
            entry = row.get("entry")
            if isinstance(entry, DecklistEntry):
                self._decklist_log(f"Carte verrouillée: édition inchangée pour {entry.name}.")
            return "break"
        entry_index = int(row["entry_index"])
        copy_number = int(row["copy_number"])
        entry = row["entry"]
        if not isinstance(entry, DecklistEntry):
            return None

        all_prints = list(self.decklist_prints_by_index.get(entry_index, []))

        title = f"Editions - {entry.name}"
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.configure(bg="#202020")
        dialog.overrideredirect(True)
        dialog.transient(self)
        dialog.geometry("1040x680")
        dialog.minsize(880, 600)

        drag_state = {"x": 0, "y": 0}

        def start_dialog_move(event: tk.Event) -> None:
            drag_state["x"] = event.x_root - dialog.winfo_x()
            drag_state["y"] = event.y_root - dialog.winfo_y()

        def move_dialog(event: tk.Event) -> None:
            x = event.x_root - drag_state["x"]
            y = event.y_root - drag_state["y"]
            dialog.geometry(f"+{x}+{y}")

        container = tk.Frame(dialog, bg="#3b3b3b", bd=0, highlightthickness=1, highlightbackground="#4a4a4a")
        container.pack(fill=tk.BOTH, expand=True)

        titlebar = tk.Frame(container, bg="#181818", height=38)
        titlebar.pack(fill=tk.X)
        titlebar.bind("<ButtonPress-1>", start_dialog_move)
        titlebar.bind("<B1-Motion>", move_dialog)

        title_logo = tk.Label(titlebar, image=self.titlebar_logo, bg="#181818")
        title_logo.pack(side=tk.LEFT, padx=(10, 8))
        title_logo.bind("<ButtonPress-1>", start_dialog_move)
        title_logo.bind("<B1-Motion>", move_dialog)

        title_label = tk.Label(
            titlebar,
            text=title,
            bg="#181818",
            fg="#f1f1f1",
            font=("Segoe UI", 10, "bold"),
        )
        title_label.pack(side=tk.LEFT)
        title_label.bind("<ButtonPress-1>", start_dialog_move)
        title_label.bind("<B1-Motion>", move_dialog)

        self._make_window_button(titlebar, "X", dialog.destroy).pack(side=tk.RIGHT)

        content = ttk.Frame(container)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        content.columnconfigure(0, weight=1, minsize=440)
        content.columnconfigure(2, weight=0, minsize=370)
        content.rowconfigure(1, weight=1)

        search_var = tk.StringVar(value="")
        ttk.Label(content, text="Recherche").grid(row=0, column=0, sticky="w", pady=(0, 6))
        search_entry = ttk.Entry(content, textvariable=search_var)
        search_entry.grid(row=0, column=0, sticky="ew", padx=(90, 0), pady=(0, 6))

        listbox = tk.Listbox(
            content,
            bg="#181818",
            fg="#e8e8e8",
            selectbackground="#000000",
            selectforeground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#666666",
            activestyle="none",
        )
        listbox.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient=tk.VERTICAL, command=listbox.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 12))
        listbox.configure(yscrollcommand=scrollbar.set)

        preview_panel = tk.Frame(content, bg="#202020", width=370, height=525)
        preview_panel.grid(row=0, column=2, rowspan=2, sticky="nsew")
        preview_panel.grid_propagate(False)
        show_lowres_var = tk.BooleanVar(value=self.decklist_show_lowres_var.get())
        preview_label = tk.Label(
            preview_panel,
            text="Preview",
            bg="#181818",
            fg="#bdbdbd",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3b3b3b",
        )
        preview_label.pack(fill=tk.BOTH, expand=True)

        selected_print = self.decklist_selected_prints.get(row_index)
        prints: list[CardPrint] = []
        custom_prints: list[CardPrint] = []
        if selected_print and selected_print.is_custom:
            custom_prints.append(selected_print)

        preview_token = {"value": 0}

        def update_preview(event: tk.Event | None = None) -> None:
            current = listbox.curselection()
            if not current:
                return
            preview_token["value"] += 1
            token = preview_token["value"]
            preview_label.configure(image="", text="Chargement...")
            self._load_decklist_preview(prints[current[0]], preview_label, dialog, token, preview_token)

        def matches_search(card_print: CardPrint) -> bool:
            query = search_var.get().strip().lower()
            if not query:
                return True
            haystack = " ".join(
                part
                for part in (
                    card_print.label,
                    card_print.name,
                    card_print.set_code,
                    card_print.set_name,
                    card_print.collector_number,
                    card_print.released_at,
                    card_print.language,
                )
                if part
            ).lower()
            return query in haystack or all(token in haystack for token in query.split())

        def refresh_prints(preferred_id: str | None = None) -> None:
            current = listbox.curselection()
            if preferred_id is None and current:
                preferred_id = prints[current[0]].id
            if preferred_id is None and selected_print is not None:
                preferred_id = selected_print.id

            visible_prints = list(self._decklist_visible_prints(all_prints, show_lowres=show_lowres_var.get()))
            refreshed = [card_print for card_print in visible_prints if matches_search(card_print)]
            for custom_print in custom_prints:
                if matches_search(custom_print) and all(existing_print.id != custom_print.id for existing_print in refreshed):
                    refreshed.append(custom_print)

            prints.clear()
            prints.extend(refreshed)
            listbox.delete(0, tk.END)

            selected_position = 0
            for position, card_print in enumerate(prints):
                listbox.insert(tk.END, card_print.label)
                if preferred_id and card_print.id == preferred_id:
                    selected_position = position

            if prints:
                listbox.selection_set(selected_position)
                listbox.activate(selected_position)
                listbox.see(selected_position)
                update_preview()
            else:
                preview_token["value"] += 1
                preview_label.configure(
                    image="",
                    text="Aucun résultat. Utilise Custom." if search_var.get().strip() else "Aucune édition. Utilise Custom.",
                )

        def refresh_lowres_toggle() -> None:
            enabled = show_lowres_var.get()
            lowres_toggle.configure(
                text="LowRes visibles" if enabled else "LowRes masquées",
                style="LowResVisible.TButton" if enabled else "LowResHidden.TButton",
            )

        listbox.bind("<<ListboxSelect>>", update_preview)
        # Après chaque clic sur la listbox, le focus revient à la recherche.
        # Sans ce bind, le focus reste sur la listbox et les frappes clavier
        # ne vont plus dans la barre de recherche.
        listbox.bind("<Button-1>", lambda _e: dialog.after_idle(search_entry.focus_set), add="+")
        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))

        toggle_frame = ttk.Frame(buttons)
        toggle_frame.pack(side=tk.LEFT, padx=(0, 10))
        lowres_toggle = ttk.Button(
            toggle_frame,
            command=lambda: show_lowres_var.set(not show_lowres_var.get()),
        )
        lowres_toggle.pack(side=tk.LEFT)

        show_lowres_var.trace_add(
            "write",
            lambda *_args: (refresh_lowres_toggle(), refresh_prints()),
        )
        search_var.trace_add("write", lambda *_args: refresh_prints())
        refresh_lowres_toggle()
        refresh_prints()

        def apply_selection() -> None:
            current = listbox.curselection()
            if not current:
                return
            self._apply_decklist_print(row_index, prints[current[0]], batch=False)
            dialog.destroy()

        def apply_batch_selection() -> None:
            current = listbox.curselection()
            if not current:
                return
            self._apply_decklist_print(row_index, prints[current[0]], batch=True)
            dialog.destroy()

        def choose_custom() -> None:
            filename = filedialog.askopenfilename(
                parent=dialog,
                initialdir=".",
                filetypes=(
                    ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
                    ("Tous les fichiers", "*.*"),
                ),
            )
            if not filename:
                return
            custom_print = self._build_custom_decklist_print(entry, Path(filename))
            for position, existing_print in enumerate(custom_prints):
                if existing_print.id == custom_print.id:
                    refresh_prints(custom_print.id)
                    return
            custom_prints.append(custom_print)
            refresh_prints(custom_print.id)

        ttk.Button(buttons, text=f"Choisir copie {copy_number}", command=apply_selection).pack(side=tk.RIGHT, padx=(8, 0))
        if entry.quantity > 1:
            ttk.Button(buttons, text="Choisir toutes les copies", command=apply_batch_selection).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Custom", command=choose_custom).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=tk.RIGHT)
        listbox.bind("<Double-1>", lambda _event: apply_selection())
        dialog.bind("<Return>", lambda _event: apply_selection())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Destroy>", lambda _event: self.decklist_show_lowres_var.set(show_lowres_var.get()))
        self._present_custom_dialog(dialog, min_width=880, min_height=600, focus_widget=search_entry)
        return "break"

    def _load_decklist_preview(
        self,
        card_print: CardPrint,
        preview_label: tk.Label,
        dialog: tk.Toplevel,
        token: int,
        preview_token: dict[str, int],
    ) -> None:
        def worker() -> None:
            try:
                path = self._ensure_decklist_preview_file(card_print)
            except Exception:
                self.after(0, lambda: self._show_decklist_preview_error(preview_label, dialog, token, preview_token))
                return

            self.after(0, lambda: self._show_decklist_preview(path, card_print, preview_label, dialog, token, preview_token))

        threading.Thread(target=worker, daemon=True).start()

    def _show_decklist_preview_error(
        self,
        preview_label: tk.Label,
        dialog: tk.Toplevel,
        token: int,
        preview_token: dict[str, int],
    ) -> None:
        if preview_token["value"] != token or not dialog.winfo_exists():
            return
        preview_label.configure(image="", text="Preview indisponible")

    def _show_decklist_preview(
        self,
        path: Path,
        card_print: CardPrint,
        preview_label: tk.Label,
        dialog: tk.Toplevel,
        token: int,
        preview_token: dict[str, int],
    ) -> None:
        if preview_token["value"] != token or not dialog.winfo_exists():
            return

        try:
            from PIL import Image, ImageTk

            with Image.open(path) as source:
                image = source.convert("RGBA")
                image.thumbnail((350, 500), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
        except Exception:
            preview_label.configure(image="", text="Preview indisponible")
            return

        self.decklist_preview_images[card_print.id] = photo
        preview_label.configure(image=photo, text="")

    def _ensure_decklist_preview_file(self, card_print: CardPrint) -> Path:
        if card_print.custom_file_path:
            return Path(card_print.custom_file_path)

        url = card_print.preview_url or card_print.image_url
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        extension = Path(urlparse(url).path).suffix or ".jpg"
        cache_dir = Path.home() / ".scryfall_art_downloader" / "preview_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{digest}{extension}"
        with self.decklist_preview_lock:
            if target.exists():
                return target

            temp_target = target.with_suffix(f"{target.suffix}.tmp")
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=30) as response:
                with temp_target.open("wb") as output:
                    copyfileobj(response, output)
            temp_target.replace(target)
        return target

    def _apply_decklist_print(self, row_index: int, card_print: CardPrint, batch: bool = False) -> None:
        if row_index >= len(self.decklist_rows):
            return

        target_indexes = [row_index]
        if batch:
            entry_index = self.decklist_rows[row_index]["entry_index"]
            target_indexes = [
                candidate_index
                for candidate_index, row in enumerate(self.decklist_rows)
                if row.get("entry_index") == entry_index
            ]

        for target_index in target_indexes:
            sized_print = card_print.for_image_size(self.decklist_image_size_var.get().strip())
            self._set_decklist_row_print(target_index, sized_print)
        self._update_decklist_fix_button_state()

    def _start_decklist_download(self) -> None:
        if self.decklist_worker and self.decklist_worker.is_alive():
            return

        if self.decklist_language_var.get().strip().lower() != self.decklist_analyzed_language:
            messagebox.showerror("Analyse à refaire", "La langue a changé. Relance l'analyse avant de télécharger.")
            return

        if self.decklist_image_size_var.get().strip() != self.decklist_analyzed_image_size:
            self._refresh_decklist_image_size()
            self.decklist_analyzed_image_size = self.decklist_image_size_var.get().strip()

        missing = [
            row["entry"].name
            for row_index, row in enumerate(self.decklist_rows)
            if isinstance(row.get("entry"), DecklistEntry) and row_index not in self.decklist_selected_prints
        ]
        if missing:
            messagebox.showerror("Editions manquantes", "Aucune édition sélectionnée pour: " + ", ".join(missing[:8]))
            return

        selections = [
            (DecklistEntry(quantity=1, name=row["entry"].name), self.decklist_selected_prints[row_index])
            for row_index, row in enumerate(self.decklist_rows)
            if isinstance(row.get("entry"), DecklistEntry)
        ]

        self.decklist_analyze_button.configure(state="disabled")
        self.decklist_download_button.configure(state="disabled")
        self.decklist_cancel_button.configure(state="normal")
        self.decklist_cancel_event.clear()
        self.decklist_progress.configure(maximum=len(selections), value=0)
        self.decklist_progress_label.configure(text="Démarrage...")

        image_size = self.decklist_image_size_var.get().strip() or "large"
        self.decklist_worker = threading.Thread(target=self._run_decklist_download, args=(selections, image_size), daemon=True)
        self.decklist_worker.start()

    def _run_decklist_download(self, selections: list[tuple[DecklistEntry, CardPrint]], image_size: str) -> None:
        """
        Thread de travail pour le téléchargement des images de la decklist.

        Arguments :
            selections (list) : Liste de (DecklistEntry, CardPrint) à télécharger.
            image_size (str)  : Taille d'image souhaitée (ex: "large").
        """
        try:
            output_root = self.decklist_output_var.get().strip() or "ART"
            downloader = ArtDownloader(output_root)
            count, target_dir = downloader.download_decklist(
                selections=selections,
                language=self.decklist_analyzed_language,
                image_size=image_size,
                overwrite=self.decklist_overwrite_var.get(),
                on_status=lambda message: self.messages.put(("decklist_log", message)),
                on_progress=lambda current, total: self.messages.put(("decklist_progress", f"{current}/{total}")),
                should_cancel=self.decklist_cancel_event.is_set,
            )
            if self.decklist_cancel_event.is_set():
                self.messages.put(("decklist_cancelled", f"Annulé. {count} image(s) traitée(s) dans {target_dir}"))
            else:
                self.messages.put(("decklist_download_done", f"{count} image(s) dans {target_dir}"))
        except Exception as error:
            self.messages.put(("decklist_error", self._format_error(error)))

    # ==========================================================================
    #  OPÉRATIONS DU SCRYFALL DOWNLOADER (onglet 1)
    # ==========================================================================

    def _start_download(self) -> None:
        """
        Lance le téléchargement d'un set ou d'une carte dans un thread de travail.

        Lit les URLs des champs de saisie, les analyse, et démarre le thread.
        Affiche un message d'erreur si l'URL est invalide.
        """
        if self.worker and self.worker.is_alive():
            return   # Un téléchargement est déjà en cours

        try:
            scryfall_request = self._selected_scryfall_request()
        except ValueError as error:
            messagebox.showerror("Lien invalide", str(error))
            return

        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_event.clear()
        self.progress.configure(maximum=100, value=0)
        self.progress_label.configure(text="Démarrage...")
        if isinstance(scryfall_request, CardRequest):
            self._log(
                f"Carte détectée: {scryfall_request.set_code.upper()} #{scryfall_request.collector_number}"
            )
        else:
            self._log(f"Set détecté: {scryfall_request.set_code.upper()} / {scryfall_request.language.upper()}")

        self.worker = threading.Thread(target=self._run_download, args=(scryfall_request,), daemon=True)
        self.worker.start()

    def _selected_scryfall_request(self) -> SetRequest | CardRequest:
        """
        Détermine la requête Scryfall à partir des champs de saisie.

        Priorité : le champ "Lien de carte" est vérifié en premier.
        Si les deux champs sont remplis, la carte individuelle a priorité.

        Retourne :
            CardRequest ou SetRequest selon l'URL saisie.

        Lève :
            ValueError : Si aucune URL valide n'est trouvée.
        """
        card_url = self.card_url_var.get().strip()
        set_url = self.url_var.get().strip()

        if card_url and card_url != self.CARD_URL_PLACEHOLDER:
            request = parse_scryfall_url(card_url)
            if not isinstance(request, CardRequest):
                raise ValueError("Le champ 'Lien de carte' doit contenir un lien de carte Scryfall.")
            return request

        if set_url and set_url != self.URL_PLACEHOLDER:
            request = parse_scryfall_url(set_url)
            if not isinstance(request, SetRequest):
                raise ValueError("Le champ 'Lien du set' doit contenir un lien de set Scryfall.")
            return request

        raise ValueError("Veuillez saisir un lien de set ou un lien de carte Scryfall.")

    def _cancel_download(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.progress_label.configure(text="Annulation...")
            self._log("Annulation demandée...")

    def _run_download(self, scryfall_request) -> None:
        """
        Thread de travail pour le téléchargement d'un set ou d'une carte.

        Arguments :
            scryfall_request : SetRequest ou CardRequest à télécharger.
        """
        try:
            output_root = self.output_var.get().strip() or "ART"
            downloader = ArtDownloader(output_root)
            count, target_dir = downloader.download(
                request=scryfall_request,
                image_size=self.image_size_var.get(),
                overwrite=self.overwrite_var.get(),
                on_status=lambda message: self.messages.put(("log", message)),
                on_progress=lambda current, total: self.messages.put(("progress", f"{current}/{total}")),
                should_cancel=self.cancel_event.is_set,
            )
            if self.cancel_event.is_set():
                self.messages.put(("cancelled", f"Annulé. {count} image(s) traitée(s) dans {target_dir}"))
            else:
                self.messages.put(("done", f"{count} image(s) dans {target_dir}"))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _start_upscale(self) -> None:
        if self.upscale_worker and self.upscale_worker.is_alive():
            return

        folder = self.upscale_folder_var.get().strip()
        if not folder:
            messagebox.showerror("Dossier invalide", "Veuillez sélectionner un dossier source.")
            return
        output_folder = self.upscale_output_var.get().strip()
        if output_folder and Path(folder).resolve() == Path(output_folder).resolve():
            messagebox.showerror("Dossier invalide", "Le dossier de sortie doit être différent du dossier source.")
            return

        self.upscale_start_button.configure(state="disabled")
        self.upscale_cancel_button.configure(state="normal")
        self.upscale_cancel_event.clear()
        self.upscale_progress.configure(maximum=100, value=0)
        self.upscale_progress_label.configure(text="Démarrage...")
        self._clear_upscale_log()

        self.upscale_worker = threading.Thread(target=self._run_upscale, args=(folder, output_folder), daemon=True)
        self.upscale_worker.start()

    def _cancel_upscale(self) -> None:
        if self.upscale_worker and self.upscale_worker.is_alive():
            self.upscale_cancel_event.set()
            self.upscale_cancel_button.configure(state="disabled")
            self.upscale_progress_label.configure(text="Annulation...")
            self._upscale_log("Annulation demandée...")

    def _run_upscale(self, folder: str, output_folder: str) -> None:
        try:
            count, target_dir = upscale_folder_dpi(
                source_folder=folder,
                output_folder=output_folder or None,
                minimum_dpi=1200,
                on_status=lambda message: self.messages.put(("upscale_log", message)),
                on_progress=lambda current, total: self.messages.put(("upscale_progress", f"{current}/{total}")),
                should_cancel=self.upscale_cancel_event.is_set,
            )
            if self.upscale_cancel_event.is_set():
                self.messages.put(("upscale_cancelled", f"Annulé. {count} image(s) traitée(s) dans {target_dir}"))
            else:
                self.messages.put(("upscale_done", f"{count} image(s) dans {target_dir}"))
        except Exception as error:
            self.messages.put(("upscale_error", str(error)))

    def _start_margin(self) -> None:
        if self.margin_worker and self.margin_worker.is_alive():
            return

        folder = self.margin_folder_var.get().strip()
        if not folder:
            messagebox.showerror("Dossier invalide", "Veuillez sélectionner un dossier source.")
            return
        output_folder = self.margin_output_var.get().strip()
        if output_folder and Path(folder).resolve() == Path(output_folder).resolve():
            messagebox.showerror("Dossier invalide", "Le dossier de sortie doit être différent du dossier source.")
            return

        self.margin_start_button.configure(state="disabled")
        self.margin_cancel_button.configure(state="normal")
        self.margin_cancel_event.clear()
        self.margin_progress.configure(maximum=100, value=0)
        self.margin_progress_label.configure(text="Démarrage...")
        self._clear_margin_log()

        self.margin_worker = threading.Thread(target=self._run_margin, args=(folder, output_folder), daemon=True)
        self.margin_worker.start()

    def _cancel_margin(self) -> None:
        if self.margin_worker and self.margin_worker.is_alive():
            self.margin_cancel_event.set()
            self.margin_cancel_button.configure(state="disabled")
            self.margin_progress_label.configure(text="Annulation...")
            self._margin_log("Annulation demandée...")

    def _run_margin(self, folder: str, output_folder: str) -> None:
        try:
            count, target_dir = create_black_margins(
                source_folder=folder,
                output_folder=output_folder or None,
                on_status=lambda message: self.messages.put(("margin_log", message)),
                on_progress=lambda current, total: self.messages.put(("margin_progress", f"{current}/{total}")),
                should_cancel=self.margin_cancel_event.is_set,
            )
            if self.margin_cancel_event.is_set():
                self.messages.put(("margin_cancelled", f"Annulé. {count} image(s) traitée(s) dans {target_dir}"))
            else:
                self.messages.put(("margin_done", f"{count} image(s) dans {target_dir}"))
        except Exception as error:
            self.messages.put(("margin_error", str(error)))

    # ==========================================================================
    #  DISPATCHER DE MESSAGES (cœur de la communication thread → UI)
    # ==========================================================================

    def _poll_messages(self) -> None:
        """
        Traite tous les messages en attente des threads de travail.

        Cette méthode est appelée automatiquement toutes les ~50ms via after().
        C'est le mécanisme central qui permet à l'interface graphique de rester
        réactive pendant les opérations longues.

        POURQUOI UN SYSTÈME DE MESSAGES ?
        En Python/Tkinter, les widgets ne peuvent être modifiés QUE depuis le
        thread principal. Si un thread de travail essaie de modifier un widget
        directement, l'application peut crasher ou se figer.
        Solution : le thread de travail dépose des messages dans une file,
        et le thread principal les lit et met à jour les widgets.

        FONCTIONNEMENT :
        - On vide la file de messages (get_nowait = non bloquant)
        - Pour chaque message : (type, contenu)
        - On effectue l'action UI correspondante
        - À la fin, on se re-programme pour dans 50ms

        TYPES DE MESSAGES :
            "log"                     : Ajoute un message dans le log du Downloader
            "progress"                : Met à jour la barre de progression du Downloader
            "done"                    : Fin de téléchargement réussi
            "cancelled"               : Téléchargement annulé
            "error"                   : Erreur de téléchargement
            "decklist_log"            : Message dans le log Decklist
            "decklist_prints"         : Impressions reçues pour une carte
            "decklist_progress"       : Progression de l'analyse
            "decklist_analysis_done"  : Analyse terminée (+ restaure état si en attente)
            "decklist_download_done"  : Téléchargement decklist terminé
            "decklist_cancelled"      : Analyse/téléchargement annulé
            "decklist_error"          : Erreur decklist
            "upscale_*"               : Messages DPI Upscaler
            "margin_*"                : Messages Margin Creator
        """
        while True:
            try:
                kind, message = self.messages.get_nowait()   # Récupère sans attendre
            except queue.Empty:
                break   # Plus de messages → on sort de la boucle

            if kind == "log":
                self._log(str(message))
            elif kind == "progress":
                self._update_progress(self.progress, self.progress_label, str(message))
            elif kind == "done":
                self._log(str(message))
                self._log("Fini !")
                self.progress.configure(value=self.progress["maximum"])
                self.progress_label.configure(text="Terminé")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
            elif kind == "cancelled":
                self._log(str(message))
                self.progress_label.configure(text="Annulé")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
            elif kind == "error":
                self._log(f"Erreur: {message}")
                self.progress_label.configure(text="Erreur")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", str(message))
            elif kind == "decklist_log":
                self._decklist_log(str(message))
            elif kind == "decklist_local_bulk_progress":
                self._update_progress(self.decklist_progress, self.decklist_progress_label, str(message), prefix="Bulk local ")
            elif kind == "decklist_progress":
                self._update_progress(self.decklist_progress, self.decklist_progress_label, str(message))
            elif kind == "decklist_prints":
                index, prints = message
                self._receive_decklist_prints(index, prints)
            elif kind == "decklist_analysis_done":
                self._decklist_log(str(message))
                self.decklist_progress.configure(value=self.decklist_progress["maximum"])
                self.decklist_progress_label.configure(text="Terminé")
                self.decklist_analyze_button.configure(state="normal")
                self._update_decklist_state_buttons()
                self.decklist_cancel_button.configure(state="disabled")
                if len(self.decklist_selected_prints) == len(self.decklist_rows):
                    self.decklist_download_button.configure(state="normal")
                if self._pending_state_restore is not None:
                    self._apply_pending_state_restore()
            elif kind == "decklist_download_done":
                self._decklist_log(str(message))
                self._decklist_log("Fini !")
                self.decklist_progress.configure(value=self.decklist_progress["maximum"])
                self.decklist_progress_label.configure(text="Terminé")
                self.decklist_analyze_button.configure(state="normal")
                self._update_decklist_state_buttons()
                self.decklist_download_button.configure(state="normal")
                self.decklist_cancel_button.configure(state="disabled")
            elif kind == "decklist_cancelled":
                self._decklist_log(str(message))
                self.decklist_progress_label.configure(text="Annulé")
                self.decklist_analyze_button.configure(state="normal")
                self._pending_state_restore = None
                self._update_decklist_state_buttons()
                self.decklist_download_button.configure(state="normal" if self.decklist_selected_prints else "disabled")
                self.decklist_cancel_button.configure(state="disabled")
            elif kind == "decklist_error":
                self._decklist_log(f"Erreur: {message}")
                self.decklist_progress_label.configure(text="Erreur")
                self.decklist_analyze_button.configure(state="normal")
                self._pending_state_restore = None
                self._update_decklist_state_buttons()
                self.decklist_download_button.configure(state="normal" if self.decklist_selected_prints else "disabled")
                self.decklist_cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", str(message))
            elif kind == "upscale_log":
                self._upscale_log(str(message))
            elif kind == "upscale_progress":
                self._update_progress(self.upscale_progress, self.upscale_progress_label, str(message))
            elif kind == "upscale_done":
                self._upscale_log(str(message))
                self._upscale_log("Fini !")
                self.upscale_progress.configure(value=self.upscale_progress["maximum"])
                self.upscale_progress_label.configure(text="Terminé")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
            elif kind == "upscale_cancelled":
                self._upscale_log(str(message))
                self.upscale_progress_label.configure(text="Annulé")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
            elif kind == "upscale_error":
                self._upscale_log(f"Erreur: {message}")
                self.upscale_progress_label.configure(text="Erreur")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", str(message))
            elif kind == "margin_log":
                self._margin_log(str(message))
            elif kind == "margin_progress":
                self._update_progress(self.margin_progress, self.margin_progress_label, str(message))
            elif kind == "margin_done":
                self._margin_log(str(message))
                self._margin_log("Fini !")
                self.margin_progress.configure(value=self.margin_progress["maximum"])
                self.margin_progress_label.configure(text="Terminé")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
            elif kind == "margin_cancelled":
                self._margin_log(str(message))
                self.margin_progress_label.configure(text="Annulé")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
            elif kind == "margin_error":
                self._margin_log(f"Erreur: {message}")
                self.margin_progress_label.configure(text="Erreur")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", str(message))
            elif kind == "xml_log":
                self._append_log(self.xml_log, str(message))
            elif kind == "xml_done":
                self._append_log(self.xml_log, str(message))
                self.xml_progress.stop()
                self.xml_progress.configure(mode="determinate", value=100)
                self.xml_generate_button.configure(state="normal")
            elif kind == "xml_error":
                self._append_log(self.xml_log, f"Erreur: {message}")
                self.xml_progress.stop()
                self.xml_progress.configure(mode="determinate", value=0)
                self.xml_generate_button.configure(state="normal")
                messagebox.showerror("Erreur XML", str(message))

        self.after(100, self._poll_messages)

    @staticmethod
    def _update_progress(progress: ttk.Progressbar, label: ttk.Label, message: str, prefix: str = "") -> None:
        current_text, total_text = message.split("/", 1)
        current = int(current_text)
        total = int(total_text)
        if total > 0:
            progress.configure(maximum=total, value=min(current, total))
            percent = int((current / total) * 100)
            label.configure(text=f"{prefix}{current} / {total} ({percent}%)")

    # ==========================================================================
    #  GESTION DU CACHE ET FERMETURE DE L'APPLICATION
    # ==========================================================================

    @staticmethod
    def _runtime_cache_dirs() -> tuple[Path, ...]:
        """
        Retourne les dossiers de cache temporaire à nettoyer à la fermeture.

        Ces dossiers contiennent des données qui peuvent être re-générées :
        - Résultats de recherche Scryfall (cache API)
        - Images de prévisualisation téléchargées
        - Index SQLite3 du bulk local

        Retourne :
            tuple[Path, ...] : Chemins des dossiers de cache.
        """
        return (
            PRINT_SEARCH_CACHE_DIR,
            Path.home() / ".scryfall_art_downloader" / "preview_cache",
            LOCAL_BULK_INDEX_DIR,
            Path.cwd() / ".scryfall_local_bulk_index",
            Path(__file__).resolve().parent.parent / ".scryfall_local_bulk_index",
        )

    @staticmethod
    def _clear_cache_dir(path: Path) -> None:
        """
        Supprime un dossier de cache s'il existe.

        Les erreurs de suppression sont ignorées silencieusement car le cache
        est optionnel — son absence ne cause pas de dysfonctionnement.

        Arguments :
            path (Path) : Chemin du dossier à supprimer.
        """
        if not path.exists():
            return
        try:
            rmtree(path)   # rmtree = suppression récursive d'un dossier
        except Exception:
            return   # Suppression impossible (fichier verrouillé, droits insuffisants...) → on ignore

    def _close_application(self) -> None:
        """
        Ferme l'application proprement.

        Nettoie les dossiers de cache avant de détruire la fenêtre.
        Appelé quand l'utilisateur clique "X" sur la barre de titre.
        """
        for path in self._runtime_cache_dirs():
            self._clear_cache_dir(path)
        self.destroy()   # Détruit la fenêtre Tkinter et arrête la boucle principale

    @staticmethod
    def _format_error(error: Exception) -> str:
        """
        Convertit une exception en message d'erreur lisible pour l'utilisateur.

        Si l'exception a un message descriptif, on l'utilise. Sinon, on affiche
        le nom de la classe de l'exception (ex: "RuntimeError").

        Arguments :
            error (Exception) : Exception à formater.

        Retourne :
            str : Message d'erreur propre et non vide.
        """
        message = str(error).strip()
        return message or error.__class__.__name__

    # ==========================================================================
    #  MÉTHODES DE LOG (écriture dans les zones de texte)
    # ==========================================================================

    def _log(self, message: str) -> None:
        """Ajoute un message dans le log de l'onglet Scryfall Downloader."""
        self._append_log(self.log, message)

    def _decklist_log(self, message: str) -> None:
        """Ajoute un message dans le log de l'onglet Decklist."""
        self._append_log(self.decklist_log, message)

    def _clear_decklist_log(self) -> None:
        """Efface tout le contenu du log de la Decklist."""
        self.decklist_log.configure(state="normal")
        self.decklist_log.delete("1.0", tk.END)
        self.decklist_log.configure(state="disabled")

    def _upscale_log(self, message: str) -> None:
        """Ajoute un message dans le log de l'onglet DPI Upscaler."""
        self._append_log(self.upscale_log, message)

    def _clear_upscale_log(self) -> None:
        """Efface tout le contenu du log du DPI Upscaler."""
        self.upscale_log.configure(state="normal")
        self.upscale_log.delete("1.0", tk.END)
        self.upscale_log.configure(state="disabled")

    def _margin_log(self, message: str) -> None:
        """Ajoute un message dans le log de l'onglet Margin Creator."""
        self._append_log(self.margin_log, message)

    def _clear_margin_log(self) -> None:
        """Efface tout le contenu du log du Margin Creator."""
        self.margin_log.configure(state="normal")
        self.margin_log.delete("1.0", tk.END)
        self.margin_log.configure(state="disabled")

    @staticmethod
    def _append_log(widget: tk.Text, message: str) -> None:
        """
        Ajoute une ligne de texte dans une zone de log.

        La zone de log est en lecture seule (state="disabled").
        Pour écrire dedans, on l'active temporairement, on écrit,
        puis on la remet en lecture seule.
        On fait défiler automatiquement vers la dernière ligne (see(tk.END)).

        Arguments :
            widget  (tk.Text) : La zone de log dans laquelle écrire.
            message (str)     : Le texte à ajouter.
        """
        widget.configure(state="normal")        # Active temporairement pour la modification
        widget.insert(tk.END, f"{message}\n")   # Ajoute le message avec retour à la ligne
        widget.see(tk.END)                      # Fait défiler jusqu'à la fin (auto-scroll)
        widget.configure(state="disabled")      # Remet en lecture seule


# =============================================================================
#  POINT D'ENTRÉE
# =============================================================================

def main() -> None:
    """
    Crée et démarre l'application.

    Cette fonction est importée par main.py et appelée pour lancer le programme.
    app.mainloop() démarre la boucle événementielle Tkinter qui :
    - Gère tous les événements (clics, frappes, redimensionnements...)
    - Redessine l'interface quand nécessaire
    - S'arrête quand la fenêtre est fermée (destroy())
    """
    app = ScryfallArtApp()
    app.mainloop()   # Boucle principale : tourne jusqu'à fermeture de la fenêtre


if __name__ == "__main__":
    # Ce bloc s'exécute si on lance app.py directement (rare, normalement via main.py)
    main()
