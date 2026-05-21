from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .aspect_cropper import TARGET_ASPECT_RATIO, centered_crop_rect, crop_image_to_ratio
from .downloader import ArtDownloader
from .dpi_upscaler import upscale_folder_dpi
from .margin_creator import create_black_margins
from .models import CardRequest, SetRequest
from .url_parser import parse_scryfall_url


class ScryfallArtApp(tk.Tk):
    URL_PLACEHOLDER = "https://scryfall.com/sets/SET/LANGUE"
    CARD_URL_PLACEHOLDER = "https://scryfall.com/card/SET/NUMERO/nom"

    def __init__(self) -> None:
        super().__init__()
        self.title("Scryfall Artwork Downloader")
        self.geometry("820x540")
        self.minsize(820, 540)
        self.resizable(True, True)
        self.configure(bg="#202020")
        self.overrideredirect(True)
        try:
            self.attributes("-alpha", 0.96)
        except tk.TclError:
            pass

        self.taskbar_logo = self._load_logo_image(64)
        self.titlebar_logo = self._load_logo_image(26, prefer_small=True)
        self.header_logo = self._load_logo_image(54)
        self.upscale_header_logo = self._load_logo_image(54, names=("logo_upscale.ico", "logo.png"))
        self.margin_header_logo = self._load_logo_image(54, names=("logo_margin.ico", "logo.png"))
        self.trim_header_logo = self._load_logo_image(54, names=("logo_trim.ico", "logo.png"))
        self.iconphoto(True, self.taskbar_logo)

        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.upscale_worker: threading.Thread | None = None
        self.upscale_cancel_event = threading.Event()
        self.margin_worker: threading.Thread | None = None
        self.margin_cancel_event = threading.Event()
        self.crop_source_image = None
        self.crop_preview_image = None
        self.crop_rect: tuple[float, float, float, float] | None = None
        self.crop_scale = 1.0
        self.crop_offset = (0, 0)
        self.crop_drag_mode: str | None = None
        self.crop_drag_start = (0.0, 0.0)
        self.crop_drag_rect: tuple[float, float, float, float] | None = None
        self.is_fullscreen = False
        self.normal_geometry = "820x540"
        self.fullscreen_button: tk.Button | None = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._resize_start_width = 820
        self._resize_start_height = 540

        self.url_var = tk.StringVar(value=self.URL_PLACEHOLDER)
        self.card_url_var = tk.StringVar(value=self.CARD_URL_PLACEHOLDER)
        self.output_var = tk.StringVar(value="")
        self.image_size_var = tk.StringVar(value="large")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.upscale_folder_var = tk.StringVar(value="")
        self.upscale_output_var = tk.StringVar(value="")
        self.margin_folder_var = tk.StringVar(value="")
        self.margin_output_var = tk.StringVar(value="")
        self.crop_image_var = tk.StringVar(value="")
        self.crop_output_var = tk.StringVar(value="")
        self.crop_status_var = tk.StringVar(value="Choisis une image pour préparer le recadrage.")

        self._build_ui()
        self._center_window()
        self.bind("<Map>", self._restore_borderless)
        self.bind("<Escape>", self._exit_fullscreen)
        self.after(100, self._poll_messages)
        self.after(150, self._show_in_windows_taskbar)

    def _center_window(self) -> None:
        self.update_idletasks()
        width = 820
        height = 540
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _load_logo_image(
        self,
        size: int,
        prefer_small: bool = False,
        names: tuple[str, ...] | None = None,
    ) -> tk.PhotoImage:
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        if names is None:
            names = ("logo32.png", "logo.png") if prefer_small else ("logo.png", "logo32.png")
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

        return tk.PhotoImage(width=size, height=size)

    def _open_logo_image(self, path: Path, size: int) -> tk.PhotoImage | None:
        try:
            from PIL import Image, ImageTk
        except ImportError:
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
        factor = max(1, -(-max(image.width(), image.height()) // size))
        if factor > 1:
            image = image.subsample(factor, factor)
        if image.width() == size and image.height() == size:
            return image

        fitted = tk.PhotoImage(width=size, height=size)
        x = max(0, (size - image.width()) // 2)
        y = max(0, (size - image.height()) // 2)
        fitted.tk.call(fitted, "copy", image, "-to", x, y)
        return fitted

    def _make_window_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg="#181818",
            fg="#f1f1f1",
            activebackground="#000000",
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            width=4,
            height=1,
            font=("Segoe UI", 13, "bold"),
        )
        button.bind("<Enter>", lambda event: button.configure(bg="#000000"))
        button.bind("<Leave>", lambda event: button.configure(bg="#181818"))
        return button

    def _show_in_windows_taskbar(self) -> None:
        if sys.platform != "win32":
            return

        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()

            gwl_exstyle = -20
            ws_ex_appwindow = 0x00040000
            ws_ex_toolwindow = 0x00000080

            style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
            style = (style & ~ws_ex_toolwindow) | ws_ex_appwindow
            ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, style)
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            return

    def _restore_borderless(self, event: tk.Event | None = None) -> None:
        if event and event.widget is not self:
            return
        if self.state() == "normal":
            self.overrideredirect(True)

    def _start_move(self, event: tk.Event) -> None:
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _move_window(self, event: tk.Event) -> None:
        if self.is_fullscreen:
            return
        x = self.winfo_pointerx() - self._drag_start_x
        y = self.winfo_pointery() - self._drag_start_y
        self.geometry(f"+{x}+{y}")

    def _start_resize(self, event: tk.Event) -> None:
        if self.is_fullscreen:
            return
        self._resize_start_x = self.winfo_pointerx()
        self._resize_start_y = self.winfo_pointery()
        self._resize_start_width = self.winfo_width()
        self._resize_start_height = self.winfo_height()

    def _resize_window(self, event: tk.Event) -> None:
        if self.is_fullscreen:
            return
        width = max(820, self._resize_start_width + self.winfo_pointerx() - self._resize_start_x)
        height = max(540, self._resize_start_height + self.winfo_pointery() - self._resize_start_y)
        self.geometry(f"{width}x{height}")

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen:
            self._set_fullscreen(False)
        else:
            self.normal_geometry = self.geometry()
            self._set_fullscreen(True)

    def _exit_fullscreen(self, event: tk.Event | None = None) -> None:
        if self.is_fullscreen:
            self._set_fullscreen(False)

    def _set_fullscreen(self, enabled: bool) -> None:
        self.is_fullscreen = enabled
        try:
            self.attributes("-fullscreen", enabled)
        except tk.TclError:
            if enabled:
                self.geometry(
                    f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0"
                )
            else:
                self.geometry(self.normal_geometry)
        if not enabled:
            self.geometry(self.normal_geometry)
        if self.fullscreen_button is not None:
            self.fullscreen_button.configure(text="▢" if enabled else "□")

    def _minimize_window(self) -> None:
        if self.is_fullscreen:
            self._set_fullscreen(False)
        self.overrideredirect(False)
        self.iconify()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background="#202020", foreground="#e8e8e8", fieldbackground="#2d2d2d")
        style.configure("TFrame", background="#202020")
        style.configure("TNotebook", background="#202020", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2d2d2d", foreground="#e8e8e8", padding=(10, 5), font=("Segoe UI", 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#000000"), ("active", "#000000")],
            foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
            padding=[("selected", (22, 10)), ("!selected", (10, 5))],
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
        style.configure("Horizontal.TProgressbar", troughcolor="#2d2d2d", background="#8a8a8a", bordercolor="#202020")
        self.option_add("*TCombobox*Listbox.background", "#181818")
        self.option_add("*TCombobox*Listbox.foreground", "#ffffff")
        self.option_add("*TCombobox*Listbox.selectBackground", "#000000")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _build_ui(self) -> None:
        self._configure_style()

        titlebar = tk.Frame(self, bg="#181818", height=42)
        titlebar.pack(fill=tk.X)
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

        self._make_window_button(titlebar, "X", self.destroy).pack(side=tk.RIGHT)
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
        upscaler_tab = ttk.Frame(notebook)
        margin_tab = ttk.Frame(notebook)
        crop_tab = ttk.Frame(notebook)
        notebook.add(scraper_tab, text="Scryfall Downloader")
        notebook.add(upscaler_tab, text="DPI Upscaler")
        notebook.add(margin_tab, text="Margin Creator")
        notebook.add(crop_tab, text="Ratio Cropper")

        self._build_scraper_tab(scraper_tab)
        self._build_upscaler_tab(upscaler_tab)
        self._build_margin_tab(margin_tab)
        self._build_crop_tab(crop_tab)

    def _build_scraper_tab(self, root: ttk.Frame) -> None:
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
        ttk.Combobox(
            root,
            textvariable=self.image_size_var,
            values=("small", "normal", "large", "png", "art_crop", "border_crop"),
            state="readonly",
            width=16,
        ).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(12, 0))

        ttk.Checkbutton(root, text="Remplacer les fichiers déjà présents", variable=self.overwrite_var).grid(
            row=5, column=1, sticky="w", padx=(12, 0), pady=(12, 0)
        )

        self.start_button = ttk.Button(root, text="Télécharger", command=self._start_download)
        self.start_button.grid(row=6, column=1, sticky="w", padx=(12, 0), pady=(16, 0))

        self.cancel_button = ttk.Button(root, text="Annuler", command=self._cancel_download, state="disabled")
        self.cancel_button.grid(row=6, column=1, sticky="w", padx=(130, 0), pady=(16, 0))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 8))

        self.progress_label = ttk.Label(root, text="En attente", anchor="e")
        self.progress_label.grid(row=7, column=2, sticky="ew", padx=(12, 0), pady=(16, 8))

        self.log = self._make_log_widget(root)
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew")

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

        self.upscale_start_button = ttk.Button(root, text="Upscaler DPI", command=self._start_upscale)
        self.upscale_start_button.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(16, 0))

        self.upscale_cancel_button = ttk.Button(root, text="Annuler", command=self._cancel_upscale, state="disabled")
        self.upscale_cancel_button.grid(row=3, column=1, sticky="w", padx=(145, 0), pady=(16, 0))

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

        self.margin_start_button = ttk.Button(root, text="Créer les marges", command=self._start_margin)
        self.margin_start_button.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(16, 0))

        self.margin_cancel_button = ttk.Button(root, text="Annuler", command=self._cancel_margin, state="disabled")
        self.margin_cancel_button.grid(row=3, column=1, sticky="w", padx=(165, 0), pady=(16, 0))

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

        ttk.Button(root, text="Enregistrer le crop", command=self._save_crop).grid(
            row=3, column=1, sticky="w", padx=(12, 0), pady=(16, 0)
        )
        ttk.Button(root, text="Centrer", command=self._reset_crop_rect).grid(
            row=3, column=1, sticky="w", padx=(165, 0), pady=(16, 0)
        )
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

    @staticmethod
    def _make_log_widget(parent: tk.Widget) -> tk.Text:
        return tk.Text(
            parent,
            height=12,
            wrap="word",
            state="disabled",
            bg="#181818",
            fg="#e8e8e8",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#666666",
        )

    def _clear_url_placeholder(self, event: tk.Event | None = None) -> None:
        if self.url_var.get() == self.URL_PLACEHOLDER:
            self.url_var.set("")
            self.url_entry.configure(foreground="#ffffff")

    def _restore_url_placeholder(self, event: tk.Event | None = None) -> None:
        if not self.url_var.get().strip():
            self.url_var.set(self.URL_PLACEHOLDER)
            self.url_entry.configure(foreground="#9a9a9a")
        else:
            self.url_entry.configure(foreground="#ffffff")

    def _clear_card_url_placeholder(self, event: tk.Event | None = None) -> None:
        if self.card_url_var.get() == self.CARD_URL_PLACEHOLDER:
            self.card_url_var.set("")
            self.card_url_entry.configure(foreground="#ffffff")

    def _restore_card_url_placeholder(self, event: tk.Event | None = None) -> None:
        if not self.card_url_var.get().strip():
            self.card_url_var.set(self.CARD_URL_PLACEHOLDER)
            self.card_url_entry.configure(foreground="#9a9a9a")
        else:
            self.card_url_entry.configure(foreground="#ffffff")

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or ".")
        if folder:
            self.output_var.set(folder)

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

    def _start_download(self) -> None:
        if self.worker and self.worker.is_alive():
            return

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

    def _poll_messages(self) -> None:
        while True:
            try:
                kind, message = self.messages.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._log(message)
            elif kind == "progress":
                self._update_progress(self.progress, self.progress_label, message)
            elif kind == "done":
                self._log(message)
                self._log("Fini !")
                self.progress.configure(value=self.progress["maximum"])
                self.progress_label.configure(text="Terminé")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
            elif kind == "cancelled":
                self._log(message)
                self.progress_label.configure(text="Annulé")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
            elif kind == "error":
                self._log(f"Erreur: {message}")
                self.progress_label.configure(text="Erreur")
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", message)
            elif kind == "upscale_log":
                self._upscale_log(message)
            elif kind == "upscale_progress":
                self._update_progress(self.upscale_progress, self.upscale_progress_label, message)
            elif kind == "upscale_done":
                self._upscale_log(message)
                self._upscale_log("Fini !")
                self.upscale_progress.configure(value=self.upscale_progress["maximum"])
                self.upscale_progress_label.configure(text="Terminé")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
            elif kind == "upscale_cancelled":
                self._upscale_log(message)
                self.upscale_progress_label.configure(text="Annulé")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
            elif kind == "upscale_error":
                self._upscale_log(f"Erreur: {message}")
                self.upscale_progress_label.configure(text="Erreur")
                self.upscale_start_button.configure(state="normal")
                self.upscale_cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", message)
            elif kind == "margin_log":
                self._margin_log(message)
            elif kind == "margin_progress":
                self._update_progress(self.margin_progress, self.margin_progress_label, message)
            elif kind == "margin_done":
                self._margin_log(message)
                self._margin_log("Fini !")
                self.margin_progress.configure(value=self.margin_progress["maximum"])
                self.margin_progress_label.configure(text="Terminé")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
            elif kind == "margin_cancelled":
                self._margin_log(message)
                self.margin_progress_label.configure(text="Annulé")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
            elif kind == "margin_error":
                self._margin_log(f"Erreur: {message}")
                self.margin_progress_label.configure(text="Erreur")
                self.margin_start_button.configure(state="normal")
                self.margin_cancel_button.configure(state="disabled")
                messagebox.showerror("Erreur", message)

        self.after(100, self._poll_messages)

    @staticmethod
    def _update_progress(progress: ttk.Progressbar, label: ttk.Label, message: str) -> None:
        current_text, total_text = message.split("/", 1)
        current = int(current_text)
        total = int(total_text)
        if total > 0:
            progress.configure(maximum=total, value=min(current, total))
            percent = int((current / total) * 100)
            label.configure(text=f"{current} / {total} ({percent}%)")

    def _log(self, message: str) -> None:
        self._append_log(self.log, message)

    def _upscale_log(self, message: str) -> None:
        self._append_log(self.upscale_log, message)

    def _clear_upscale_log(self) -> None:
        self.upscale_log.configure(state="normal")
        self.upscale_log.delete("1.0", tk.END)
        self.upscale_log.configure(state="disabled")

    def _margin_log(self, message: str) -> None:
        self._append_log(self.margin_log, message)

    def _clear_margin_log(self) -> None:
        self.margin_log.configure(state="normal")
        self.margin_log.delete("1.0", tk.END)
        self.margin_log.configure(state="disabled")

    @staticmethod
    def _append_log(widget: tk.Text, message: str) -> None:
        widget.configure(state="normal")
        widget.insert(tk.END, f"{message}\n")
        widget.see(tk.END)
        widget.configure(state="disabled")


def main() -> None:
    app = ScryfallArtApp()
    app.mainloop()


if __name__ == "__main__":
    main()
