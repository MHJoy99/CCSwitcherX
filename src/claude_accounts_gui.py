"""Claude Accounts window (tkinter). Started by claude_accounts.py with no arguments."""
import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import claude_accounts_core as core
import claude_accounts_providers as providers

# --------------------------------------------------------------------------- theme
LIGHT = dict(bg="#f5f4ee", card="#ffffff", card_hover="#faf9f5", border="#e0ddd2", text="#1f1e1d", muted="#6b6a65",
             accent="#c96442", accent_hover="#b5573a", accent_text="#ffffff", btn="#efece2", btn_hover="#e5e1d4",
             good="#2f8f5b", good_bg="#e3f3ea", warn="#a8741a", bad="#c0392b", banner="#fbeee6", team="#4f6fc4",
             provider="#6a52b8")
DARK = dict(bg="#1f1e1d", card="#2a2927", card_hover="#302f2c", border="#3b3936", text="#f0eee6", muted="#a3a099",
            accent="#d97757", accent_hover="#e48a6c", accent_text="#ffffff", btn="#3a3835", btn_hover="#46443f",
            good="#5bbf8a", good_bg="#23382d", warn="#e0a84b", bad="#e5726b", banner="#3a2a22", team="#6c8be0",
            provider="#7d68c9")
FONT = "Segoe UI"


def system_is_dark() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return True


PAL = DARK if system_is_dark() else LIGHT
SCALE = 1.0


def px(n):
    return int(round(n * SCALE))


class Btn(tk.Label):
    """Flat button built from a label so it can follow the theme."""

    def __init__(self, master, text, command, kind="secondary", size=10, padx=16, pady=7, bg_parent=None):
        self.kind, self.command, self.enabled = kind, command, True
        super().__init__(master, text=text, font=(FONT, size, "bold" if kind == "primary" else "normal"),
                         padx=px(padx), pady=px(pady), cursor="hand2")
        self._parent_bg = bg_parent
        self._paint(False)
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<Button-1>", lambda e: self.enabled and self.command())

    def _paint(self, hover):
        p = PAL
        if self.kind == "primary":
            bg, fg = (p["accent_hover"] if hover else p["accent"]), p["accent_text"]
        elif self.kind == "ghost":
            bg, fg = (p["btn"] if hover else (self._parent_bg or p["card"])), p["muted"]
        else:
            bg, fg = (p["btn_hover"] if hover else p["btn"]), p["text"]
        if not self.enabled:
            fg = p["muted"]
        self.configure(bg=bg, fg=fg, cursor="hand2" if self.enabled else "arrow")

    def set_enabled(self, on):
        self.enabled = on
        self._paint(False)


def center(win, parent=None, width=None, height=None):
    win.update_idletasks()
    w, h = width or win.winfo_reqwidth(), height or win.winfo_reqheight()
    if parent is not None:
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    else:
        x, y = (win.winfo_screenwidth() - w) // 2, (win.winfo_screenheight() - h) // 3
    win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")


def dark_titlebar(win):
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if PAL is DARK else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def themed_toplevel(parent, title):
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=PAL["bg"])
    top.transient(parent)
    top.resizable(False, False)
    try:
        top.iconbitmap(str(core.APP_DIR / "icon.ico"))
    except Exception:
        pass
    dark_titlebar(top)
    return top


def ask_text(parent, title, prompt, initial=""):
    top = themed_toplevel(parent, title)
    body = tk.Frame(top, bg=PAL["bg"], padx=px(22), pady=px(18))
    body.pack()
    tk.Label(body, text=prompt, bg=PAL["bg"], fg=PAL["text"], font=(FONT, 10), justify="left",
             wraplength=px(340)).pack(anchor="w")
    entry = tk.Entry(body, font=(FONT, 11), bg=PAL["card"], fg=PAL["text"], insertbackground=PAL["text"], relief="flat",
                     highlightthickness=1, highlightbackground=PAL["border"], highlightcolor=PAL["accent"], width=32)
    entry.pack(fill="x", pady=(px(10), px(14)), ipady=px(5))
    entry.insert(0, initial)
    row = tk.Frame(body, bg=PAL["bg"])
    row.pack(anchor="e")
    out = {"v": None}

    def ok(_=None):
        out["v"] = entry.get().strip() or None
        top.destroy()

    Btn(row, "Cancel", top.destroy, bg_parent=PAL["bg"]).pack(side="left", padx=(0, px(8)))
    Btn(row, "Save", ok, kind="primary").pack(side="left")
    top.bind("<Return>", ok)
    top.bind("<Escape>", lambda e: top.destroy())
    center(top, parent)
    top.grab_set()
    entry.focus_set()
    entry.select_range(0, "end")
    parent.wait_window(top)
    return out["v"]


def fit(text, font, width):
    """Shorten text with an ellipsis so it is at most `width` pixels wide in `font`."""
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


TIER_LABELS = (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("subagent", "subagents"))


def model_groups(models):
    """[("Opus, Sonnet", model), ...]: tiers that share a model are listed together, in tier order."""
    groups = {}
    for tier, label in TIER_LABELS:
        if models.get(tier):
            groups.setdefault(models[tier], []).append(label)
    return [("All models" if len(labels) == 4 else ", ".join(labels), model) for model, labels in groups.items()]


def result_line(row):
    """One test row as (text, colour): "✓  model   3.2 s   detail" or "✗  model   detail"."""
    if row["ok"]:
        return f'✓  {row["model"]}   {row["seconds"]:.1f} s   {row["detail"]}', PAL["good"]
    return f'✗  {row["model"]}   {row["detail"]}', PAL["bad"]


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
           x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class HintEntry(tk.Entry):
    """Entry with a muted placeholder; value() is "" while the placeholder shows. Can mask what is typed."""

    def __init__(self, master, hint="", secret=False, width=34):
        super().__init__(master, font=(FONT, 11), bg=PAL["card"], fg=PAL["text"], insertbackground=PAL["text"],
                         relief="flat", highlightthickness=1, highlightbackground=PAL["border"],
                         highlightcolor=PAL["accent"], width=width)
        self.hint, self.secret, self.hinting, self.reveal = hint, secret, False, False
        self.bind("<FocusIn>", lambda e: self._clear_hint())
        self.bind("<FocusOut>", lambda e: self._show_hint())
        self._show_hint()

    def _mask(self):
        return "•" if self.secret and not self.reveal else ""

    def _show_hint(self):
        if self.hint and not self.hinting and not self.get():
            self.hinting = True
            self.configure(show="", fg=PAL["muted"])
            self.insert(0, self.hint)

    def _clear_hint(self):
        if self.hinting:
            self.hinting = False
            self.delete(0, "end")
            self.configure(fg=PAL["text"], show=self._mask())

    def value(self):
        return "" if self.hinting else self.get().strip()

    def put(self, text):
        self._clear_hint()
        self.delete(0, "end")
        self.insert(0, text)
        self.configure(fg=PAL["text"], show=self._mask())
        if not text and self.focus_get() is not self:
            self._show_hint()

    def set_reveal(self, on):
        self.reveal = on
        if not self.hinting:
            self.configure(show=self._mask())


class ProviderDialog:
    """Add or edit a provider. `existing` is a provider dict from providers.full_state() (it never holds the key)."""

    def __init__(self, app, existing=None):
        self.app, self.existing = app, existing
        self.testing, self.token = False, None
        top = self.top = themed_toplevel(app.root, "Edit provider" if existing else "Add provider")
        body = tk.Frame(top, bg=PAL["bg"], padx=px(22), pady=px(18))
        body.pack()
        heading = f'Edit "{existing["name"]}"' if existing else "Add a provider"
        tk.Label(body, text=heading, bg=PAL["bg"], fg=PAL["text"], font=(FONT, 12, "bold")).pack(anchor="w")

        form = tk.Frame(body, bg=PAL["bg"])
        form.pack(fill="x", pady=(px(10), 0))
        form.columnconfigure(1, weight=1)
        self.name = HintEntry(form, "for example My gateway")
        self.url = HintEntry(form, "https://your-gateway.example.com")
        self.key = HintEntry(form, "saved, leave blank to keep" if existing else "paste the key from your provider",
                             secret=True)
        self.models = {"opus": HintEntry(form, "model name"), "sonnet": HintEntry(form, "model name"),
                       "haiku": HintEntry(form, "model name"), "subagent": HintEntry(form, "blank = same as Haiku")}
        rows = [("Name", self.name), ("Address", self.url), ("API key", self.key),
                ("Opus →", self.models["opus"]), ("Sonnet →", self.models["sonnet"]),
                ("Haiku →", self.models["haiku"]), ("Subagents →", self.models["subagent"])]
        for r, (label, entry) in enumerate(rows):
            gap = px(14) if r == 3 else 0
            tk.Label(form, text=label, bg=PAL["bg"], fg=PAL["text"] if r < 3 else PAL["muted"], font=(FONT, 10)).grid(
                row=r, column=0, sticky="w", padx=(0, px(12)), pady=(gap, 0))
            entry.grid(row=r, column=1, sticky="ew", pady=(gap + px(3), px(3)), ipady=px(4))
        self.show_btn = Btn(form, "show", self.toggle_key, kind="ghost", size=9, padx=8, pady=3, bg_parent=PAL["bg"])
        self.show_btn.grid(row=2, column=2, padx=(px(6), 0))
        tk.Label(body, text="Model names exactly as your gateway lists them.", bg=PAL["bg"], fg=PAL["muted"],
                 font=(FONT, 9)).pack(anchor="w", pady=(px(6), 0))

        self.results = tk.Frame(body, bg=PAL["bg"])
        self.results.pack(fill="x", anchor="w")
        self.error = tk.Label(body, text="", bg=PAL["bg"], fg=PAL["bad"], font=(FONT, 9, "bold"), justify="left",
                              anchor="w", wraplength=px(430))
        self.error.pack(fill="x")
        row = tk.Frame(body, bg=PAL["bg"])
        row.pack(fill="x", pady=(px(14), 0))
        self.test_btn = Btn(row, "Test connection", self.test, bg_parent=PAL["bg"])
        self.test_btn.pack(side="left")
        Btn(row, "Save", self.save, kind="primary").pack(side="right")
        Btn(row, "Cancel", self.cancel, bg_parent=PAL["bg"]).pack(side="right", padx=(0, px(8)))
        top.bind("<Return>", lambda e: self.save())
        top.bind("<Escape>", lambda e: self.cancel())

        if existing:
            self.name.put(existing["name"])
            self.url.put(existing["baseUrl"])
            for tier, entry in self.models.items():
                model = existing["models"].get(tier, "")
                entry.put("" if tier == "subagent" and model == existing["models"].get("haiku") else model)
        center(top, app.root)

    def run(self):
        try:
            self.top.grab_set()
        except tk.TclError:
            pass
        self.name.focus_set()
        self.app.root.wait_window(self.top)

    def cancel(self):
        self.token = None
        self.top.destroy()

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def toggle_key(self):
        reveal = not self.key.reveal
        self.key.set_reveal(reveal)
        self.show_btn.configure(text="hide" if reveal else "show")

    def show_error(self, text):
        self.error.configure(text=text)
        self.top.geometry("")

    def collect(self, need_key):
        """Validate in the order of the fields. Returns (name, url, key, models) or raises core.SwitchError."""
        pid = self.existing["id"] if self.existing else None
        try:
            name = providers._clean_name(self.name.value(), exclude_id=pid)
        except core.SwitchError:
            self.name.focus_set()
            raise
        try:
            url = providers._normalize_url(self.url.value())
        except core.SwitchError:
            self.url.focus_set()
            raise
        key = self.key.value()
        if need_key and not key and not self.existing:
            self.key.focus_set()
            raise core.SwitchError("Enter the API key for this provider.")
        typed = {tier: entry.value() for tier, entry in self.models.items()}
        try:
            models = providers._clean_models(typed)
        except core.SwitchError:
            empty = [t for t in ("opus", "sonnet", "haiku") if not typed[t]]
            (self.models[empty[0]] if empty else self.models["subagent"]).focus_set()
            raise
        return name, url, key, models

    def save(self):
        try:
            name, url, key, models = self.collect(need_key=True)
            was_active = bool(self.existing and self.existing.get("active"))
            if self.existing:
                providers.update_provider(self.existing["id"], name=name, base_url=url, api_key=key or None,
                                          models=models)
            else:
                providers.add_provider(name, url, key, models)
        except core.SwitchError as exc:
            self.show_error(str(exc))
            return
        self.token = None
        self.top.destroy()
        extra = " Claude Code uses the change from its next message." if was_active else ""
        self.app.say(f'Saved provider "{name}".{extra}', "ok")
        self.app.refresh(force=True)

    def add_line(self, text, color, before=None):
        label = tk.Label(self.results, text=text, bg=PAL["bg"], fg=color, font=(FONT, 9), justify="left", anchor="w",
                         wraplength=px(430))
        label.pack(anchor="w", pady=(px(3), 0), **({"before": before} if before else {}))
        self.top.geometry("")
        return label

    def test(self):
        if self.testing:
            return
        try:
            _, url, key, models = self.collect(need_key=False)
        except core.SwitchError as exc:
            self.show_error(str(exc))
            return
        pid = self.existing["id"] if self.existing else None
        if not key and not pid:
            self.key.focus_set()
            self.show_error("Enter the API key for this provider.")
            return
        distinct = list(dict.fromkeys(models[t] for t, _ in TIER_LABELS))
        self.testing = True
        self.token = token = object()
        self.test_btn.configure(text="Testing…")
        self.test_btn.set_enabled(False)
        self.error.configure(text="")
        for child in self.results.winfo_children():
            child.destroy()
        note = self.add_line(f'Testing {len(distinct)} model{"s" if len(distinct) != 1 else ""}…', PAL["muted"])

        def work():
            saved = None if key else providers.get_provider(pid)
            if not key and not saved:
                raise core.SwitchError("That provider is no longer saved.")
            use = key or saved["apiKey"]   # the saved key is only used here, never shown
            for model in distinct:
                row = providers.check_model(url, use, model, deep=True)
                self.app.tasks.put((lambda r, e, row=row: self.got_row(token, note, row), None, None))

        def done(res, exc):
            if self.token is not token or not self.alive():
                return
            self.testing = False
            self.test_btn.configure(text="Test connection")
            self.test_btn.set_enabled(True)
            note.destroy()
            if exc:
                self.show_error(str(exc))
            else:
                self.top.geometry("")

        self.app.run_bg(work, done)

    def got_row(self, token, note, row):
        if self.token is token and self.alive():
            self.add_line(*result_line(row), before=note)


def show_test_results(parent, name, rows):
    """Small themed window with one line per model. Not modal, so background work keeps flowing."""
    top = themed_toplevel(parent, "Test connection")
    body = tk.Frame(top, bg=PAL["bg"], padx=px(22), pady=px(18))
    body.pack()
    tk.Label(body, text=name, bg=PAL["bg"], fg=PAL["text"], font=(FONT, 12, "bold")).pack(anchor="w")
    tk.Label(body, text="Each model was asked to use a tool and carry on, the way Claude Code does.", bg=PAL["bg"],
             fg=PAL["muted"], font=(FONT, 9), wraplength=px(430), justify="left").pack(anchor="w", pady=(px(2), px(8)))
    labels = dict(TIER_LABELS)
    for row in rows:
        text, color = result_line(row)
        tk.Label(body, text=text, bg=PAL["bg"], fg=color, font=(FONT, 10), justify="left", anchor="w",
                 wraplength=px(430)).pack(anchor="w", pady=(px(6), 0))
        tiers = ", ".join(labels[t] for t in row.get("tiers", []))
        if tiers:
            tk.Label(body, text=f"Used for {tiers}", bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 9)).pack(anchor="w")
    Btn(body, "Close", top.destroy, kind="primary").pack(anchor="e", pady=(px(16), 0))
    top.bind("<Return>", lambda e: top.destroy())
    top.bind("<Escape>", lambda e: top.destroy())
    center(top, parent)
    top.focus_set()
    return top


# --------------------------------------------------------------------------- app
class App:
    POLL_MS = 2500

    def __init__(self, root=None):
        global SCALE
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = root or tk.Tk()
        SCALE = self.root.winfo_fpixels("1i") / 96.0
        self.root.title("Claude Accounts")
        self.root.configure(bg=PAL["bg"])
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(str(core.APP_DIR / "icon.ico"))
        except Exception:
            pass
        self.busy = False
        self.waiting = None
        self.confirm_token = None
        self.signature = None
        self.usage = {}
        self.usage_busy = False
        self.tasks = queue.Queue()
        self.status_kind = "muted"
        self.route = {"kind": "account"}
        self.providers = {}
        self.scrollable = False
        self.fonts = {}

        shell = tk.Frame(self.root, bg=PAL["bg"], padx=px(22), pady=px(20), width=px(500))
        shell.pack(fill="both", expand=True)
        tk.Label(shell, text="Claude Accounts", bg=PAL["bg"], fg=PAL["text"], font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(shell, text="Choose what Claude Code uses: a Claude account or another provider.", bg=PAL["bg"],
                 fg=PAL["muted"], font=(FONT, 10)).pack(anchor="w", pady=(px(2), px(14)))
        self.banner_host = tk.Frame(shell, bg=PAL["bg"])
        self.banner_host.pack(fill="x")
        # The cards live in a canvas so a long list scrolls instead of making the window taller than the screen.
        self.list_wrap = tk.Frame(shell, bg=PAL["bg"])
        self.list_wrap.pack(fill="x")
        self.list_canvas = tk.Canvas(self.list_wrap, width=px(456), height=1, bg=PAL["bg"], highlightthickness=0,
                                     yscrollcommand=self.draw_thumb)
        self.list_canvas.pack(fill="x")
        self.list_host = tk.Frame(self.list_canvas, bg=PAL["bg"])
        self.list_canvas.create_window((0, 0), window=self.list_host, anchor="nw", width=px(456))
        self.thumb = tk.Canvas(shell, width=px(6), bg=PAL["bg"], highlightthickness=0, cursor="hand2")
        self.thumb.bind("<Button-1>", self.drag_thumb)
        self.thumb.bind("<B1-Motion>", self.drag_thumb)
        self.root.bind("<MouseWheel>", self.on_wheel)
        footer = tk.Frame(shell, bg=PAL["bg"])
        footer.pack(fill="x", pady=(px(12), 0))
        self.add_btn = Btn(footer, "+  Add account", self.add_account, kind="primary", size=10, padx=16, pady=9)
        self.add_btn.pack(side="left")
        self.provider_btn = Btn(footer, "+  Add provider", self.add_provider, size=10, padx=14, pady=9)
        self.provider_btn.pack(side="left", padx=(px(8), 0))
        self.usage_btn = Btn(footer, "↻  Refresh usage", lambda: self.fetch_usage(manual=True), size=10, padx=14, pady=9)
        self.usage_btn.pack(side="left", padx=(px(8), 0))
        self.status = tk.Label(shell, text="", bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 9), justify="left",
                               wraplength=px(456), anchor="w")
        self.status.pack(fill="x", pady=(px(12), 0))

        self.refresh()
        center(self.root)
        dark_titlebar(self.root)
        self.root.after(self.POLL_MS, self.tick)
        self.root.after(100, self.pump)
        self.root.after(400, self.usage_loop)

    # ---- status + background work
    def say(self, text, kind="muted"):
        color = {"muted": PAL["muted"], "ok": PAL["good"], "warn": PAL["warn"], "bad": PAL["bad"]}[kind]
        self.status.configure(text=text, fg=color)

    def run_bg(self, work, done):
        def runner():
            try:
                self.tasks.put((done, work(), None))
            except Exception as exc:  # shown to the user, never swallowed
                self.tasks.put((done, None, exc))
        threading.Thread(target=runner, daemon=True).start()

    def pump(self):
        try:
            while True:
                done, result, exc = self.tasks.get_nowait()
                done(result, exc)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def set_busy(self, on):
        self.busy = on
        self.add_btn.set_enabled(not on)

    # ---- rendering
    def tick(self):
        if not self.busy:
            try:
                core.recover()
                self.refresh()
            except Exception as exc:
                self.say(f"Could not read the login files: {exc}", "bad")
        self.root.after(self.POLL_MS, self.tick)

    def snapshot(self):
        """providers.full_state(); only accounts if Claude Code's settings.json can't be read (the error is shown)."""
        try:
            return providers.full_state()
        except core.SwitchError as exc:
            st = core.state()
            st["route"], st["providers"] = {"kind": "account"}, []
            for item in st["profiles"]:
                item["inUse"] = item["active"]
            self.say(str(exc), "bad")
            return st

    def refresh(self, force=False):
        st = self.snapshot()  # also keeps saved tokens current
        usage = {p["id"]: p["usage"] for p in st["profiles"]}
        in_use = {p["id"] for p in st["profiles"] if p["inUse"]}
        live = core.read_live()
        profiles = core.list_profiles()
        live_id = core.profile_id(live["oauthAccount"]) if live else None
        route = st["route"]
        sig = (live_id, live["oauthAccount"].get("emailAddress") if live else None, int(time.time() // 300),
               (route["kind"], route.get("id"), route.get("name"), route.get("baseUrl"), route.get("missing")),
               tuple((p["id"], p["name"], p["baseUrl"], tuple(sorted(p["models"].items())), p["keyOk"], p["active"])
                     for p in st["providers"]),
               tuple((p["id"], p["name"], core.refresh_expired(p["claudeAiOauth"]),
                      int(d) if (d := core.refresh_days_left(p["claudeAiOauth"])) is not None else None,
                      (usage[p["id"]] or {}).get("text"), (usage[p["id"]] or {}).get("note"))
                     for p in profiles))
        if sig == self.signature and not force:
            return
        self.signature = sig
        self.usage = usage
        self.route = route
        self.providers = {p["id"]: p for p in st["providers"]}
        self.render(st, live, live_id, profiles, in_use)

    # ---- usage
    def usage_loop(self):
        self.fetch_usage()
        self.root.after(5 * 60 * 1000, self.usage_loop)

    def fetch_usage(self, manual=False):
        if self.usage_busy:
            return
        self.usage_busy = True
        if manual:
            self.say("Refreshing usage…")

        def done(report, exc):
            self.usage_busy = False
            if manual:
                if exc:
                    self.say(f"Could not refresh usage: {exc}", "bad")
                else:
                    idle = [s for s in report.values() if s == "token-expired"]
                    limited = [s for s in report.values() if s == "http-429" or s == "backoff"]
                    note = ""
                    if limited:
                        note = " The server asked us to slow down, so some numbers are older."
                    elif idle:
                        note = (" Accounts you haven't used for a few hours can't be read without signing in "
                                "again, so those show their last known numbers.")
                    self.say("Usage updated." + note, "ok" if not (limited or idle) else "muted")
            try:
                self.refresh(force=True)
            except Exception:
                pass

        self.run_bg(lambda: core.refresh_usage(force=manual), done)

    def render_usage(self, parent, u):
        row = tk.Frame(parent, bg=PAL["card"])
        if not u:
            tk.Label(row, text="No usage numbers yet. They appear once this account has been used.",
                     bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9), wraplength=px(250), justify="left").pack(anchor="w")
            return row
        width = px(104)
        for label, w in (("Session", u["session"]), ("Week", u["week"])):
            if not w:
                continue
            col = tk.Frame(row, bg=PAL["card"])
            col.pack(side="left", padx=(0, px(18)), anchor="n")
            pct = 0 if w["over"] else w["pct"]
            color = PAL["good"] if pct < 70 else PAL["warn"] if pct < 90 else PAL["bad"]
            tk.Label(col, text=f'{label} {"reset" if w["over"] else str(pct) + "%"}', bg=PAL["card"], fg=PAL["text"],
                     font=(FONT, 9, "bold")).pack(anchor="w")
            bar = tk.Canvas(col, width=width, height=px(5), bg=PAL["border"], highlightthickness=0)
            bar.create_rectangle(0, 0, max(px(2), width * pct / 100) if pct else 0, px(5), fill=color, outline="")
            bar.pack(anchor="w", pady=(px(3), px(2)))
            caption = ""
            if not w["over"] and w["resetsAt"]:
                left = w["resetsAt"] - time.time()
                caption = f"resets in {core.fmt_duration(left)}" if left < 86400 else f"resets {core.fmt_clock(w['resetsAt'])}"
            tk.Label(col, text=caption, bg=PAL["card"], fg=PAL["muted"], font=(FONT, 8)).pack(anchor="w")
        if u["note"]:
            tk.Label(row, text=u["note"], bg=PAL["card"], fg=PAL["muted"], font=(FONT, 8, "italic")).pack(
                side="left", anchor="n", pady=(px(1), 0))
        return row

    def font(self, size, weight="normal"):
        key = (size, weight)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=FONT, size=size, weight=weight)
        return self.fonts[key]

    def max_height(self):
        """The tallest the window may be: 80% of the screen, minus the title bar and borders."""
        return int(self.root.winfo_screenheight() * 0.8) - px(48)

    def render(self, st, live, live_id, profiles, in_use):
        frac = self.list_canvas.yview()[0]
        for host in (self.banner_host, self.list_host):
            for child in host.winfo_children():
                child.destroy()
        saved_ids = {p["id"] for p in profiles}
        self.render_route_banner(st["route"])
        if live and live_id not in saved_ids:
            self.render_banner(live)
        self.render_section("ACCOUNTS", first=True)
        if not profiles and not live:
            box = tk.Frame(self.list_host, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"],
                           padx=px(18), pady=px(18))
            box.pack(fill="x", pady=px(5))
            tk.Label(box, text="Not signed in", bg=PAL["card"], fg=PAL["text"], font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(box, text="Add an account to sign in. It will be saved here so you can switch back anytime.",
                     bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9), wraplength=px(400), justify="left").pack(anchor="w", pady=(px(4), 0))
        for p in profiles:
            self.render_card(p, active=(p["id"] == live_id), in_use=(p["id"] in in_use))
        self.render_section("PROVIDERS")
        for p in st["providers"]:
            self.render_provider_card(p)
        if not st["providers"]:
            box = tk.Frame(self.list_host, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"],
                           padx=px(14), pady=px(12))
            box.pack(fill="x", pady=px(5))
            tk.Label(box, text="Use other models, for example your own gateway. Click Add provider.", bg=PAL["card"],
                     fg=PAL["muted"], font=(FONT, 9), wraplength=px(400), justify="left").pack(anchor="w")
        self.fit_list(frac)

    def render_section(self, title, first=False):
        spaced = " ".join(title)
        tk.Label(self.list_host, text=spaced, bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 8, "bold"), anchor="w").pack(
            fill="x", pady=(px(2) if first else px(12), 0))

    def fit_list(self, frac):
        """Size the card area to its content; when that would make the window too tall, scroll instead."""
        canvas = self.list_canvas
        self.list_host.update_idletasks()
        self.inner_h = inner = self.list_host.winfo_reqheight()
        canvas.configure(height=inner, scrollregion=(0, 0, px(456), inner))
        self.root.update_idletasks()
        over = self.root.winfo_reqheight() - self.max_height()
        self.scrollable = over > 0
        if self.scrollable:
            canvas.configure(height=max(px(160), inner - over))
            self.thumb.place(in_=self.list_wrap, relx=1.0, x=px(8), y=0, relheight=1.0)
            canvas.yview_moveto(frac)
            self.draw_thumb(*canvas.yview())
        else:
            self.thumb.place_forget()
            canvas.yview_moveto(0)
        self.root.update_idletasks()
        self.root.geometry("")  # let the window grow or shrink with the list

    def draw_thumb(self, first, last):
        self.thumb.delete("all")
        first, last = float(first), float(last)
        if not self.scrollable or last - first >= 0.999:
            return
        height = int(float(self.list_canvas.cget("height")))
        top = height * first
        bottom = max(height * last, top + px(28))
        round_rect(self.thumb, 0, top, px(6), min(bottom, height), px(3), fill=PAL["btn_hover"], outline="")

    def drag_thumb(self, event):
        first, last = self.list_canvas.yview()
        height = max(1, int(float(self.list_canvas.cget("height"))))
        self.list_canvas.yview_moveto(event.y / height - (last - first) / 2)

    def on_wheel(self, event):
        if not self.scrollable:
            return
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return
        except (tk.TclError, KeyError, AttributeError):
            return
        first = self.list_canvas.yview()[0]
        self.list_canvas.yview_moveto(first - (event.delta / 120) * px(64) / max(1, self.inner_h))

    def render_notice(self, text, tone="info", button=None):
        warn = tone != "info"
        bg = PAL["banner"] if warn else PAL["card"]
        box = tk.Frame(self.banner_host, bg=bg, padx=px(14), pady=px(12),
                       highlightthickness=0 if warn else 1, highlightbackground=PAL["border"])
        box.pack(fill="x", pady=(0, px(8)))
        if button:
            Btn(box, button[0], button[1], kind="primary", padx=14, pady=6).pack(side="right", padx=(px(10), 0))
        tk.Label(box, text=text, bg=bg, fg=PAL["warn"] if tone == "warn" else PAL["text"], font=(FONT, 9, "bold" if warn else "normal"),
                 wraplength=px(button and 330 or 428), justify="left", anchor="w").pack(side="left", fill="x", expand=True)

    def render_route_banner(self, route):
        if route["kind"] == "provider" and route.get("missing"):
            self.render_notice("The provider in use was removed.", "warn", ("Switch back", self.leave_provider))
        elif route["kind"] == "provider":
            self.render_notice(f'Using {route["name"]} (provider). Your Claude subscription is not used until you '
                               f"switch back to an account.")
        elif route["kind"] == "custom":
            self.render_notice(f'Claude Code is pointed at {route["baseUrl"]}, set outside this app.', "warn")

    def render_banner(self, live):
        info = core.describe(live)
        box = tk.Frame(self.banner_host, bg=PAL["banner"], padx=px(14), pady=px(12))
        box.pack(fill="x", pady=(0, px(8)))
        text = tk.Frame(box, bg=PAL["banner"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text="Current login isn't saved yet", bg=PAL["banner"], fg=PAL["text"],
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(text, text=f'{info["email"]} · {info["detail"]}', bg=PAL["banner"], fg=PAL["muted"],
                 font=(FONT, 9)).pack(anchor="w")
        Btn(box, "Save it", lambda: self.save_current(live), kind="primary", padx=14, pady=6).pack(side="right")

    def render_card(self, p, active, in_use):
        info = core.describe(p)
        expired = core.refresh_expired(p["claudeAiOauth"]) and not active
        edge = PAL["accent"] if in_use else PAL["border"]
        outer = tk.Frame(self.list_host, bg=edge, padx=1, pady=1)
        outer.pack(fill="x", pady=px(5))
        card = tk.Frame(outer, bg=PAL["card"], padx=px(14), pady=px(12))
        card.pack(fill="x")

        size = px(44)
        avatar = tk.Canvas(card, width=size, height=size, bg=PAL["card"], highlightthickness=0)
        avatar.create_oval(2, 2, size - 2, size - 2, fill=PAL["team"] if info["team"] else PAL["accent"], outline="")
        avatar.create_text(size // 2, size // 2, text=(p["name"][:1] or "?").upper(), fill="#ffffff",
                           font=(FONT, 14, "bold"))
        avatar.grid(row=0, column=0, rowspan=2, padx=(0, px(12)), sticky="n")

        tk.Label(card, text=p["name"], bg=PAL["card"], fg=PAL["text"], font=(FONT, 12, "bold"), anchor="w").grid(
            row=0, column=1, sticky="sw")
        tk.Label(card, text=f'{info["email"]} · {info["detail"]}', bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9),
                 anchor="w").grid(row=1, column=1, sticky="nw")
        self.render_usage(card, self.usage.get(p["id"])).grid(row=2, column=1, sticky="nw", pady=(px(8), 0))
        days = core.refresh_days_left(p["claudeAiOauth"])
        warning = None
        if expired:
            warning = ("Sign-in expired. Use Add account to sign in again.", PAL["bad"])
        elif not active and days is not None and days < 7:
            when = "today" if days < 1 else f"in {int(days)} day{'s' if int(days) != 1 else ''}"
            warning = (f"Saved sign-in expires {when}. Use it before then.", PAL["warn"])
        if warning:
            tk.Label(card, text=warning[0], bg=PAL["card"], fg=warning[1], font=(FONT, 9, "bold"), anchor="w").grid(
                row=3, column=1, sticky="nw", pady=(px(6), 0))
        avatar.grid_configure(rowspan=4 if warning else 3)

        right = tk.Frame(card, bg=PAL["card"])
        right.grid(row=0, column=2, rowspan=4 if warning else 3, sticky="ne")
        card.columnconfigure(1, weight=1)
        if in_use:
            tk.Label(right, text="IN USE", bg=PAL["good_bg"], fg=PAL["good"], font=(FONT, 8, "bold"),
                     padx=px(9), pady=px(3)).pack(side="left", padx=(0, px(6)))
        else:
            sw = Btn(right, "Switch", lambda pid=p["id"]: self.switch(pid), padx=14, pady=6)
            sw.pack(side="left", padx=(0, px(6)))
            if expired:
                sw.set_enabled(False)
        more = Btn(right, "⋯", lambda b=None, pid=p["id"], n=p["name"]: None, kind="ghost", size=12, padx=8, pady=2)
        more.pack(side="left")
        more.command = lambda m=more, pid=p["id"], n=p["name"], a=active: self.card_menu(m, pid, n, a)

    def render_provider_card(self, p):
        active = p["active"]
        outer = tk.Frame(self.list_host, bg=PAL["accent"] if active else PAL["border"], padx=1, pady=1)
        outer.pack(fill="x", pady=px(5))
        card = tk.Frame(outer, bg=PAL["card"], padx=px(14), pady=px(12))
        card.pack(fill="x")

        size = px(44)
        avatar = tk.Canvas(card, width=size, height=size, bg=PAL["card"], highlightthickness=0)
        round_rect(avatar, 2, 2, size - 2, size - 2, px(10), fill=PAL["provider"], outline="")
        avatar.create_text(size // 2, size // 2, text=(p["name"][:1] or "?").upper(), fill="#ffffff",
                           font=(FONT, 14, "bold"))
        avatar.grid(row=0, column=0, rowspan=3, padx=(0, px(12)), sticky="n")

        tk.Label(card, text=p["name"], bg=PAL["card"], fg=PAL["text"], font=(FONT, 12, "bold"), anchor="w").grid(
            row=0, column=1, sticky="sw")
        tk.Label(card, text=p["host"], bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9), anchor="w").grid(
            row=1, column=1, sticky="nw")
        lines = tk.Frame(card, bg=PAL["card"])
        lines.grid(row=2, column=1, columnspan=2, sticky="nw", pady=(px(6), 0))
        small = self.font(9)
        for label, model in model_groups(p["models"]):
            tk.Label(lines, text=fit(f"{label} → {model}", small, px(366)), bg=PAL["card"], fg=PAL["muted"],
                     font=(FONT, 9), anchor="w").pack(anchor="w")

        right = tk.Frame(card, bg=PAL["card"])
        right.grid(row=0, column=2, rowspan=2, sticky="ne")
        card.columnconfigure(1, weight=1)
        if active:
            tk.Label(right, text="IN USE", bg=PAL["good_bg"], fg=PAL["good"], font=(FONT, 8, "bold"),
                     padx=px(9), pady=px(3)).pack(side="left", padx=(0, px(6)))
        else:
            Btn(right, "Switch", lambda pid=p["id"]: self.switch_provider(pid), padx=14, pady=6).pack(
                side="left", padx=(0, px(6)))
        more = Btn(right, "⋯", lambda: None, kind="ghost", size=12, padx=8, pady=2)
        more.pack(side="left")
        more.command = lambda m=more, item=p: self.provider_menu(m, item)

    def popup(self, anchor, entries):
        menu = tk.Menu(self.root, tearoff=0, bg=PAL["card"], fg=PAL["text"], activebackground=PAL["accent"],
                       activeforeground="#ffffff", bd=0, font=(FONT, 10))
        for label, command in entries:
            menu.add_command(label=label, command=command)
        menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())

    def card_menu(self, anchor, pid, name, active):
        self.popup(anchor, [("Rename…", lambda: self.rename(pid, name)),
                            ("Remove from list…", lambda: self.remove(pid, name, active))])

    def provider_menu(self, anchor, p):
        pid, name = p["id"], p["name"]
        self.popup(anchor, [("Edit…", lambda: self.edit_provider(pid)),
                            ("Test connection", lambda: self.check_provider(pid, name)),
                            ("Remove…", lambda: self.remove_provider(pid, name, p["active"]))])

    # ---- actions
    def save_current(self, live):
        suggested = core.suggest_name(live["oauthAccount"])
        name = ask_text(self.root, "Save account", "Name this account (for example Personal or Team):", suggested)
        if not name:
            return
        try:
            saved = core.save_profile(live, name)
            self.say(f'Saved "{saved["name"]}".', "ok")
        except core.SwitchError as exc:
            self.say(str(exc), "bad")
        self.refresh(force=True)

    def rename(self, pid, current):
        name = ask_text(self.root, "Rename account", "New name:", current)
        if not name or name == current:
            return
        try:
            core.rename_profile(pid, name)
        except core.SwitchError as exc:
            self.say(str(exc), "bad")
        self.refresh(force=True)

    def remove(self, pid, name, active):
        extra = "\n\nYou stay signed in with it right now; only the saved copy is removed." if active else \
            "\n\nYou can add it back later by signing in again."
        if not messagebox.askyesno("Remove account", f'Remove "{name}" from this list?{extra}', parent=self.root):
            return
        core.delete_profile(pid)
        self.say(f'Removed "{name}".', "muted")
        self.refresh(force=True)

    def switch(self, pid):
        if self.busy:
            return
        target = core.get_profile(pid)
        if not target:
            self.refresh(force=True)
            return
        self.set_busy(True)
        self.say(f'Switching to "{target["name"]}"…')

        def work():
            before = core.read_live()
            res = providers.use_account(pid)  # also leaves a provider if one is in use
            res["accountChanged"] = res["changed"] and not (before and core.profile_id(before["oauthAccount"]) == pid)
            return res

        def done(res, exc):
            self.set_busy(False)
            if exc:
                self.say(str(exc), "bad")
                self.refresh(force=True)
                return
            if not res["changed"]:
                self.say(f'"{res["name"]}" is already the active account.', "muted")
            elif not res["accountChanged"]:
                self.say(f'Back on {res["name"]}. Your next message uses it.', "ok")
            else:
                note = f' (your previous login was saved as "{res["autosaved"]}")' if res["autosaved"] else ""
                self.say(f'Now using "{res["name"]}"{note}. Confirming…', "ok")
                self.confirm_in_background(pid, res["name"], note)
            self.refresh(force=True)

        self.run_bg(work, done)

    # ---- provider actions
    def add_provider(self):
        ProviderDialog(self).run()

    def edit_provider(self, pid):
        p = self.providers.get(pid)
        if not p:
            self.refresh(force=True)
            return
        ProviderDialog(self, p).run()

    def switch_provider(self, pid):
        if self.busy:
            return
        target = self.providers.get(pid)
        if not target:
            self.refresh(force=True)
            return
        self.set_busy(True)
        self.say(f'Switching to {target["name"]}…')

        def done(res, exc):
            self.set_busy(False)
            if exc:
                self.say(str(exc), "bad")
                self.refresh(force=True)
                return
            name = res["name"]
            msg = f"Now using {name}. Chats you already have open follow on their next message."
            self.say(msg, "ok")
            self.root.after(int(res["settleSeconds"] * 1000), lambda: self.say(
                f"Ready: your next message uses {name}.", "ok") if self.status.cget("text") == msg else None)
            self.refresh(force=True)

        self.run_bg(lambda: providers.activate(pid), done)

    def leave_provider(self):
        if self.busy:
            return
        self.set_busy(True)
        self.say("Switching back to your Claude account…")

        def done(name, exc):
            self.set_busy(False)
            if exc:
                self.say(str(exc), "bad")
            else:
                self.say("Back on your Claude account. Your next message uses it.", "ok")
            self.refresh(force=True)

        self.run_bg(providers.deactivate, done)

    def remove_provider(self, pid, name, active):
        extra = "\n\nClaude Code switches back to your Claude account first." if active else ""
        if not messagebox.askyesno("Remove provider", f'Remove provider "{name}"? Its saved key is deleted.{extra}',
                                   parent=self.root):
            return
        try:
            providers.delete_provider(pid)
        except core.SwitchError as exc:
            self.say(str(exc), "bad")
            self.refresh(force=True)
            return
        self.say(f'Removed provider "{name}".' + (" Claude Code is back on your Claude account." if active else ""))
        self.refresh(force=True)

    def check_provider(self, pid, name):
        self.say(f"Testing {name}…")

        def done(rows, exc):
            if exc:
                self.say(str(exc), "bad")
                return
            failed = sum(1 for r in rows if not r["ok"])
            if failed:
                self.say(f"{name}: {failed} of {len(rows)} model{'s' if len(rows) != 1 else ''} did not pass.", "warn")
            else:
                self.say(f"{name}: every model works.", "ok")
            show_test_results(self.root, name, rows)

        self.run_bg(lambda: providers.test_provider(pid, deep=True), done)

    def confirm_in_background(self, pid, name, note):
        """The switch is already in effect; double-check it stuck and that the claude tool agrees."""
        target = core.get_profile(pid)
        self.confirm_token = token = object()

        def work():
            stuck = core.settle_check(pid)
            return stuck, (core.verify_active(target["oauthAccount"]) if stuck else None)

        def done(res, exc):
            if self.confirm_token is not token:
                return  # the user did something else in the meantime
            if exc or not res[0]:
                self.say("A running Claude Code session keeps putting the old account back. Close its chats "
                         "(or reload the VS Code window) and switch again.", "warn")
            elif res[1] is False:
                self.say(f'Switched, but the claude command-line tool did not confirm "{name}". Try again.', "warn")
            else:
                self.say(f'Now using "{name}"{note}. Open chats switch on their next message; '
                         f"if one doesn't, start a new chat.", "ok")

        self.run_bg(work, done)

    def add_account(self):
        if self.busy:
            return
        if not core.claude_exe():
            self.say("The `claude` command was not found, so sign-in can't be started from here.", "bad")
            return
        self.set_busy(True)
        self.say("Waiting for you to finish signing in in the browser…")
        cancel = threading.Event()
        self.show_waiting_window(cancel)
        self.run_bg(lambda: core.add_login(cancelled=cancel.is_set), lambda res, exc: self.after_login(res, exc))

    def show_waiting_window(self, cancel):
        top = themed_toplevel(self.root, "Add account")
        body = tk.Frame(top, bg=PAL["bg"], padx=px(24), pady=px(20))
        body.pack()
        tk.Label(body, text="Sign in with the account you want to add", bg=PAL["bg"], fg=PAL["text"],
                 font=(FONT, 12, "bold")).pack(anchor="w")
        steps = ("A browser tab opened on the Claude sign-in page.\n"
                 "1. Pick the account to add. If the page shows the wrong one, choose “Switch account”.\n"
                 "2. For a Team account, choose the team workspace if asked.\n"
                 "3. Press Authorize, then come back here. This window closes by itself.")
        tk.Label(body, text=steps, bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 10), justify="left",
                 wraplength=px(380)).pack(anchor="w", pady=(px(8), px(14)))
        dots = tk.Label(body, text="Waiting", bg=PAL["bg"], fg=PAL["accent"], font=(FONT, 10, "bold"))
        dots.pack(anchor="w")
        Btn(body, "Cancel", cancel.set, bg_parent=PAL["bg"]).pack(anchor="e", pady=(px(14), 0))
        top.protocol("WM_DELETE_WINDOW", cancel.set)
        self.waiting = top
        state = {"tick": 0}

        def animate():
            if self.waiting is not top:
                return
            state["tick"] += 1
            dots.configure(text="Waiting" + "." * (state["tick"] % 4))
            top.after(500, animate)

        center(top, self.root)
        top.after(500, animate)

    def after_login(self, res, exc):
        if getattr(self, "waiting", None) is not None:
            self.waiting.destroy()
            self.waiting = None
        self.set_busy(False)
        if exc:
            self.say(str(exc), "bad")
            self.refresh(force=True)
            return
        status = res["status"]
        if status == "cancelled":
            self.say("Sign-in cancelled. Your previous account is still active.")
        elif status in ("failed", "timeout"):
            detail = f' ({res["detail"]})' if res.get("detail") else ""
            self.say(f"Sign-in did not finish{detail}. Your previous account is still active.", "warn")
        elif status == "same":
            self.say(f'That was the same account as "{res["name"]}". To add a different one, choose '
                     f"“Switch account” on the browser's sign-in page.", "warn")
        elif status == "known":
            self.say(f'Signed in as "{res["name"]}" (already saved, sign-in refreshed).', "ok")
        else:  # new: already saved under a suggested name, offer a better one
            name = ask_text(self.root, "Name this account", "Signed in and saved. Name this account (for example "
                            "Personal or Team):", res["name"])
            if name and name != res["name"]:
                try:
                    core.rename_profile(res["id"], name)
                    res["name"] = name
                except core.SwitchError as exc2:
                    self.say(f'{exc2} Kept the name "{res["name"]}".', "warn")
            self.say(f'Added "{res["name"]}" and signed in with it. Open chats switch on their next message; '
                     f"if one doesn't, start a new chat.", "ok")
        self.refresh(force=True)

    def run(self):
        self.root.mainloop()
