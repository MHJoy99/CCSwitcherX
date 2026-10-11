"""Claude Accounts window (tkinter). Started by claude_accounts.py with no arguments."""
import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

import claude_accounts_core as core

# --------------------------------------------------------------------------- theme
LIGHT = dict(bg="#f5f4ee", card="#ffffff", card_hover="#faf9f5", border="#e0ddd2", text="#1f1e1d", muted="#6b6a65",
             accent="#c96442", accent_hover="#b5573a", accent_text="#ffffff", btn="#efece2", btn_hover="#e5e1d4",
             good="#2f8f5b", good_bg="#e3f3ea", warn="#a8741a", bad="#c0392b", banner="#fbeee6", team="#4f6fc4")
DARK = dict(bg="#1f1e1d", card="#2a2927", card_hover="#302f2c", border="#3b3936", text="#f0eee6", muted="#a3a099",
            accent="#d97757", accent_hover="#e48a6c", accent_text="#ffffff", btn="#3a3835", btn_hover="#46443f",
            good="#5bbf8a", good_bg="#23382d", warn="#e0a84b", bad="#e5726b", banner="#3a2a22", team="#6c8be0")
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


# --------------------------------------------------------------------------- app
class App:
    POLL_MS = 2500

    def __init__(self):
        global SCALE
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
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

        shell = tk.Frame(self.root, bg=PAL["bg"], padx=px(22), pady=px(20), width=px(500))
        shell.pack(fill="both", expand=True)
        tk.Label(shell, text="Claude Accounts", bg=PAL["bg"], fg=PAL["text"], font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(shell, text="Choose which account Claude Code signs in with.", bg=PAL["bg"], fg=PAL["muted"],
                 font=(FONT, 10)).pack(anchor="w", pady=(px(2), px(14)))
        self.banner_host = tk.Frame(shell, bg=PAL["bg"])
        self.banner_host.pack(fill="x")
        self.list_host = tk.Frame(shell, bg=PAL["bg"], width=px(456))
        self.list_host.pack(fill="x")
        footer = tk.Frame(shell, bg=PAL["bg"])
        footer.pack(fill="x", pady=(px(12), 0))
        self.add_btn = Btn(footer, "+  Add account", self.add_account, kind="primary", size=10, padx=18, pady=9)
        self.add_btn.pack(side="left")
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

    def refresh(self, force=False):
        usage = {p["id"]: p["usage"] for p in core.state()["profiles"]}  # also keeps saved tokens current
        live = core.read_live()
        profiles = core.list_profiles()
        live_id = core.profile_id(live["oauthAccount"]) if live else None
        sig = (live_id, live["oauthAccount"].get("emailAddress") if live else None, int(time.time() // 300),
               tuple((p["id"], p["name"], core.refresh_expired(p["claudeAiOauth"]),
                      int(d) if (d := core.refresh_days_left(p["claudeAiOauth"])) is not None else None,
                      (usage[p["id"]] or {}).get("text"), (usage[p["id"]] or {}).get("note"))
                     for p in profiles))
        if sig == self.signature and not force:
            return
        self.signature = sig
        self.usage = usage
        self.render(live, live_id, profiles)

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
                     bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9)).pack(anchor="w")
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

    def render(self, live, live_id, profiles):
        for host in (self.banner_host, self.list_host):
            for child in host.winfo_children():
                child.destroy()
        saved_ids = {p["id"] for p in profiles}
        if live and live_id not in saved_ids:
            self.render_banner(live)
        if not profiles and not live:
            box = tk.Frame(self.list_host, bg=PAL["card"], highlightthickness=1, highlightbackground=PAL["border"],
                           padx=px(18), pady=px(18))
            box.pack(fill="x", pady=px(5))
            tk.Label(box, text="Not signed in", bg=PAL["card"], fg=PAL["text"], font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(box, text="Add an account to sign in. It will be saved here so you can switch back anytime.",
                     bg=PAL["card"], fg=PAL["muted"], font=(FONT, 9), wraplength=px(400), justify="left").pack(anchor="w", pady=(px(4), 0))
        for p in profiles:
            self.render_card(p, active=(p["id"] == live_id))
        self.root.update_idletasks()
        self.root.geometry("")  # let the window grow or shrink with the list

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

    def render_card(self, p, active):
        info = core.describe(p)
        expired = core.refresh_expired(p["claudeAiOauth"]) and not active
        edge = PAL["accent"] if active else PAL["border"]
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
        if active:
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

    def card_menu(self, anchor, pid, name, active):
        menu = tk.Menu(self.root, tearoff=0, bg=PAL["card"], fg=PAL["text"], activebackground=PAL["accent"],
                       activeforeground="#ffffff", bd=0, font=(FONT, 10))
        menu.add_command(label="Rename…", command=lambda: self.rename(pid, name))
        menu.add_command(label="Remove from list…", command=lambda: self.remove(pid, name, active))
        menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())

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

        def done(res, exc):
            self.set_busy(False)
            if exc:
                self.say(str(exc), "bad")
                self.refresh(force=True)
                return
            if not res["changed"]:
                self.say(f'"{res["name"]}" is already the active account.', "muted")
            else:
                note = f' (your previous login was saved as "{res["autosaved"]}")' if res["autosaved"] else ""
                self.say(f'Now using "{res["name"]}"{note}. Confirming…', "ok")
                self.confirm_in_background(pid, res["name"], note)
            self.refresh(force=True)

        self.run_bg(lambda: core.switch_to(pid), done)

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
