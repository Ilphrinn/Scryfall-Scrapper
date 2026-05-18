from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
        self.resizable(False, False)
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
        self.iconphoto(True, self.taskbar_logo)

        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.upscale_worker: threading.Thread | None = None
        self.upscale_cancel_event = threading.Event()
        self.margin_worker: threading.Thread | None = None
        self.margin_cancel_event = threading.Event()
        self._drag_start_x = 0
        self._drag_start_y = 0

        self.url_var = tk.StringVar(value=self.URL_PLACEHOLDER)
        self.card_url_var = tk.StringVar(value=self.CARD_URL_PLACEHOLDER)
        self.output_var = tk.StringVar(value="")
        self.image_size_var = tk.StringVar(value="large")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.upscale_folder_var = tk.StringVar(value="")
        self.upscale_output_var = tk.StringVar(value="")
        self.margin_folder_var = tk.StringVar(value="")
        self.margin_output_var = tk.StringVar(value="")

        self._build_ui()
        self._center_window()
        self.bind("<Map>", self._restore_borderless)
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
        x = self.winfo_pointerx() - self._drag_start_x
        y = self.winfo_pointery() - self._drag_start_y
        self.geometry(f"+{x}+{y}")

    def _minimize_window(self) -> None:
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
        self._make_window_button(titlebar, "-", self._minimize_window).pack(side=tk.RIGHT)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        scraper_tab = ttk.Frame(notebook)
        upscaler_tab = ttk.Frame(notebook)
        margin_tab = ttk.Frame(notebook)
        notebook.add(scraper_tab, text="Scryfall Downloader")
        notebook.add(upscaler_tab, text="DPI Upscaler")
        notebook.add(margin_tab, text="Margin Creator")

        self._build_scraper_tab(scraper_tab)
        self._build_upscaler_tab(upscaler_tab)
        self._build_margin_tab(margin_tab)

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
