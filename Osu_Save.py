import os
import re
import json
import threading
import requests
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

APP_VERSION = "1.0.0"
MANIFEST_NAME = "osu_most_played_manifest.json"
CONFIG_NAME = "osu_save_config.json"

DEFAULT_MIRRORS = [
    "https://api.osu.direct/d/{}",
    "https://api.chimu.moe/v1/download/{}",
    "https://api.nerinyan.moe/d/{}"
]


def load_config():
    if not os.path.exists(CONFIG_NAME):
        return {
            "mirrors": DEFAULT_MIRRORS[:],
        }
    try:
        with open(CONFIG_NAME, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "mirrors": DEFAULT_MIRRORS[:],
        }


def save_config(cfg):
    try:
        with open(CONFIG_NAME, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def log(gui, msg):
    # thread-safe logging via Tkinter's event loop
    def _append():
        gui.log_box.configure(state="normal")
        gui.log_box.insert(tk.END, msg + "\n")
        gui.log_box.see(tk.END)
        gui.log_box.configure(state="disabled")
    gui.root.after(0, _append)


def extract_user_id(url):
    m = re.search(r"/users/(\d+)", url)
    return m.group(1) if m else None


def fetch_most_played(user_id):
    beatmaps = []
    offset = 0
    limit = 50

    while True:
        api_url = f"https://osu.ppy.sh/users/{user_id}/beatmapsets/most_played?offset={offset}&limit={limit}"
        r = requests.get(api_url)

        if r.status_code != 200:
            break

        try:
            data = r.json()
        except Exception:
            break

        if not data:
            break

        for entry in data:
            bm = entry.get("beatmapset")
            if bm:
                beatmaps.append(str(bm["id"]))

        offset += limit

    return beatmaps


def load_manifest(folder):
    path = os.path.join(folder, MANIFEST_NAME)
    if not os.path.exists(path):
        return set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("downloaded", []))
    except Exception:
        return set()


def save_manifest(folder, downloaded_ids):
    path = os.path.join(folder, MANIFEST_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"downloaded": sorted(list(downloaded_ids))}, f, indent=2)
    except Exception:
        pass


def try_download(url, filepath):
    try:
        r = requests.get(url, stream=True, timeout=30)
    except Exception:
        return False

    if r.status_code != 200 or "text/html" in r.headers.get("Content-Type", ""):
        return False

    try:
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    except Exception:
        return False

    return True


def download_one(beatmapset_id, download_folder, auto_import, mirrors):
    filename = f"{beatmapset_id}.osz"
    filepath = os.path.join(download_folder, filename)

    if os.path.exists(filepath):
        if auto_import:
            try:
                os.startfile(filepath)
            except Exception:
                pass
        return beatmapset_id, "skipped-existing", None

    for mirror in mirrors:
        url = mirror.format(beatmapset_id)
        if try_download(url, filepath):
            if auto_import:
                try:
                    os.startfile(filepath)
                except Exception:
                    pass
            return beatmapset_id, "ok", url

    return beatmapset_id, "failed", None


class MirrorSettingsWindow(tk.Toplevel):
    def __init__(self, parent, config, on_save):
        super().__init__(parent.root)
        self.parent = parent
        self.config = config
        self.on_save = on_save

        self.title("Mirror settings")
        self.geometry("400x260")
        self.resizable(False, False)
        self.configure(bg=parent.panel)

        tk.Label(
            self,
            text="Mirror priority (top = first):",
            bg=parent.panel,
            fg=parent.fg,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 4))

        self.listbox = tk.Listbox(
            self,
            bg="#101012",
            fg=parent.fg,
            selectbackground=parent.accent,
            selectforeground="black",
            relief="flat",
            font=("Segoe UI", 9),
            height=6
        )
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        for m in self.config.get("mirrors", DEFAULT_MIRRORS):
            self.listbox.insert(tk.END, m)

        btn_frame = tk.Frame(self, bg=parent.panel)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        up_btn = ttk.Button(
            btn_frame,
            text="Up",
            style="Lazer.TButton",
            command=self.move_up
        )
        up_btn.pack(side="left", padx=4)

        down_btn = ttk.Button(
            btn_frame,
            text="Down",
            style="Lazer.TButton",
            command=self.move_down
        )
        down_btn.pack(side="left", padx=4)

        reset_btn = ttk.Button(
            btn_frame,
            text="Reset",
            style="Lazer.TButton",
            command=self.reset
        )
        reset_btn.pack(side="left", padx=4)

        save_btn = ttk.Button(
            btn_frame,
            text="Save",
            style="Lazer.TButton",
            command=self.save
        )
        save_btn.pack(side="right", padx=4)

    def move_up(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        if i == 0:
            return
        text = self.listbox.get(i)
        self.listbox.delete(i)
        self.listbox.insert(i - 1, text)
        self.listbox.select_set(i - 1)

    def move_down(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        if i == self.listbox.size() - 1:
            return
        text = self.listbox.get(i)
        self.listbox.delete(i)
        self.listbox.insert(i + 1, text)
        self.listbox.select_set(i + 1)

    def reset(self):
        self.listbox.delete(0, tk.END)
        for m in DEFAULT_MIRRORS:
            self.listbox.insert(tk.END, m)

    def save(self):
        mirrors = [self.listbox.get(i) for i in range(self.listbox.size())]
        if not mirrors:
            messagebox.showerror("Error", "You must have at least one mirror.")
            return
        self.config["mirrors"] = mirrors
        save_config(self.config)
        if self.on_save:
            self.on_save(mirrors)
        self.destroy()


class OsuGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Osu Save")
        self.root.geometry("780x640")
        self.root.resizable(False, False)

        try:
            self.root.iconbitmap("Osu_Save.ico")
        except Exception:
            pass

        self.bg = "#0e0e0f"
        self.panel = "#1a1a1c"
        self.fg = "#ffffff"
        self.accent = "#ff4fd8"

        self.root.configure(bg=self.bg)

        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(
            "Lazer.TButton",
            background=self.accent,
            foreground="black",
            padding=6,
            borderwidth=0
        )
        style.map("Lazer.TButton", background=[("active", "#ff7ae6")])

        style.configure(
            "TProgressbar",
            troughcolor="#2a2a2d",
            background=self.accent,
            borderwidth=0
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#2a2a2d",
            background=self.accent,
            borderwidth=0
        )

        self.config_data = load_config()
        self.mirrors = self.config_data.get("mirrors", DEFAULT_MIRRORS[:])

        # USER INPUT
        self.user_frame = tk.Frame(self.root, bg=self.panel)
        self.user_frame.pack(fill="x", padx=12, pady=(12, 6))

        tk.Label(
            self.user_frame,
            text="osu! user link",
            bg=self.panel,
            fg=self.fg,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 0))

        self.url_entry = tk.Entry(
            self.user_frame,
            bg="#101012",
            fg=self.fg,
            insertbackground=self.fg,
            relief="flat",
            font=("Segoe UI", 10)
        )
        self.url_entry.pack(fill="x", padx=10, pady=(4, 10))

        # FOLDER PICKER
        self.folder_frame = tk.Frame(self.root, bg=self.panel)
        self.folder_frame.pack(fill="x", padx=12, pady=6)

        tk.Label(
            self.folder_frame,
            text="Save beatmaps to",
            bg=self.panel,
            fg=self.fg,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 0))

        self.folder_var = tk.StringVar(value="")

        self.folder_entry = tk.Entry(
            self.folder_frame,
            textvariable=self.folder_var,
            bg="#101012",
            fg=self.fg,
            insertbackground=self.fg,
            relief="flat",
            font=("Segoe UI", 10)
        )
        self.folder_entry.pack(fill="x", padx=10, pady=(4, 6))

        ttk.Button(
            self.folder_frame,
            text="Browse",
            style="Lazer.TButton",
            command=self.browse_folder
        ).pack(anchor="e", padx=10, pady=(0, 10))

        # OPTIONS
        self.options_frame = tk.Frame(self.root, bg=self.panel)
        self.options_frame.pack(fill="x", padx=12, pady=6)

        self.auto_import_var = tk.BooleanVar(value=True)

        tk.Checkbutton(
            self.options_frame,
            text="Auto-import into osu! (open .osz after download)",
            variable=self.auto_import_var,
            bg=self.panel,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.panel,
            activeforeground=self.fg,
            font=("Segoe UI", 10)
        ).pack(anchor="w", padx=10, pady=(8, 4))

        # THREAD SLIDER
        threads_frame = tk.Frame(self.root, bg=self.panel)
        threads_frame.pack(fill="x", padx=12, pady=6)

        tk.Label(
            threads_frame,
            text="Download threads:",
            bg=self.panel,
            fg=self.fg,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 0))

        self.thread_count_var = tk.IntVar(value=4)

        self.thread_slider = tk.Scale(
            threads_frame,
            from_=1,
            to=16,
            orient="horizontal",
            bg=self.panel,
            fg=self.fg,
            troughcolor="#101012",
            highlightthickness=0,
            variable=self.thread_count_var
        )
        self.thread_slider.pack(fill="x", padx=10, pady=(0, 8))

        # BUTTONS
        self.buttons_frame = tk.Frame(self.root, bg=self.panel)
        self.buttons_frame.pack(fill="x", padx=12, pady=6)

        self.start_btn = ttk.Button(
            self.buttons_frame,
            text="Start",
            style="Lazer.TButton",
            command=self.start
        )
        self.start_btn.pack(side="left", padx=(10, 5), pady=10)

        self.cancel_btn = ttk.Button(
            self.buttons_frame,
            text="Cancel",
            style="Lazer.TButton",
            command=self.cancel,
            state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=5, pady=10)

        self.retry_btn = ttk.Button(
            self.buttons_frame,
            text="Retry failed",
            style="Lazer.TButton",
            command=self.retry_failed,
            state="disabled"
        )
        self.retry_btn.pack(side="left", padx=5, pady=10)

        self.verify_btn = ttk.Button(
            self.buttons_frame,
            text="Verify existing files",
            style="Lazer.TButton",
            command=self.verify_threaded
        )
        self.verify_btn.pack(side="left", padx=5, pady=10)

        self.mirror_btn = ttk.Button(
            self.buttons_frame,
            text="Mirror settings",
            style="Lazer.TButton",
            command=self.open_mirror_settings
        )
        self.mirror_btn.pack(side="left", padx=5, pady=10)

        self.update_btn = ttk.Button(
            self.buttons_frame,
            text="Check for updates",
            style="Lazer.TButton",
            command=self.check_updates
        )
        self.update_btn.pack(side="left", padx=5, pady=10)

        # PROGRESS BAR
        self.progress = ttk.Progressbar(
            self.root,
            mode="determinate",
            style="Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", padx=12, pady=(6, 0))

        self.progress_label_var = tk.StringVar(value="0 / 0")
        tk.Label(
            self.root,
            textvariable=self.progress_label_var,
            bg=self.bg,
            fg=self.fg,
            font=("Segoe UI", 9)
        ).pack(anchor="e", padx=18, pady=(2, 10))

        # LOG WINDOW
        self.log_box = scrolledtext.ScrolledText(
            self.root,
            state="disabled",
            height=14,
            bg="#0c0c0d",
            fg=self.fg,
            insertbackground=self.fg,
            relief="flat",
            font=("Consolas", 10)
        )
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.thread = None
        self.cancel_flag = False
        self.last_failed = []

    # WORKER THREAD
    def worker(self, url, folder, auto_import, retry_mode):
        user_id = extract_user_id(url)
        if not user_id:
            log(self, "❌ Invalid osu! user link.")
            self.start_btn.config(state="normal")
            self.cancel_btn.config(state="disabled")
            return

        log(self, f"Fetching most-played maps for user {user_id}...")
        beatmap_ids = fetch_most_played(user_id)

        if not beatmap_ids:
            log(self, "❌ No beatmaps found.")
            self.start_btn.config(state="normal")
            self.cancel_btn.config(state="disabled")
            return

        log(self, f"Found {len(beatmap_ids)} beatmapsets.")

        downloaded = load_manifest(folder)
        to_download = [bid for bid in beatmap_ids if bid not in downloaded]

        if retry_mode:
            to_download = self.last_failed

        self.progress["value"] = 0
        self.progress["maximum"] = len(to_download) if to_download else 1
        self.progress_label_var.set(f"0 / {len(to_download)}")

        self.last_failed = []
        new_downloads = set()

        max_workers = max(1, min(16, self.thread_count_var.get()))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_one, bid, folder, auto_import, self.mirrors): bid
                for bid in to_download
            }

            for future in as_completed(futures):
                if self.cancel_flag:
                    log(self, "⛔ Download cancelled.")
                    break

                bid = futures[future]
                try:
                    beatmap_id, status, used_url = future.result()
                except Exception as e:
                    log(self, f"{bid}: ❌ Error {e}")
                    self.last_failed.append(bid)
                    continue

                if status == "ok":
                    log(self, f"{bid}: ✔ Downloaded")
                    new_downloads.add(bid)
                elif status == "skipped-existing":
                    log(self, f"{bid}: ⏭ Already exists")
                else:
                    log(self, f"{bid}: ❌ Failed on all mirrors")
                    self.last_failed.append(bid)

                self.progress["value"] += 1
                done = int(self.progress["value"])
                total = int(self.progress["maximum"])
                self.progress_label_var.set(f"{done} / {total}")

        if new_downloads:
            downloaded.update(new_downloads)
            save_manifest(folder, downloaded)

        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.retry_btn.config(state="normal" if self.last_failed else "disabled")

        if not self.cancel_flag:
            if self.last_failed:
                log(self, f"⚠ {len(self.last_failed)} downloads failed. You can retry.")
            else:
                log(self, "🎉 All downloads completed successfully!")

    def cancel(self):
        self.cancel_flag = True

    def retry_failed(self):
        if not self.last_failed:
            messagebox.showinfo("Info", "No failed downloads to retry.")
            return

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("Error", "Please select a download folder.")
            return

        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.retry_btn.config(state="disabled")
        self.progress["value"] = 0
        self.progress_label_var.set("0 / 0")
        self.cancel_flag = False

        self.thread = threading.Thread(
            target=self.worker,
            args=(self.url_entry.get().strip(), folder, self.auto_import_var.get(), True),
            daemon=True
        )
        self.thread.start()

    def verify_threaded(self):
        if self.thread and self.thread.is_alive():
            return

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("Error", "Please select a download folder.")
            return

        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="disabled")
        self.retry_btn.config(state="disabled")

        # reset bar for verify
        self.progress["value"] = 0
        self.progress_label_var.set("Verifying...")

        self.thread = threading.Thread(
            target=self.verify_existing,
            daemon=True
        )
        self.thread.start()

    def verify_existing(self):
        folder = self.folder_var.get().strip()
        if not folder:
            log(self, "❌ No folder selected for verification.")
            self.start_btn.config(state="normal")
            self.retry_btn.config(state="normal")
            return

        os.makedirs(folder, exist_ok=True)
        backup_folder = os.path.join(folder, "corrupted_backup")
        os.makedirs(backup_folder, exist_ok=True)

        log(self, "🔍 Verifying existing .osz files...")
        manifest_ids = load_manifest(folder)
        corrupted = []
        missing = []

        total = len(manifest_ids) if manifest_ids else 1
        self.progress["maximum"] = total
        self.progress["value"] = 0

        checked = 0

        for bid in list(manifest_ids):
            name = f"{bid}.osz"
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                log(self, f"{name}: ⚠ Missing file (will be removed from manifest and redownloaded)")
                missing.append(bid)
            else:
                try:
                    with zipfile.ZipFile(path, "r") as zf:
                        bad = zf.testzip()
                        if bad is not None:
                            raise zipfile.BadZipFile(f"Bad file in archive: {bad}")
                        has_osu = any(
                            info.filename.lower().endswith(".osu")
                            for info in zf.infolist()
                        )
                        if not has_osu:
                            raise zipfile.BadZipFile("No .osu files in archive")
                except Exception as e:
                    log(self, f"{name}: ⚠ Corrupted/invalid ({e})")
                    corrupted.append(bid)

            checked += 1
            self.progress["value"] = checked
            self.progress_label_var.set(f"Verifying... {checked} / {total}")

        if missing:
            for bid in missing:
                if bid in manifest_ids:
                    manifest_ids.remove(bid)
            save_manifest(folder, manifest_ids)
            log(self, f"🧹 Removed {len(missing)} missing entries from manifest.")

        to_fix = set(missing + corrupted)
        if not to_fix:
            log(self, "✅ No corrupted or missing .osz files found.")
            self.start_btn.config(state="normal")
            self.retry_btn.config(state="normal")
            self.progress_label_var.set("0 / 0")
            self.progress["value"] = 0
            return

        log(self, f"Found {len(to_fix)} beatmaps to fix. Backing up corrupted and redownloading...")

        # reuse bar for fixing
        self.progress["maximum"] = len(to_fix)
        self.progress["value"] = 0
        fixed_count = 0

        for bid in to_fix:
            name = f"{bid}.osz"
            path = os.path.join(folder, name)
            if os.path.exists(path):
                try:
                    shutil.move(path, os.path.join(backup_folder, name))
                    log(self, f"{name}: moved to backup.")
                except Exception as e:
                    log(self, f"{name}: ❌ Error moving to backup: {e}")
                    continue

            beatmap_id, status, _ = download_one(bid, folder, self.auto_import_var.get(), self.mirrors)
            if status == "ok":
                log(self, f"{bid}: ✔ Redownloaded successfully")
                manifest_ids.add(bid)
            else:
                log(self, f"{bid}: ❌ Redownload failed")

            fixed_count += 1
            self.progress["value"] = fixed_count
            self.progress_label_var.set(f"Fixing... {fixed_count} / {len(to_fix)}")

        save_manifest(folder, manifest_ids)
        log(self, "✅ Verification and repair process finished.")

        self.start_btn.config(state="normal")
        self.retry_btn.config(state="normal")
        self.progress_label_var.set("0 / 0")
        self.progress["value"] = 0

    def open_mirror_settings(self):
        MirrorSettingsWindow(self, self.config_data, self.update_mirrors)

    def update_mirrors(self, mirrors):
        self.mirrors = mirrors

    def check_updates(self):
        log(self, "🔎 Checking for updates...")
        try:
            r = requests.get(
                "https://api.github.com/repos/MisterThs/Osu-Save/releases/latest",
                timeout=10
            )
            if r.status_code != 200:
                log(self, f"❌ GitHub API error: {r.status_code}")
                messagebox.showerror("Error", "Failed to check for updates.")
                return

            data = r.json()
            latest_tag = data.get("tag_name") or data.get("name") or ""
            latest_tag = latest_tag.lstrip("vV")

            def version_tuple(v):
                return tuple(int(x) for x in re.findall(r"\d+", v))

            current = version_tuple(APP_VERSION)
            latest = version_tuple(latest_tag) if latest_tag else current

            if latest > current:
                log(self, f"🆕 New version available: {latest_tag}")
                messagebox.showinfo(
                    "Update available",
                    f"A new version is available: {latest_tag}\n\n"
                    "Download it from:\nhttps://github.com/MisterThs/Osu-Save/releases/latest"
                )
            else:
                log(self, "✅ You are on the latest version.")
                messagebox.showinfo("Up to date", "You are on the latest version.")
        except Exception as e:
            log(self, f"❌ Update check failed: {e}")
            messagebox.showerror("Error", f"Update check failed:\n{e}")

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def start(self):
        if self.thread and self.thread.is_alive():
            return

        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter an osu! user link.")
            return

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("Error", "Please select a download folder.")
            return

        os.makedirs(folder, exist_ok=True)

        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.retry_btn.config(state="disabled")

        self.progress["value"] = 0
        self.progress_label_var.set("0 / 0")
        self.cancel_flag = False
        self.last_failed = []

        self.thread = threading.Thread(
            target=self.worker,
            args=(url, folder, self.auto_import_var.get(), False),
            daemon=True
        )
        self.thread.start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    gui = OsuGUI()
    gui.run()
