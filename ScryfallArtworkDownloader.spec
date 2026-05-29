# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import sys


project_root = Path.cwd()
python_root = Path(sys.base_prefix)
icon_path = str(project_root / "assets" / "logo.ico")
manifest_path = str(project_root / "windows_app.manifest")
version_path = str(project_root / "windows_version_info.txt")
use_windows_metadata = (
    sys.platform == "win32"
    and os.environ.get("SCRYFALL_SAFE_BUILD") != "1"
    and Path(manifest_path).exists()
    and Path(version_path).exists()
)

tcl_dir = python_root / "tcl" / "tcl8.6"
tk_dir  = python_root / "tcl" / "tk8.6"
tk_binaries = [
    path
    for path in (
        python_root / "DLLs" / "_tkinter.pyd",
        python_root / "DLLs" / "tcl86t.dll",
        python_root / "DLLs" / "tk86t.dll",
    )
    if path.exists()
]
tk_datas = [
    (str(source), target)
    for source, target in (
        (tcl_dir, "tcl/tcl8.6"),
        (tk_dir,  "tcl/tk8.6"),
    )
    if source.exists()
]

exe_options = {}
if use_windows_metadata:
    exe_options.update({"version": version_path, "manifest": manifest_path})


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[(str(path), ".") for path in tk_binaries],
    datas=[
        ("assets/logo.png",          "assets"),
        ("assets/logo32.png",         "assets"),
        ("assets/logo.ico",           "assets"),
        ("assets/logo_upscale.ico",   "assets"),
        ("assets/logo_margin.ico",    "assets"),
        ("assets/logo_trim.ico",      "assets"),
        ("assets/logo_decklist.ico",  "assets"),
        ("assets/logo_XML.ico",       "assets"),
        ("assets/Cardback.jpg",       "assets"),
    ] + tk_datas,
    hiddenimports=[
        # Tkinter
        "tkinter", "_tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
        # Pillow — modules de base
        "PIL", "PIL.Image", "PIL.ImageTk", "PIL.ImageDraw", "PIL.ImageFilter", "PIL.ImageOps",
        # Pillow — décodeurs de formats utilisés dans le projet
        "PIL.JpegImagePlugin", "PIL.PngImagePlugin", "PIL.WebPImagePlugin",
        "PIL.TiffImagePlugin", "PIL.BmpImagePlugin",
        # Pillow — décodeurs internes nécessaires au runtime
        "PIL._imaging", "PIL._tkinter_finder",
    ],
    excludes=[
        # Modules scientifiques lourds non utilisés
        "numpy", "scipy", "pandas", "matplotlib",
        # Tests et outils de dev
        "unittest", "test", "doctest", "pdb", "profile", "cProfile",
        # Protocoles réseau non utilisés
        "xmlrpc", "http.server", "ftplib", "imaplib", "smtplib", "poplib",
        "email.mime",
        # Concurrence non utilisée
        "multiprocessing", "concurrent.futures", "asyncio",
        # Divers non utilisés
        "turtle", "curses", "audioop", "cgi", "cgitb",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ScryfallArtworkDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    **exe_options,
)
