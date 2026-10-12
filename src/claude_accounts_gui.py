"""Claude Accounts window (tkinter). Started by claude_accounts.py with no arguments."""
import ctypes
import math
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox

import claude_accounts_core as core
import claude_accounts_providers as providers

# --------------------------------------------------------------------------- theme
def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    """Colour a moved fraction t of the way toward colour b ("#rrggbb" in, "#rrggbb" out)."""
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(_rgb(a), _rgb(b)))


LIGHT = dict(bg="#f5f4ee", card="#ffffff", card_hover="#faf9f5", border="#e0ddd2", text="#1f1e1d", muted="#6b6a65",
             accent="#c96442", accent_hover="#b5573a", accent_text="#ffffff", btn="#efece2", btn_hover="#e5e1d4",
             good="#2f8f5b", good_bg="#e3f3ea", warn="#a8741a", bad="#c0392b", banner="#fbeee6", team="#4f6fc4",
             provider="#6a52b8", track="#dedacb", row_hover="#ffffff")
DARK = dict(bg="#1f1e1d", card="#2a2927", card_hover="#302f2c", border="#3b3936", text="#f0eee6", muted="#a3a099",
            accent="#d97757", accent_hover="#e48a6c", accent_text="#ffffff", btn="#3a3835", btn_hover="#46443f",
            good="#5bbf8a", good_bg="#23382d", warn="#e0a84b", bad="#e5726b", banner="#3a2a22", team="#6c8be0",
            provider="#7d68c9", track="#4c4944", row_hover="#2d2c29")
for _p, _t in ((LIGHT, 0.10), (DARK, 0.15)):   # derived: accent-tinted hero surface, hairlines, chips
    _p["hero"] = mix(_p["card"], _p["accent"], _t)
    _p["hero_edge"] = mix(_p["border"], _p["accent"], 0.55)
    _p["hair"] = mix(_p["border"], _p["bg"], 0.45)
    _p["chip"] = mix(_p["btn"], _p["card"], 0.25)
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
    on_paint = None   # optional callback(bg): lets a rounded wrapper (rbtn) repaint its pill

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
        elif self.kind == "soft":
            bg, fg = (p["btn_hover"] if hover else p["btn"]), p["accent"]
        elif self.kind == "ghost":
            bg, fg = (p["btn"] if hover else (self._parent_bg or p["card"])), p["muted"]
        else:
            bg, fg = (p["btn_hover"] if hover else p["btn"]), p["text"]
        if not self.enabled:
            fg = p["muted"]
        self.configure(bg=bg, fg=fg, cursor="hand2" if self.enabled else "arrow")
        if self.on_paint:
            self.on_paint(bg)

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
    """Rounded rectangle as a smooth polygon: true circular corners (sampled arcs) plus midpoints on the straight
    edges so the spline keeps them flat. Tk canvases have no anti-aliasing; this is the smoothest pure-tk shape."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    if r < 2:
        return canvas.create_polygon(x1, y1, x2, y1, x2, y2, x1, y2, **kw)
    steps = max(3, int(r // 2))
    pts = []
    for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(steps + 1):
            ang = math.radians(a0 + 90 * i / steps)
            pts += [cx + r * math.cos(ang), cy + r * math.sin(ang)]
    return canvas.create_polygon(pts, smooth=True, splinesteps=4, **kw)


# --------------------------------------------------------------------------- drawing helpers
def walk_widgets(w):
    for child in w.winfo_children():
        yield child
        yield from walk_widgets(child)


def retone(root, old, new):
    """Swap one surface colour for another on every descendant (used for the hover tint)."""
    for w in walk_widgets(root):
        if isinstance(w, Btn):
            if w._parent_bg == old:
                w._parent_bg = new
            w._paint(False)
            continue
        try:
            if w.cget("bg") == old:
                w.configure(bg=new)
            if isinstance(w, tk.Canvas):
                for item in w.find_all():
                    if w.type(item) in ("polygon", "oval") and w.itemcget(item, "fill") == old:
                        w.itemconfigure(item, fill=new)
        except tk.TclError:
            pass


class Surface(tk.Frame):
    """A smooth rounded panel. `body` is the frame to put content in; the panel redraws itself to fit it."""

    def __init__(self, master, fill, hover=None, radius=14, outline=None, padx=14, pady=12, page=None):
        page = page or PAL["bg"]
        super().__init__(master, bg=page)
        self.fill, self.hover_fill, self.cur, self.outline = fill, hover, fill, outline
        self.radius, self.hovering = px(radius), False
        self.back = tk.Canvas(self, bg=page, highlightthickness=0, bd=0)
        self.back.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="x", padx=px(padx), pady=px(pady))
        self.bind("<Configure>", self.draw)

    def draw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 6 or h < 6:
            return
        self.back.delete("all")
        round_rect(self.back, 1, 1, w - 2, h - 2, min(self.radius, h // 2, w // 2), fill=self.cur,
                   outline=self.outline or "", width=1)

    def set_fill(self, color):
        old, self.cur = self.cur, color
        self.body.configure(bg=color)
        retone(self.body, old, color)
        self.draw()

    def arm(self):
        """Whole-panel hover feedback: enter/leave on every child, decided by where the pointer really is."""
        if not self.hover_fill:
            return
        for w in (self, *walk_widgets(self)):
            w.bind("<Enter>", lambda e: self.hover(True), add="+")
            w.bind("<Leave>", lambda e: self.after(40, self._check), add="+")

    def _check(self):
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (tk.TclError, KeyError):
            return
        while w is not None and w is not self:
            w = getattr(w, "master", None)
        self.hover(w is self)

    def hover(self, on):
        if on != self.hovering and self.winfo_exists():
            self.hovering = on
            self.set_fill(self.hover_fill if on else self.fill)


def pill(parent, text, fg, bg, page, size=8, weight="bold", padx=9, pady=2, dot=None):
    """Small rounded tag. The text is a real Label (inside a canvas) so it stays readable and testable."""
    c = tk.Canvas(parent, bg=page, highlightthickness=0, bd=0)
    lab = tk.Label(c, text=text, bg=bg, fg=fg, font=(FONT, size, weight), padx=0, pady=px(pady))
    lw, lh = lab.winfo_reqwidth(), lab.winfo_reqheight()
    gap, dotw = px(padx), (px(11) if dot else 0)
    w, h = lw + 2 * gap + dotw, lh
    c.configure(width=w, height=h)
    round_rect(c, 0, 0, w - 1, h - 1, h // 2, fill=bg, outline="")
    if dot:
        d = px(6)
        c.create_oval(gap, h // 2 - d // 2, gap + d, h // 2 + d // 2, fill=dot, outline="")
    c.create_window(gap + dotw + lw // 2, h // 2, window=lab)
    return c


def rbtn(parent, text, command, kind="secondary", size=10, padx=14, pady=7, parent_bg=None, radius=9, width=None):
    """A Btn drawn on a rounded pill. Returns the Btn; .pack/.grid act on the pill, so it is a drop-in.
    `width` (px at 100%) fixes the pill's total width so buttons line up from row to row; it never goes below the text."""
    pbg = parent_bg or parent.cget("bg")
    wrap = tk.Canvas(parent, bg=pbg, highlightthickness=0, bd=0)
    b = Btn(wrap, text, command, kind=kind, size=size, padx=padx, pady=pady, bg_parent=pbg)
    m = px(radius)
    w, h = max(b.winfo_reqwidth() + 2 * m, px(width) if width else 0), b.winfo_reqheight()
    wrap.configure(width=w, height=h)
    shape = round_rect(wrap, 0, 0, w - 1, h - 1, min(m, h // 2), fill=b.cget("bg"), outline="")
    wrap.create_window(w // 2, h // 2, window=b, width=w - 2 * m, height=h)
    b.on_paint = lambda bg: wrap.itemconfigure(shape, fill=bg)
    b._paint(False)
    for ev, fn in (("<Enter>", lambda e: b._paint(True)), ("<Leave>", lambda e: b._paint(False)),
                   ("<Button-1>", lambda e: b.enabled and b.command())):
        wrap.bind(ev, fn)
    b.pack, b.grid, b.pack_forget = wrap.pack, wrap.grid, wrap.pack_forget
    return b


def meter(parent, width, height, pct, color, track, page):
    """Rounded progress bar: smooth track plus a smooth fill (never thinner than a full cap)."""
    c = tk.Canvas(parent, width=width, height=height, bg=page, highlightthickness=0, bd=0)
    r = height // 2
    round_rect(c, 0, 0, width - 1, height - 1, r, fill=track, outline="")
    if pct and pct > 0:
        fw = min(width, max(height, int(width * min(pct, 100) / 100)))
        round_rect(c, 0, 0, fw - 1, height - 1, r, fill=color, outline="")
    return c


def monogram(parent, letter, color, size, page, radius=0.32):
    s = px(size)
    c = tk.Canvas(parent, width=s, height=s, bg=page, highlightthickness=0, bd=0)
    round_rect(c, 0, 0, s - 1, s - 1, int(s * radius), fill=color, outline="")
    c.create_text(s // 2, s // 2 - 1, text=(letter or "?").upper(), fill="#ffffff", font=(FONT, int(size * 0.34) + 1, "bold"))
    return c


def level_color(pct):
    return PAL["good"] if pct < 70 else PAL["warn"] if pct < 90 else PAL["bad"]


def clock_label(epoch):
    """"4:00 PM" today, "tomorrow 4:00 PM", else "Fri 4:00 PM" (rounded to the minute, like core.fmt_clock)."""
    t = datetime.fromtimestamp(round(epoch / 60) * 60)
    clock = t.strftime("%I:%M %p").lstrip("0")
    days = (t.date() - datetime.now().date()).days
    return clock if days <= 0 else f"tomorrow {clock}" if days == 1 else t.strftime("%a ") + clock


def window_info(label, w):
    """What one usage window should say: (big text, colour, bar pct, caption, caption colour, caption bold)."""
    p = PAL
    if w["over"]:
        return "Fresh", p["good"], 0, f"Full {label.lower()} available", p["good"], False
    pct = w["pct"] or 0
    if pct >= 100:
        until = f"Full until {clock_label(w['resetsAt'])}" if w.get("resetsAt") else "Full"
        return f"{pct}%", p["bad"], 100, until, p["bad"], True
    cap = ""
    if w.get("resetsAt"):
        left = w["resetsAt"] - time.time()
        cap = f"Resets in {core.fmt_duration(left)}" if left < 86400 else f"Resets {core.fmt_clock(w['resetsAt'])}"
    return f"{pct}%", (p["text"] if pct < 70 else level_color(pct)), pct, cap, p["muted"], False


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
# Fixed column grid of a list row (px at 100%): avatar | name/status zone | meters | buttons. Every row uses the same
# widths, so meters and buttons line up; only the name is shortened (with an ellipsis) to fit its zone.
ROW_INNER, ROW_AVATAR, ROW_METERS, ROW_BTN_W, ROW_MORE_W = 432, 44, 126, 94, 26
ROW_RIGHT = ROW_BTN_W + 2 + ROW_MORE_W  # main button + gap + the "⋯" pill
ROW_ZONE = ROW_INNER - ROW_AVATAR - ROW_METERS - ROW_RIGHT


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
        self.items = {}
        self.scrollable = False
        self.fonts = {}

        shell = tk.Frame(self.root, bg=PAL["bg"], padx=px(22), pady=px(20), width=px(500))
        shell.pack(fill="both", expand=True)
        tk.Label(shell, text="Claude Accounts", bg=PAL["bg"], fg=PAL["text"], font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(shell, text="Choose what Claude Code uses: a Claude account or another provider.", bg=PAL["bg"],
                 fg=PAL["muted"], font=(FONT, 10)).pack(anchor="w", pady=(px(2), px(10)))
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
        tk.Frame(shell, height=1, bg=PAL["hair"]).pack(fill="x", pady=(px(10), 0))
        footer = tk.Frame(shell, bg=PAL["bg"])
        footer.pack(fill="x", pady=(px(10), 0))
        self.add_btn = rbtn(footer, "+  Add account", self.add_account, kind="primary", size=10, padx=14, pady=8,
                            parent_bg=PAL["bg"])
        self.add_btn.pack(side="left")
        self.provider_btn = rbtn(footer, "+  Add provider", self.add_provider, kind="ghost", size=10, padx=6, pady=8,
                                 parent_bg=PAL["bg"])
        self.provider_btn.pack(side="left", padx=(px(6), 0))
        self.usage_btn = rbtn(footer, "↻  Refresh usage", lambda: self.fetch_usage(manual=True), kind="ghost", size=10,
                              padx=6, pady=8, parent_bg=PAL["bg"])
        self.usage_btn.pack(side="left")
        # Status line as a quiet toast row: only takes space while it has something to say.
        self.toast = Surface(shell, fill=PAL["card"], radius=10, padx=12, pady=8)
        self.toast_dot = tk.Label(self.toast.body, text="●", bg=PAL["card"], fg=PAL["muted"], font=(FONT, 7))
        self.toast_dot.pack(side="left", padx=(0, px(8)), anchor="n", pady=(px(3), 0))
        self.status = tk.Label(self.toast.body, text="", bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9), justify="left",
                               wraplength=px(456 - 24 - 20), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        self.refresh()
        center(self.root)
        dark_titlebar(self.root)
        self.root.after(self.POLL_MS, self.tick)
        self.root.after(100, self.pump)
        self.root.after(400, self.usage_loop)

    # ---- status + background work
    def say(self, text, kind="muted"):
        color = {"muted": PAL["muted"], "ok": PAL["good"], "warn": PAL["warn"], "bad": PAL["bad"]}[kind]
        tint = {"muted": PAL["card"], "ok": PAL["good_bg"], "warn": PAL["banner"],
                "bad": mix(PAL["card"], PAL["bad"], 0.14)}[kind]
        self.status.configure(text=text, fg=PAL["text"] if kind == "muted" else color)
        self.toast_dot.configure(fg=color)
        self.toast.set_fill(tint)
        if text:
            if not self.toast.winfo_manager():
                self.toast.pack(fill="x", pady=(px(12), 0))
        else:
            self.toast.pack_forget()

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
        items = {p["id"]: p for p in st["profiles"]}
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
                      (usage[p["id"]] or {}).get("text"), (usage[p["id"]] or {}).get("note"),
                      (usage[p["id"]] or {}).get("signInRejected"), (items.get(p["id"]) or {}).get("signInRejected"),
                      (items.get(p["id"]) or {}).get("plan"))
                     for p in profiles))
        if sig == self.signature and not force:
            return
        self.signature = sig
        self.usage = usage
        self.items = items
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
                    idle = [s for s in report.values() if s == "signin-rejected"]
                    limited = [s for s in report.values() if s == "http-429" or s == "backoff"]
                    note = ""
                    if limited:
                        note = " The server asked us to slow down, so some numbers are older."
                    elif idle:
                        note = " A saved sign-in was refused. Use Sign in again on that account."
                    self.say("Usage updated." + note, "ok" if not (limited or idle) else "muted")
            try:
                self.refresh(force=True)
            except Exception:
                pass

        self.run_bg(lambda: core.refresh_usage(force=manual), done)

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
        # Hero: whatever Claude Code is using right now (a provider, else the account in use).
        active_provider = next((p for p in st["providers"] if p["active"]), None)
        hero_account = None if active_provider else next((p for p in profiles if p["id"] in in_use), None)
        if active_provider:
            self.render_provider_card(active_provider, hero=True)
        elif hero_account:
            self.render_card(hero_account, active=True, in_use=True, hero=True)
        self.render_section("ACCOUNTS", first=not (active_provider or hero_account))
        rows = sorted((p for p in profiles if p is not hero_account), key=self.account_rank)
        if not profiles and not live:
            box = tk.Frame(self.list_host, bg=PAL["bg"], padx=px(4), pady=px(10))
            box.pack(fill="x")
            tk.Label(box, text="Not signed in", bg=PAL["bg"], fg=PAL["text"], font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(box, text="Add an account to sign in. It will be saved here so you can switch back anytime.",
                     bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 9), wraplength=px(420), justify="left").pack(anchor="w", pady=(px(4), 0))
        elif not rows:
            self.render_hint("No other saved accounts. Add one to switch between them in a click.")
        for i, p in enumerate(rows):
            if i:
                self.divider()
            self.render_card(p, active=(p["id"] == live_id), in_use=(p["id"] in in_use))
        self.render_section("PROVIDERS")
        others = [p for p in st["providers"] if p is not active_provider]
        if not st["providers"]:
            self.render_hint("Use other models, for example your own gateway. Click Add provider.")
        elif not others:
            self.render_hint("No other providers saved.")
        for i, p in enumerate(others):
            if i:
                self.divider()
            self.render_provider_card(p)
        self.fit_list(frac)

    def render_section(self, title, first=False):
        row = tk.Frame(self.list_host, bg=PAL["bg"])
        row.pack(fill="x", pady=(px(4) if first else px(14), px(2)))
        tk.Label(row, text="\u2009".join(title), bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 8, "bold"), anchor="w").pack(side="left")
        tk.Frame(row, height=1, bg=PAL["hair"]).pack(side="left", fill="x", expand=True, padx=(px(10), 0))

    def divider(self):
        tk.Frame(self.list_host, height=1, bg=PAL["hair"]).pack(fill="x", padx=px(12))

    def render_hint(self, text):
        tk.Label(self.list_host, text=text, bg=PAL["bg"], fg=PAL["muted"], font=(FONT, 9), wraplength=px(420),
                 justify="left", anchor="w").pack(anchor="w", padx=px(10), pady=(px(6), px(2)))

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
        fill = PAL["banner"] if warn else PAL["card"]
        panel = Surface(self.banner_host, fill=fill, radius=12, outline=None if warn else PAL["border"], padx=14, pady=10)
        panel.pack(fill="x", pady=(0, px(10)))
        box = panel.body
        if button:
            rbtn(box, button[0], button[1], kind="primary", padx=12, pady=5, parent_bg=fill).pack(side="right", padx=(px(10), 0))
        tk.Label(box, text=text, bg=fill, fg=PAL["warn"] if tone == "warn" else PAL["muted"],
                 font=(FONT, 9, "bold" if warn else "normal"),
                 wraplength=px(button and 320 or 410), justify="left", anchor="w").pack(side="left", fill="x", expand=True)

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
        panel = Surface(self.banner_host, fill=PAL["banner"], radius=12, padx=14, pady=10)
        panel.pack(fill="x", pady=(0, px(10)))
        box = panel.body
        text = tk.Frame(box, bg=PAL["banner"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text="Current login isn't saved yet", bg=PAL["banner"], fg=PAL["text"],
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(text, text=f'{info["email"]} · {info["detail"]}', bg=PAL["banner"], fg=PAL["muted"],
                 font=(FONT, 9)).pack(anchor="w")
        rbtn(box, "Save it", lambda: self.save_current(live), kind="primary", padx=12, pady=5,
             parent_bg=PAL["banner"]).pack(side="right")

    # ---- accounts
    def account_view(self, p, active):
        item = self.items.get(p["id"]) or {}
        u = self.usage.get(p["id"])
        expired = core.refresh_expired(p["claudeAiOauth"]) and not active
        rejected = bool(item.get("signInRejected") or (u or {}).get("signInRejected"))
        return u, expired, rejected, core.refresh_days_left(p["claudeAiOauth"])

    def account_rank(self, p):
        """Rows most worth switching to first: live room, then unknown/old numbers, then full, then needs sign-in."""
        u, expired, rejected, _ = self.account_view(p, False)
        if expired or rejected:
            return (3, 0)
        if not u:
            return (1, 0)
        wins = [w for w in (u["session"], u["week"]) if w]
        used = [w["pct"] for w in wins if not w["over"] and w["pct"] is not None]
        if any(x >= 100 for x in used):
            return (2, 0)
        return (1 if u["stale"] else 0, max(used) if used else 0)

    def account_status(self, u, expired, rejected, days, active):
        """(headline, colour, [(extra text, colour, italic)]) for a list row."""
        p, extra = PAL, []
        if expired:
            head = ("Sign-in expired", p["bad"])
        elif rejected:
            head = ("Sign-in rejected", p["bad"])
        elif not u:
            head = ("No usage yet", p["muted"])
        else:
            wins = [(n, w) for n, w in (("Session", u["session"]), ("Week", u["week"])) if w]
            full = [w for n, w in wins if not w["over"] and (w["pct"] or 0) >= 100]
            fresh = [n for n, w in wins if w["over"]]
            used = [w["pct"] for n, w in wins if not w["over"] and w["pct"] is not None]
            if full:
                until = max((w["resetsAt"] or 0) for w in full)
                head = (f"Full until {clock_label(until)}" if until else "Full", p["bad"])
            elif fresh and not used:
                head = ("Fresh: all available", p["good"])
            elif fresh:
                head = (f"{fresh[0]} fresh", p["good"])
            else:
                top = max(used) if used else 0
                head = ("Room left", p["good"]) if top < 70 else ("Getting full", p["warn"]) if top < 90 else \
                    ("Almost full", p["bad"])
            if u["stale"] and head[1] in (p["good"], p["warn"]) and not fresh:
                head = (head[0], p["muted"])
            if u["note"]:
                extra.append((u["note"], p["muted"], True))
        if not active and not expired and days is not None and days < 7:
            when = "today" if days < 1 else f"in {int(days)} day{'s' if int(days) != 1 else ''}"
            extra.append((f"Sign-in expires {when}", p["warn"], False))
        return head[0], head[1], extra

    def render_card(self, p, active, in_use, hero=False):
        info = core.describe(p)
        u, expired, rejected, days = self.account_view(p, active)
        if hero:
            return self.render_hero_account(p, info, u, rejected)
        needs = expired or rejected
        stale = bool(u and u["stale"]) or needs
        page = PAL["bg"]
        panel = Surface(self.list_host, fill=page, hover=PAL["row_hover"], radius=12, padx=12, pady=7)
        panel.pack(fill="x")
        card = panel.body
        soften = (lambda c: mix(c, page, 0.4)) if stale else (lambda c: c)
        avatar = monogram(card, p["name"][:1], soften(PAL["team"] if info["team"] else PAL["accent"]), 36, page)
        head, head_color, extra = self.account_status(u, expired, rejected, days, active)
        rows = 2 + (1 if extra else 0)
        avatar.grid(row=0, column=0, rowspan=rows, padx=(0, px(8)))
        plan_text = fit(info["plan"], self.font(9), px(44))
        name_w = px(ROW_ZONE) - self.font(9).measure(plan_text) - px(8)   # name + plan share one fixed zone
        tk.Label(card, bd=0, padx=0, text=fit(p["name"], self.font(11, "bold"), name_w), bg=page,
                 fg=mix(PAL["text"], page, 0.3) if stale else PAL["text"], font=(FONT, 11, "bold"), anchor="w").grid(
            row=0, column=1, sticky="sw")
        # the name label above is the card's key widget: its .master (this frame) holds everything else
        tk.Label(card, bd=0, padx=0, text=plan_text, bg=page, fg=PAL["muted"], font=(FONT, 9),
                 anchor="w").grid(row=0, column=2, sticky="sw", padx=(px(8), 0), pady=(0, px(2)))
        line = tk.Frame(card, bg=page)
        line.grid(row=1, column=1, columnspan=3, sticky="w")
        tk.Label(line, bd=0, padx=0, text=head, bg=page, fg=head_color, font=(FONT, 9, "bold" if head_color == PAL["bad"] else "normal"),
                 anchor="w", justify="left", wraplength=px(ROW_ZONE)).pack(side="left")
        if extra:
            ex = tk.Frame(card, bg=page)
            ex.grid(row=2, column=1, columnspan=3, sticky="w")
            for text, color, italic in extra:
                tk.Label(ex, bd=0, padx=0, text=text, bg=page, fg=color, font=(FONT, 8, "italic" if italic else "normal"),
                         anchor="w", justify="left", wraplength=px(ROW_ZONE)).pack(anchor="w")
        card.columnconfigure(0, minsize=px(ROW_AVATAR))
        card.columnconfigure(3, weight=1)
        card.columnconfigure(4, minsize=px(ROW_METERS))
        card.columnconfigure(5, minsize=px(ROW_RIGHT))
        if u and not needs:
            box = tk.Frame(card, bg=page)
            box.grid(row=0, column=4, rowspan=rows, sticky="e", padx=(0, px(6)))
            box.columnconfigure(0, minsize=px(36))
            box.columnconfigure(2, minsize=px(28))
            for r, (label, w) in enumerate((("Session", u["session"]), ("Week", u["week"]))):
                if not w:
                    continue
                big, color, pct, _cap, _cc, _b = window_info(label, w)
                if stale and not w["over"]:
                    color = mix(color, page, 0.45)
                tk.Label(box, bd=0, padx=0, text=label, bg=page, fg=PAL["muted"], font=(FONT, 8), anchor="w").grid(
                    row=r, column=0, sticky="w")
                bar = meter(box, px(36), px(6), pct, soften(level_color(pct)) if not w["over"] else PAL["good"],
                            PAL["track"], page)
                bar.grid(row=r, column=1, padx=(px(2), px(4)), pady=px(2))
                tk.Label(box, bd=0, padx=0, text=("fresh" if w["over"] else big), bg=page,
                         fg=color if (w["over"] or not stale) else PAL["muted"],
                         font=(FONT, 8, "bold"), anchor="e").grid(row=r, column=2, sticky="e")
        right = tk.Frame(card, bg=page)
        right.grid(row=0, column=5, rowspan=rows, sticky="e")
        if needs:
            rbtn(right, "Sign in again", self.add_account, kind="soft", size=9, padx=4, pady=6, parent_bg=page, radius=7,
                 width=ROW_BTN_W).pack(side="left")
        else:
            rbtn(right, "Switch", lambda pid=p["id"]: self.switch(pid), size=9, padx=4, pady=6, parent_bg=page, radius=7,
                 width=ROW_BTN_W).pack(side="left")
        more = rbtn(right, "⋯", lambda: None, kind="ghost", size=12, padx=0, pady=1, parent_bg=page, radius=4,
                    width=ROW_MORE_W)
        more.pack(side="left", padx=(px(2), 0))
        more.command = lambda m=more, pid=p["id"], n=p["name"], a=active: self.card_menu(m, pid, n, a)
        panel.arm()

    def render_hero_account(self, p, info, u, rejected):
        fill = PAL["hero"]
        panel = Surface(self.list_host, fill=fill, radius=18, outline=PAL["hero_edge"], padx=18, pady=14)
        panel.pack(fill="x", pady=(0, px(2)))
        card = panel.body
        monogram(card, p["name"][:1], PAL["team"] if info["team"] else PAL["accent"], 48, fill).grid(
            row=0, column=0, rowspan=2, padx=(0, px(14)), sticky="n")
        tk.Label(card, text=p["name"], bg=fill, fg=PAL["text"], font=(FONT, 15, "bold"), anchor="w").grid(
            row=0, column=1, sticky="sw")
        tk.Label(card, text=fit(f'{info["email"]} · {info["detail"]}', self.font(9), px(236)), bg=fill, fg=PAL["muted"],
                 font=(FONT, 9), anchor="w").grid(row=1, column=1, sticky="nw")
        right = tk.Frame(card, bg=fill)
        right.grid(row=0, column=2, rowspan=2, sticky="ne")
        card.columnconfigure(1, weight=1)
        pill(right, "IN USE", PAL["good"], PAL["good_bg"], fill, padx=10, pady=3).pack(side="left", padx=(0, px(4)))
        more = rbtn(right, "⋯", lambda: None, kind="ghost", size=12, padx=2, pady=1, parent_bg=fill, radius=5)
        more.pack(side="left")
        more.command = lambda m=more, pid=p["id"], n=p["name"]: self.card_menu(m, pid, n, True)
        body = tk.Frame(card, bg=fill)
        body.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(px(14), 0))
        if rejected:
            tk.Label(body, text="Anthropic rejected this sign-in, so usage can't be read.", bg=fill, fg=PAL["bad"],
                     font=(FONT, 9, "bold"), anchor="w").pack(anchor="w")
            rbtn(body, "Sign in again", self.add_account, kind="primary", padx=14, pady=6, parent_bg=fill).pack(
                anchor="w", pady=(px(8), 0))
        if not u:
            if not rejected:
                tk.Label(body, text="No usage numbers yet. They appear once this account has been used.", bg=fill,
                         fg=PAL["muted"], font=(FONT, 9), anchor="w").pack(anchor="w")
        else:
            stale = u["stale"]
            cols = tk.Frame(body, bg=fill)
            cols.pack(anchor="w", pady=(px(14) if rejected else 0, 0))
            wins = [(n, w) for n, w in (("Session", u["session"]), ("Week", u["week"])) if w]
            for i, (label, w) in enumerate(wins):
                col = tk.Frame(cols, bg=fill)
                col.grid(row=0, column=i, sticky="nw", padx=(0, px(24)) if i < len(wins) - 1 else 0)
                big, color, pct, cap, cap_color, bold = window_info(label, w)
                if stale and not w["over"]:
                    color, cap_color = mix(color, fill, 0.35), mix(cap_color, fill, 0.35)
                tk.Label(col, text=label.upper(), bg=fill, fg=PAL["muted"], font=(FONT, 8, "bold")).pack(anchor="w")
                num = tk.Frame(col, bg=fill)
                num.pack(anchor="w", pady=(px(1), px(5)))
                tk.Label(num, text=big, bg=fill, fg=color, font=(FONT, 26, "bold")).pack(side="left")
                if not w["over"]:
                    tk.Label(num, text="used", bg=fill, fg=PAL["muted"], font=(FONT, 9)).pack(
                        side="left", anchor="s", padx=(px(5), 0), pady=(0, px(7)))
                bar_color = PAL["good"] if w["over"] else level_color(pct)
                meter(col, px(198), px(8), pct, mix(bar_color, fill, 0.35) if stale and not w["over"] else bar_color,
                      mix(PAL["track"], fill, 0.3), fill).pack(anchor="w")
                tk.Label(col, text=cap, bg=fill, fg=cap_color, font=(FONT, 9, "bold" if bold else "normal")).pack(
                    anchor="w", pady=(px(5), 0))
            if u["models"] or u["note"]:
                foot = tk.Frame(body, bg=fill)
                foot.pack(fill="x", pady=(px(14), 0))
                if u["models"]:
                    tk.Label(foot, text="MODEL LIMITS", bg=fill, fg=PAL["muted"], font=(FONT, 8, "bold")).pack(
                        side="left", padx=(0, px(8)))
                for m in u["models"][:3]:
                    pill(foot, f'{m["name"]} {m["pct"]}%', PAL["text"], PAL["chip"], fill, size=9, weight="normal",
                         padx=9, pady=3, dot=level_color(m["pct"])).pack(side="left", padx=(0, px(6)))
                if u["note"]:
                    tk.Label(foot, text=u["note"], bg=fill, fg=PAL["muted"], font=(FONT, 8, "italic")).pack(side="right")

    # ---- providers
    def render_provider_card(self, p, hero=False):
        active = p["active"]
        fill = PAL["hero"] if hero else PAL["bg"]
        if hero:
            panel = Surface(self.list_host, fill=fill, radius=18, outline=PAL["hero_edge"], padx=18, pady=14)
            panel.pack(fill="x", pady=(0, px(2)))
        else:
            panel = Surface(self.list_host, fill=fill, hover=PAL["row_hover"], radius=12, padx=12, pady=7)
            panel.pack(fill="x")
        card = panel.body
        monogram(card, p["name"][:1], PAL["provider"], 48 if hero else 36, fill).grid(
            row=0, column=0, rowspan=2 if hero else 3, padx=(0, px(14 if hero else 8)), sticky="n")
        tk.Label(card, text=p["name"], bg=fill, fg=PAL["text"], font=(FONT, 15 if hero else 11, "bold"), anchor="w").grid(
            row=0, column=1, sticky="sw")
        tk.Label(card, text=p["host"], bg=fill, fg=PAL["muted"], font=(FONT, 9), anchor="w").grid(
            row=1, column=1, sticky="nw")
        right = tk.Frame(card, bg=fill)
        right.grid(row=0, column=2, rowspan=2, sticky="ne")
        card.columnconfigure(1, weight=1)
        if active:
            pill(right, "IN USE", PAL["good"], PAL["good_bg"], fill, padx=10, pady=3).pack(side="left", padx=(0, px(4)))
        else:
            rbtn(right, "Switch", lambda pid=p["id"]: self.switch_provider(pid), size=9, padx=4, pady=6, parent_bg=fill,
                 radius=7, width=ROW_BTN_W).pack(side="left")
        more = rbtn(right, "⋯", lambda: None, kind="ghost", size=12, padx=0 if not hero else 2, pady=1, parent_bg=fill,
                    radius=4 if not hero else 5, width=None if hero else ROW_MORE_W)
        more.pack(side="left", padx=(px(2), 0))
        more.command = lambda m=more, item=p: self.provider_menu(m, item)
        lines = tk.Frame(card, bg=fill)
        lines.grid(row=2, column=1, columnspan=2, sticky="nw", pady=(px(14 if hero else 6), 0))
        if hero:
            tk.Label(lines, text="MODEL MAP", bg=fill, fg=PAL["muted"], font=(FONT, 8, "bold")).pack(anchor="w", pady=(0, px(4)))
        small = self.font(9)
        for label, model in model_groups(p["models"]):
            tk.Label(lines, text=fit(f"{label} → {model}", small, px(366)), bg=fill,
                     fg=PAL["text"] if hero else PAL["muted"], font=(FONT, 9), anchor="w").pack(anchor="w", pady=(0, px(1)))
        panel.arm()

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
