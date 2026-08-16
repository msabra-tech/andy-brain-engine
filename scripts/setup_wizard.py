#!/usr/bin/env python3
"""Small Windows setup wizard for a non-technical Andy Brain installation."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover - available in standard Windows Python.
    raise SystemExit("Tkinter is required for the setup wizard. Run scripts\\setup_windows.py from a standard Windows Python installation.") from exc


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from brain.config import Config, load_runtime
from brain.connectors import authorize_google_drive, configure_notion_token, import_google_drive_client
from brain.windows_notifications import install_schedule
from setup_windows import main as setup_main


class SetupWizard(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=20)
        self.root = root
        self.grid(sticky="nsew")
        root.title("Andy Brain Setup")
        root.minsize(700, 500)
        documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
        self.owner = tk.StringVar(value="Andy")
        self.vault = tk.StringVar(value=str(documents / "Andy-Brain"))
        self.output = tk.StringVar(value=str(documents / "Andy-Brain Exports"))
        self.local_folders = tk.StringVar()
        self.google_client = tk.StringVar()
        self.notion_token = tk.StringVar()
        self.schedule = tk.StringVar(value="09:00")
        self.status = tk.StringVar(value="Choose the folders Andy Brain may use, then click Set up core.")
        self._build()

    def _row(self, row: int, label: str, variable: tk.StringVar, browse: str | None = None, secret: bool = False) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(self, textvariable=variable, width=62, show="*" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", padx=(10, 8), pady=5)
        if browse == "folder":
            ttk.Button(self, text="Browse", command=lambda: self._choose_folder(variable)).grid(row=row, column=2, pady=5)
        elif browse == "file":
            ttk.Button(self, text="Browse", command=lambda: self._choose_file(variable)).grid(row=row, column=2, pady=5)

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="Andy Brain", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(self, text="Claude Desktop does the thinking; this wizard safely connects sources and creates the Obsidian map.", wraplength=650).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 14))
        self._row(2, "Owner name", self.owner)
        self._row(3, "Obsidian vault", self.vault, "folder")
        self._row(4, "Approved local folders (separate with ;)", self.local_folders)
        self._row(5, "Generated-file output folder", self.output, "folder")
        ttk.Separator(self).grid(row=6, column=0, columnspan=3, sticky="ew", pady=10)
        self._row(7, "Google Desktop OAuth client JSON (optional)", self.google_client, "file")
        self._row(8, "Notion Personal Access Token (optional, hidden)", self.notion_token, secret=True)
        self._row(9, "Daily reminder time", self.schedule)
        buttons = ttk.Frame(self)
        buttons.grid(row=10, column=0, columnspan=3, sticky="w", pady=(15, 8))
        ttk.Button(buttons, text="1. Set up core", command=self.setup_core).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="2. Authorize Google Drive", command=self.authorize_google).grid(row=0, column=1, padx=8)
        ttk.Button(buttons, text="3. Install daily reminder", command=self.install_reminder).grid(row=0, column=2, padx=8)
        ttk.Label(self, textvariable=self.status, wraplength=650, foreground="#1f4d2e").grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _choose_folder(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()))
        if chosen:
            variable.set(chosen)

    def _choose_file(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if chosen:
            variable.set(chosen)

    def _config(self) -> Config:
        return Config(REPO, Path(self.vault.get()).expanduser(), REPO / "data/staging")

    def setup_core(self) -> None:
        try:
            args = ["--yes", "--owner-name", self.owner.get().strip() or "Andy", "--vault-title", "Andy Brain", "--vault-path", self.vault.get(), "--output-path", self.output.get()]
            for folder in [item.strip() for item in self.local_folders.get().split(";") if item.strip()]:
                args.extend(["--local-folder", folder])
            if setup_main(args) != 0:
                raise RuntimeError("core setup validation failed")
            config = self._config()
            if self.google_client.get().strip():
                import_google_drive_client(config, self.google_client.get().strip())
            if self.notion_token.get().strip():
                configure_notion_token(config, self.notion_token.get().strip())
                self.notion_token.set("")
            self.status.set("Core setup is complete. Open the vault in Obsidian, then use Claude Desktop. Authorize Drive when ready.")
        except Exception as exc:
            self.status.set(f"Setup needs attention: {exc}")
            messagebox.showerror("Andy Brain setup", str(exc))

    def authorize_google(self) -> None:
        self.status.set("Opening Google consent in the browser. Complete it with Andy's JDS account...")

        def run() -> None:
            try:
                authorize_google_drive(self._config())
                self.root.after(0, lambda: self.status.set("Google Drive is connected. Claude can now sync current Drive material."))
            except Exception as exc:
                self.root.after(0, lambda: self.status.set(f"Google Drive authorization needs attention: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def install_reminder(self) -> None:
        try:
            result = install_schedule(self._config(), load_runtime(self._config()), self.schedule.get().strip())
            self.status.set("Daily reminder installed." if result.get("installed") else f"Reminder needs attention: {result.get('reason') or result.get('stderr', '')}")
        except Exception as exc:
            self.status.set(f"Reminder setup needs attention: {exc}")


def main() -> int:
    root = tk.Tk()
    SetupWizard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
