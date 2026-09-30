import json
import re
import socket
import threading
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox
from config import CONTROL_DB, CONTROL_SCHEMA
from ui.widgets import ReadOnlyTable


def _get_window_work_area(owner):
    if not owner:
        return None

    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(owner.winfo_id())
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
        if not monitor:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor_info = MONITORINFO()
        monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return None

        work = monitor_info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top
    except Exception:
        return None


def center_window(window, owner=None):
    window.update_idletasks()

    geometry = window.geometry()
    match = re.match(r"(\d+)x(\d+)", geometry)

    configured_width = int(match.group(1)) if match else 0
    configured_height = int(match.group(2)) if match else 0
    requested_width = window.winfo_reqwidth()
    requested_height = window.winfo_reqheight()

    width = max(configured_width, requested_width, window.winfo_width())
    height = max(configured_height, requested_height, window.winfo_height())
    work_area = _get_window_work_area(owner or window.master)
    if work_area:
        screen_x, screen_y, screen_width, screen_height = work_area
    else:
        screen_x = 0
        screen_y = 0
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()

    x = screen_x + max((screen_width - width) // 2, 0)
    y = screen_y + max((screen_height - height) // 2, 0)
    window.geometry(f"{width}x{height}+{x}+{y}")


def show_centered_dialog(window, owner=None):
    center_window(window, owner=owner)
    window.deiconify()
    window.lift()
    window.focus_force()
    window.grab_set()


class AdminActionDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, workstation_name: str):
        super().__init__(master)

        self.title(title)
        self.geometry("560x430")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self, text="Workstation").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.workstation_entry = ctk.CTkEntry(self)
        self.workstation_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")
        self.workstation_entry.insert(0, workstation_name)
        self.workstation_entry.configure(state="disabled")

        ctk.CTkLabel(self, text="Your name").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.name_entry = ctk.CTkEntry(self)
        self.name_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Justification").grid(row=3, column=0, padx=20, pady=8, sticky="nw")
        self.justification_text = ctk.CTkTextbox(self, height=130)
        self.justification_text.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Password").grid(row=4, column=0, padx=20, pady=8, sticky="w")
        self.password_entry = ctk.CTkEntry(self, show="*")
        self.password_entry.grid(row=4, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=5, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        name = self.name_entry.get().strip()
        justification = self.justification_text.get("1.0", "end").strip()
        password = self.password_entry.get()

        if not name:
            messagebox.showerror("Error", "Enter your name.", parent=self)
            return
        if not justification:
            messagebox.showerror("Error", "Enter a justification.", parent=self)
            return
        if not password:
            messagebox.showerror("Error", "Enter the password.", parent=self)
            return

        self.result = {
            "workstation_name": socket.gethostname(),
            "requested_by": name,
            "justification": justification,
            "password": password,
        }
        self.destroy()


class DangerConfirmDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        message: str,
        confirmation_text: str | None = None,
    ):
        super().__init__(master)

        self.title(title)
        self.confirmation_text = (confirmation_text or "").strip()
        self.geometry("520x340" if self.confirmation_text else "520x250")
        self.resizable(False, False)
        self.result = False

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        box = ctk.CTkFrame(self, fg_color="#7f1d1d", corner_radius=14)
        box.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            box,
            text=title,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white"
        ).pack(anchor="w", padx=18, pady=(18, 10))

        ctk.CTkLabel(
            box,
            text=message,
            justify="left",
            wraplength=450,
            text_color="white"
        ).pack(anchor="w", padx=18, pady=(0, 16))

        self.confirmation_entry = None
        self.confirm_button = None
        if self.confirmation_text:
            ctk.CTkLabel(
                box,
                text=f"Type {self.confirmation_text} to confirm deletion.",
                justify="left",
                wraplength=450,
                text_color="white",
            ).pack(anchor="w", padx=18, pady=(0, 6))

            self.confirmation_entry = ctk.CTkEntry(
                box,
                placeholder_text=self.confirmation_text,
            )
            self.confirmation_entry.pack(fill="x", padx=18, pady=(0, 16))
            self.confirmation_entry.bind("<KeyRelease>", lambda _event: self._sync_confirm_state())
            self.confirmation_entry.bind("<FocusOut>", lambda _event: self._sync_confirm_state())

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="e", padx=18, pady=(0, 18))

        ctk.CTkButton(row, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        self.confirm_button = ctk.CTkButton(
            row,
            text="Delete anyway",
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._confirm,
            state="disabled" if self.confirmation_text else "normal",
        )
        self.confirm_button.pack(side="right")

        if self.confirmation_entry:
            self.confirmation_entry.focus_set()
            self._sync_confirm_state()

    def _sync_confirm_state(self):
        if not self.confirm_button:
            return

        if not self.confirmation_text:
            self.confirm_button.configure(state="normal")
            return

        typed_value = self.confirmation_entry.get().strip() if self.confirmation_entry else ""
        state = "normal" if typed_value == self.confirmation_text else "disabled"
        self.confirm_button.configure(state=state)

    def _cancel(self):
        self.result = False
        self.destroy()

    def _confirm(self):
        if self.confirmation_text:
            typed_value = self.confirmation_entry.get().strip() if self.confirmation_entry else ""
            if typed_value != self.confirmation_text:
                messagebox.showerror(
                    "Error",
                    f"Type exactly {self.confirmation_text} to continue.",
                    parent=self,
                )
                if self.confirmation_entry:
                    self.confirmation_entry.focus_set()
                return
        self.result = True
        self.destroy()


class ConnectionDialog(ctk.CTkToplevel):
    OTHER_SERVER_LABEL = "Custom"

    def __init__(self, master, saved_credentials: dict | None = None):
        super().__init__(master)

        saved_credentials = dict(saved_credentials or {})
        self.result = None
        self.title("Connect to server")
        self.geometry("620x680")
        self.resizable(False, False)

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self, owner=master))

        self.grid_columnconfigure(1, weight=1)

        self.server_option_var = tk.StringVar(value=self.OTHER_SERVER_LABEL)
        self.host_var = tk.StringVar(value=str(saved_credentials.get("custom_host") or "").strip())
        self.ssh_port_var = tk.StringVar(
            value=str(saved_credentials.get("ssh_port") or saved_credentials.get("port") or 22).strip()
        )
        self.ssh_username_var = tk.StringVar(value=str(saved_credentials.get("ssh_username") or "").strip())
        self.ssh_password_var = tk.StringVar(value=str(saved_credentials.get("ssh_password") or ""))
        self.postgres_port_var = tk.StringVar(
            value=str(saved_credentials.get("postgres_port") or 5432).strip()
        )
        self.sql_username_var = tk.StringVar(value=str(saved_credentials.get("sql_username") or "").strip())
        self.sql_password_var = tk.StringVar(value=str(saved_credentials.get("sql_password") or ""))
        self.remember_var = tk.BooleanVar(value=bool(saved_credentials))

        ctk.CTkLabel(
            self,
            text="Connect",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 16), sticky="w")

        ctk.CTkLabel(self, text="Server profile").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.server_option_menu = ctk.CTkOptionMenu(
            self,
            values=[self.OTHER_SERVER_LABEL],
            variable=self.server_option_var,
        )
        self.server_option_menu.grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Address").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.host_entry = ctk.CTkEntry(self, textvariable=self.host_var)
        self.host_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="SSH port").grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.ssh_port_entry = ctk.CTkEntry(self, textvariable=self.ssh_port_var)
        self.ssh_port_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(
            self,
            text="SSH server",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=4, column=0, columnspan=2, padx=20, pady=(18, 8), sticky="w")

        ctk.CTkLabel(self, text="Username").grid(row=5, column=0, padx=20, pady=8, sticky="w")
        self.ssh_username_entry = ctk.CTkEntry(self, textvariable=self.ssh_username_var)
        self.ssh_username_entry.grid(row=5, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Password").grid(row=6, column=0, padx=20, pady=8, sticky="w")
        self.ssh_password_entry = ctk.CTkEntry(self, textvariable=self.ssh_password_var, show="*")
        self.ssh_password_entry.grid(row=6, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(
            self,
            text="PostgreSQL",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=7, column=0, columnspan=2, padx=20, pady=(18, 8), sticky="w")

        ctk.CTkLabel(self, text="PostgreSQL port").grid(row=8, column=0, padx=20, pady=8, sticky="w")
        self.postgres_port_entry = ctk.CTkEntry(self, textvariable=self.postgres_port_var)
        self.postgres_port_entry.grid(row=8, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Username").grid(row=9, column=0, padx=20, pady=8, sticky="w")
        self.sql_username_entry = ctk.CTkEntry(self, textvariable=self.sql_username_var)
        self.sql_username_entry.grid(row=9, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Password").grid(row=10, column=0, padx=20, pady=8, sticky="w")
        self.sql_password_entry = ctk.CTkEntry(self, textvariable=self.sql_password_var, show="*")
        self.sql_password_entry.grid(row=10, column=1, padx=20, pady=8, sticky="ew")

        self.remember_check = ctk.CTkCheckBox(
            self,
            text="Remember credentials",
            variable=self.remember_var,
            onvalue=True,
            offvalue=False,
        )
        self.remember_check.grid(row=11, column=0, columnspan=2, padx=20, pady=(14, 0), sticky="w")

        setup_box = ctk.CTkFrame(self, corner_radius=10)
        setup_box.grid(row=12, column=0, columnspan=2, padx=20, pady=(14, 0), sticky="ew")
        ctk.CTkLabel(
            setup_box,
            text=(
                "Required PostgreSQL setup\n"
                f"Database: {CONTROL_DB} (created automatically with CREATEDB)\n"
                f"Schema: {CONTROL_SCHEMA} (created automatically by the app)"
            ),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=10)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=13, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Connect", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        host = self.host_var.get().strip()
        ssh_username = self.ssh_username_var.get().strip()
        ssh_password = self.ssh_password_var.get()
        sql_username = self.sql_username_var.get().strip()
        sql_password = self.sql_password_var.get()

        if not host:
            messagebox.showerror("Error", "Enter the server address.", parent=self)
            return

        try:
            ssh_port = int(self.ssh_port_var.get().strip())
            if ssh_port <= 0 or ssh_port > 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Enter a valid SSH port between 1 and 65535.", parent=self)
            return

        try:
            postgres_port = int(self.postgres_port_var.get().strip())
            if postgres_port <= 0 or postgres_port > 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Error",
                "Enter a valid PostgreSQL port between 1 and 65535.",
                parent=self,
            )
            return

        if not ssh_username:
            messagebox.showerror("Error", "Enter the SSH username.", parent=self)
            return
        if not ssh_password:
            messagebox.showerror("Error", "Enter the SSH password.", parent=self)
            return
        if not sql_username:
            messagebox.showerror("Error", "Enter the PostgreSQL username.", parent=self)
            return
        if not sql_password:
            messagebox.showerror("Error", "Enter the PostgreSQL password.", parent=self)
            return

        self.result = {
            "server_option": "other",
            "custom_host": host,
            "ssh_host": host,
            "ssh_port": ssh_port,
            "postgres_port": postgres_port,
            "ssh_username": ssh_username,
            "ssh_password": ssh_password,
            "sql_username": sql_username,
            "sql_password": sql_password,
            "remember_credentials": bool(self.remember_var.get()),
        }
        self.destroy()


class VersionInfoDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str = "New version", version_label: str = "Version title"):
        super().__init__(master)

        self.title(title)
        self.geometry("520x250")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self, text=version_label).grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.version_title_entry = ctk.CTkEntry(self)
        self.version_title_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Your name").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.author_entry = ctk.CTkEntry(self)
        self.author_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        version_title = self.version_title_entry.get().strip()
        author = self.author_entry.get().strip()

        if not version_title:
            messagebox.showerror("Error", "Enter the version title.", parent=self)
            return

        if not author:
            messagebox.showerror("Error", "Enter your name.", parent=self)
            return

        self.result = {
            "version_title": version_title,
            "requested_by": author,
        }
        self.destroy()


class VersionHistoryDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, content: str):
        super().__init__(master)

        self.withdraw()
        self.title(title)
        self.geometry("980x720")
        self.minsize(760, 520)

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        self.content_box = ctk.CTkTextbox(self, font=("Cascadia Code", 11))
        self.content_box.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.content_box.insert("1.0", content)
        self.content_box.configure(state="disabled")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self.after(0, lambda: show_centered_dialog(self, master))

    def close(self):
        self.destroy()


class SqlCodeEditor(ctk.CTkFrame):
    SQL_KEYWORDS = [
        "select", "insert", "update", "delete", "merge", "into", "values", "from",
        "where", "group", "by", "order", "having", "limit", "offset", "fetch",
        "with", "recursive", "distinct", "all", "as", "on", "join", "left", "right",
        "inner", "outer", "full", "cross", "union", "intersect", "except",
        "create", "alter", "drop", "truncate", "table", "view", "materialized",
        "index", "schema", "database", "sequence", "trigger", "function", "procedure",
        "replace", "or", "if", "exists", "not", "null", "default", "primary",
        "foreign", "key", "references", "constraint", "check", "unique", "cascade",
        "restrict", "rename", "add", "column", "type", "using", "owner", "to",
        "grant", "revoke", "begin", "commit", "rollback", "savepoint", "set",
        "show", "case", "when", "then", "else", "end", "returning", "over",
        "partition", "filter", "window", "asc", "desc", "and", "in", "is", "between",
        "like", "ilike", "similar", "escape", "any", "some", "exists", "do",
        "language", "declare", "perform", "execute", "loop", "for", "while",
        "raise", "notice", "exception", "temporary", "temp", "analyze", "vacuum",
    ]
    SQL_CONSTANTS = ["true", "false", "null", "current_date", "current_time", "current_timestamp", "now"]
    TOKEN_PATTERN = re.compile(
        r"(?P<comment>--[^\n]*|/\*.*?\*/)"
        r"|(?P<string>E?'(?:''|[^'])*')"
        r"|(?P<meta>^[ \t]*\\[^\n]*)"
        r"|(?P<keyword>\b(?:"
        + "|".join(SQL_KEYWORDS)
        + r")\b)"
        r"|(?P<constant>\b(?:"
        + "|".join(SQL_CONSTANTS)
        + r")\b)"
        r"|(?P<number>\b\d+(?:\.\d+)?\b)"
        r"|(?P<function>\b[a-z_][a-z0-9_$]*(?=\s*\())",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )

    def __init__(self, master, height: int = 320):
        super().__init__(master, fg_color="#eef3f9", corner_radius=14)

        self._refresh_job = None
        self._line_font = ("Cascadia Code", 10)
        self._editor_font = ("Cascadia Code", 11)

        self.grid_rowconfigure(1, weight=1, minsize=height)
        self.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=10)
        toolbar.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            toolbar,
            text="SQL Console",
            font=ctk.CTkFont(family="Cascadia Code", size=15, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")

        editor_shell = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=12)
        editor_shell.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        editor_shell.grid_rowconfigure(0, weight=1)
        editor_shell.grid_columnconfigure(1, weight=1)

        self.line_numbers = tk.Canvas(
            editor_shell,
            width=58,
            bg="#f1f5f9",
            highlightthickness=0,
            bd=0,
        )
        self.line_numbers.grid(row=0, column=0, sticky="ns")

        self.editor = tk.Text(
            editor_shell,
            wrap="none",
            undo=True,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            selectbackground="#fed7aa",
            selectforeground="#0f172a",
            relief="flat",
            borderwidth=0,
            font=self._editor_font,
            padx=14,
            pady=12,
            tabs=("2c",),
            spacing1=1,
            spacing3=1,
        )
        self.editor.grid(row=0, column=1, sticky="nsew")

        self.v_scroll = ctk.CTkScrollbar(editor_shell, orientation="vertical", command=self._on_vertical_scrollbar)
        self.v_scroll.grid(row=0, column=2, sticky="ns", padx=(6, 8), pady=8)

        self.h_scroll = ctk.CTkScrollbar(editor_shell, orientation="horizontal", command=self.editor.xview)
        self.h_scroll.grid(row=1, column=1, sticky="ew", padx=(0, 0), pady=(0, 8))

        self.editor.configure(
            yscrollcommand=self._on_vertical_scroll,
            xscrollcommand=self.h_scroll.set,
        )

        self.editor.tag_configure("active_line", background="#fffaf5")
        self.editor.tag_configure("comment", foreground="#64748b")
        self.editor.tag_configure("string", foreground="#15803d")
        self.editor.tag_configure("keyword", foreground="#f97316")
        self.editor.tag_configure("constant", foreground="#7c3aed")
        self.editor.tag_configure("number", foreground="#c2410c")
        self.editor.tag_configure("function", foreground="#0f766e")
        self.editor.tag_configure("meta", foreground="#b91c1c")

        self.editor.bind("<<Modified>>", self._on_text_modified)
        self.editor.bind("<Configure>", self._schedule_refresh)
        self.editor.bind("<KeyRelease>", self._schedule_refresh)
        self.editor.bind("<ButtonRelease-1>", self._schedule_refresh)
        self.editor.bind("<MouseWheel>", self._on_mousewheel)
        self.line_numbers.bind("<MouseWheel>", self._on_mousewheel)
        self.editor.bind("<Tab>", self._insert_soft_tab)

        self._schedule_refresh()

    def get_sql(self) -> str:
        return self.editor.get("1.0", "end-1c")

    def set_sql(self, sql_text: str):
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", sql_text or "")
        self.editor.edit_modified(False)
        self._schedule_refresh()

    def focus_editor(self):
        self.editor.focus_set()

    def _insert_soft_tab(self, _event=None):
        self.editor.insert("insert", "    ")
        return "break"

    def _on_mousewheel(self, event):
        self.editor.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._redraw_line_numbers()
        return "break"

    def _on_vertical_scroll(self, first, last):
        self.v_scroll.set(first, last)
        self._redraw_line_numbers()

    def _on_vertical_scrollbar(self, *args):
        self.editor.yview(*args)
        self._redraw_line_numbers()

    def _on_text_modified(self, _event=None):
        self.editor.edit_modified(False)
        self._schedule_refresh()

    def _schedule_refresh(self, _event=None):
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(50, self._refresh_editor_state)

    def _refresh_editor_state(self):
        self._refresh_job = None
        self._highlight_sql()
        self._highlight_active_line()
        self._redraw_line_numbers()

    def _highlight_sql(self):
        sql_text = self.get_sql()
        for tag_name in ("comment", "string", "keyword", "constant", "number", "function", "meta"):
            self.editor.tag_remove(tag_name, "1.0", "end")

        for match in self.TOKEN_PATTERN.finditer(sql_text):
            tag_name = match.lastgroup
            if not tag_name:
                continue
            start = f"1.0+{match.start()}c"
            end = f"1.0+{match.end()}c"
            self.editor.tag_add(tag_name, start, end)

        self.editor.tag_lower("active_line")
        self.editor.tag_raise("sel")

    def _highlight_active_line(self):
        self.editor.tag_remove("active_line", "1.0", "end")
        line_start = self.editor.index("insert linestart")
        line_end = self.editor.index("insert lineend+1c")
        self.editor.tag_add("active_line", line_start, line_end)
        self.editor.tag_lower("active_line")

    def _redraw_line_numbers(self):
        self.line_numbers.delete("all")
        canvas_height = max(self.line_numbers.winfo_height(), 1)
        self.line_numbers.create_line(57, 0, 57, canvas_height, fill="#dbe4ee")

        index = self.editor.index("@0,0")
        current_line = self.editor.index("insert").split(".", 1)[0]
        while True:
            line_info = self.editor.dlineinfo(index)
            if line_info is None:
                break

            y_pos = line_info[1]
            line_height = line_info[3]
            line_number = index.split(".", 1)[0]
            text_color = "#0f172a" if line_number == current_line else "#94a3b8"
            self.line_numbers.create_text(
                50,
                y_pos + (line_height / 2),
                anchor="e",
                text=line_number,
                fill=text_color,
                font=self._line_font,
            )
            index = self.editor.index(f"{index}+1line")


class SqlExecutionDialog(ctk.CTkToplevel):
    def __init__(self, master, database_name: str, table_name: str):
        super().__init__(master)

        self.title("Execute SQL")
        self.geometry("960x640")
        self.minsize(860, 560)
        self.result = None

        self.configure(fg_color="#f4f7fb")
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(10, lambda: center_window(self))
        self.after(80, lambda: center_window(self))

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(18, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            header,
            text="Editor SQL",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=f"Base: {database_name}   |   Tabela: {table_name}",
            font=ctk.CTkFont(family="Cascadia Code", size=12, weight="bold"),
            text_color="#f97316",
        ).grid(row=0, column=1, padx=(12, 0), sticky="e")

        ctk.CTkLabel(
            header,
            text=(
                "SQL seguro com versionamento da tabela selecionada. A transacao e automatica; "
                "INSERT, DELETE e TRUNCATE atualizam o contador exato de linhas. "
                "DELETE exige uma confirmacao adicional sobre relacoes com outras tabelas. "
                "Nao use BEGIN/COMMIT e nao altere outra tabela no mesmo script."
            ),
            justify="left",
            wraplength=900,
            text_color="#64748b",
        ).grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        self.sql_editor = SqlCodeEditor(self, height=300)
        self.sql_editor.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="nsew")

        metadata_box = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff")
        metadata_box.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        metadata_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(metadata_box, text="Titulo da versao", text_color="#334155").grid(row=0, column=0, padx=18, pady=(14, 8), sticky="w")
        self.version_title_entry = ctk.CTkEntry(metadata_box)
        self.version_title_entry.grid(row=0, column=1, padx=18, pady=(14, 8), sticky="ew")

        ctk.CTkLabel(metadata_box, text="Seu nome", text_color="#334155").grid(row=1, column=0, padx=18, pady=(0, 14), sticky="w")
        self.author_entry = ctk.CTkEntry(metadata_box)
        self.author_entry.grid(row=1, column=1, padx=18, pady=(0, 14), sticky="ew")

        ctk.CTkLabel(
            metadata_box,
            text="Consulta usa apenas o SQL acima. Titulo da versao e seu nome sao exigidos apenas em Rodar SQL.",
            justify="left",
            wraplength=860,
            text_color="#64748b",
        ).grid(row=2, column=0, columnspan=2, padx=18, pady=(0, 14), sticky="w")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 18), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer,
            text="Consulta",
            command=self._confirm_query,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer,
            text="Rodar SQL",
            command=self._confirm_run,
        ).pack(side="right")

        self.sql_editor.editor.bind("<Control-Return>", self._confirm_run_from_shortcut)
        self.after(120, self.sql_editor.focus_editor)

    def _confirm_run_from_shortcut(self, _event=None):
        self._confirm_run()
        return "break"

    def _cancel(self):
        self.result = None
        self.destroy()

    def _read_sql_text(self) -> str | None:
        sql_text = self.sql_editor.get_sql().strip()
        if not sql_text:
            messagebox.showerror("Error", "Digite um codigo SQL para executar.", parent=self)
            return None
        return self.sql_editor.get_sql()

    def _confirm_query(self):
        sql_text = self._read_sql_text()
        if sql_text is None:
            return

        self.result = {
            "mode": "query",
            "sql": sql_text,
        }
        self.destroy()

    def _confirm_run(self):
        sql_text = self._read_sql_text()
        if sql_text is None:
            return

        version_title = self.version_title_entry.get().strip()
        author = self.author_entry.get().strip()
        if not version_title:
            messagebox.showerror("Error", "Digite o titulo da versao.", parent=self)
            return

        if not author:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            return

        self.result = {
            "mode": "execute",
            "sql": sql_text,
            "version_title": version_title,
            "requested_by": author,
        }
        self.destroy()


class SqlExpansionDialog(ctk.CTkToplevel):
    SOURCE_PLACEHOLDER = "{{source}}"

    def __init__(
        self,
        master,
        database_name: str,
        source_table_name: str,
        available_tables,
        available_raw_schemas,
    ):
        super().__init__(master)

        self.title("Expand data across tables")
        self.geometry("1120x820")
        self.minsize(960, 680)
        self.result = None
        self._source_table_name = source_table_name
        self._available_tables = [
            str(table_name)
            for table_name in available_tables or []
            if str(table_name) and str(table_name) != source_table_name
        ]
        self._available_raw_schemas = [
            str(raw_schema)
            for raw_schema in available_raw_schemas or []
            if str(raw_schema)
        ]
        self._destination_tabs = {}
        self._next_destination_number = 1

        self.configure(fg_color="#f4f7fb")
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(10, lambda: center_window(self))
        self.after(80, lambda: center_window(self))

        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(18, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Expand",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text=(
                f"Database: {database_name}   |   Read-only source: "
                f"{source_table_name}"
            ),
            font=ctk.CTkFont(family="Cascadia Code", size=12, weight="bold"),
            text_color="#f97316",
        ).grid(row=0, column=1, padx=(12, 0), sticky="e")
        ctk.CTkLabel(
            header,
            text=(
                "Each destination creates its own version with the same title. "
                "Use only INSERT INTO ... "
                f"SELECT ... FROM {self.SOURCE_PLACEHOLDER} AS source. "
                "The source and raw_schema range are protected automatically. "
                "A destination may read itself and earlier destinations in this "
                "batch, so tab order defines dependency order."
            ),
            justify="left",
            wraplength=1060,
            text_color="#64748b",
        ).grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="w")

        scope_box = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff")
        scope_box.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        scope_box.grid_columnconfigure(1, weight=1)
        scope_box.grid_columnconfigure(3, weight=1)
        scope_box.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(
            scope_box,
            text="First raw_schema",
            text_color="#334155",
        ).grid(row=0, column=0, padx=(18, 8), pady=14, sticky="w")
        self.first_raw_schema_combo = ctk.CTkComboBox(
            scope_box,
            values=self._available_raw_schemas,
            state="readonly",
        )
        self.first_raw_schema_combo.grid(
            row=0,
            column=1,
            padx=(0, 16),
            pady=14,
            sticky="ew",
        )

        ctk.CTkLabel(
            scope_box,
            text="Last raw_schema",
            text_color="#334155",
        ).grid(row=0, column=2, padx=(0, 8), pady=14, sticky="w")
        self.last_raw_schema_combo = ctk.CTkComboBox(
            scope_box,
            values=self._available_raw_schemas,
            state="readonly",
        )
        self.last_raw_schema_combo.grid(
            row=0,
            column=3,
            padx=(0, 16),
            pady=14,
            sticky="ew",
        )

        ctk.CTkLabel(
            scope_box,
            text="Your name",
            text_color="#334155",
        ).grid(row=0, column=4, padx=(0, 8), pady=14, sticky="w")
        self.author_entry = ctk.CTkEntry(scope_box)
        self.author_entry.grid(
            row=0,
            column=5,
            padx=(0, 18),
            pady=14,
            sticky="ew",
        )

        ctk.CTkLabel(
            scope_box,
            text="Version title",
            text_color="#334155",
        ).grid(row=1, column=0, padx=(18, 8), pady=(0, 14), sticky="w")
        self.version_title_entry = ctk.CTkEntry(scope_box)
        self.version_title_entry.grid(
            row=1,
            column=1,
            columnspan=5,
            padx=(0, 18),
            pady=(0, 14),
            sticky="ew",
        )

        if self._available_raw_schemas:
            latest_raw_schema = self._available_raw_schemas[-1]
            self.first_raw_schema_combo.set(latest_raw_schema)
            self.last_raw_schema_combo.set(latest_raw_schema)
        self._bind_combo_mousewheel(
            self.first_raw_schema_combo,
            self._available_raw_schemas,
        )
        self._bind_combo_mousewheel(
            self.last_raw_schema_combo,
            self._available_raw_schemas,
        )

        destination_toolbar = ctk.CTkFrame(self, fg_color="transparent")
        destination_toolbar.grid(
            row=2,
            column=0,
            padx=20,
            pady=(0, 8),
            sticky="ew",
        )
        destination_toolbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            destination_toolbar,
            text="Destinations and recipes",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            destination_toolbar,
            text="+ Destination",
            width=110,
            command=self._add_destination_tab,
        ).grid(row=0, column=1, padx=(8, 0), sticky="e")
        self.remove_destination_button = ctk.CTkButton(
            destination_toolbar,
            text="Remove",
            width=100,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._remove_current_destination_tab,
        )
        self.remove_destination_button.grid(
            row=0,
            column=2,
            padx=(8, 0),
            sticky="e",
        )

        self.destination_tabview = ctk.CTkTabview(self, corner_radius=14)
        self.destination_tabview.grid(
            row=3,
            column=0,
            padx=20,
            pady=(0, 10),
            sticky="nsew",
        )

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 18), sticky="e")
        ctk.CTkButton(
            footer,
            text="Cancel",
            command=self._cancel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer,
            text="Run expansion",
            command=self._confirm,
        ).pack(side="right")

        self._add_destination_tab()

    @staticmethod
    def _step_combo_value(values, current_value: str, step: int) -> str:
        if not values:
            return ""
        try:
            current_index = values.index(current_value)
        except ValueError:
            return values[0] if step >= 0 else values[-1]
        next_index = max(0, min(len(values) - 1, current_index + step))
        return values[next_index]

    def _bind_combo_mousewheel(self, combo, values):
        def select_adjacent(event):
            delta = getattr(event, "delta", 0)
            event_number = getattr(event, "num", None)
            if delta > 0 or event_number == 4:
                step = -1
            elif delta < 0 or event_number == 5:
                step = 1
            else:
                return None

            selected_value = self._step_combo_value(
                values,
                combo.get().strip(),
                step,
            )
            if selected_value:
                combo.set(selected_value)
                dropdown_menu = getattr(combo, "_dropdown_menu", None)
                if dropdown_menu is not None:
                    dropdown_menu.activate(values.index(selected_value))
            return "break"

        wheel_targets = (
            getattr(combo, "_entry", None),
            getattr(combo, "_canvas", None),
            getattr(combo, "_dropdown_menu", None),
        )
        for target in wheel_targets:
            if target is None:
                continue
            target.bind("<MouseWheel>", select_adjacent, add="+")
            target.bind("<Button-4>", select_adjacent, add="+")
            target.bind("<Button-5>", select_adjacent, add="+")

    def _next_unused_destination(self) -> str:
        used = {
            widgets["table_combo"].get().strip()
            for widgets in self._destination_tabs.values()
        }
        return next(
            (
                table_name
                for table_name in self._available_tables
                if table_name not in used
            ),
            self._available_tables[0] if self._available_tables else "",
        )

    def _default_sql(self, destination_table_name: str) -> str:
        destination_label = destination_table_name or "public.destination_table"
        return (
            f"INSERT INTO {destination_label} (\n"
            "    destination_column\n"
            ")\n"
            "SELECT\n"
            "    source.raw -> 'values' ->> 'key'\n"
            f"FROM {self.SOURCE_PLACEHOLDER} AS source;\n"
        )

    def _add_destination_tab(self):
        tab_name = f"Destination {self._next_destination_number}"
        self._next_destination_number += 1
        tab = self.destination_tabview.add(tab_name)
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Destination table",
            text_color="#334155",
        ).grid(row=0, column=0, padx=(12, 8), pady=(10, 8), sticky="w")
        table_combo = ctk.CTkComboBox(
            tab,
            values=self._available_tables,
            state="readonly",
        )
        table_combo.grid(
            row=0,
            column=1,
            padx=(0, 16),
            pady=(10, 8),
            sticky="ew",
        )
        selected_destination = self._next_unused_destination()
        if selected_destination:
            table_combo.set(selected_destination)

        sql_editor = SqlCodeEditor(tab, height=320)
        sql_editor.grid(
            row=1,
            column=0,
            columnspan=2,
            padx=12,
            pady=(0, 12),
            sticky="nsew",
        )
        initial_sql = self._default_sql(selected_destination)
        sql_editor.set_sql(initial_sql)
        template_state = {"sql": initial_sql}

        def update_destination_template(selected_table_name: str):
            current_sql = sql_editor.get_sql()
            if current_sql.strip() != template_state["sql"].strip():
                return
            updated_sql = self._default_sql(selected_table_name)
            sql_editor.set_sql(updated_sql)
            template_state["sql"] = updated_sql

        table_combo.configure(command=update_destination_template)
        self._destination_tabs[tab_name] = {
            "table_combo": table_combo,
            "sql_editor": sql_editor,
        }
        self.destination_tabview.set(tab_name)
        self._sync_remove_button()
        self.after(80, sql_editor.focus_editor)

    def _remove_current_destination_tab(self):
        if len(self._destination_tabs) <= 1:
            return
        current_tab = self.destination_tabview.get()
        if current_tab not in self._destination_tabs:
            return
        self.destination_tabview.delete(current_tab)
        self._destination_tabs.pop(current_tab, None)
        self._sync_remove_button()

    def _sync_remove_button(self):
        self.remove_destination_button.configure(
            state="normal" if len(self._destination_tabs) > 1 else "disabled"
        )

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        if not self._available_raw_schemas:
            messagebox.showerror(
                "Error",
                "The source has no raw_schema registered in version history.",
                parent=self,
            )
            return
        first_raw_schema = self.first_raw_schema_combo.get().strip()
        last_raw_schema = self.last_raw_schema_combo.get().strip()
        if first_raw_schema not in self._available_raw_schemas:
            messagebox.showerror(
                "Error",
                "Select the first raw_schema.",
                parent=self,
            )
            return
        if last_raw_schema not in self._available_raw_schemas:
            messagebox.showerror(
                "Error",
                "Select the last raw_schema.",
                parent=self,
            )
            return
        first_index = self._available_raw_schemas.index(first_raw_schema)
        last_index = self._available_raw_schemas.index(last_raw_schema)
        if first_index > last_index:
            messagebox.showerror(
                "Error",
                "The first raw_schema must not come after the last raw_schema.",
                parent=self,
            )
            return

        version_title = self.version_title_entry.get().strip()
        if not version_title:
            messagebox.showerror(
                "Error",
                "Enter the version title.",
                parent=self,
            )
            return

        requested_by = self.author_entry.get().strip()
        if not requested_by:
            messagebox.showerror(
                "Error",
                "Enter your name.",
                parent=self,
            )
            return

        destinations = []
        seen_tables = set()
        for tab_name, widgets in self._destination_tabs.items():
            table_name = widgets["table_combo"].get().strip()
            sql_text = widgets["sql_editor"].get_sql().strip()
            if not table_name:
                messagebox.showerror(
                    "Error",
                    f"Select the table for {tab_name}.",
                    parent=self,
                )
                return
            if table_name == self._source_table_name:
                messagebox.showerror(
                    "Error",
                    f"{tab_name}: the source cannot be a destination.",
                    parent=self,
                )
                return
            if table_name in seen_tables:
                messagebox.showerror(
                    "Error",
                    f"Table {table_name} was selected for more than one destination.",
                    parent=self,
                )
                return
            if not sql_text:
                messagebox.showerror(
                    "Error",
                    f"Enter the SQL for {tab_name}.",
                    parent=self,
                )
                return
            seen_tables.add(table_name)
            destinations.append(
                {
                    "table_name": table_name,
                    "version_title": version_title,
                    "sql": widgets["sql_editor"].get_sql(),
                }
            )

        self.result = {
            "first_raw_schema": first_raw_schema,
            "last_raw_schema": last_raw_schema,
            "raw_schemas": self._available_raw_schemas[
                first_index:last_index + 1
            ],
            "requested_by": requested_by,
            "version_title": version_title,
            "destinations": destinations,
        }
        self.destroy()


class SqlQueryResultDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        database_name: str,
        table_name: str,
        headers,
        rows,
        message: str,
        query_kind: str,
    ):
        super().__init__(master)

        self.title("Query results")
        self.geometry("1080x720")
        self.minsize(920, 560)
        self._headers = list(headers or [])
        self._rows = [list(row) for row in (rows or [])]

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(10, lambda: center_window(self))
        self.after(80, lambda: center_window(self))

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(18, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Query results",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=f"Base: {database_name}   |   Tabela: {table_name}   |   Tipo: {query_kind.upper()}",
            font=ctk.CTkFont(family="Cascadia Code", size=12, weight="bold"),
            text_color="#f97316",
        ).grid(row=1, column=0, pady=(4, 0), sticky="w")

        ctk.CTkLabel(
            self,
            text=message,
            justify="left",
            wraplength=1020,
            text_color="#475569",
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")

        self.result_table = ReadOnlyTable(self, "Resultado")
        self.result_table.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.result_table.set_data(self._headers, self._rows)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 18), sticky="e")
        ctk.CTkButton(footer, text="Exportar JSON", command=self._export_json).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Close", command=self._close).pack(side="right")

    def _build_export_payload(self):
        if not self._headers:
            return [list(row) for row in self._rows]

        payload = []
        for row in self._rows:
            item = {}
            for index, header in enumerate(self._headers):
                item[str(header)] = row[index] if index < len(row) else ""
            payload.append(item)
        return payload

    def _export_json(self):
        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar resultado da consulta",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Todos os arquivos", "*.*")],
            initialfile="consulta_resultado.json",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as output_file:
                json.dump(self._build_export_payload(), output_file, ensure_ascii=False, indent=2)
            messagebox.showinfo("Exportacao concluida", f"Resultado salvo em:\n{file_path}", parent=self)
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class SqlExpressionDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        database_name: str,
        table_name: str,
        target_type: str,
        context_label: str,
        initial_sql: str = "",
    ):
        super().__init__(master)

        self.title(title)
        self.geometry("960x620")
        self.minsize(860, 520)
        self.result = None

        self.configure(fg_color="#f4f7fb")
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(10, lambda: center_window(self))
        self.after(80, lambda: center_window(self))

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(18, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            header,
            text="Editor SQL",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=f"Base: {database_name}   |   Tabela: {table_name}",
            font=ctk.CTkFont(family="Cascadia Code", size=12, weight="bold"),
            text_color="#f97316",
        ).grid(row=0, column=1, padx=(12, 0), sticky="e")

        ctk.CTkLabel(
            header,
            text=(
                f"{context_label}\n"
                f"Escreva uma expressao SQL unica que devolva um valor compativel com o tipo {target_type}.\n"
                "Variaveis disponiveis: input_text, normalized_text. Nao use ponto e virgula."
            ),
            justify="left",
            wraplength=900,
            text_color="#64748b",
        ).grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        self.sql_editor = SqlCodeEditor(self, height=280)
        self.sql_editor.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="nsew")
        self.sql_editor.set_sql(initial_sql)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(0, 18), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer,
            text="Usar SQL",
            command=self._confirm,
        ).pack(side="right")

        self.sql_editor.editor.bind("<Control-Return>", self._confirm_from_shortcut)
        self.after(120, self.sql_editor.focus_editor)

    def _confirm_from_shortcut(self, _event=None):
        self._confirm()
        return "break"

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        sql_text = self.sql_editor.get_sql().strip()
        if not sql_text:
            messagebox.showerror("Error", "Digite uma expressao SQL para continuar.", parent=self)
            return
        if ";" in sql_text:
            messagebox.showerror(
                "Error",
                "Informe apenas uma expressao SQL, sem ponto e virgula.",
                parent=self,
            )
            return

        self.result = sql_text
        self.destroy()


class CreateTableDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)

        self.title("Create table")
        self.geometry("560x310")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Create table",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self, text="Nome da tabela").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.table_name_entry = ctk.CTkEntry(self, placeholder_text="public.minha_tabela")
        self.table_name_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Titulo da versao 0000").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.version_title_entry = ctk.CTkEntry(self)
        self.version_title_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Seu nome").grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.author_entry = ctk.CTkEntry(self)
        self.author_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Criar", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        table_name = self.table_name_entry.get().strip()
        version_title = self.version_title_entry.get().strip()
        requested_by = self.author_entry.get().strip()

        if not table_name:
            messagebox.showerror("Error", "Digite o nome da tabela.", parent=self)
            return

        if not version_title:
            messagebox.showerror("Error", "Digite o titulo da versao inicial.", parent=self)
            return

        if not requested_by:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            return

        self.result = {
            "table_name": table_name,
            "version_title": version_title,
            "requested_by": requested_by,
        }
        self.destroy()


class RootPasswordDialog(ctk.CTkToplevel):
    def __init__(self, master, on_result=None):
        super().__init__(master)

        self.withdraw()
        self.title("Root password")
        self.geometry("460x210")
        self.resizable(False, False)
        self.result = None
        self._on_result = on_result
        self._finished = False

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Permissao administrativa",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text="Digite a senha root para aplicar esta alteracao.",
            justify="left",
            wraplength=400,
        ).grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 12), sticky="w")

        ctk.CTkLabel(self, text="Password").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.password_entry = ctk.CTkEntry(self, show="*")
        self.password_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, padx=20, pady=18, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")

        self.after(0, self._show)

    def _show(self):
        show_centered_dialog(self, self.master)
        try:
            self.attributes("-topmost", True)
            self.after(250, lambda: self.attributes("-topmost", False) if self.winfo_exists() else None)
        except Exception:
            pass
        self.password_entry.focus_set()

    def _finish(self, result):
        if self._finished:
            return

        self._finished = True
        self.result = result
        callback = self._on_result
        callback_owner = self.master

        try:
            self.grab_release()
        except Exception:
            pass

        self.destroy()

        if callback:
            try:
                callback_owner.after(0, lambda: callback(result))
            except Exception:
                callback(result)

    def _cancel(self):
        self._finish(None)

    def _confirm(self):
        password = self.password_entry.get()
        if not password:
            messagebox.showerror("Error", "Digite a senha root.", parent=self)
            return
        self._finish(password)


class CreateDatabaseUserDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)

        self.title("New user")
        self.geometry("520x310")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="New user",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self, text="User").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.user_name_entry = ctk.CTkEntry(self)
        self.user_name_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Password").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.password_entry = ctk.CTkEntry(self, show="*")
        self.password_entry.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Confirmar senha").grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.confirm_password_entry = ctk.CTkEntry(self, show="*")
        self.confirm_password_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Criar", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        user_name = self.user_name_entry.get().strip()
        password = self.password_entry.get()
        confirm_password = self.confirm_password_entry.get()

        if not user_name:
            messagebox.showerror("Error", "Digite o nome do usuario.", parent=self)
            return
        if not password:
            messagebox.showerror("Error", "Digite a senha do usuario.", parent=self)
            return
        if password != confirm_password:
            messagebox.showerror("Error", "As senhas nao conferem.", parent=self)
            return

        self.result = {
            "user_name": user_name,
            "password": password,
        }
        self.destroy()


class RenameDatabaseUserDialog(ctk.CTkToplevel):
    def __init__(self, master, current_user_name: str):
        super().__init__(master)

        self.title("Edit user")
        self.geometry("520x210")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Edit user",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self, text="Novo nome").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.user_name_entry = ctk.CTkEntry(self)
        self.user_name_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")
        self.user_name_entry.insert(0, current_user_name)
        self.user_name_entry.select_range(0, "end")
        self.user_name_entry.focus_set()

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Save", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        user_name = self.user_name_entry.get().strip()
        if not user_name:
            messagebox.showerror("Error", "Digite o novo nome do usuario.", parent=self)
            return

        self.result = user_name
        self.destroy()


class ColumnActionDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        column_name: str,
        can_link_dictionary: bool = False,
        can_delete: bool = True,
        can_expand: bool = False,
        can_group: bool = False,
        can_group_columns: bool = False,
        can_validate: bool = True,
        can_make_primary_key: bool = False,
        can_make_foreign_key: bool = False,
    ):
        super().__init__(master)

        self.title(f"Acoes da coluna {column_name}")
        action_count = (
            1
            + int(bool(can_link_dictionary))
            + int(bool(can_make_primary_key))
            + int(bool(can_make_foreign_key))
            + int(bool(can_validate))
            + int(bool(can_delete))
            + int(bool(can_expand))
            + int(bool(can_group))
            + int(bool(can_group_columns))
        )
        self.geometry(f"360x{170 + action_count * 48}")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=column_name,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 14), sticky="ew")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        actions.grid_columnconfigure(0, weight=1)

        row_index = 0

        def add_action(text: str, action: str, **button_kwargs):
            nonlocal row_index
            ctk.CTkButton(
                actions,
                text=text,
                height=38,
                command=lambda: self._choose(action),
                **button_kwargs,
            ).grid(row=row_index, column=0, pady=5, sticky="ew")
            row_index += 1

        if can_link_dictionary:
            add_action(
                "Vincular ao data_dictionary",
                "link_dictionary",
                fg_color="#16a34a",
                hover_color="#15803d",
            )

        add_action("Filtro", "filter")

        if can_make_primary_key:
            add_action(
                "Tornar PK",
                "make_primary_key",
                fg_color="#15803d",
                hover_color="#166534",
            )

        if can_make_foreign_key:
            add_action(
                "Tornar FK",
                "make_foreign_key",
                fg_color="#facc15",
                hover_color="#eab308",
                text_color="#111827",
            )

        if can_validate:
            add_action("Validar", "validate")

        if can_delete:
            add_action("Delete", "delete", fg_color="#dc2626", hover_color="#b91c1c")

        if can_expand:
            add_action("Expand", "expand")

        if can_group:
            add_action("Agrupar linhas", "group")

        if can_group_columns:
            add_action("Agrupar colunas", "group_columns")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right")

    def _choose(self, action: str):
        self.result = action
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ForeignKeyReferenceDialog(ctk.CTkToplevel):
    def __init__(self, master, column_name: str, reference_options):
        super().__init__(master)

        normalized_options = []
        for option in reference_options or []:
            full_table_name = str((option or {}).get("full_table_name") or "").strip()
            referenced_column_name = str((option or {}).get("column_name") or "").strip()
            if not full_table_name or not referenced_column_name:
                continue
            normalized_options.append(
                {
                    "full_table_name": full_table_name,
                    "column_name": referenced_column_name,
                    "data_type": str((option or {}).get("data_type") or "").strip(),
                    "constraint_type": str((option or {}).get("constraint_type") or "").strip() or "constraint",
                    "constraint_name": str((option or {}).get("constraint_name") or "").strip(),
                }
            )
        if not normalized_options:
            raise ValueError("Nenhuma referencia valida foi informada para a foreign key.")

        self.title(f"Tornar FK: {column_name}")
        self.geometry("560x340")
        self.resizable(False, False)
        self.result = None
        self._column_name = column_name
        self._options_by_table = {}
        self._option_by_label = {}

        for option in normalized_options:
            table_name = option["full_table_name"]
            column_label = option["column_name"]
            if option["constraint_type"] or option["data_type"]:
                suffix_parts = [part for part in [option["constraint_type"], option["data_type"]] if part]
                column_label = f"{column_label} ({', '.join(suffix_parts)})"
            option["selection_label"] = column_label
            self._options_by_table.setdefault(table_name, []).append(option)
            self._option_by_label[(table_name, column_label)] = option

        self._table_names = sorted(self._options_by_table.keys())

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Definir foreign key para {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Selecione a tabela e a coluna de referencia. "
                "A constraint sera criada com ON UPDATE CASCADE e ON DELETE CASCADE."
            ),
            justify="left",
            anchor="w",
            wraplength=500,
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Tabela de referencia").grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="w")
        self.table_menu = ctk.CTkOptionMenu(
            form,
            values=self._table_names,
            command=self._handle_table_change,
        )
        self.table_menu.grid(row=0, column=1, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(form, text="Coluna de referencia").grid(row=1, column=0, padx=(0, 10), pady=(0, 10), sticky="w")
        initial_column_values = [item["selection_label"] for item in self._options_by_table[self._table_names[0]]]
        self.column_menu = ctk.CTkOptionMenu(
            form,
            values=initial_column_values,
            command=lambda _value: self._update_reference_label(),
        )
        self.column_menu.grid(row=1, column=1, pady=(0, 10), sticky="ew")

        self.reference_label = ctk.CTkLabel(
            self,
            text="",
            justify="left",
            anchor="w",
            wraplength=500,
            text_color="#475569",
        )
        self.reference_label.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right")
        ctk.CTkButton(footer, text="Confirm", command=self._submit).pack(side="right", padx=(0, 10))

        self.table_menu.set(self._table_names[0])
        self.column_menu.set(initial_column_values[0])
        self._update_reference_label()

    def _handle_table_change(self, table_name: str):
        options = list(self._options_by_table.get(table_name) or [])
        labels = [item["selection_label"] for item in options] or ["(sem opcoes)"]
        self.column_menu.configure(values=labels)
        self.column_menu.set(labels[0])
        self._update_reference_label()

    def _get_selected_option(self):
        table_name = str(self.table_menu.get() or "").strip()
        selection_label = str(self.column_menu.get() or "").strip()
        return self._option_by_label.get((table_name, selection_label))

    def _update_reference_label(self):
        selected_option = self._get_selected_option()
        if not selected_option:
            self.reference_label.configure(text="Nenhuma referencia disponivel para a selecao atual.")
            return
        reference_text = (
            f"Referencia: {selected_option['full_table_name']}.{selected_option['column_name']}\n"
            f"Tipo: {selected_option['data_type'] or '(nao informado)'} | "
            f"Constraint alvo: {selected_option['constraint_type']}"
        )
        self.reference_label.configure(text=reference_text)

    def _submit(self):
        selected_option = self._get_selected_option()
        if not selected_option:
            messagebox.showerror("Error", "Selecione uma referencia valida.", parent=self)
            return
        self.result = {
            "referenced_table_name": selected_option["full_table_name"],
            "referenced_column_name": selected_option["column_name"],
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ColumnValidationDialog(ctk.CTkToplevel):
    GENERAL_RULES = [
        {
            "key": "not_blank",
            "label": "Nao deve ser nulo/vazio",
            "fields": [],
        },
        {
            "key": "is_blank",
            "label": "Deve ser nulo/vazio",
            "fields": [],
        },
        {
            "key": "contains",
            "label": "Contem texto",
            "fields": [("text", "Texto", "trecho esperado")],
        },
        {
            "key": "not_contains",
            "label": "Nao contem texto",
            "fields": [("text", "Texto", "trecho proibido")],
        },
        {
            "key": "length_lt",
            "label": "Comprimento menor que",
            "fields": [("limit", "Caracteres", "25")],
        },
        {
            "key": "length_lte",
            "label": "Comprimento menor ou igual a",
            "fields": [("limit", "Caracteres", "25")],
        },
        {
            "key": "length_gt",
            "label": "Comprimento maior que",
            "fields": [("limit", "Caracteres", "25")],
        },
        {
            "key": "length_gte",
            "label": "Comprimento maior ou igual a",
            "fields": [("limit", "Caracteres", "25")],
        },
        {
            "key": "length_between",
            "label": "Comprimento entre",
            "fields": [("lower", "Minimo", "5"), ("upper", "Maximo", "25")],
        },
    ]
    NUMERIC_RULES = [
        {
            "key": "numeric_gt",
            "label": "Valor maior que",
            "fields": [("value", "Valor", "0")],
        },
        {
            "key": "numeric_gte",
            "label": "Valor maior ou igual a",
            "fields": [("value", "Valor", "0")],
        },
        {
            "key": "numeric_lt",
            "label": "Valor menor que",
            "fields": [("value", "Valor", "0")],
        },
        {
            "key": "numeric_lte",
            "label": "Valor menor ou igual a",
            "fields": [("value", "Valor", "0")],
        },
        {
            "key": "numeric_between",
            "label": "Valor entre",
            "fields": [("lower", "Minimo", "0"), ("upper", "Maximo", "100")],
        },
        {
            "key": "numeric_mean_stddev_within",
            "label": "Dentro de N desvios da media",
            "fields": [("stddevs", "N desvios", "2")],
        },
        {
            "key": "numeric_max_mean_stddev",
            "label": "Nao acima de media + N desvios",
            "fields": [("stddevs", "N desvios", "2")],
        },
        {
            "key": "numeric_min_mean_stddev",
            "label": "Nao abaixo de media - N desvios",
            "fields": [("stddevs", "N desvios", "2")],
        },
    ]
    BLANK_RULES = {"not_blank", "is_blank"}
    INTEGER_FIELDS = {"limit", "lower", "upper"}
    DECIMAL_FIELDS = {"value", "stddevs"}

    def __init__(self, master, column_name: str, is_numeric: bool = False, data_type: str = ""):
        super().__init__(master)

        self.title(f"Validar {column_name}")
        self.geometry("560x430")
        self.resizable(False, False)
        self.result = None
        self.column_name = column_name
        self.is_numeric = is_numeric
        self.data_type = data_type
        self.rules = list(self.GENERAL_RULES)
        if is_numeric:
            self.rules.extend(self.NUMERIC_RULES)
        self.rules_by_label = {rule["label"]: rule for rule in self.rules}

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Validar: {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(self, text="Type").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        ctk.CTkLabel(
            self,
            text=data_type or ("numerico" if is_numeric else "texto/geral"),
            anchor="w",
        ).grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Regra").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.rule_menu = ctk.CTkOptionMenu(
            self,
            values=[rule["label"] for rule in self.rules],
            command=lambda _value: self._sync_rule_fields(),
        )
        self.rule_menu.grid(row=2, column=1, padx=20, pady=8, sticky="ew")
        self.rule_menu.set(self.rules[0]["label"])

        self.field1_label = ctk.CTkLabel(self, text="")
        self.field1_label.grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.field1_entry = ctk.CTkEntry(self)
        self.field1_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        self.field2_label = ctk.CTkLabel(self, text="")
        self.field2_label.grid(row=4, column=0, padx=20, pady=8, sticky="w")
        self.field2_entry = ctk.CTkEntry(self)
        self.field2_entry.grid(row=4, column=1, padx=20, pady=8, sticky="ew")

        self.ignore_blank_var = tk.BooleanVar(value=False)
        self.ignore_blank_checkbox = ctk.CTkCheckBox(
            self,
            text="Ignorar nulos/vazios",
            variable=self.ignore_blank_var,
        )
        self.ignore_blank_checkbox.grid(row=5, column=1, padx=20, pady=(10, 8), sticky="w")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, columnspan=2, padx=20, pady=(28, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Criar flag", command=self._confirm).pack(side="right")

        self._sync_rule_fields()

    def _selected_rule(self):
        return self.rules_by_label[self.rule_menu.get()]

    def _sync_rule_fields(self):
        rule = self._selected_rule()
        fields = list(rule.get("fields", []))
        self._configure_field(self.field1_label, self.field1_entry, fields[0] if fields else None)
        self._configure_field(self.field2_label, self.field2_entry, fields[1] if len(fields) > 1 else None)

        if rule["key"] in self.BLANK_RULES:
            self.ignore_blank_var.set(False)
            self.ignore_blank_checkbox.configure(state="disabled")
        else:
            self.ignore_blank_checkbox.configure(state="normal")

    @staticmethod
    def _configure_field(label, entry, field_spec):
        entry.delete(0, "end")
        if not field_spec:
            label.grid_remove()
            entry.grid_remove()
            return

        _key, caption, placeholder = field_spec
        label.configure(text=caption)
        entry.configure(placeholder_text=placeholder)
        label.grid()
        entry.grid()

    @staticmethod
    def _normalize_decimal(value: str) -> str | None:
        normalized = str(value or "").strip().replace(",", ".")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
            return None
        return normalized

    @staticmethod
    def _normalize_integer(value: str) -> int | None:
        normalized = str(value or "").strip()
        if not re.fullmatch(r"\d+", normalized):
            return None
        return int(normalized)

    def _read_params(self, rule: dict) -> dict | None:
        entries = [self.field1_entry, self.field2_entry]
        params = {}
        for index, field_spec in enumerate(rule.get("fields", [])):
            key, caption, _placeholder = field_spec
            raw_value = entries[index].get().strip()
            if key == "text":
                if not raw_value:
                    messagebox.showerror("Error", f"Informe {caption.lower()}.", parent=self)
                    return None
                params[key] = raw_value
            elif key in self.INTEGER_FIELDS and rule["key"].startswith("length"):
                value = self._normalize_integer(raw_value)
                if value is None:
                    messagebox.showerror("Error", f"Informe um numero inteiro em {caption}.", parent=self)
                    return None
                params[key] = value
            elif key in self.DECIMAL_FIELDS or rule["key"].startswith("numeric"):
                value = self._normalize_decimal(raw_value)
                if value is None:
                    messagebox.showerror("Error", f"Informe um numero valido em {caption}.", parent=self)
                    return None
                params[key] = value

        if rule["key"] in {"length_between", "numeric_between"}:
            lower = params.get("lower")
            upper = params.get("upper")
            if lower is None or upper is None:
                return None
            if float(lower) > float(upper):
                messagebox.showerror("Error", "O minimo nao pode ser maior que o maximo.", parent=self)
                return None

        if "stddevs" in params and float(params["stddevs"]) <= 0:
            messagebox.showerror("Error", "Use N desvios maior que zero.", parent=self)
            return None

        return params

    @staticmethod
    def _format_rule_description(rule: dict, params: dict, ignore_blank: bool) -> str:
        label = rule["label"]
        if not params:
            description = label
        elif "text" in params:
            description = f"{label}: {params['text']}"
        elif rule["key"] in {"length_between", "numeric_between"}:
            description = f"{label}: {params['lower']} e {params['upper']}"
        elif "stddevs" in params:
            description = f"{label}: {params['stddevs']}"
        elif "value" in params:
            description = f"{label}: {params['value']}"
        elif "limit" in params:
            description = f"{label}: {params['limit']}"
        else:
            description = label

        if ignore_blank:
            description += " (ignorando nulos/vazios)"
        return description

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        rule = self._selected_rule()
        params = self._read_params(rule)
        if params is None:
            return

        ignore_blank = bool(self.ignore_blank_var.get()) and rule["key"] not in self.BLANK_RULES
        self.result = {
            "rule": rule["key"],
            "rule_label": rule["label"],
            "params": params,
            "ignore_blank": ignore_blank,
            "description": self._format_rule_description(rule, params, ignore_blank),
        }
        self.destroy()


class RawExpandKeysDialog(ctk.CTkToplevel):
    def __init__(self, master, column_name: str, keys):
        super().__init__(master)

        self.title(f"Expandir {column_name}")
        self.geometry("420x560")
        self.minsize(340, 360)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Expandir: {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 12), sticky="w")

        keys_box = ctk.CTkScrollableFrame(self, corner_radius=12)
        keys_box.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="nsew")
        keys_box.grid_columnconfigure(0, weight=1)

        key_list = list(keys or [])
        if not key_list:
            ctk.CTkLabel(
                keys_box,
                text="Nenhuma chave encontrada.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            for row_index, key in enumerate(key_list):
                ctk.CTkButton(
                    keys_box,
                    text=str(key),
                    height=34,
                    command=lambda _key=key: self._choose(_key),
                ).grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.destroy).pack(side="right")

    def _choose(self, key):
        self.result = key
        self.destroy()


class RawExpandMultiValueModeDialog(ctk.CTkToplevel):
    def __init__(self, master, raw_key: str):
        super().__init__(master)

        self.title(f"Expandir {raw_key}")
        self.geometry("520x260")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Header com multiplos valores: {raw_key}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Essa chave possui listas em pelo menos uma linha. "
                "Escolha se a expansao deve usar apenas o primeiro valor da lista "
                "ou se os valores serao buscados em uma tabela filha."
            ),
            justify="left",
            anchor="w",
            wraplength=470,
        ).grid(row=1, column=0, padx=20, pady=(0, 18), sticky="ew")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            actions,
            text="Primeiro valor",
            command=lambda: self._select("first_value"),
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            actions,
            text="Tabela filha",
            command=lambda: self._select("child_table"),
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        ctk.CTkButton(
            self,
            text="Cancel",
            command=self._cancel,
        ).grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")

    def _select(self, mode: str):
        self.result = mode
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class RawExpandTypeMismatchDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        raw_key: str,
        child_column_label: str,
        selected_type: str,
        child_type: str,
        allow_child_type_conversion: bool = True,
    ):
        super().__init__(master)

        self.title("Tipo diferente na tabela filha")
        self.geometry("620x300")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Conflito de tipo",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        auto_message = (
            f"O header raw {raw_key} foi configurado como {selected_type}, "
            f"mas a coluna {child_column_label} usa o tipo {child_type}.\n\n"
            "Escolha se a expansao deve tentar converter para o tipo da coluna filha "
            "ou se voce vai informar uma expressao SQL para controlar a conversao."
        )
        if not allow_child_type_conversion:
            auto_message = (
                f"O header raw {raw_key} foi configurado como {selected_type}, "
                f"mas a coluna {child_column_label} usa o tipo {child_type}.\n\n"
                "A conversao automatica para esse tipo nao esta disponivel com a configuracao atual. "
                "Use uma expressao SQL para decidir como converter os dados."
            )

        ctk.CTkLabel(
            self,
            text=auto_message,
            justify="left",
            wraplength=570,
        ).grid(row=1, column=0, padx=20, pady=(0, 18), sticky="w")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)

        self.convert_button = ctk.CTkButton(
            actions,
            text=f"Converter para {child_type}",
            command=lambda: self._choose("child_type"),
            state="normal" if allow_child_type_conversion else "disabled",
        )
        self.convert_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            actions,
            text="Digitar SQL",
            command=lambda: self._choose("custom_sql"),
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right")

    def _choose(self, mode: str):
        self.result = mode
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class TableSelectionDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, heading: str, items):
        super().__init__(master)

        self.title(title)
        self.geometry("460x560")
        self.minsize(360, 380)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=heading,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 12), sticky="w")

        box = ctk.CTkScrollableFrame(self, corner_radius=12)
        box.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="nsew")
        box.grid_columnconfigure(0, weight=1)

        item_list = list(items or [])
        if not item_list:
            ctk.CTkLabel(
                box,
                text="Nenhum item disponivel.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            for row_index, item in enumerate(item_list):
                ctk.CTkButton(
                    box,
                    text=str(item),
                    height=34,
                    command=lambda selected=item: self._choose(selected),
                ).grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right")

    def _choose(self, value):
        self.result = value
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class RawGroupConfigDialog(ctk.CTkToplevel):
    ROLE_OPTIONS = ["Ignorar", "Chave", "Exclusivo"]

    def __init__(self, master, column_name: str, keys):
        super().__init__(master)

        self.title(f"Agrupar {column_name}")
        self.geometry("620x620")
        self.minsize(460, 420)
        self.result = None
        self._role_menus = {}

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Agrupar: {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Selecione exatamente um header como Chave. "
                "Os marcados como Ignorar consolidam os valores unicos do grupo; "
                "os marcados como Exclusivo listam todos os valores, inclusive repetidos."
            ),
            justify="left",
            anchor="w",
            wraplength=560,
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        content = ctk.CTkScrollableFrame(self, corner_radius=12)
        content.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)

        key_list = list(keys or [])
        if not key_list:
            ctk.CTkLabel(
                content,
                text="Nenhum header encontrado no raw.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            for row_index, key in enumerate(key_list):
                row = ctk.CTkFrame(content, corner_radius=10)
                row.grid(row=row_index, column=0, padx=4, pady=4, sticky="ew")
                row.grid_columnconfigure(0, weight=1)

                ctk.CTkLabel(
                    row,
                    text=str(key),
                    justify="left",
                    anchor="w",
                    wraplength=360,
                ).grid(row=0, column=0, padx=(12, 10), pady=10, sticky="ew")

                role_menu = ctk.CTkOptionMenu(
                    row,
                    values=self.ROLE_OPTIONS,
                    width=120,
                )
                role_menu.grid(row=0, column=1, padx=(0, 12), pady=10, sticky="e")
                role_menu.set("Ignorar")
                self._role_menus[str(key)] = role_menu

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Confirm", command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        key_headers = []
        equal_headers = []
        exclusive_headers = []

        for header_name, role_menu in self._role_menus.items():
            role = role_menu.get()
            if role == "Chave":
                key_headers.append(header_name)
            elif role == "Ignorar":
                equal_headers.append(header_name)
            elif role == "Exclusivo":
                exclusive_headers.append(header_name)

        if len(key_headers) != 1:
            messagebox.showerror(
                "Error",
                "Selecione exatamente um header como Chave.",
                parent=self,
            )
            return

        self.result = {
            "key_header": key_headers[0],
            "equal_headers": equal_headers,
            "exclusive_headers": exclusive_headers,
        }
        self.destroy()


class RawGroupColumnsConfigDialog(ctk.CTkToplevel):
    def __init__(self, master, column_name: str, keys):
        super().__init__(master)

        self.title(f"Agrupar colunas de {column_name}")
        self.geometry("760x700")
        self.minsize(620, 520)
        self.result = None
        self._keys = [str(item) for item in (keys or []) if str(item).strip()]
        self._groups = []

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Agrupar colunas: {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Escolha uma coluna mestre e indique, na ordem desejada, "
                "quais headers serao absorvidos por ela. "
                "O valor final da mestre vira uma lista com o valor atual dela "
                "seguido pelos valores das demais colunas selecionadas."
            ),
            justify="left",
            anchor="w",
            wraplength=700,
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.content = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.content.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            footer,
            text="+ Mestre",
            width=120,
            command=self._add_group,
        ).grid(row=0, column=0, sticky="w")

        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(actions, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(actions, text="Confirm", command=self._confirm).pack(side="right")

        if self._keys:
            self._add_group()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _default_source_key(self, master_header: str, used_headers=None) -> str:
        blocked = {str(master_header or "").strip()}
        blocked.update(str(item or "").strip() for item in (used_headers or []) if str(item or "").strip())
        for key in self._keys:
            if key not in blocked:
                return key
        return self._keys[0] if self._keys else ""

    def _add_group(self, initial_master: str | None = None, initial_sources=None):
        if not self._keys:
            return

        group_frame = ctk.CTkFrame(self.content, corner_radius=12)
        group_frame.pack(fill="x", padx=4, pady=6)

        header = ctk.CTkFrame(group_frame, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))

        ctk.CTkLabel(
            header,
            text="Coluna mestre",
            justify="left",
            anchor="w",
        ).pack(side="left")

        master_menu = ctk.CTkOptionMenu(
            header,
            values=self._keys,
            width=220,
        )
        master_menu.pack(side="left", padx=(12, 10))
        master_menu.set(initial_master if initial_master in self._keys else self._keys[0])

        group_data = {
            "frame": group_frame,
            "master_menu": master_menu,
            "sources_frame": None,
            "source_rows": [],
        }

        ctk.CTkButton(
            header,
            text="+ Coluna",
            width=96,
            command=lambda current_group=group_data: self._add_source_row(current_group),
        ).pack(side="right")

        ctk.CTkButton(
            header,
            text="X",
            width=34,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=lambda current_group=group_data: self._remove_group(current_group),
        ).pack(side="right", padx=(0, 8))

        ctk.CTkLabel(
            group_frame,
            text="Colunas agrupadas nesta ordem",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12)

        sources_frame = ctk.CTkFrame(group_frame, fg_color="transparent")
        sources_frame.pack(fill="x", padx=12, pady=(6, 12))
        group_data["sources_frame"] = sources_frame
        self._groups.append(group_data)

        source_values = [
            str(item)
            for item in (initial_sources or [])
            if str(item or "").strip()
        ]
        if not source_values:
            source_values = [self._default_source_key(master_menu.get())]

        for source_value in source_values:
            self._add_source_row(group_data, initial_source=source_value)

    def _remove_group(self, group_data: dict):
        if group_data not in self._groups:
            return

        self._groups.remove(group_data)
        group_data["frame"].destroy()

    def _add_source_row(self, group_data: dict, initial_source: str | None = None):
        source_row_frame = ctk.CTkFrame(group_data["sources_frame"], fg_color="transparent")
        source_row_frame.pack(fill="x", pady=4)

        ctk.CTkLabel(
            source_row_frame,
            text="Receber",
            justify="left",
            anchor="w",
            width=80,
        ).pack(side="left")

        used_headers = [row_data["menu"].get() for row_data in group_data["source_rows"]]
        source_menu = ctk.CTkOptionMenu(
            source_row_frame,
            values=self._keys,
            width=240,
        )
        source_menu.pack(side="left", padx=(12, 8))
        source_menu.set(
            initial_source
            if initial_source in self._keys
            else self._default_source_key(group_data["master_menu"].get(), used_headers=used_headers)
        )

        row_data = {
            "frame": source_row_frame,
            "menu": source_menu,
        }
        group_data["source_rows"].append(row_data)

        ctk.CTkButton(
            source_row_frame,
            text="X",
            width=34,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=lambda current_group=group_data, current_row=row_data: self._remove_source_row(
                current_group,
                current_row,
            ),
        ).pack(side="left")

    def _remove_source_row(self, group_data: dict, row_data: dict):
        if row_data not in group_data["source_rows"]:
            return

        group_data["source_rows"].remove(row_data)
        row_data["frame"].destroy()

    def _confirm(self):
        if not self._groups:
            messagebox.showerror(
                "Error",
                "Adicione ao menos um mestre para agrupar colunas.",
                parent=self,
            )
            return

        groups = []
        used_headers = set()

        for group_index, group_data in enumerate(self._groups, start=1):
            master_header = str(group_data["master_menu"].get() or "").strip()
            if not master_header:
                messagebox.showerror(
                    "Error",
                    f"Selecione a coluna mestre do agrupamento {group_index}.",
                    parent=self,
                )
                return

            source_headers = []
            local_headers = {master_header}
            for row_index, row_data in enumerate(group_data["source_rows"], start=1):
                source_header = str(row_data["menu"].get() or "").strip()
                if not source_header:
                    messagebox.showerror(
                        "Error",
                        f"Selecione a coluna recebida {row_index} do agrupamento {group_index}.",
                        parent=self,
                    )
                    return

                if source_header in local_headers:
                    messagebox.showerror(
                        "Error",
                        f"O header {source_header} esta repetido no agrupamento {group_index}.",
                        parent=self,
                    )
                    return

                source_headers.append(source_header)
                local_headers.add(source_header)

            if not source_headers:
                messagebox.showerror(
                    "Error",
                    f"Adicione ao menos uma coluna recebida no agrupamento {group_index}.",
                    parent=self,
                )
                return

            repeated_headers = [header for header in local_headers if header in used_headers]
            if repeated_headers:
                repeated_label = ", ".join(sorted(repeated_headers))
                messagebox.showerror(
                    "Error",
                    "Um mesmo header nao pode participar de mais de um agrupamento. "
                    f"Conflitos encontrados: {repeated_label}.",
                    parent=self,
                )
                return

            used_headers.update(local_headers)
            groups.append(
                {
                    "master_header": master_header,
                    "source_headers": source_headers,
                }
            )

        self.result = {"groups": groups}
        self.destroy()


class DataDictionaryEditor(ctk.CTkFrame):
    NUMERIC_TYPES = {"integer", "bigint", "smallint", "numeric", "real", "double precision"}
    DATE_TIME_TYPES = {"date", "timestamp", "timestamptz", "time"}
    UNIT_OPTIONS = [
        "dimensionless",
        "m",
        "km",
        "cm",
        "mm",
        "m2",
        "km2",
        "ha",
        "m3",
        "g",
        "kg",
        "t",
        "N",
        "kN",
        "Pa",
        "kPa",
        "MPa",
        "s",
        "sec",
        "min",
        "h",
        "day",
        "m/s",
        "km/h",
        "%",
        "veh/h",
        "veh/day",
        "custom",
    ]
    VALUE_DOMAIN_TEMPLATES = [
        "Custom",
        "Plausible range",
        "Less than",
        "Greater than",
        "Boolean mapping",
        "Date format",
    ]

    def __init__(
        self,
        master,
        column_name: str = "",
        column_type: str = "varchar",
        dictionary_lookup_callback=None,
    ):
        super().__init__(master, corner_radius=12, fg_color="#f4f7fb")

        self._dictionary_lookup_callback = dictionary_lookup_callback
        self._resolved_entry = None
        self._last_default_standard_name = ""
        self._column_type = ""
        self._declared_column_type = ""
        self._date_formats = []
        self._false_values = []
        self._true_values = []
        self._lookup_running = False
        self._template_fields = {}
        self.manual_value_domain_var = tk.BooleanVar(value=False)

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Data Dictionary",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=14, pady=(14, 8), sticky="w")

        ctk.CTkLabel(self, text="Standard name").grid(row=1, column=0, padx=(14, 8), pady=8, sticky="w")
        self.standard_name_entry = ctk.CTkEntry(self, placeholder_text="Standardized name in English")
        self.standard_name_entry.grid(row=1, column=1, padx=(0, 8), pady=8, sticky="ew")
        self.standard_name_entry.bind("<KeyRelease>", self._handle_standard_name_change)

        self.verify_button = ctk.CTkButton(
            self,
            text="Verificar",
            width=96,
            command=self._verify_existing_entry,
            state="normal" if dictionary_lookup_callback else "disabled",
        )
        self.verify_button.grid(row=1, column=2, padx=(0, 14), pady=8, sticky="e")

        self.reuse_existing_var = tk.BooleanVar(value=False)
        self.reuse_existing_check = ctk.CTkCheckBox(
            self,
            text="Reutilizar entrada encontrada",
            variable=self.reuse_existing_var,
        )
        self.reuse_existing_check.grid(row=2, column=0, columnspan=3, padx=14, pady=(0, 4), sticky="w")
        self.reuse_existing_check.configure(state="disabled")

        self.lookup_status_label = ctk.CTkLabel(
            self,
            text="Busca por standard name e aliases existentes.",
            justify="left",
            anchor="w",
            text_color="#64748b",
        )
        self.lookup_status_label.grid(row=3, column=0, columnspan=3, padx=14, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(self, text="Definition").grid(row=4, column=0, padx=(14, 8), pady=8, sticky="nw")
        self.definition_text = ctk.CTkTextbox(self, height=84)
        self.definition_text.grid(row=4, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")

        ctk.CTkLabel(self, text="Units").grid(row=5, column=0, padx=(14, 8), pady=8, sticky="w")
        self.units_menu = ctk.CTkOptionMenu(
            self,
            values=self.UNIT_OPTIONS,
            command=lambda _value: self._handle_units_change(),
        )
        self.units_menu.grid(row=5, column=1, padx=(0, 8), pady=8, sticky="ew")
        self.units_menu.set("dimensionless")

        self.custom_units_entry = ctk.CTkEntry(self, placeholder_text="Custom unit")
        self.custom_units_entry.grid(row=5, column=2, padx=(0, 14), pady=8, sticky="ew")
        self.custom_units_entry.bind("<KeyRelease>", lambda _event: self._handle_units_change())

        ctk.CTkLabel(self, text="Aliases").grid(row=6, column=0, padx=(14, 8), pady=8, sticky="w")
        self.aliases_entry = ctk.CTkEntry(self, placeholder_text="alias_1, alias_2")
        self.aliases_entry.grid(row=6, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")

        template_row = ctk.CTkFrame(self, fg_color="transparent")
        template_row.grid(row=7, column=0, columnspan=3, padx=14, pady=(4, 8), sticky="ew")
        template_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(template_row, text="Value domain template").grid(
            row=0,
            column=0,
            padx=(0, 10),
            pady=0,
            sticky="w",
        )
        self.value_domain_template_menu = ctk.CTkOptionMenu(
            template_row,
            values=self.VALUE_DOMAIN_TEMPLATES,
            command=lambda _value: self._handle_value_domain_template_change(),
        )
        self.value_domain_template_menu.grid(row=0, column=1, pady=0, sticky="ew")
        self.value_domain_template_menu.set("Custom")

        self.manual_value_domain_check = ctk.CTkCheckBox(
            template_row,
            text="Texto livre",
            variable=self.manual_value_domain_var,
            command=self._sync_value_domain_mode,
        )
        self.manual_value_domain_check.grid(row=0, column=2, padx=(10, 0), pady=0, sticky="e")

        self.template_fields_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.template_fields_frame.grid(row=8, column=0, columnspan=3, padx=14, pady=(0, 8), sticky="ew")
        self.template_fields_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Value domain").grid(row=9, column=0, padx=(14, 8), pady=8, sticky="nw")
        self.value_domain_text = ctk.CTkTextbox(self, height=72)
        self.value_domain_text.grid(row=9, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")

        self._sync_units_field()
        self.set_column_name(column_name)
        self.set_column_type(column_type)
        self._handle_value_domain_template_change()
        self._sync_value_domain_mode()

    @staticmethod
    def default_standard_name(column_name: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_]+", " ", str(column_name or "").strip())
        normalized = normalized.replace("_", " ")
        words = [word for word in normalized.split() if word]
        if not words:
            return ""
        return " ".join(word.capitalize() for word in words)

    @staticmethod
    def normalize_aliases_text(text: str) -> str:
        aliases = []
        seen = set()
        for raw_alias in str(text or "").split(","):
            alias = raw_alias.strip()
            if not alias:
                continue
            alias_key = alias.lower()
            if alias_key in seen:
                continue
            seen.add(alias_key)
            aliases.append(alias)
        return ", ".join(aliases)

    @staticmethod
    def _split_alias_values(text: str) -> list[str]:
        return [item.strip() for item in str(text or "").split(",") if item.strip()]

    def set_column_name(self, column_name: str):
        standard_name = self.default_standard_name(column_name)
        current_value = self.standard_name_entry.get().strip()
        if not current_value or current_value == self._last_default_standard_name:
            self.standard_name_entry.delete(0, "end")
            self.standard_name_entry.insert(0, standard_name)
        self._last_default_standard_name = standard_name

    @staticmethod
    def _normalize_column_type(column_type: str) -> str:
        normalized_type = str(column_type or "").strip().lower()
        aliases = {
            "serial": "integer",
            "bigserial": "bigint",
        }
        return aliases.get(normalized_type, normalized_type)

    def set_column_type(self, column_type: str):
        declared_type = str(column_type or "").strip().lower()
        normalized_type = self._normalize_column_type(column_type)
        previous_default_unit = self._default_unit_for_type(self._column_type)
        current_unit = self._current_units_value()
        self._declared_column_type = declared_type or normalized_type
        self._column_type = normalized_type

        default_unit = self._default_unit_for_type(normalized_type)
        if not current_unit or current_unit == previous_default_unit:
            self._set_units_value(default_unit)

        preferred_template = self._preferred_template_for_type(normalized_type)
        if self.value_domain_template_menu.get() != preferred_template:
            self.value_domain_template_menu.set(preferred_template)
        self.manual_value_domain_var.set(preferred_template == "Custom")
        self._handle_value_domain_template_change()

    def set_boolean_values(self, false_values, true_values):
        self._false_values = list(false_values or [])
        self._true_values = list(true_values or [])
        if self.value_domain_template_menu.get() == "Boolean mapping":
            self._render_template_fields()
        self._refresh_value_domain_preview()

    def set_date_formats(self, date_formats):
        self._date_formats = list(date_formats or [])
        if self.value_domain_template_menu.get() == "Date format":
            self._render_template_fields()
        self._refresh_value_domain_preview()

    def populate_from_entry(self, entry: dict, reuse_existing: bool = True):
        if not entry:
            return

        self._resolved_entry = dict(entry)

        self.standard_name_entry.delete(0, "end")
        self.standard_name_entry.insert(0, entry.get("standard_name") or "")

        self.definition_text.delete("1.0", "end")
        self.definition_text.insert("1.0", entry.get("definition") or "")

        self._set_units_value(entry.get("units") or "dimensionless")
        self._restore_value_domain_state(entry.get("value_domain") or "")

        self.aliases_entry.delete(0, "end")
        self.aliases_entry.insert(0, entry.get("aliases") or "")

        usage_count = int(entry.get("usage_count") or 0)
        status = "Entrada encontrada no dicionario."
        if usage_count:
            status = f"{status} Ja vinculada a {usage_count} coluna(s)."
        self.lookup_status_label.configure(text=status, text_color="#f97316")
        self.reuse_existing_check.configure(state="normal")
        self.reuse_existing_var.set(bool(reuse_existing))

    def _restore_value_domain_state(self, value_domain: str):
        value_domain_text = str(value_domain or "").strip()
        template_state = self._parse_value_domain_template(value_domain_text)
        template_name = template_state["template"]
        field_values = dict(template_state.get("fields") or {})

        self._true_values = self._split_alias_values(field_values.get("true_entry"))
        self._false_values = self._split_alias_values(field_values.get("false_entry"))
        self._date_formats = self._split_alias_values(field_values.get("formats_entry"))

        self.value_domain_template_menu.set(template_name)
        self._handle_value_domain_template_change()

        if template_name == "Custom":
            self._set_value_domain_text(value_domain_text, editable=True)
            return

        for field_name, field_value in field_values.items():
            widget = self._template_fields.get(field_name)
            if not widget:
                continue
            widget.delete(0, "end")
            if field_value:
                widget.insert(0, field_value)

        self.manual_value_domain_var.set(False)
        self._sync_value_domain_mode()
        restored_text = self.value_domain_text.get("1.0", "end-1c").strip()
        if restored_text != value_domain_text:
            self.value_domain_template_menu.set("Custom")
            self._handle_value_domain_template_change()
            self._set_value_domain_text(value_domain_text, editable=True)

    def _parse_value_domain_template(self, value_domain: str) -> dict:
        value_domain_text = str(value_domain or "").strip()
        custom_state = {"template": "Custom", "fields": {}}
        if not value_domain_text:
            return custom_state

        current_unit = self._current_units_value()
        expected_unit = "" if not current_unit or current_unit.lower() == "dimensionless" else current_unit

        range_match = re.fullmatch(
            r"Valid domain:\s*(.*?)\s+-\s+(.*?)\s*(?:\((.*?)\))?\.\s*",
            value_domain_text,
            flags=re.IGNORECASE,
        )
        if range_match and self._column_type in self.NUMERIC_TYPES:
            unit_value = str(range_match.group(3) or "").strip()
            if unit_value == expected_unit:
                return {
                    "template": "Plausible range",
                    "fields": {
                        "lower_entry": str(range_match.group(1) or "").strip(),
                        "upper_entry": str(range_match.group(2) or "").strip(),
                    },
                }

        threshold_match = re.fullmatch(
            r"Valid domain:\s*([<>])\s*(.*?)\s*(?:\((.*?)\))?\.\s*",
            value_domain_text,
            flags=re.IGNORECASE,
        )
        if threshold_match and self._column_type in self.NUMERIC_TYPES:
            unit_value = str(threshold_match.group(3) or "").strip()
            if unit_value == expected_unit:
                return {
                    "template": "Less than" if threshold_match.group(1) == "<" else "Greater than",
                    "fields": {
                        "limit_entry": str(threshold_match.group(2) or "").strip(),
                    },
                }

        boolean_match = re.fullmatch(
            r"Valid domain:\s*true\s*=\s*(.*?);\s*false\s*=\s*(.*?)\.\s*",
            value_domain_text,
            flags=re.IGNORECASE,
        )
        if boolean_match and self._column_type == "boolean":
            return {
                "template": "Boolean mapping",
                "fields": {
                    "true_entry": str(boolean_match.group(1) or "").strip(),
                    "false_entry": str(boolean_match.group(2) or "").strip(),
                },
            }

        datetime_match = re.fullmatch(
            r"Expected\s+(date|time|datetime)\s+formats?:\s*(.*?)\.\s*",
            value_domain_text,
            flags=re.IGNORECASE,
        )
        if datetime_match and self._column_type in self.DATE_TIME_TYPES:
            expected_kind = "datetime"
            if self._column_type == "date":
                expected_kind = "date"
            elif self._column_type == "time":
                expected_kind = "time"

            if str(datetime_match.group(1) or "").strip().lower() == expected_kind:
                return {
                    "template": "Date format",
                    "fields": {
                        "formats_entry": str(datetime_match.group(2) or "").strip(),
                    },
                }

        return custom_state

    def get_payload(self, parent=None) -> dict | None:
        standard_name = self.standard_name_entry.get().strip()
        definition = self.definition_text.get("1.0", "end-1c").strip()
        units = self._current_units_value()
        value_domain = self.value_domain_text.get("1.0", "end-1c").strip()
        aliases = self.normalize_aliases_text(self.aliases_entry.get())
        reuse_existing = bool(self.reuse_existing_var.get() and self._resolved_entry)
        manual_value_domain = bool(self.manual_value_domain_var.get())

        if not standard_name:
            messagebox.showerror("Error", "Informe o standard name.", parent=parent or self)
            self.standard_name_entry.focus_set()
            return None
        if not reuse_existing and not definition:
            messagebox.showerror("Error", "Informe a definition em ingles.", parent=parent or self)
            self.definition_text.focus_set()
            return None
        if not units:
            messagebox.showerror("Error", "Informe as units.", parent=parent or self)
            if self.units_menu.get() == "custom":
                self.custom_units_entry.focus_set()
            else:
                self.units_menu.focus_set()
            return None
        if not reuse_existing and not manual_value_domain:
            try:
                value_domain = self._build_auto_value_domain(validate=True)
            except ValueError as exc:
                messagebox.showerror("Error", str(exc), parent=parent or self)
                self._focus_first_template_input()
                return None
            self._set_value_domain_text(value_domain, editable=False)
        if not reuse_existing and not value_domain:
            messagebox.showerror("Error", "Informe o value domain.", parent=parent or self)
            self.value_domain_text.focus_set()
            return None

        resolved_standard_name = standard_name
        if reuse_existing:
            resolved_standard_name = str(self._resolved_entry.get("standard_name") or "").strip() or standard_name

        return {
            "standard_name": standard_name,
            "definition": definition,
            "units": units,
            "value_domain": value_domain,
            "aliases": aliases,
            "data_type": self._declared_column_type or self._column_type,
            "reuse_existing": reuse_existing,
            "existing_standard_name": resolved_standard_name if reuse_existing else "",
        }

    def _verify_existing_entry(self):
        if not self._dictionary_lookup_callback or self._lookup_running:
            return

        search_term = self.standard_name_entry.get().strip()
        if not search_term:
            messagebox.showerror("Error", "Digite o standard name para verificar.", parent=self.winfo_toplevel())
            self.standard_name_entry.focus_set()
            return

        self._lookup_running = True
        self.verify_button.configure(state="disabled")
        self.lookup_status_label.configure(text="Consultando dicionario...", text_color="#64748b")

        def worker():
            try:
                result = self._dictionary_lookup_callback(search_term)
            except Exception as exc:
                self.after(0, lambda: self._finish_lookup(search_term, None, str(exc)))
                return
            self.after(0, lambda: self._finish_lookup(search_term, result, None))

        threading.Thread(target=worker, daemon=True, name="dictionary-lookup").start()

    def _finish_lookup(self, search_term: str, entry: dict | None, error_message: str | None):
        if not self.winfo_exists():
            return
        self._lookup_running = False
        self.verify_button.configure(
            state="normal" if self._dictionary_lookup_callback else "disabled"
        )

        if error_message:
            self.lookup_status_label.configure(
                text=f"Falha ao consultar o dicionario: {error_message}",
                text_color="#b91c1c",
            )
            return

        if entry:
            self.populate_from_entry(entry, reuse_existing=True)
            return

        self._resolved_entry = None
        self.reuse_existing_var.set(False)
        self.reuse_existing_check.configure(state="disabled")
        self.lookup_status_label.configure(
            text=f"Nenhuma entrada encontrada para \"{search_term}\". Sera criada uma nova linha.",
            text_color="#64748b",
        )

    def _handle_standard_name_change(self, _event=None):
        if not self._resolved_entry:
            return

        current_value = self.standard_name_entry.get().strip()
        resolved_standard_name = str(self._resolved_entry.get("standard_name") or "").strip()
        if current_value == resolved_standard_name:
            return

        self._resolved_entry = None
        self.reuse_existing_var.set(False)
        self.reuse_existing_check.configure(state="disabled")
        self.lookup_status_label.configure(
            text="Standard name alterado. Verifique novamente para reutilizar ou preencha uma nova entrada.",
            text_color="#64748b",
        )

    def _current_units_value(self) -> str:
        selected = self.units_menu.get()
        if selected == "custom":
            return self.custom_units_entry.get().strip()
        return str(selected or "").strip()

    def _set_units_value(self, units_value: str):
        normalized = str(units_value or "").strip()
        if normalized in self.UNIT_OPTIONS and normalized != "custom":
            self.units_menu.set(normalized)
            self.custom_units_entry.delete(0, "end")
        else:
            self.units_menu.set("custom")
            self.custom_units_entry.delete(0, "end")
            if normalized:
                self.custom_units_entry.insert(0, normalized)
        self._sync_units_field()

    def _sync_units_field(self):
        if self.units_menu.get() == "custom":
            self.custom_units_entry.grid()
        else:
            self.custom_units_entry.grid_remove()

    def _handle_units_change(self):
        self._sync_units_field()
        self._refresh_value_domain_preview()

    def _handle_value_domain_template_change(self):
        self.manual_value_domain_var.set(self.value_domain_template_menu.get() == "Custom")
        self._render_template_fields()
        self._sync_value_domain_mode()

    def _sync_value_domain_mode(self):
        is_custom_template = self.value_domain_template_menu.get() == "Custom"
        if is_custom_template:
            self.manual_value_domain_var.set(True)
            self.manual_value_domain_check.configure(state="disabled")
        else:
            self.manual_value_domain_check.configure(state="normal")

        is_manual = bool(self.manual_value_domain_var.get())
        self.value_domain_text.configure(state="normal")
        if is_manual:
            return

        self._refresh_value_domain_preview()
        self.value_domain_text.configure(state="disabled")

    def _set_value_domain_text(self, text: str, editable: bool):
        self.value_domain_text.configure(state="normal")
        self.value_domain_text.delete("1.0", "end")
        self.value_domain_text.insert("1.0", text or "")
        self.value_domain_text.configure(state="normal" if editable else "disabled")

    def _bind_template_entry(self, entry):
        entry.bind("<KeyRelease>", lambda _event: self._refresh_value_domain_preview())

    def _focus_first_template_input(self):
        for key in (
            "lower_entry",
            "upper_entry",
            "limit_entry",
            "true_entry",
            "false_entry",
            "formats_entry",
        ):
            widget = self._template_fields.get(key)
            if widget:
                widget.focus_set()
                return
        self.value_domain_text.focus_set()

    def _value_domain_unit_suffix(self) -> str:
        units = self._current_units_value()
        if not units or units.lower() == "dimensionless":
            return ""
        return f" ({units})"

    def _build_auto_value_domain(self, validate: bool = False) -> str:
        template_name = self.value_domain_template_menu.get()
        if template_name == "Custom":
            return self.value_domain_text.get("1.0", "end-1c").strip()

        if template_name == "Plausible range":
            lower_value = self._template_fields["lower_entry"].get().strip()
            upper_value = self._template_fields["upper_entry"].get().strip()
            if not lower_value or not upper_value:
                if validate:
                    raise ValueError("Informe os limites minimo e maximo do dominio valido.")
                return ""
            return f"Valid domain: {lower_value} - {upper_value}{self._value_domain_unit_suffix()}."

        if template_name == "Less than":
            limit_value = self._template_fields["limit_entry"].get().strip()
            if not limit_value:
                if validate:
                    raise ValueError("Informe o limite numerico do dominio valido.")
                return ""
            return f"Valid domain: < {limit_value}{self._value_domain_unit_suffix()}."

        if template_name == "Greater than":
            limit_value = self._template_fields["limit_entry"].get().strip()
            if not limit_value:
                if validate:
                    raise ValueError("Informe o limite numerico do dominio valido.")
                return ""
            return f"Valid domain: > {limit_value}{self._value_domain_unit_suffix()}."

        if template_name == "Boolean mapping":
            true_values = self.normalize_aliases_text(self._template_fields["true_entry"].get())
            false_values = self.normalize_aliases_text(self._template_fields["false_entry"].get())
            if not true_values or not false_values:
                if validate:
                    raise ValueError("Informe os valores que representam verdadeiro e falso.")
                return ""
            return f"Valid domain: true = {true_values}; false = {false_values}."

        if template_name == "Date format":
            formats_value = self.normalize_aliases_text(self._template_fields["formats_entry"].get())
            if not formats_value:
                if validate:
                    raise ValueError("Informe ao menos um formato esperado para a data/hora.")
                return ""
            if "," in formats_value:
                if self._column_type == "date":
                    return f"Expected date formats: {formats_value}."
                if self._column_type == "time":
                    return f"Expected time formats: {formats_value}."
                return f"Expected datetime formats: {formats_value}."
            if self._column_type == "date":
                return f"Expected date format: {formats_value}."
            if self._column_type == "time":
                return f"Expected time format: {formats_value}."
            return f"Expected datetime format: {formats_value}."

        return ""

    def _refresh_value_domain_preview(self):
        if bool(self.manual_value_domain_var.get()):
            return
        auto_text = self._build_auto_value_domain(validate=False)
        self._set_value_domain_text(auto_text, editable=False)

    def _preferred_template_for_type(self, column_type: str) -> str:
        if column_type == "boolean":
            return "Boolean mapping"
        if column_type in self.DATE_TIME_TYPES:
            return "Date format"
        if column_type in self.NUMERIC_TYPES:
            return "Plausible range"
        return "Custom"

    def _default_unit_for_type(self, column_type: str) -> str:
        return "dimensionless"

    def _render_template_fields(self):
        for child in self.template_fields_frame.winfo_children():
            child.destroy()
        self.template_fields_frame.grid_columnconfigure(0, weight=1)
        self._template_fields = {}

        template_name = self.value_domain_template_menu.get()
        if template_name == "Plausible range":
            row = ctk.CTkFrame(self.template_fields_frame, fg_color="transparent")
            row.grid(row=0, column=0, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=1)
            lower_entry = ctk.CTkEntry(row, placeholder_text="Minimum")
            lower_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
            upper_entry = ctk.CTkEntry(row, placeholder_text="Maximum")
            upper_entry.grid(row=0, column=1, padx=(6, 0), sticky="ew")
            self._bind_template_entry(lower_entry)
            self._bind_template_entry(upper_entry)
            self._template_fields = {"lower_entry": lower_entry, "upper_entry": upper_entry}
            self._refresh_value_domain_preview()
            return

        if template_name in {"Less than", "Greater than"}:
            limit_entry = ctk.CTkEntry(self.template_fields_frame, placeholder_text="Numeric threshold")
            limit_entry.grid(row=0, column=0, sticky="ew")
            self._bind_template_entry(limit_entry)
            self._template_fields = {"limit_entry": limit_entry}
            self._refresh_value_domain_preview()
            return

        if template_name == "Boolean mapping":
            row = ctk.CTkFrame(self.template_fields_frame, fg_color="transparent")
            row.grid(row=0, column=0, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=1)
            true_entry = ctk.CTkEntry(row, placeholder_text="true, yes, 1")
            true_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
            false_entry = ctk.CTkEntry(row, placeholder_text="false, no, 0")
            false_entry.grid(row=0, column=1, padx=(6, 0), sticky="ew")
            if self._true_values:
                true_entry.insert(0, ", ".join(self._true_values))
            if self._false_values:
                false_entry.insert(0, ", ".join(self._false_values))
            self._bind_template_entry(true_entry)
            self._bind_template_entry(false_entry)
            self._template_fields = {"true_entry": true_entry, "false_entry": false_entry}
            self._refresh_value_domain_preview()
            return

        if template_name == "Date format":
            formats_entry = ctk.CTkEntry(self.template_fields_frame, placeholder_text="DD/MM/YYYY")
            formats_entry.grid(row=0, column=0, sticky="ew")
            if self._date_formats:
                formats_entry.insert(0, ", ".join(self._date_formats))
            self._template_fields = {"formats_entry": formats_entry}
            self._bind_template_entry(formats_entry)
            self._refresh_value_domain_preview()
            return

        self._refresh_value_domain_preview()

    def _apply_value_domain_template(self):
        try:
            template_text = self._build_auto_value_domain(validate=True)
        except ValueError as exc:
            messagebox.showerror("Error", str(exc), parent=self.winfo_toplevel())
            self._focus_first_template_input()
            return
        self._set_value_domain_text(template_text, editable=bool(self.manual_value_domain_var.get()))


class RawExpandColumnDialog(ctk.CTkToplevel):
    COLUMN_TYPES = [
        "varchar",
        "boolean",
        "integer",
        "bigint",
        "smallint",
        "numeric",
        "real",
        "double precision",
        "date",
        "timestamp",
        "timestamptz",
        "time",
        "uuid",
        "jsonb",
    ]
    DATE_TIME_TYPES = {"date", "timestamp", "timestamptz", "time"}

    def __init__(self, master, raw_key: str, dictionary_lookup_callback=None):
        super().__init__(master)

        self.title(f"Expandir chave {raw_key}")
        self.geometry("760x780")
        self.resizable(False, False)
        self.result = None
        self.extra_date_format_entries = []

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.content = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.content,
            text=f"Expandir: {raw_key}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(self.content, text="Nome da coluna").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.column_name_entry = ctk.CTkEntry(self.content)
        self.column_name_entry.grid(row=1, column=1, padx=20, pady=8, sticky="ew")
        self.column_name_entry.insert(0, self._default_column_name(raw_key))
        self.column_name_entry.bind("<KeyRelease>", lambda _event: self._sync_dictionary_context())

        ctk.CTkLabel(self.content, text="Type").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.type_menu = ctk.CTkOptionMenu(
            self.content,
            values=self.COLUMN_TYPES,
            command=lambda _value: self._sync_type_fields(),
        )
        self.type_menu.grid(row=2, column=1, padx=20, pady=8, sticky="ew")
        self.type_menu.set("varchar")

        self.false_values_label = ctk.CTkLabel(self.content, text="Valores falsos")
        self.false_values_label.grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.false_values_entry = ctk.CTkEntry(self.content)
        self.false_values_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")
        self.false_values_entry.insert(0, "false,f,0,nao,n,no")
        self.false_values_entry.bind("<KeyRelease>", lambda _event: self._sync_dictionary_context())

        self.true_values_label = ctk.CTkLabel(self.content, text="Valores verdadeiros")
        self.true_values_label.grid(row=4, column=0, padx=20, pady=8, sticky="w")
        self.true_values_entry = ctk.CTkEntry(self.content)
        self.true_values_entry.grid(row=4, column=1, padx=20, pady=8, sticky="ew")
        self.true_values_entry.insert(0, "true,t,1,sim,s,yes,y")
        self.true_values_entry.bind("<KeyRelease>", lambda _event: self._sync_dictionary_context())

        self.date_format_label = ctk.CTkLabel(self.content, text="Formato da data/hora")
        self.date_format_label.grid(row=5, column=0, padx=20, pady=8, sticky="w")
        self.date_format_row = ctk.CTkFrame(self.content, fg_color="transparent")
        self.date_format_row.grid(row=5, column=1, padx=20, pady=8, sticky="ew")
        self.date_format_row.grid_columnconfigure(0, weight=1)
        self.date_format_entry = ctk.CTkEntry(self.date_format_row, placeholder_text="DD/MM/AAAA HH:MM")
        self.date_format_entry.grid(row=0, column=0, sticky="ew")
        self.date_format_entry.bind("<KeyRelease>", lambda _event: self._sync_dictionary_context())
        self.add_date_format_button = ctk.CTkButton(
            self.date_format_row,
            text="+",
            width=34,
            command=self._add_date_format_entry,
        )
        self.add_date_format_button.grid(row=0, column=1, padx=(8, 0))

        self.extra_date_formats_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.extra_date_formats_frame.grid(row=6, column=1, padx=20, pady=(0, 8), sticky="ew")
        self.extra_date_formats_frame.grid_columnconfigure(0, weight=1)

        self.dictionary_editor = DataDictionaryEditor(
            self.content,
            column_name=self.column_name_entry.get().strip(),
            column_type=self.type_menu.get(),
            dictionary_lookup_callback=dictionary_lookup_callback,
        )
        self.dictionary_editor.grid(row=7, column=0, columnspan=2, padx=20, pady=(8, 0), sticky="ew")

        buttons = ctk.CTkFrame(self.content, fg_color="transparent")
        buttons.grid(row=8, column=0, columnspan=2, padx=20, pady=24, sticky="e")
        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")

        self._sync_type_fields()

    @staticmethod
    def _default_column_name(raw_key: str) -> str:
        value = str(raw_key or "").strip().lower()
        value = re.sub(r"[^a-z0-9]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        if not value or not re.match(r"^[a-z]", value):
            value = f"raw_{value}" if value else "raw_value"
        return value[:58].rstrip("_") or "raw_value"

    @staticmethod
    def _split_values(text: str) -> list[str]:
        return [
            value.strip().lower()
            for value in str(text or "").split(",")
            if value.strip()
        ]

    def _is_boolean_type(self) -> bool:
        return self.type_menu.get() == "boolean"

    def _is_date_time_type(self) -> bool:
        return self.type_menu.get() in self.DATE_TIME_TYPES

    def _sync_type_fields(self):
        if self._is_boolean_type():
            self.false_values_label.grid()
            self.false_values_entry.grid()
            self.true_values_label.grid()
            self.true_values_entry.grid()
        else:
            self.false_values_label.grid_remove()
            self.false_values_entry.grid_remove()
            self.true_values_label.grid_remove()
            self.true_values_entry.grid_remove()

        if self._is_date_time_type():
            self.date_format_label.grid()
            self.date_format_row.grid()
            self.extra_date_formats_frame.grid()
        else:
            self.date_format_label.grid_remove()
            self.date_format_row.grid_remove()
            self.extra_date_formats_frame.grid_remove()

        self._sync_dictionary_context()

    def _add_date_format_entry(self):
        row_index = len(self.extra_date_format_entries)
        row = ctk.CTkFrame(self.extra_date_formats_frame, fg_color="transparent")
        row.grid(row=row_index, column=0, pady=(0, 6), sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(row, placeholder_text="Outro formato")
        entry.grid(row=0, column=0, sticky="ew")
        remove_button = ctk.CTkButton(
            row,
            text="-",
            width=34,
            command=lambda item=row: self._remove_date_format_entry(item),
        )
        remove_button.grid(row=0, column=1, padx=(8, 0))
        self.extra_date_format_entries.append((row, entry))
        entry.bind("<KeyRelease>", lambda _event: self._sync_dictionary_context())

    def _remove_date_format_entry(self, row):
        self.extra_date_format_entries = [
            item for item in self.extra_date_format_entries if item[0] != row
        ]
        row.destroy()
        self._sync_dictionary_context()

    def _collect_date_formats(self) -> list[str]:
        formats = [self.date_format_entry.get().strip()]
        formats.extend(entry.get().strip() for _row, entry in self.extra_date_format_entries)
        deduped = []
        seen = set()
        for item in formats:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _sync_dictionary_context(self):
        if not hasattr(self, "dictionary_editor"):
            return
        self.dictionary_editor.set_column_name(self.column_name_entry.get().strip())
        self.dictionary_editor.set_column_type(self.type_menu.get())
        self.dictionary_editor.set_boolean_values(
            self._split_values(self.false_values_entry.get()),
            self._split_values(self.true_values_entry.get()),
        )
        self.dictionary_editor.set_date_formats(self._collect_date_formats())

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        column_name = self.column_name_entry.get().strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", column_name):
            messagebox.showerror(
                "Error",
                "Use apenas letras minusculas, numeros e underscore, sem espacos.",
                parent=self,
            )
            return
        if len(column_name) > 58:
            messagebox.showerror("Error", "Use no maximo 58 caracteres.", parent=self)
            return

        column_type = self.type_menu.get()
        false_values = self._split_values(self.false_values_entry.get())
        true_values = self._split_values(self.true_values_entry.get())
        date_formats = self._collect_date_formats()
        if self._is_boolean_type() and (not false_values or not true_values):
            messagebox.showerror("Error", "Informe valores falsos e verdadeiros.", parent=self)
            return
        if self._is_date_time_type() and not date_formats:
            messagebox.showerror("Error", "Informe pelo menos um formato de data/hora.", parent=self)
            return

        dictionary_payload = self.dictionary_editor.get_payload(parent=self)
        if not dictionary_payload:
            return

        self.result = {
            "column_name": column_name,
            "column_type": column_type,
            "false_values": false_values,
            "true_values": true_values,
            "date_formats": date_formats,
            "dictionary": dictionary_payload,
        }
        self.destroy()


class RawExpandChildTableDialog(ctk.CTkToplevel):
    DATE_TIME_TYPES = RawExpandColumnDialog.DATE_TIME_TYPES
    TYPE_ALIASES = {
        "bool": "boolean",
        "character varying": "varchar",
        "character": "varchar",
        "text": "varchar",
        "decimal": "numeric",
        "float4": "real",
        "float8": "double precision",
        "int2": "smallint",
        "int4": "integer",
        "int8": "bigint",
        "json": "jsonb",
        "timestamp without time zone": "timestamp",
        "timestamp with time zone": "timestamptz",
    }

    def __init__(
        self,
        master,
        raw_key: str,
        child_table_name: str,
        parent_field_options,
        child_field_options,
        multi_value_raw_keys,
        dictionary_lookup_callback=None,
    ):
        super().__init__(master)

        self.title(f"Expandir {raw_key} via tabela filha")
        self.geometry("980x720")
        self.resizable(False, False)
        self.result = None
        self.mapping_rows = []
        self._parent_options_by_label = {
            option["label"]: option
            for option in (parent_field_options or [])
            if option.get("label")
        }
        self._child_options_by_label = {
            option["label"]: option
            for option in (child_field_options or [])
            if option.get("label")
        }
        self._placeholder_label = "Selecione..."
        self._base_raw_key = str(raw_key or "").strip()
        self._raw_key_values = []
        seen_raw_keys = set()
        for item in [self._base_raw_key, *(multi_value_raw_keys or [])]:
            raw_key_name = str(item or "").strip()
            if not raw_key_name or raw_key_name in seen_raw_keys:
                continue
            seen_raw_keys.add(raw_key_name)
            self._raw_key_values.append(raw_key_name)

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Expandir via tabela filha: {raw_key}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Selecione a chave de ligacao com a tabela filha e adicione outros headers "
                "multivalorados para expandi-los no mesmo mestre. "
                "As linhas serao inseridas apenas na tabela filha. "
                "Se a coluna da filha for booleana ou temporal, informe os parametros de conversao."
            ),
            justify="left",
            anchor="w",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 12), sticky="ew")

        ctk.CTkLabel(self, text="Tabela filha").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        ctk.CTkLabel(self, text=child_table_name, anchor="w").grid(
            row=2,
            column=1,
            padx=20,
            pady=8,
            sticky="ew",
        )

        parent_labels = [self._placeholder_label] + list(self._parent_options_by_label.keys())
        child_labels = [self._placeholder_label] + list(self._child_options_by_label.keys())

        ctk.CTkLabel(self, text="Chave na tabela atual").grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.parent_key_menu = ctk.CTkOptionMenu(self, values=parent_labels)
        self.parent_key_menu.grid(row=3, column=1, padx=20, pady=8, sticky="ew")
        self.parent_key_menu.set(self._placeholder_label)

        ctk.CTkLabel(self, text="Chave correspondente na tabela filha").grid(
            row=4,
            column=0,
            padx=20,
            pady=8,
            sticky="w",
        )
        self.child_key_menu = ctk.CTkOptionMenu(self, values=child_labels)
        self.child_key_menu.grid(row=4, column=1, padx=20, pady=8, sticky="ew")
        self.child_key_menu.set(self._placeholder_label)

        ctk.CTkLabel(
            self,
            text="Mapeamentos de valores",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=5, column=0, columnspan=2, padx=20, pady=(12, 8), sticky="w")

        self.mappings_frame = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.mappings_frame.grid(row=6, column=0, columnspan=2, padx=20, pady=(0, 12), sticky="nsew")
        self.mappings_frame.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=7, column=0, columnspan=2, padx=20, pady=(0, 20), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        self.add_mapping_button = ctk.CTkButton(
            footer,
            text="+ Coluna",
            width=110,
            command=self._add_extra_mapping_row,
        )
        self.add_mapping_button.grid(row=0, column=0, sticky="w")

        buttons = ctk.CTkFrame(footer, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")

        self._add_mapping_row(initial_raw_key=self._base_raw_key, fixed_raw_key=True)

    @staticmethod
    def _split_values(text: str) -> list[str]:
        return RawExpandColumnDialog._split_values(text)

    @classmethod
    def _normalize_column_type(cls, column_type: str) -> str:
        value = str(column_type or "").strip().lower()
        return cls.TYPE_ALIASES.get(value, value)

    def _unused_raw_key_candidates(self) -> list[str]:
        selected_keys = {
            str(row_info["raw_key_menu"].get() or "").strip()
            for row_info in self.mapping_rows
        }
        return [raw_key for raw_key in self._raw_key_values if raw_key not in selected_keys]

    def _match_child_value_label(self, raw_key: str) -> str:
        raw_key_value = str(raw_key or "").strip().lower()
        for option in self._child_options_by_label.values():
            if str(option.get("name") or "").strip().lower() == raw_key_value:
                return option["label"]
        return self._placeholder_label

    def _resolve_selection(self, label: str, mapping: dict):
        if label == self._placeholder_label:
            return None
        return mapping.get(label)

    def _child_option_for_row(self, row_info: dict):
        return self._resolve_selection(
            str(row_info["child_value_menu"].get() or "").strip(),
            self._child_options_by_label,
        )

    def _add_extra_mapping_row(self):
        candidates = self._unused_raw_key_candidates()
        if not candidates:
            messagebox.showerror(
                "Error",
                "Nao ha outros headers multivalorados disponiveis para adicionar.",
                parent=self,
            )
            return

        self._add_mapping_row(initial_raw_key=candidates[0], fixed_raw_key=False)

    def _add_mapping_row(self, initial_raw_key: str, fixed_raw_key: bool):
        row_index = len(self.mapping_rows)
        card = ctk.CTkFrame(self.mappings_frame, corner_radius=12)
        card.grid(row=row_index, column=0, padx=2, pady=(0, 10), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        title_label = ctk.CTkLabel(
            card,
            text=f"Valor {row_index + 1}",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        title_label.grid(row=0, column=0, columnspan=2, padx=14, pady=(12, 8), sticky="w")

        remove_button = ctk.CTkButton(
            card,
            text="X",
            width=34,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=lambda current=card: self._remove_mapping_row(current),
        )
        remove_button.grid(row=0, column=2, padx=(0, 14), pady=(12, 8), sticky="e")

        ctk.CTkLabel(card, text="Header raw").grid(row=1, column=0, padx=(14, 8), pady=8, sticky="w")
        raw_key_values = [initial_raw_key] if fixed_raw_key else list(self._raw_key_values)
        raw_key_menu = ctk.CTkOptionMenu(
            card,
            values=raw_key_values,
            command=lambda selected, current_card=card: self._on_mapping_raw_key_change(current_card, selected),
        )
        raw_key_menu.grid(row=1, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")
        raw_key_menu.set(initial_raw_key)
        if fixed_raw_key:
            raw_key_menu.configure(state="disabled")

        ctk.CTkLabel(card, text="Coluna na tabela filha").grid(row=2, column=0, padx=(14, 8), pady=8, sticky="w")
        child_value_menu = ctk.CTkOptionMenu(
            card,
            values=[self._placeholder_label] + list(self._child_options_by_label.keys()),
            command=lambda selected, current_card=card: self._on_child_value_change(current_card, selected),
        )
        child_value_menu.grid(row=2, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")
        matched_child_label = self._match_child_value_label(initial_raw_key)
        child_value_menu.set(matched_child_label)

        ctk.CTkLabel(card, text="Tipo da coluna filha").grid(row=3, column=0, padx=(14, 8), pady=8, sticky="w")
        child_type_value_label = ctk.CTkLabel(card, text="-", anchor="w")
        child_type_value_label.grid(row=3, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")

        false_values_label = ctk.CTkLabel(card, text="Valores falsos")
        false_values_label.grid(row=4, column=0, padx=(14, 8), pady=8, sticky="w")
        false_values_entry = ctk.CTkEntry(card)
        false_values_entry.grid(row=4, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")
        false_values_entry.insert(0, "false,f,0,nao,n,no")

        true_values_label = ctk.CTkLabel(card, text="Valores verdadeiros")
        true_values_label.grid(row=5, column=0, padx=(14, 8), pady=8, sticky="w")
        true_values_entry = ctk.CTkEntry(card)
        true_values_entry.grid(row=5, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")
        true_values_entry.insert(0, "true,t,1,sim,s,yes,y")

        date_format_label = ctk.CTkLabel(card, text="Formato da data/hora")
        date_format_label.grid(row=6, column=0, padx=(14, 8), pady=8, sticky="w")
        date_format_row = ctk.CTkFrame(card, fg_color="transparent")
        date_format_row.grid(row=6, column=1, columnspan=2, padx=(0, 14), pady=8, sticky="ew")
        date_format_row.grid_columnconfigure(0, weight=1)
        date_format_entry = ctk.CTkEntry(date_format_row, placeholder_text="DD/MM/AAAA HH:MM")
        date_format_entry.grid(row=0, column=0, sticky="ew")
        add_date_format_button = ctk.CTkButton(
            date_format_row,
            text="+",
            width=34,
            command=lambda current=card: self._add_date_format_entry(current),
        )
        add_date_format_button.grid(row=0, column=1, padx=(8, 0))

        extra_date_formats_frame = ctk.CTkFrame(card, fg_color="transparent")
        extra_date_formats_frame.grid(row=7, column=1, columnspan=2, padx=(0, 14), pady=(0, 8), sticky="ew")
        extra_date_formats_frame.grid_columnconfigure(0, weight=1)

        row_info = {
            "card": card,
            "title_label": title_label,
            "remove_button": remove_button,
            "fixed_raw_key": fixed_raw_key,
            "raw_key_menu": raw_key_menu,
            "child_value_menu": child_value_menu,
            "child_type_value_label": child_type_value_label,
            "last_auto_child_label": matched_child_label,
            "false_values_label": false_values_label,
            "false_values_entry": false_values_entry,
            "true_values_label": true_values_label,
            "true_values_entry": true_values_entry,
            "date_format_label": date_format_label,
            "date_format_row": date_format_row,
            "date_format_entry": date_format_entry,
            "extra_date_formats_frame": extra_date_formats_frame,
            "extra_date_format_entries": [],
        }
        self.mapping_rows.append(row_info)
        self._sync_mapping_rows()
        self._sync_mapping_type_fields(card)

    def _remove_mapping_row(self, card):
        target_index = next(
            (
                index
                for index, row_info in enumerate(self.mapping_rows)
                if row_info["card"] == card
            ),
            None,
        )
        if target_index is None:
            return

        row_info = self.mapping_rows[target_index]
        if row_info["fixed_raw_key"]:
            return

        self.mapping_rows.pop(target_index)
        card.destroy()
        self._sync_mapping_rows()

    def _sync_mapping_rows(self):
        for row_index, row_info in enumerate(self.mapping_rows):
            row_info["card"].grid_configure(row=row_index)
            row_info["title_label"].configure(text=f"Valor {row_index + 1}")
            row_info["remove_button"].configure(
                state="disabled" if row_info["fixed_raw_key"] else "normal"
            )

    def _on_mapping_raw_key_change(self, card, selected_raw_key: str):
        row_info = next((item for item in self.mapping_rows if item["card"] == card), None)
        if not row_info:
            return

        matched_child_label = self._match_child_value_label(selected_raw_key)
        current_child_label = row_info["child_value_menu"].get()
        if current_child_label in {self._placeholder_label, row_info["last_auto_child_label"]}:
            row_info["child_value_menu"].set(matched_child_label)
        row_info["last_auto_child_label"] = matched_child_label
        self._sync_mapping_type_fields(card)

    def _on_child_value_change(self, card, selected_label: str):
        row_info = next((item for item in self.mapping_rows if item["card"] == card), None)
        if not row_info:
            return
        row_info["last_auto_child_label"] = str(selected_label or "").strip()
        self._sync_mapping_type_fields(card)

    def _is_boolean_type(self, row_info: dict) -> bool:
        child_option = self._child_option_for_row(row_info)
        return self._normalize_column_type((child_option or {}).get("type")) == "boolean"

    def _is_date_time_type(self, row_info: dict) -> bool:
        child_option = self._child_option_for_row(row_info)
        return self._normalize_column_type((child_option or {}).get("type")) in self.DATE_TIME_TYPES

    def _sync_mapping_type_fields(self, card):
        row_info = next((item for item in self.mapping_rows if item["card"] == card), None)
        if not row_info:
            return

        child_option = self._child_option_for_row(row_info) or {}
        child_type_text = str(child_option.get("type") or "").strip() or "-"
        row_info["child_type_value_label"].configure(text=child_type_text)

        if self._is_boolean_type(row_info):
            row_info["false_values_label"].grid()
            row_info["false_values_entry"].grid()
            row_info["true_values_label"].grid()
            row_info["true_values_entry"].grid()
        else:
            row_info["false_values_label"].grid_remove()
            row_info["false_values_entry"].grid_remove()
            row_info["true_values_label"].grid_remove()
            row_info["true_values_entry"].grid_remove()

        if self._is_date_time_type(row_info):
            row_info["date_format_label"].grid()
            row_info["date_format_row"].grid()
            row_info["extra_date_formats_frame"].grid()
        else:
            row_info["date_format_label"].grid_remove()
            row_info["date_format_row"].grid_remove()
            row_info["extra_date_formats_frame"].grid_remove()

    def _add_date_format_entry(self, card):
        row_info = next((item for item in self.mapping_rows if item["card"] == card), None)
        if not row_info:
            return

        row_index = len(row_info["extra_date_format_entries"])
        row = ctk.CTkFrame(row_info["extra_date_formats_frame"], fg_color="transparent")
        row.grid(row=row_index, column=0, pady=(0, 6), sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(row, placeholder_text="Outro formato")
        entry.grid(row=0, column=0, sticky="ew")
        remove_button = ctk.CTkButton(
            row,
            text="X",
            width=34,
            command=lambda item=row, current=card: self._remove_date_format_entry(current, item),
        )
        remove_button.grid(row=0, column=1, padx=(8, 0))
        row_info["extra_date_format_entries"].append((row, entry))

    def _remove_date_format_entry(self, card, row):
        row_info = next((item for item in self.mapping_rows if item["card"] == card), None)
        if not row_info:
            return

        row_info["extra_date_format_entries"] = [
            item for item in row_info["extra_date_format_entries"] if item[0] != row
        ]
        row.destroy()

    def _collect_date_formats(self, row_info: dict) -> list[str]:
        formats = [row_info["date_format_entry"].get().strip()]
        formats.extend(entry.get().strip() for _row, entry in row_info["extra_date_format_entries"])
        deduped = []
        seen = set()
        for item in formats:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        parent_key_source = self._resolve_selection(self.parent_key_menu.get(), self._parent_options_by_label)
        child_key_source = self._resolve_selection(self.child_key_menu.get(), self._child_options_by_label)
        if not parent_key_source or not child_key_source:
            messagebox.showerror(
                "Error",
                "Selecione a chave da tabela atual e a chave da tabela filha.",
                parent=self,
            )
            return

        if not self.mapping_rows:
            messagebox.showerror("Error", "Adicione ao menos um mapeamento de valores.", parent=self)
            return

        value_mappings = []
        seen_raw_keys = set()
        seen_child_value_columns = set()
        for row_index, row_info in enumerate(self.mapping_rows, start=1):
            raw_key_value = str(row_info["raw_key_menu"].get() or "").strip()
            child_value_source = self._resolve_selection(
                row_info["child_value_menu"].get(),
                self._child_options_by_label,
            )
            if not raw_key_value or not child_value_source:
                messagebox.showerror(
                    "Error",
                    f"Preencha o header raw e a coluna da tabela filha do valor {row_index}.",
                    parent=self,
                )
                return

            if raw_key_value in seen_raw_keys:
                messagebox.showerror(
                    "Error",
                    f"O header raw {raw_key_value} foi selecionado mais de uma vez.",
                    parent=self,
                )
                return

            child_value_column_name = str(child_value_source.get("name") or "").strip()
            if child_value_column_name in seen_child_value_columns:
                messagebox.showerror(
                    "Error",
                    f"A coluna da filha {child_value_column_name} foi informada mais de uma vez.",
                    parent=self,
                )
                return

            child_column_type = self._normalize_column_type(child_value_source.get("type"))
            false_values = self._split_values(row_info["false_values_entry"].get())
            true_values = self._split_values(row_info["true_values_entry"].get())
            date_formats = self._collect_date_formats(row_info)
            if self._is_boolean_type(row_info) and (not false_values or not true_values):
                messagebox.showerror("Error", "Informe valores falsos e verdadeiros.", parent=self)
                return
            if self._is_date_time_type(row_info) and not date_formats:
                messagebox.showerror("Error", "Informe pelo menos um formato de data/hora.", parent=self)
                return

            seen_raw_keys.add(raw_key_value)
            seen_child_value_columns.add(child_value_column_name)
            value_mappings.append(
                {
                    "raw_key": raw_key_value,
                    "child_value_source": child_value_source,
                    "column_name": child_value_column_name,
                    "column_type": child_column_type or str(child_value_source.get("type") or "").strip().lower(),
                    "false_values": false_values,
                    "true_values": true_values,
                    "date_formats": date_formats,
                }
            )

        primary_mapping = value_mappings[0]
        self.result = {
            "expand_mode": "child_table",
            "parent_key_source": parent_key_source,
            "child_key_source": child_key_source,
            "child_value_source": primary_mapping["child_value_source"],
            "column_name": primary_mapping["column_name"],
            "column_type": primary_mapping["column_type"],
            "false_values": primary_mapping["false_values"],
            "true_values": primary_mapping["true_values"],
            "date_formats": primary_mapping["date_formats"],
            "value_mappings": value_mappings,
        }
        self.destroy()


class CreateColumnsDialog(ctk.CTkToplevel):
    MAX_COLUMN_NAME_LENGTH = 58
    COLUMN_TYPES = [
        "varchar",
        "boolean",
        "integer",
        "serial",
        "bigint",
        "bigserial",
        "smallint",
        "numeric",
        "real",
        "double precision",
        "date",
        "timestamp",
        "timestamptz",
        "time",
        "uuid",
        "jsonb",
    ]

    def __init__(self, master, dictionary_lookup_callback=None):
        super().__init__(master)

        self.title("Create columns")
        self.geometry("920x760")
        self.resizable(False, False)
        self.result = None
        self.column_rows = []
        self._dictionary_lookup_callback = dictionary_lookup_callback

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="Create columns",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text="Informe nomes, tipos e o dicionario de dados de cada coluna na ordem de criacao.",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.rows_frame = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.rows_frame.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.rows_frame.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        self.add_button = ctk.CTkButton(
            footer,
            text="+",
            width=40,
            command=self._add_column_row,
        )
        self.add_button.grid(row=0, column=0, sticky="w")

        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(actions, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(actions, text="Confirm", command=self._confirm).pack(side="right")

        self._add_column_row()

    def _add_column_row(self, column_name: str = "", column_type: str = "varchar"):
        row_index = len(self.column_rows)
        card = ctk.CTkFrame(self.rows_frame, corner_radius=12)
        card.grid(row=row_index, column=0, padx=2, pady=(0, 10), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        title_label = ctk.CTkLabel(
            card,
            text=f"Coluna {row_index + 1}",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        title_label.grid(row=0, column=0, columnspan=3, padx=14, pady=(12, 8), sticky="w")

        ctk.CTkLabel(card, text="Nome da coluna").grid(row=1, column=0, padx=(14, 8), pady=(0, 10), sticky="w")
        name_entry = ctk.CTkEntry(card, placeholder_text="minha_coluna")
        name_entry.grid(row=1, column=1, padx=(0, 8), pady=(0, 10), sticky="ew")
        if column_name:
            name_entry.insert(0, column_name)

        ctk.CTkLabel(card, text="Type").grid(row=2, column=0, padx=(14, 8), pady=(0, 14), sticky="w")
        type_menu = ctk.CTkOptionMenu(
            card,
            values=self.COLUMN_TYPES,
            command=lambda _value, current_card=card: self._sync_column_dictionary(current_card),
        )
        type_menu.grid(row=2, column=1, padx=(0, 8), pady=(0, 14), sticky="ew")
        type_menu.set(column_type if column_type in self.COLUMN_TYPES else "varchar")

        remove_button = ctk.CTkButton(
            card,
            text="X",
            width=36,
            height=name_entry.cget("height"),
            command=lambda current=card: self._remove_column_row(current),
        )
        remove_button.grid(row=1, column=2, padx=(0, 14), pady=(0, 10), sticky="n")

        dictionary_editor = DataDictionaryEditor(
            card,
            column_name=name_entry.get().strip(),
            column_type=type_menu.get(),
            dictionary_lookup_callback=self._dictionary_lookup_callback,
        )
        dictionary_editor.grid(row=3, column=0, columnspan=3, padx=14, pady=(0, 12), sticky="ew")

        name_entry.bind(
            "<KeyRelease>",
            lambda _event, current_card=card: self._sync_column_dictionary(current_card),
        )

        row_info = {
            "card": card,
            "title_label": title_label,
            "name_entry": name_entry,
            "type_menu": type_menu,
            "remove_button": remove_button,
            "dictionary_editor": dictionary_editor,
        }
        self.column_rows.append(row_info)
        self._sync_column_rows()
        self._sync_column_dictionary(card)
        name_entry.focus_set()

    def _remove_column_row(self, card):
        if len(self.column_rows) <= 1:
            return

        self.column_rows = [row for row in self.column_rows if row["card"] != card]
        card.destroy()
        self._sync_column_rows()

    def _sync_column_rows(self):
        for row_index, row_info in enumerate(self.column_rows):
            row_info["card"].grid_configure(row=row_index)
            row_info["title_label"].configure(text=f"Coluna {row_index + 1}")
            row_info["remove_button"].configure(state="normal" if len(self.column_rows) > 1 else "disabled")

    def _sync_column_dictionary(self, card):
        row_info = next((row for row in self.column_rows if row["card"] == card), None)
        if not row_info:
            return

        row_info["dictionary_editor"].set_column_name(row_info["name_entry"].get().strip())
        row_info["dictionary_editor"].set_column_type(row_info["type_menu"].get())

    def _collect_columns(self) -> list[dict] | None:
        columns = []
        seen_names = set()
        for row_info in self.column_rows:
            column_name = row_info["name_entry"].get().strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", column_name):
                messagebox.showerror(
                    "Error",
                    "Use apenas letras minusculas, numeros e underscore, sem espacos.",
                    parent=self,
                )
                row_info["name_entry"].focus_set()
                return None
            if len(column_name) > self.MAX_COLUMN_NAME_LENGTH:
                messagebox.showerror(
                    "Error",
                    f"Use no maximo {self.MAX_COLUMN_NAME_LENGTH} caracteres por coluna.",
                    parent=self,
                )
                row_info["name_entry"].focus_set()
                return None
            if column_name in seen_names:
                messagebox.showerror(
                    "Error",
                    f"A coluna {column_name} foi informada mais de uma vez.",
                    parent=self,
                )
                row_info["name_entry"].focus_set()
                return None

            seen_names.add(column_name)
            dictionary_payload = row_info["dictionary_editor"].get_payload(parent=self)
            if not dictionary_payload:
                return None
            columns.append(
                {
                    "column_name": column_name,
                    "column_type": row_info["type_menu"].get(),
                    "dictionary": dictionary_payload,
                }
            )

        return columns

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        columns = self._collect_columns()
        if not columns:
            return

        self.result = {"columns": columns}
        self.destroy()


class LinkDataDictionaryDialog(ctk.CTkToplevel):
    def __init__(self, master, column_name: str, column_type: str = "varchar", dictionary_lookup_callback=None):
        super().__init__(master)

        self.title(f"Vincular coluna: {column_name}")
        self.geometry("820x760")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Vincular ao data dictionary",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text=f"Coluna: {column_name}\nTipo atual: {column_type}",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        author_row = ctk.CTkFrame(self, fg_color="transparent")
        author_row.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        author_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(author_row, text="Seu nome").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.author_entry = ctk.CTkEntry(author_row)
        self.author_entry.grid(row=0, column=1, sticky="ew")

        self.dictionary_editor = DataDictionaryEditor(
            self,
            column_name=column_name,
            column_type=column_type,
            dictionary_lookup_callback=dictionary_lookup_callback,
        )
        self.dictionary_editor.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Vincular", command=self._confirm).pack(side="right")

        self.author_entry.focus_set()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        requested_by = self.author_entry.get().strip()
        if not requested_by:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            self.author_entry.focus_set()
            return

        dictionary_payload = self.dictionary_editor.get_payload(parent=self)
        if not dictionary_payload:
            return

        self.result = {
            "requested_by": requested_by,
            "dictionary": dictionary_payload,
        }
        self.destroy()


class EditDataDictionaryEntryDialog(ctk.CTkToplevel):
    PRESERVE_TYPE_LABEL = "(manter atual)"
    CANONICAL_TYPES = [
        *CreateColumnsDialog.COLUMN_TYPES,
        "integer[]",
        "smallint[]",
    ]

    def __init__(self, master, entry: dict, inferred_data_type: str = "", detected_usage_types=None, stale_usage_count: int = 0):
        super().__init__(master)

        self.title("Editar entrada do data dictionary")
        self.geometry("920x820")
        self.resizable(False, False)
        self.result = None
        self._entry = dict(entry or {})
        self._detected_usage_types = list(detected_usage_types or [])
        self._stale_usage_count = int(stale_usage_count or 0)

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Editar entrada do data dictionary",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        standard_name = str(self._entry.get("standard_name") or "").strip() or "(sem standard name)"
        usage_count = int(self._entry.get("usage_count") or 0)
        ctk.CTkLabel(
            self,
            text=f"Entrada: {standard_name}\nUso atual: {usage_count} coluna(s) vinculada(s)",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        editor_state = ctk.CTkFrame(self, fg_color="transparent")
        editor_state.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        editor_state.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(editor_state, text="Seu nome").grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="w")
        self.author_entry = ctk.CTkEntry(editor_state)
        self.author_entry.grid(row=0, column=1, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(editor_state, text="Tipo canonico").grid(row=1, column=0, padx=(0, 10), sticky="w")
        type_values = [self.PRESERVE_TYPE_LABEL, *self.CANONICAL_TYPES]
        self.type_menu = ctk.CTkOptionMenu(
            editor_state,
            values=type_values,
            command=lambda _value: self._sync_type_selection(),
        )
        self.type_menu.grid(row=1, column=1, sticky="ew")

        stored_data_type = str(self._entry.get("data_type") or "").strip().lower()
        initial_type = stored_data_type or str(inferred_data_type or "").strip().lower()
        if initial_type and initial_type in self.CANONICAL_TYPES:
            self.type_menu.set(initial_type)
        else:
            self.type_menu.set(self.PRESERVE_TYPE_LABEL)

        info_lines = [
            "Se o tipo for alterado, o sistema tentara converter automaticamente as colunas vinculadas antes de aplicar a mudanca.",
        ]
        if self._detected_usage_types:
            info_lines.append("Tipos detectados nas colunas vinculadas: " + ", ".join(self._detected_usage_types))
        if self._stale_usage_count:
            info_lines.append(
                f"Ha {self._stale_usage_count} vinculo(s) obsoleto(s) no usage. Eles serao ignorados na inferencia e reconciliados antes da aplicacao."
            )
        self.type_info_label = ctk.CTkLabel(
            self,
            text="\n".join(info_lines),
            justify="left",
            anchor="w",
            wraplength=860,
            text_color="#475569",
        )
        self.type_info_label.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.dictionary_editor = DataDictionaryEditor(
            self,
            column_name=standard_name,
            column_type=initial_type if initial_type in self.CANONICAL_TYPES else "varchar",
            dictionary_lookup_callback=None,
        )
        self.dictionary_editor.grid(row=4, column=0, padx=20, pady=(0, 12), sticky="ew")
        self.dictionary_editor.verify_button.grid_remove()
        self.dictionary_editor.reuse_existing_check.grid_remove()
        self.dictionary_editor.lookup_status_label.configure(
            text="Edite os metadados e confirme para propagar a atualizacao.",
            text_color="#64748b",
        )
        self.dictionary_editor.populate_from_entry(self._entry, reuse_existing=False)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Save", command=self._confirm).pack(side="right")

        self.author_entry.focus_set()

    def _selected_type(self) -> str:
        selected_type = str(self.type_menu.get() or "").strip().lower()
        if selected_type == self.PRESERVE_TYPE_LABEL:
            return ""
        return selected_type

    def _sync_type_selection(self):
        selected_type = self._selected_type()
        if selected_type:
            self.dictionary_editor.set_column_type(selected_type)

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        requested_by = self.author_entry.get().strip()
        if not requested_by:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            self.author_entry.focus_set()
            return

        dictionary_payload = self.dictionary_editor.get_payload(parent=self)
        if not dictionary_payload:
            return

        selected_type = self._selected_type()
        if selected_type:
            dictionary_payload["data_type"] = selected_type
        else:
            dictionary_payload["data_type"] = ""

        self.result = {
            "requested_by": requested_by,
            "dictionary": dictionary_payload,
            "data_type": selected_type,
        }
        self.destroy()


class EditDataDictionaryUsageEntryDialog(ctk.CTkToplevel):
    def __init__(self, master, entry: dict):
        super().__init__(master)

        self.title("Editar entrada do data dictionary usage")
        self.geometry("760x360")
        self.resizable(False, False)
        self.result = None
        self._entry = dict(entry or {})

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Editar entrada do data dictionary usage",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        database_name = str(self._entry.get("database_name") or "").strip() or "(sem base)"
        schema_name = str(self._entry.get("schema_name") or "").strip() or "(sem schema)"
        table_name = str(self._entry.get("table_name") or "").strip() or "(sem tabela)"
        standard_name = str(self._entry.get("standard_name") or "").strip() or "(sem standard name)"

        ctk.CTkLabel(
            self,
            text=(
                f"Tabela vinculada: {database_name}.{schema_name}.{table_name}\n"
                f"Standard name atual: {standard_name}"
            ),
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Seu nome").grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="w")
        self.author_entry = ctk.CTkEntry(form)
        self.author_entry.grid(row=0, column=1, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(form, text="Column name").grid(row=1, column=0, padx=(0, 10), sticky="w")
        self.column_name_entry = ctk.CTkEntry(
            form,
            placeholder_text="nome_fisico_da_coluna",
        )
        self.column_name_entry.grid(row=1, column=1, sticky="ew")
        self.column_name_entry.insert(0, str(self._entry.get("column_name") or "").strip())

        ctk.CTkLabel(
            self,
            text=(
                "Ao salvar, o sistema renomeia a coluna fisica na tabela vinculada e atualiza o "
                "registro correspondente no data dictionary usage."
            ),
            justify="left",
            anchor="w",
            wraplength=700,
            text_color="#475569",
        ).grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Save", command=self._confirm).pack(side="right")

        self.author_entry.focus_set()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        requested_by = self.author_entry.get().strip()
        if not requested_by:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            self.author_entry.focus_set()
            return

        column_name = self.column_name_entry.get().strip()
        if not column_name:
            messagebox.showerror("Error", "Informe o column name.", parent=self)
            self.column_name_entry.focus_set()
            return

        self.result = {
            "requested_by": requested_by,
            "column_name": column_name,
        }
        self.destroy()


class ColumnFilterDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        column_name: str,
        values,
        current_values=None,
        is_numeric: bool = False,
        on_search=None,
    ):
        super().__init__(master)

        self.title(f"Filtrar {column_name}")
        self.geometry("500x620")
        self.minsize(380, 420)
        self.result = None
        self._values = []
        self._vars = {}
        self._value_by_key = {}
        self._is_numeric = is_numeric
        self._on_search = on_search

        selected_keys = None
        if current_values is not None:
            selected_keys = {
                self._value_key(item)
                for item in current_values
            }

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Filtrar: {column_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        search_row.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(search_row, placeholder_text="Pesquisar valores")
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<Return>", lambda _event: self._request_search())

        self.search_button = ctk.CTkButton(
            search_row,
            text="Pesquisar",
            width=104,
            command=self._request_search,
        )
        self.search_button.grid(row=0, column=1, padx=(8, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")

        ctk.CTkButton(actions, text="Todos", width=86, command=lambda: self._set_all(True)).pack(side="left")
        ctk.CTkButton(actions, text="Nenhum", width=92, command=lambda: self._set_all(False)).pack(side="left", padx=(8, 0))
        ctk.CTkButton(actions, text="Limpar filtro", width=120, command=self._clear_filter).pack(side="left", padx=(8, 0))

        if self._is_numeric:
            numeric_actions = ctk.CTkFrame(self, fg_color="transparent")
            numeric_actions.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")
            ctk.CTkButton(
                numeric_actions,
                text="MAIOR()",
                width=92,
                command=lambda: self._insert_numeric_template("MAIOR()"),
            ).pack(side="left")
            ctk.CTkButton(
                numeric_actions,
                text="MENOR()",
                width=92,
                command=lambda: self._insert_numeric_template("MENOR()"),
            ).pack(side="left", padx=(8, 0))
            ctk.CTkButton(
                numeric_actions,
                text="ENTRE(,)",
                width=100,
                command=lambda: self._insert_numeric_template("ENTRE(,)"),
            ).pack(side="left", padx=(8, 0))
            values_row = 4
            status_row = 5
            footer_row = 6
            self.grid_rowconfigure(4, weight=1)
            self.grid_rowconfigure(3, weight=0)
        else:
            values_row = 3
            status_row = 4
            footer_row = 5

        self.values_box = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.values_box.grid(row=values_row, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.values_box.grid_columnconfigure(0, weight=1)

        self.set_values(values, selected_keys=selected_keys, preserve_selected=False)

        self.status_label = ctk.CTkLabel(
            self,
            text=self._build_status_text(),
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=status_row, column=0, padx=20, pady=(0, 10), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=footer_row, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(footer, text="Aplicar", command=self._apply).pack(side="right")

    def set_values(self, values, selected_keys=None, preserve_selected=True):
        previous_selected = set()
        if preserve_selected:
            previous_selected = {
                key
                for key, var in self._vars.items()
                if var.get()
            }

        if selected_keys is None:
            selected_keys = previous_selected or None

        self._values = list(values)
        self._vars = {}
        self._value_by_key = {}

        for child in self.values_box.winfo_children():
            child.destroy()

        if not self._values:
            ctk.CTkLabel(
                self.values_box,
                text="Nenhum valor encontrado.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            for row_index, item in enumerate(self._values):
                key = self._value_key(item)
                self._value_by_key[key] = item
                var = tk.BooleanVar(value=selected_keys is None or key in selected_keys)
                self._vars[key] = var
                ctk.CTkCheckBox(
                    self.values_box,
                    text=self._display_value(item),
                    variable=var,
                ).grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")

        if hasattr(self, "status_label"):
            self.status_label.configure(text=self._build_status_text())

    @staticmethod
    def _value_key(item) -> tuple[bool, str]:
        return bool(item.get("is_null")), str(item.get("value", ""))

    @staticmethod
    def _display_value(item) -> str:
        count = item.get("count")
        if item.get("is_null"):
            base = "(Vazio)"
            return f"{base} ({count})" if count is not None else base

        value = str(item.get("value", ""))
        if value == "":
            base = "(Vazio)"
            return f"{base} ({count})" if count is not None else base
        value = value.replace("\n", "\\n").replace("\r", "\\r")
        if len(value) > 140:
            return value[:137] + "..."
        return f"{value} ({count})" if count is not None else value

    def _build_status_text(self) -> str:
        return f"{len(self._values)} valor(es) carregado(s). Use a pesquisa para buscar na lista completa."

    @staticmethod
    def _normalize_numeric_text(value: str) -> str:
        return str(value).strip().replace(",", ".")

    @classmethod
    def _parse_numeric_filter_expression(cls, text: str):
        normalized = str(text or "").strip()
        if not normalized:
            return None

        number_pattern = r"([+-]?\d+(?:[\.,]\d+)?)"
        match = re.fullmatch(rf"MAIOR\s*\(\s*{number_pattern}\s*\)", normalized, flags=re.IGNORECASE)
        if match:
            value = cls._normalize_numeric_text(match.group(1))
            return {
                "operator": "gt",
                "numeric_value": value,
                "value": f"MAIOR({value})",
                "display_value": f"MAIOR({value})",
                "is_null": False,
            }

        match = re.fullmatch(rf"MENOR\s*\(\s*{number_pattern}\s*\)", normalized, flags=re.IGNORECASE)
        if match:
            value = cls._normalize_numeric_text(match.group(1))
            return {
                "operator": "lt",
                "numeric_value": value,
                "value": f"MENOR({value})",
                "display_value": f"MENOR({value})",
                "is_null": False,
            }

        match = re.fullmatch(
            rf"ENTRE\s*\(\s*{number_pattern}\s*[,;]\s*{number_pattern}\s*\)",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            lower_value = cls._normalize_numeric_text(match.group(1))
            upper_value = cls._normalize_numeric_text(match.group(2))
            return {
                "operator": "between",
                "lower_value": lower_value,
                "upper_value": upper_value,
                "value": f"ENTRE({lower_value},{upper_value})",
                "display_value": f"ENTRE({lower_value},{upper_value})",
                "is_null": False,
            }

        if re.fullmatch(number_pattern, normalized):
            value = cls._normalize_numeric_text(normalized)
            return {
                "operator": "eq_numeric",
                "numeric_value": value,
                "value": value,
                "display_value": value,
                "is_null": False,
            }

        return None

    def _insert_numeric_template(self, template: str):
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, template)
        cursor_position = template.find("(") + 1
        self.search_entry.focus_set()
        if cursor_position > 0:
            self.search_entry.icursor(cursor_position)

    def set_search_busy(self, busy: bool, message: str | None = None):
        self.search_button.configure(state="disabled" if busy else "normal")
        if message and hasattr(self, "status_label"):
            self.status_label.configure(text=message)

    def _request_search(self):
        if self._on_search:
            self.set_search_busy(True, "Pesquisando valores nas facetas...")
            self._on_search(self, self.search_entry.get().strip())

    def _set_all(self, selected: bool):
        for var in self._vars.values():
            var.set(selected)

    def _clear_filter(self):
        self.result = {
            "applied": True,
            "values": None,
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _apply(self):
        numeric_expression = (
            self._parse_numeric_filter_expression(self.search_entry.get())
            if self._is_numeric
            else None
        )
        if numeric_expression:
            self.result = {
                "applied": True,
                "values": [numeric_expression],
            }
            self.destroy()
            return

        selected_items = [
            self._value_by_key[key]
            for key, var in self._vars.items()
            if var.get()
        ]
        values = (
            None
            if not self.search_entry.get().strip() and len(selected_items) == len(self._values)
            else selected_items
        )
        self.result = {
            "applied": True,
            "values": values,
        }
        self.destroy()


class UserSelectionDialog(ctk.CTkToplevel):
    def __init__(self, master, users, on_select=None, on_create=None, on_edit=None, on_delete=None):
        super().__init__(master)

        self.withdraw()
        self.title("PostgreSQL users")
        self.geometry("620x620")
        self.minsize(520, 460)
        self._users = list(users)
        self._on_select = on_select
        self._on_create = on_create
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._busy = False

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self,
            text="Usuarios do banco",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            toolbar,
            text="Selecione um usuario para gerenciar permissoes.",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.create_button = ctk.CTkButton(
            toolbar,
            text="+ Novo usuario",
            width=140,
            command=self._create,
        )
        self.create_button.grid(row=0, column=1, padx=(12, 0), sticky="e")

        self.users_box = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.users_box.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.users_box.grid_columnconfigure(0, weight=1)

        self._render_users()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self.after(0, lambda: show_centered_dialog(self, master))

    def _render_users(self):
        for child in self.users_box.winfo_children():
            child.destroy()

        if not self._users:
            ctk.CTkLabel(
                self.users_box,
                text="Nenhum usuario com LOGIN encontrado.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            for row_index, user_name in enumerate(self._users):
                row = ctk.CTkFrame(self.users_box, fg_color="transparent")
                row.grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")
                row.grid_columnconfigure(0, weight=1)

                ctk.CTkButton(
                    row,
                    text=user_name,
                    anchor="w",
                    state="disabled" if self._busy else "normal",
                    command=lambda current=user_name: self._select(current),
                ).grid(row=0, column=0, sticky="ew")

                ctk.CTkButton(
                    row,
                    text="Editar",
                    width=72,
                    state="disabled" if self._busy else "normal",
                    command=lambda current=user_name: self._edit(current),
                ).grid(row=0, column=1, padx=(8, 0), sticky="e")

                ctk.CTkButton(
                    row,
                    text="X",
                    width=42,
                    fg_color="#dc2626",
                    hover_color="#b91c1c",
                    state="disabled" if self._busy else "normal",
                    command=lambda current=user_name: self._delete(current),
                ).grid(row=0, column=2, padx=(8, 0), sticky="e")

    def set_users(self, users):
        self._users = list(users)
        self._render_users()

    def set_busy(self, busy: bool, message: str | None = None):
        self._busy = busy
        self.create_button.configure(state="disabled" if busy else "normal")
        if message:
            self.status_label.configure(text=message)
        self._render_users()

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def _select(self, user_name: str):
        if self._busy:
            return
        if self._on_select:
            self._on_select(user_name)
        self.close()

    def _create(self):
        if self._busy:
            return
        if self._on_create:
            self._on_create(self)

    def _edit(self, user_name: str):
        if self._busy:
            return
        if self._on_edit:
            self._on_edit(self, user_name)

    def _delete(self, user_name: str):
        if self._busy:
            return
        if self._on_delete:
            self._on_delete(self, user_name)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class DatabaseOperationsDialog(ctk.CTkToplevel):
    QUERY_PREVIEW_LIMIT = 180

    def __init__(
        self,
        master,
        sessions,
        summary,
        collected_at: str = "",
        on_refresh=None,
        on_cancel_query=None,
        on_terminate_session=None,
    ):
        super().__init__(master)

        self.withdraw()
        self.title("PostgreSQL operations")
        self.geometry("1260x780")
        self.minsize(1040, 620)
        self._sessions = list(sessions or [])
        self._summary = dict(summary or {})
        self._collected_at = str(collected_at or "").strip()
        self._on_refresh = on_refresh
        self._on_cancel_query = on_cancel_query
        self._on_terminate_session = on_terminate_session
        self._busy = False
        self._selected_pid = None
        if self._sessions:
            self._selected_pid = self._safe_int(self._sessions[0].get("pid"))

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self,
            text="Operacoes em andamento",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            toolbar,
            text="Selecione uma sessao para ver detalhes, cancelar a query ou encerrar a conexao.",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.refresh_button = ctk.CTkButton(
            toolbar,
            text="Refresh",
            width=110,
            command=self._refresh,
        )
        self.refresh_button.grid(row=0, column=1, padx=(12, 0), sticky="e")

        self.summary_label = ctk.CTkLabel(
            self,
            text="",
            justify="left",
            anchor="w",
            text_color="#475569",
        )
        self.summary_label.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.sessions_box = ctk.CTkScrollableFrame(content, corner_radius=12)
        self.sessions_box.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        self.sessions_box.grid_columnconfigure(0, weight=1)

        detail_panel = ctk.CTkFrame(content, corner_radius=12)
        detail_panel.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        detail_panel.grid_columnconfigure(0, weight=1)
        detail_panel.grid_rowconfigure(2, weight=1)

        self.detail_title_label = ctk.CTkLabel(
            detail_panel,
            text="Detalhes da sessao",
            font=ctk.CTkFont(size=17, weight="bold"),
            justify="left",
            anchor="w",
        )
        self.detail_title_label.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")

        action_row = ctk.CTkFrame(detail_panel, fg_color="transparent")
        action_row.grid(row=1, column=0, padx=16, pady=(0, 10), sticky="ew")
        action_row.grid_columnconfigure(2, weight=1)

        self.cancel_query_button = ctk.CTkButton(
            action_row,
            text="Cancelar query",
            width=140,
            command=self._cancel_selected_query,
        )
        self.cancel_query_button.grid(row=0, column=0, sticky="w")

        self.terminate_session_button = ctk.CTkButton(
            action_row,
            text="Encerrar sessao",
            width=150,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._terminate_selected_session,
        )
        self.terminate_session_button.grid(row=0, column=1, padx=(10, 0), sticky="w")

        self.detail_text = ctk.CTkTextbox(detail_panel)
        self.detail_text.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="nsew")
        self.detail_text.configure(state="disabled")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self._render_sessions()
        self._refresh_summary_label()
        self._render_detail()
        self._sync_action_buttons()
        self.after(0, lambda: show_centered_dialog(self, master))

    @staticmethod
    def _safe_int(value) -> int | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            return int(normalized)
        except Exception:
            return None

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @classmethod
    def _query_preview(cls, text: str) -> str:
        normalized = cls._normalize_whitespace(text)
        if not normalized:
            return "(sem SQL visivel)"
        if len(normalized) <= cls.QUERY_PREVIEW_LIMIT:
            return normalized
        return normalized[: cls.QUERY_PREVIEW_LIMIT - 3].rstrip() + "..."

    @staticmethod
    def _format_seconds(value) -> str:
        try:
            total_seconds = int(float(value or 0))
        except Exception:
            total_seconds = 0

        if total_seconds <= 0:
            return "0s"

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    @staticmethod
    def _state_label(session: dict) -> str:
        state = str(session.get("state") or "").strip() or "desconhecido"
        if session.get("waiting_lock"):
            return f"{state} | aguardando lock"
        blocking_count = int(session.get("blocking_pid_count") or 0)
        if blocking_count > 0:
            return f"{state} | bloqueada por {blocking_count} PID(s)"
        return state

    def _selected_session(self) -> dict | None:
        selected_pid = self._selected_pid
        if selected_pid is None:
            return None
        for session in self._sessions:
            if self._safe_int(session.get("pid")) == selected_pid:
                return session
        return None

    def _render_sessions(self):
        for child in self.sessions_box.winfo_children():
            child.destroy()

        if not self._sessions:
            ctk.CTkLabel(
                self.sessions_box,
                text="Nenhuma sessao cliente encontrada no PostgreSQL neste momento.",
                justify="left",
                wraplength=420,
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for row_index, session in enumerate(self._sessions):
            pid = self._safe_int(session.get("pid"))
            is_selected = pid is not None and pid == self._selected_pid
            fg_color = "#ffedd5" if is_selected else "#f8fafc"
            hover_color = "#fed7aa" if is_selected else "#e2e8f0"
            text_color = "#111827"

            database_name = str(session.get("database_name") or "").strip() or "(sem base)"
            user_name = str(session.get("user_name") or "").strip() or "(sem usuario)"
            headline = f"PID {pid or '?'} | {database_name} | {user_name} | {self._state_label(session)}"

            relation_names = self._normalize_whitespace(session.get("relation_names") or "")
            lock_modes = self._normalize_whitespace(session.get("lock_modes") or "")
            meta_parts = []
            if relation_names:
                meta_parts.append(f"Tabelas: {relation_names}")
            if lock_modes:
                meta_parts.append(f"Locks: {lock_modes}")
            wait_event_type = str(session.get("wait_event_type") or "").strip()
            wait_event = str(session.get("wait_event") or "").strip()
            if wait_event_type:
                wait_label = wait_event_type
                if wait_event:
                    wait_label += f"/{wait_event}"
                meta_parts.append(f"Wait: {wait_label}")
            query_age = self._format_seconds(session.get("query_age_seconds"))
            xact_age = self._format_seconds(session.get("xact_age_seconds"))
            meta_parts.append(f"Query: {query_age}")
            meta_parts.append(f"Tx: {xact_age}")
            meta_line = " | ".join(meta_parts)
            preview = self._query_preview(session.get("query_text") or "")
            button_text = headline
            if meta_line:
                button_text += f"\n{meta_line}"
            button_text += f"\n{preview}"

            ctk.CTkButton(
                self.sessions_box,
                text=button_text,
                anchor="w",
                height=96,
                fg_color=fg_color,
                hover_color=hover_color,
                text_color=text_color,
                state="disabled" if self._busy else "normal",
                command=lambda current_pid=pid: self._select_session(current_pid),
            ).grid(row=row_index, column=0, padx=6, pady=5, sticky="ew")

    def _refresh_summary_label(self):
        if not self._sessions:
            self.summary_label.configure(text="0 sessoes cliente visiveis.")
            return

        summary = dict(self._summary or {})
        parts = [
            f"{int(summary.get('total_sessions') or 0)} sessao(oes)",
            f"{int(summary.get('active_sessions') or 0)} ativa(s)",
            f"{int(summary.get('blocked_sessions') or 0)} bloqueada(s)",
            f"{int(summary.get('waiting_lock_sessions') or 0)} aguardando lock",
            f"{int(summary.get('idle_in_transaction_sessions') or 0)} idle em transacao",
        ]
        if self._collected_at:
            parts.append(f"Atualizado: {self._collected_at}")
        self.summary_label.configure(text=" | ".join(parts))

    def _render_detail(self):
        session = self._selected_session()
        if not session:
            self.detail_title_label.configure(text="Detalhes da sessao")
            self._set_detail_text("Selecione uma sessao para ver os detalhes.")
            return

        pid = self._safe_int(session.get("pid"))
        self.detail_title_label.configure(text=f"Detalhes da sessao | PID {pid or '?'}")

        lines = [
            f"PID: {pid or '?'}",
            f"Base: {str(session.get('database_name') or '').strip() or '(sem base)'}",
            f"Usuario: {str(session.get('user_name') or '').strip() or '(sem usuario)'}",
            f"Aplicacao: {str(session.get('application_name') or '').strip() or '(sem aplicacao)'}",
            f"Cliente: {str(session.get('client_addr') or '').strip() or '(sem cliente)'}",
            f"Estado: {str(session.get('state') or '').strip() or 'desconhecido'}",
            f"Esperando lock: {'sim' if session.get('waiting_lock') else 'nao'}",
            f"Bloqueada por: {str(session.get('blocking_pids') or '').strip() or '(ninguem)'}",
            f"Wait event: {self._normalize_whitespace((session.get('wait_event_type') or '') + ('/' + str(session.get('wait_event') or '').strip() if str(session.get('wait_event') or '').strip() else '')) or '(nenhum)'}",
            f"Inicio da query: {str(session.get('query_start') or '').strip() or '(desconhecido)'}",
            f"Duracao da query: {self._format_seconds(session.get('query_age_seconds'))}",
            f"Inicio da transacao: {str(session.get('xact_start') or '').strip() or '(desconhecido)'}",
            f"Duracao da transacao: {self._format_seconds(session.get('xact_age_seconds'))}",
            f"Tabelas relacionadas: {str(session.get('relation_names') or '').strip() or '(nenhuma)'}",
            f"Locks observados: {str(session.get('lock_modes') or '').strip() or '(nenhum)'}",
            "",
            "SQL:",
            str(session.get("query_text") or "").strip() or "(sem SQL visivel)",
        ]
        self._set_detail_text("\n".join(lines))

    def _set_detail_text(self, text: str):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    def _sync_action_buttons(self):
        selected = self._selected_session()
        state = "disabled" if self._busy or not selected else "normal"
        self.cancel_query_button.configure(state=state)
        self.terminate_session_button.configure(state=state)
        self.refresh_button.configure(state="disabled" if self._busy else "normal")

    def _select_session(self, pid: int | None):
        if self._busy or pid is None:
            return
        self._selected_pid = pid
        self._render_sessions()
        self._render_detail()
        self._sync_action_buttons()

    def set_snapshot(self, sessions, summary, collected_at: str = "", message: str | None = None):
        previous_pid = self._selected_pid
        self._sessions = list(sessions or [])
        self._summary = dict(summary or {})
        self._collected_at = str(collected_at or "").strip()
        visible_pids = {
            self._safe_int(session.get("pid"))
            for session in self._sessions
        }
        if previous_pid in visible_pids:
            self._selected_pid = previous_pid
        elif self._sessions:
            self._selected_pid = self._safe_int(self._sessions[0].get("pid"))
        else:
            self._selected_pid = None
        self._render_sessions()
        self._refresh_summary_label()
        self._render_detail()
        if message:
            self.status_label.configure(text=message)
        self._sync_action_buttons()

    def set_busy(self, busy: bool, message: str | None = None):
        self._busy = busy
        if message:
            self.status_label.configure(text=message)
        self._render_sessions()
        self._render_detail()
        self._sync_action_buttons()

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def _refresh(self):
        if self._busy:
            return
        if self._on_refresh:
            self._on_refresh(self)

    def _cancel_selected_query(self):
        if self._busy:
            return
        session = self._selected_session()
        if session and self._on_cancel_query:
            self._on_cancel_query(self, session)

    def _terminate_selected_session(self):
        if self._busy:
            return
        session = self._selected_session()
        if session and self._on_terminate_session:
            self._on_terminate_session(self, session)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class DataDictionaryEntriesDialog(ctk.CTkToplevel):
    def __init__(self, master, entries, on_delete=None, on_edit=None):
        super().__init__(master)

        self.withdraw()
        self.title("Data Dictionary")
        self.geometry("1180x820")
        self.minsize(980, 640)
        self._entries = list(entries)
        self._on_delete = on_delete
        self._on_edit = on_edit
        self._busy = False

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="Entradas do Data Dictionary",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            toolbar,
            text="Selecione uma entrada para editar ou excluir.",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.entries_box = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.entries_box.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.entries_box.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self._render_entries()
        self.after(0, lambda: show_centered_dialog(self, master))

    def _render_entries(self):
        for child in self.entries_box.winfo_children():
            child.destroy()

        total_entries = len(self._entries)
        if not total_entries:
            ctk.CTkLabel(
                self.entries_box,
                text="Nenhuma entrada encontrada no data dictionary.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for row_index, entry in enumerate(self._entries):
            row = ctk.CTkFrame(self.entries_box, corner_radius=10)
            row.grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            info_box = ctk.CTkFrame(row, fg_color="transparent")
            info_box.grid(row=0, column=0, padx=(12, 8), pady=10, sticky="ew")
            info_box.grid_columnconfigure(0, weight=1)

            standard_name = str(entry.get("standard_name") or "").strip() or "(sem standard name)"
            units = str(entry.get("units") or "").strip() or "(sem unit)"
            data_type = str(entry.get("data_type") or "").strip() or "(sem tipo)"
            usage_count = int(entry.get("usage_count") or 0)
            aliases = str(entry.get("aliases") or "").strip()
            definition = str(entry.get("definition") or "").strip()
            value_domain = str(entry.get("value_domain") or "").strip()

            ctk.CTkLabel(
                info_box,
                text=standard_name,
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
            ).grid(row=0, column=0, sticky="ew")

            meta_parts = [f"Type: {data_type}", f"Units: {units}", f"Usage: {usage_count}"]
            if aliases:
                meta_parts.append(f"Aliases: {aliases}")
            ctk.CTkLabel(
                info_box,
                text=" | ".join(meta_parts),
                justify="left",
                anchor="w",
                text_color="#64748b",
                wraplength=900,
            ).grid(row=1, column=0, pady=(2, 0), sticky="ew")

            if definition:
                ctk.CTkLabel(
                    info_box,
                    text=definition,
                    justify="left",
                    anchor="w",
                    wraplength=900,
                ).grid(row=2, column=0, pady=(6, 0), sticky="ew")

            if value_domain:
                ctk.CTkLabel(
                    info_box,
                    text=value_domain,
                    justify="left",
                    anchor="w",
                    text_color="#475569",
                    wraplength=900,
                ).grid(row=3, column=0, pady=(6, 0), sticky="ew")

            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ne")

            ctk.CTkButton(
                actions,
                text="Editar",
                width=86,
                state="disabled" if self._busy else "normal",
                command=lambda current=entry: self._edit(current),
            ).pack(side="top", pady=(0, 8))
            ctk.CTkButton(
                actions,
                text="Delete",
                width=86,
                fg_color="#dc2626",
                hover_color="#b91c1c",
                state="disabled" if self._busy else "normal",
                command=lambda current=entry: self._delete(current),
            ).pack(side="top")

    def set_entries(self, entries):
        self._entries = list(entries)
        self._render_entries()
        if not self._busy:
            self.status_label.configure(text=f"{len(self._entries)} entrada(s) encontradas.")

    def set_busy(self, busy: bool, message: str | None = None):
        self._busy = busy
        self._render_entries()
        if message:
            self.status_label.configure(text=message)
        elif not busy:
            self.status_label.configure(text=f"{len(self._entries)} entrada(s) encontradas.")

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def _delete(self, entry: dict):
        if self._busy:
            return
        if self._on_delete:
            self._on_delete(self, entry)

    def _edit(self, entry: dict):
        if self._busy:
            return
        if self._on_edit:
            self._on_edit(self, entry)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class DataDictionaryUsageEntriesDialog(ctk.CTkToplevel):
    def __init__(self, master, entries, on_edit=None):
        super().__init__(master)

        self.withdraw()
        self.title("Data Dictionary Usage")
        self.geometry("1180x820")
        self.minsize(980, 640)
        self._entries = list(entries)
        self._on_edit = on_edit
        self._busy = False

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="Entradas do Data Dictionary Usage",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            toolbar,
            text="Selecione uma entrada para editar o column name.",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.entries_box = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.entries_box.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.entries_box.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self._render_entries()
        self.after(0, lambda: show_centered_dialog(self, master))

    def _render_entries(self):
        for child in self.entries_box.winfo_children():
            child.destroy()

        if not self._entries:
            ctk.CTkLabel(
                self.entries_box,
                text="Nenhuma entrada encontrada no data dictionary usage.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for row_index, entry in enumerate(self._entries):
            row = ctk.CTkFrame(self.entries_box, corner_radius=10)
            row.grid(row=row_index, column=0, padx=8, pady=5, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            info_box = ctk.CTkFrame(row, fg_color="transparent")
            info_box.grid(row=0, column=0, padx=(12, 8), pady=10, sticky="ew")
            info_box.grid_columnconfigure(0, weight=1)

            database_name = str(entry.get("database_name") or "").strip()
            schema_name = str(entry.get("schema_name") or "").strip()
            table_name = str(entry.get("table_name") or "").strip()
            full_table_name = ".".join(part for part in [database_name, schema_name, table_name] if part)
            column_name = str(entry.get("column_name") or "").strip() or "(sem column name)"
            standard_name = str(entry.get("standard_name") or "").strip() or "(sem standard name)"
            linked_by = str(entry.get("linked_by") or "").strip()
            linked_at = str(entry.get("linked_at") or "").strip()

            ctk.CTkLabel(
                info_box,
                text=f"{full_table_name}.{column_name}" if full_table_name else column_name,
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
            ).grid(row=0, column=0, sticky="ew")

            ctk.CTkLabel(
                info_box,
                text=f"Standard name: {standard_name}",
                justify="left",
                anchor="w",
                text_color="#475569",
                wraplength=900,
            ).grid(row=1, column=0, pady=(4, 0), sticky="ew")

            meta_parts = []
            if linked_by:
                meta_parts.append(f"Linked by: {linked_by}")
            if linked_at:
                meta_parts.append(f"Linked at: {linked_at}")
            if meta_parts:
                ctk.CTkLabel(
                    info_box,
                    text=" | ".join(meta_parts),
                    justify="left",
                    anchor="w",
                    text_color="#64748b",
                    wraplength=900,
                ).grid(row=2, column=0, pady=(4, 0), sticky="ew")

            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ne")

            ctk.CTkButton(
                actions,
                text="Editar",
                width=86,
                state="disabled" if self._busy else "normal",
                command=lambda current=entry: self._edit(current),
            ).pack(side="top")

    def set_entries(self, entries):
        self._entries = list(entries)
        self._render_entries()
        if not self._busy:
            self.status_label.configure(text=f"{len(self._entries)} entrada(s) encontradas.")

    def set_busy(self, busy: bool, message: str | None = None):
        self._busy = busy
        self._render_entries()
        if message:
            self.status_label.configure(text=message)
        elif not busy:
            self.status_label.configure(text=f"{len(self._entries)} entrada(s) encontradas.")

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def _edit(self, entry: dict):
        if self._busy:
            return
        if self._on_edit:
            self._on_edit(self, entry)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class RolePermissionsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        role_name: str,
        permissions,
        assigned_permission_keys,
        on_grant=None,
        on_revoke=None,
    ):
        super().__init__(master)

        self.withdraw()
        self.title(f"Permissoes de {role_name}")
        self.geometry("1120x720")
        self.minsize(980, 620)
        self._role_name = role_name
        self._permissions_by_key = {
            permission["key"]: permission
            for permission in permissions
        }
        self._assigned_keys = set(assigned_permission_keys)
        self._available_keys = set(self._permissions_by_key) - self._assigned_keys
        self._available_vars = {}
        self._assigned_vars = {}
        self._on_grant = on_grant
        self._on_revoke = on_revoke
        self._busy = False
        self._root_password_callback = None

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Permissoes: {role_name}",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        self.status_label = ctk.CTkLabel(
            self,
            text="Marque permissoes e use as setas para conceder ou remover.",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(2, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.available_box = self._build_permission_panel(
            content,
            column=0,
            title="Permissoes disponiveis",
            side="available",
        )

        arrows = ctk.CTkFrame(content, fg_color="transparent")
        arrows.grid(row=0, column=1, padx=14, pady=0, sticky="ns")
        arrows.grid_rowconfigure(0, weight=1)
        arrows.grid_rowconfigure(3, weight=1)

        self.grant_button = ctk.CTkButton(
            arrows,
            text="→",
            width=54,
            height=42,
            command=self._grant_selected,
        )
        self.grant_button.grid(row=1, column=0, pady=(0, 10))

        self.revoke_button = ctk.CTkButton(
            arrows,
            text="←",
            width=54,
            height=42,
            command=self._revoke_selected,
        )
        self.revoke_button.grid(row=2, column=0)

        self.assigned_box = self._build_permission_panel(
            content,
            column=2,
            title="Permissoes do usuario",
            side="assigned",
        )

        self.root_password_frame = ctk.CTkFrame(self, corner_radius=12)
        self.root_password_frame.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")
        self.root_password_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.root_password_frame,
            text="Root password",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, padx=(14, 10), pady=(12, 6), sticky="w")

        self.root_password_entry = ctk.CTkEntry(self.root_password_frame, show="*")
        self.root_password_entry.grid(row=0, column=1, padx=(0, 10), pady=(12, 6), sticky="ew")

        self.root_password_confirm_button = ctk.CTkButton(
            self.root_password_frame,
            text="Confirm",
            width=96,
            command=self._confirm_root_password,
        )
        self.root_password_confirm_button.grid(row=0, column=2, padx=(0, 8), pady=(12, 6))

        self.root_password_cancel_button = ctk.CTkButton(
            self.root_password_frame,
            text="Cancel",
            width=88,
            command=self._cancel_root_password,
        )
        self.root_password_cancel_button.grid(row=0, column=3, padx=(0, 14), pady=(12, 6))

        ctk.CTkLabel(
            self.root_password_frame,
            text="A tentativa normal falhou por falta de permissao. Digite a senha root para aplicar esta alteracao.",
            justify="left",
            anchor="w",
            wraplength=950,
        ).grid(row=1, column=0, columnspan=4, padx=14, pady=(0, 12), sticky="ew")

        self.root_password_frame.grid_remove()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

        self._render_permissions()
        self.after(0, lambda: show_centered_dialog(self, master))

    def _build_permission_panel(self, parent, column: int, title: str, side: str):
        panel = ctk.CTkFrame(parent, corner_radius=12)
        panel.grid(row=0, column=column, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            panel,
            text=title,
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        shortcuts = ctk.CTkFrame(panel, fg_color="transparent")
        shortcuts.grid(row=1, column=0, padx=16, pady=(0, 10), sticky="w")

        ctk.CTkButton(
            shortcuts,
            text="Marcar todas",
            width=118,
            command=lambda current=side: self._set_all(current, True),
        ).pack(side="left")
        ctk.CTkButton(
            shortcuts,
            text="Desmarcar todas",
            width=138,
            command=lambda current=side: self._set_all(current, False),
        ).pack(side="left", padx=(8, 0))

        scroll = ctk.CTkScrollableFrame(panel, corner_radius=10)
        scroll.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        return scroll

    def _ordered_keys(self, keys):
        order = {
            permission["key"]: index
            for index, permission in enumerate(self._permissions_by_key.values())
        }
        return sorted(keys, key=lambda key: order.get(key, 9999))

    def _render_permission_list(self, box, keys, vars_store, empty_text):
        for child in box.winfo_children():
            child.destroy()
        vars_store.clear()

        ordered_keys = self._ordered_keys(keys)
        if not ordered_keys:
            ctk.CTkLabel(
                box,
                text=empty_text,
                justify="left",
                wraplength=430,
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for row_index, permission_key in enumerate(ordered_keys):
            permission = self._permissions_by_key[permission_key]
            var = tk.BooleanVar(value=False)
            vars_store[permission_key] = var

            item = ctk.CTkFrame(box, corner_radius=10)
            item.grid(row=row_index, column=0, padx=4, pady=4, sticky="ew")
            item.grid_columnconfigure(1, weight=1)

            ctk.CTkCheckBox(
                item,
                text="",
                variable=var,
                width=24,
            ).grid(row=0, column=0, padx=(10, 8), pady=10, sticky="n")

            label_text = f"{permission['label']}\n{permission['description']}"
            ctk.CTkLabel(
                item,
                text=label_text,
                justify="left",
                anchor="w",
                wraplength=390,
            ).grid(row=0, column=1, padx=(0, 10), pady=10, sticky="ew")

    def _render_permissions(self):
        self._render_permission_list(
            self.available_box,
            self._available_keys,
            self._available_vars,
            "Nenhuma permissao disponivel para conceder.",
        )
        self._render_permission_list(
            self.assigned_box,
            self._assigned_keys,
            self._assigned_vars,
            "Este usuario nao possui nenhuma permissao gerenciada aqui.",
        )

    def _set_all(self, side: str, selected: bool):
        if self._busy:
            return
        vars_store = self._available_vars if side == "available" else self._assigned_vars
        for var in vars_store.values():
            var.set(selected)

    @staticmethod
    def _selected_keys(vars_store: dict) -> list[str]:
        return [
            permission_key
            for permission_key, var in vars_store.items()
            if var.get()
        ]

    def _set_busy(self, busy: bool, message: str | None = None):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.grant_button.configure(state=state)
        self.revoke_button.configure(state=state)
        if message:
            self.status_label.configure(text=message)
        self.update_idletasks()

    def complete_grant(self, selected_keys):
        try:
            if not self.winfo_exists():
                return
            self._available_keys.difference_update(selected_keys)
            self._assigned_keys.update(selected_keys)
            self._render_permissions()
            self._set_busy(False, "Permissoes concedidas.")
        except Exception:
            pass

    def complete_revoke(self, selected_keys):
        try:
            if not self.winfo_exists():
                return
            self._assigned_keys.difference_update(selected_keys)
            self._available_keys.update(selected_keys)
            self._render_permissions()
            self._set_busy(False, "Permissoes removidas.")
        except Exception:
            pass

    def fail_permission_change(self, message: str = "Alteracao nao aplicada."):
        try:
            if not self.winfo_exists():
                return
            self._set_busy(False, message)
        except Exception:
            pass

    def set_permission_change_message(self, message: str):
        try:
            if not self.winfo_exists():
                return
            self.status_label.configure(text=message)
            self.update_idletasks()
        except Exception:
            pass

    def request_root_password(self, on_result):
        try:
            if not self.winfo_exists():
                return
            self._root_password_callback = on_result
            self.root_password_entry.delete(0, "end")
            self.root_password_frame.grid()
            self.set_permission_change_message(
                "Permissao insuficiente. Digite a senha root abaixo para continuar."
            )
            self.lift()
            self.focus_force()
            self.root_password_entry.focus_set()
        except Exception:
            if on_result:
                on_result(None)

    def _finish_root_password_request(self, password):
        callback = self._root_password_callback
        self._root_password_callback = None
        try:
            self.root_password_entry.delete(0, "end")
            self.root_password_frame.grid_remove()
        except Exception:
            pass

        if callback:
            callback(password)

    def _confirm_root_password(self):
        password = self.root_password_entry.get()
        if not password:
            messagebox.showerror("Error", "Digite a senha root.", parent=self)
            return
        self._finish_root_password_request(password)

    def _cancel_root_password(self):
        self._finish_root_password_request(None)

    def _grant_selected(self):
        if self._busy:
            return
        selected_keys = self._selected_keys(self._available_vars)
        if not selected_keys:
            messagebox.showwarning("Permissoes", "Marque ao menos uma permissao disponivel.", parent=self)
            return

        self._set_busy(True, "Concedendo permissoes...")
        try:
            if self._on_grant:
                self._on_grant(self, selected_keys)
                return
            self.complete_grant(selected_keys)
        except Exception as exc:
            self.fail_permission_change(str(exc))

    def _revoke_selected(self):
        if self._busy:
            return
        selected_keys = self._selected_keys(self._assigned_vars)
        if not selected_keys:
            messagebox.showwarning("Permissoes", "Marque ao menos uma permissao do usuario.", parent=self)
            return

        self._set_busy(True, "Removendo permissoes...")
        try:
            if self._on_revoke:
                self._on_revoke(self, selected_keys)
                return
            self.complete_revoke(selected_keys)
        except Exception as exc:
            self.fail_permission_change(str(exc))

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class ExcelSheetSelectionDialog(ctk.CTkToplevel):
    def __init__(self, master, file_name: str, sheet_names):
        super().__init__(master)

        self.withdraw()
        self.title("Select sheets")
        self.geometry("560x520")
        self.minsize(500, 420)
        self.result = None
        self._sheet_names = list(sheet_names)
        self._sheet_vars = {}

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self,
            text="Selecionar abas do Excel",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            self,
            text=f"Arquivo: {file_name}\nMarque as abas que devem entrar no import Raw.",
            justify="left",
            wraplength=500,
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        shortcuts = ctk.CTkFrame(self, fg_color="transparent")
        shortcuts.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="w")

        ctk.CTkButton(
            shortcuts,
            text="Marcar todas",
            width=120,
            command=lambda: self._set_all_sheets(True),
        ).pack(side="left")
        ctk.CTkButton(
            shortcuts,
            text="Desmarcar todas",
            width=140,
            command=lambda: self._set_all_sheets(False),
        ).pack(side="left", padx=(8, 0))

        sheet_box = ctk.CTkScrollableFrame(self, corner_radius=10)
        sheet_box.grid(row=3, column=0, padx=20, pady=(0, 16), sticky="nsew")
        sheet_box.grid_columnconfigure(0, weight=1)

        for index, sheet_name in enumerate(self._sheet_names, start=1):
            label = f"{self._sheet_key(index)} - {sheet_name}"
            var = tk.BooleanVar(value=True)
            self._sheet_vars[sheet_name] = var
            ctk.CTkCheckBox(
                sheet_box,
                text=label,
                variable=var,
            ).grid(row=index - 1, column=0, padx=8, pady=6, sticky="w")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Continue", command=self._confirm).pack(side="right")
        self.after(0, lambda: show_centered_dialog(self, master))

    @staticmethod
    def _sheet_key(index: int) -> str:
        letters = []
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            letters.append(chr(ord("A") + remainder))
        return "".join(reversed(letters))

    def _cancel(self):
        self.result = None
        self.destroy()

    def _set_all_sheets(self, selected: bool):
        for var in self._sheet_vars.values():
            var.set(selected)

    def _confirm(self):
        selected_sheets = [
            sheet_name
            for sheet_name in self._sheet_names
            if self._sheet_vars[sheet_name].get()
        ]
        if not selected_sheets:
            messagebox.showerror("Error", "Selecione pelo menos uma aba para importar.", parent=self)
            return

        self.result = selected_sheets
        self.destroy()


class RawSourceSelectionDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)

        self.title("Add raw data")
        self.geometry("420x320")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Add raw data",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        ctk.CTkLabel(
            self,
            text="Escolha o tipo de arquivo bruto que deseja abrir.",
            justify="left",
            wraplength=360,
        ).grid(row=1, column=0, padx=20, pady=(0, 14), sticky="w")

        ctk.CTkLabel(
            self,
            text="Raw schema",
        ).grid(row=2, column=0, padx=20, pady=(0, 6), sticky="w")

        self.raw_schema_entry = ctk.CTkEntry(
            self,
            placeholder_text="Ex.: 001 - cadastro_base",
        )
        self.raw_schema_entry.grid(row=3, column=0, padx=20, pady=(0, 16), sticky="ew")
        self.raw_schema_entry.bind("<KeyRelease>", lambda _event: self._sync_actions_state())
        self.raw_schema_entry.bind("<FocusOut>", lambda _event: self._sync_actions_state())

        button_box = ctk.CTkFrame(self, fg_color="transparent")
        button_box.grid(row=4, column=0, padx=20, pady=(0, 16), sticky="ew")
        button_box.grid_columnconfigure(0, weight=1, uniform="raw_source")
        button_box.grid_columnconfigure(1, weight=1, uniform="raw_source")

        self.excel_button = ctk.CTkButton(
            button_box,
            text="Excel",
            height=84,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=lambda: self._choose("excel"),
            state="disabled",
        )
        self.excel_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        self.json_button = ctk.CTkButton(
            button_box,
            text="JSON",
            height=84,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=lambda: self._choose("json"),
            state="disabled",
        )
        self.json_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(footer, text="Cancel", command=self._cancel).pack(side="right")

        self._sync_actions_state()
        self.raw_schema_entry.focus_set()

    def _sync_actions_state(self):
        raw_schema = self.raw_schema_entry.get().strip()
        state = "normal" if raw_schema else "disabled"
        self.excel_button.configure(state=state)
        self.json_button.configure(state=state)

    def _choose(self, source_kind: str):
        raw_schema = self.raw_schema_entry.get().strip()
        if not raw_schema:
            messagebox.showerror("Error", "Digite o valor de raw schema para continuar.", parent=self)
            self.raw_schema_entry.focus_set()
            return

        self.result = {
            "source_kind": source_kind,
            "raw_schema": raw_schema,
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class JsonStructureDialog(ctk.CTkToplevel):
    BASE_BUTTON_WIDTH = 170
    BASE_BUTTON_HEIGHT = 34
    BASE_X_GAP = 28
    BASE_Y_GAP = 90
    BASE_PADDING_X = 80
    BASE_PADDING_Y = 60
    MIN_BUTTON_WIDTH = 94
    MAX_BUTTON_WIDTH = 220
    LABEL_CHAR_WIDTH = 7.1
    COMPACT_SIBLING_THRESHOLD = 5
    COMPACT_VERTICAL_JITTER = 24
    MIN_X_GAP = 8
    MAX_X_OVERLAP = 64
    LEAF_X_OVERLAP_RATIO = 0.42
    MIXED_X_OVERLAP_RATIO = 0.26
    BRANCH_X_OVERLAP_RATIO = 0.14
    LAYOUT_RELAXATION_PASSES = 60
    LAYOUT_RELAXATION_STRENGTH = 0.18
    COLLISION_MARGIN = 6
    MAX_ARRAY_SCHEMA_SAMPLES = 2048
    HEAD_ARRAY_SCHEMA_SAMPLES = 1536

    def __init__(self, master, file_name: str, json_data):
        super().__init__(master)

        self.withdraw()
        self.title("JSON structure")
        self.geometry("1220x760")
        self.minsize(940, 620)
        self._file_name = file_name
        self._json_data = json_data
        self._node_layout = []
        self._schema_tree = None
        self._zoom_scale = 1.0
        self.result = None

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="JSON structure",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        header_info = ctk.CTkFrame(self, fg_color="transparent")
        header_info.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")
        header_info.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_info,
            text=(
                f"Arquivo: {file_name}\n"
                "Arraste o quadro branco com a maozinha para explorar a estrutura completa. "
                "Use a roda do mouse para zoom. Listas repetitivas sao consolidadas em uma unica estrutura."
            ),
            justify="left",
            wraplength=1120,
        ).grid(row=0, column=0, sticky="w")

        self.status_label = ctk.CTkLabel(
            header_info,
            text="Consolidando estrutura JSON...",
            justify="left",
            text_color="#475569",
        )
        self.status_label.grid(row=1, column=0, pady=(4, 0), sticky="w")

        board_frame = ctk.CTkFrame(self, corner_radius=12)
        board_frame.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        board_frame.grid_rowconfigure(0, weight=1)
        board_frame.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            board_frame,
            background="#ffffff",
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        x_scroll = tk.Scrollbar(board_frame, orient="horizontal", command=self.canvas.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")

        y_scroll = tk.Scrollbar(board_frame, orient="vertical", command=self.canvas.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")

        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.bind("<Enter>", self._focus_canvas)
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-1>", self._end_pan)
        self.canvas.bind("<MouseWheel>", self._handle_zoom)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        self.add_button = ctk.CTkButton(
            footer,
            text="Adicionar",
            fg_color="#15803d",
            hover_color="#166534",
            state="disabled",
            command=self._confirm_add,
        )
        self.add_button.pack(side="right")
        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right", padx=(0, 8))

        self.after(0, lambda: show_centered_dialog(self, master))
        self.after(30, self._start_schema_build)

    @classmethod
    def _node_label(cls, key) -> str:
        if key is None:
            return "root"
        return str(key)

    @classmethod
    def _classify_primitive(cls, value) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "string"

    @classmethod
    def _empty_summary(cls):
        return {
            "forms": set(),
            "children": {},
            "array_item": None,
            "array_count": 0,
            "array_sampled_count": 0,
            "primitive_types": set(),
        }

    @classmethod
    def _clone_summary(cls, summary):
        if summary is None:
            return None
        return {
            "forms": set(summary.get("forms", set())),
            "children": {
                key: cls._clone_summary(child_summary)
                for key, child_summary in (summary.get("children") or {}).items()
            },
            "array_item": cls._clone_summary(summary.get("array_item")),
            "array_count": int(summary.get("array_count") or 0),
            "array_sampled_count": int(summary.get("array_sampled_count") or 0),
            "primitive_types": set(summary.get("primitive_types", set())),
        }

    @classmethod
    def _merge_summaries(cls, left, right):
        if left is None:
            return cls._clone_summary(right)
        if right is None:
            return left

        left["forms"].update(right.get("forms", set()))
        left["primitive_types"].update(right.get("primitive_types", set()))
        left["array_count"] += int(right.get("array_count") or 0)
        left["array_sampled_count"] += int(right.get("array_sampled_count") or 0)

        for key, child_summary in (right.get("children") or {}).items():
            if key in left["children"]:
                left["children"][key] = cls._merge_summaries(left["children"][key], child_summary)
            else:
                left["children"][key] = cls._clone_summary(child_summary)

        if right.get("array_item") is not None:
            left["array_item"] = cls._merge_summaries(left.get("array_item"), right.get("array_item"))

        return left

    @classmethod
    def _sample_array_indices(cls, total_count: int):
        if total_count <= cls.MAX_ARRAY_SCHEMA_SAMPLES:
            return list(range(total_count))

        head_count = min(cls.HEAD_ARRAY_SCHEMA_SAMPLES, cls.MAX_ARRAY_SCHEMA_SAMPLES, total_count)
        remaining_slots = max(cls.MAX_ARRAY_SCHEMA_SAMPLES - head_count, 0)
        sampled_indices = set(range(head_count))

        if remaining_slots > 0 and total_count > head_count:
            tail_span = total_count - head_count
            for slot in range(remaining_slots):
                offset = int(((slot + 1) * tail_span) / (remaining_slots + 1))
                sampled_indices.add(min(head_count + offset, total_count - 1))

        sampled_indices.add(total_count - 1)
        return sorted(sampled_indices)

    @classmethod
    def _build_structure_summary(cls, value, stats=None):
        if stats is None:
            stats = {"sampled_large_arrays": 0}

        summary = cls._empty_summary()
        if isinstance(value, dict):
            summary["forms"].add("object")
            for child_key, child_value in value.items():
                summary["children"][str(child_key)] = cls._build_structure_summary(child_value, stats=stats)
            return summary

        if isinstance(value, list):
            summary["forms"].add("array")
            summary["array_count"] = len(value)
            sample_indices = cls._sample_array_indices(len(value))
            summary["array_sampled_count"] = len(sample_indices)
            if len(sample_indices) < len(value):
                stats["sampled_large_arrays"] += 1

            item_summary = None
            for index in sample_indices:
                item_summary = cls._merge_summaries(
                    item_summary,
                    cls._build_structure_summary(value[index], stats=stats),
                )
            summary["array_item"] = item_summary
            return summary

        summary["forms"].add("primitive")
        summary["primitive_types"].add(cls._classify_primitive(value))
        return summary

    @classmethod
    def _collect_visible_children(cls, summary):
        child_map = {}
        direct_children = summary.get("children") or {}
        for key, child_summary in direct_children.items():
            child_map[key] = cls._clone_summary(child_summary)

        array_item = summary.get("array_item")
        nested_array_children = []
        if array_item:
            for key, child_summary in (array_item.get("children") or {}).items():
                if key in child_map:
                    child_map[key] = cls._merge_summaries(child_map[key], child_summary)
                else:
                    child_map[key] = cls._clone_summary(child_summary)

            if "array" in array_item.get("forms", set()) and not (array_item.get("children") or {}):
                nested_array_children.append(array_item)
            elif "array" in array_item.get("forms", set()) and array_item.get("array_item") is not None:
                nested_array_children.append(array_item)

        return child_map, nested_array_children

    @classmethod
    def _build_display_tree(cls, summary, label="root", is_root=False):
        base_label = cls._node_label(label)
        forms = set(summary.get("forms", set()))
        if "array" in forms:
            array_count = int(summary.get("array_count") or 0)
            if is_root and base_label == "root":
                node_label = f"root [] x{array_count}"
            elif base_label.startswith("[]"):
                node_label = f"{base_label} x{array_count}"
            else:
                node_label = f"{base_label} [] x{array_count}"
        else:
            node_label = base_label

        node = {"label": node_label, "children": []}
        child_map, nested_array_children = cls._collect_visible_children(summary)
        for child_key in sorted(child_map.keys(), key=lambda current: current.lower()):
            node["children"].append(
                cls._build_display_tree(child_map[child_key], child_key, is_root=False)
            )

        for nested_index, nested_summary in enumerate(nested_array_children, start=1):
            nested_label = "[]" if len(nested_array_children) == 1 else f"[] {nested_index}"
            node["children"].append(
                cls._build_display_tree(nested_summary, nested_label, is_root=False)
            )

        return node

    @classmethod
    def _estimate_button_width(cls, label: str, scale: float) -> int:
        raw_width = int((26 + (len(str(label or "")) * cls.LABEL_CHAR_WIDTH)) * scale)
        return max(int(cls.MIN_BUTTON_WIDTH * scale), min(int(cls.MAX_BUTTON_WIDTH * scale), raw_width))

    @staticmethod
    def _is_leaf(node) -> bool:
        return not bool((node or {}).get("children"))

    @classmethod
    def _child_lane_offsets(cls, children, scale: float):
        child_count = len(children or [])
        if child_count < cls.COMPACT_SIBLING_THRESHOLD:
            return [0.0] * child_count

        jitter = cls.COMPACT_VERTICAL_JITTER * scale
        offsets = []
        for index in range(child_count):
            if index == 0 and child_count % 2 == 1:
                offsets.append(0.0)
                continue
            offsets.append(-jitter if index % 2 == 0 else jitter)
        return offsets

    @classmethod
    def _horizontal_gap(
        cls,
        scale: float,
        sibling_count: int,
        left_child,
        right_child,
        left_lane_offset: float,
        right_lane_offset: float,
    ) -> float:
        gap = cls.BASE_X_GAP * scale
        if sibling_count >= cls.COMPACT_SIBLING_THRESHOLD:
            gap *= 0.62
        elif sibling_count >= 3:
            gap *= 0.82

        both_leaf = cls._is_leaf(left_child) and cls._is_leaf(right_child)
        one_leaf = cls._is_leaf(left_child) or cls._is_leaf(right_child)
        if both_leaf:
            gap *= 0.62
        elif one_leaf:
            gap *= 0.78

        if left_lane_offset != right_lane_offset:
            min_width = min(
                float(left_child.get("_button_width") or 0),
                float(right_child.get("_button_width") or 0),
            )
            if both_leaf:
                overlap_ratio = cls.LEAF_X_OVERLAP_RATIO
            elif one_leaf:
                overlap_ratio = cls.MIXED_X_OVERLAP_RATIO
            else:
                overlap_ratio = cls.BRANCH_X_OVERLAP_RATIO

            overlap_amount = min(cls.MAX_X_OVERLAP * scale, min_width * overlap_ratio)
            gap -= overlap_amount
        else:
            gap = max(cls.MIN_X_GAP * scale, gap)

        return gap

    @classmethod
    def _measure_subtree(cls, node, scale: float = 1.0):
        children = node.get("children", [])
        button_width = cls._estimate_button_width(node.get("label", ""), scale)
        node["_button_width"] = button_width
        if not children:
            node["_subtree_width"] = button_width
            node["_child_lane_offsets"] = []
            node["_child_gaps"] = []
            return node["_subtree_width"]

        child_widths = [cls._measure_subtree(child, scale=scale) for child in children]
        lane_offsets = cls._child_lane_offsets(children, scale=scale)
        child_gaps = []
        for index in range(len(children) - 1):
            child_gaps.append(
                cls._horizontal_gap(
                    scale,
                    len(children),
                    children[index],
                    children[index + 1],
                    lane_offsets[index],
                    lane_offsets[index + 1],
                )
            )

        node["_child_lane_offsets"] = lane_offsets
        node["_child_gaps"] = child_gaps
        combined_width = sum(child_widths) + sum(child_gaps)
        node["_subtree_width"] = max(button_width, combined_width)
        return node["_subtree_width"]

    @classmethod
    def _layout_tree(
        cls,
        node,
        x_center: float,
        depth: int,
        placed_nodes: list,
        scale: float = 1.0,
        level_offset: float = 0.0,
    ):
        y_center = cls.BASE_PADDING_Y * scale + depth * cls.BASE_Y_GAP * scale + level_offset
        node["_x_center"] = x_center
        node["_y_center"] = y_center
        node["_preferred_x_center"] = x_center
        placed_nodes.append(node)

        children = node.get("children", [])
        if not children:
            return

        child_gaps = list(node.get("_child_gaps") or [])
        total_children_width = sum(child["_subtree_width"] for child in children) + sum(child_gaps)
        cursor_x = x_center - (total_children_width / 2)
        lane_offsets = list(node.get("_child_lane_offsets") or [0.0] * len(children))
        for index, child in enumerate(children):
            child_center = cursor_x + (child["_subtree_width"] / 2)
            child["_parent"] = node
            cls._layout_tree(
                child,
                child_center,
                depth + 1,
                placed_nodes,
                scale=scale,
                level_offset=lane_offsets[index],
            )
            if index < len(child_gaps):
                cursor_x += child["_subtree_width"] + child_gaps[index]
            else:
                cursor_x += child["_subtree_width"]

    @classmethod
    def _shift_subtree(cls, node, delta_x: float):
        node["_x_center"] = float(node.get("_x_center", 0.0)) + delta_x
        for child in node.get("children", []):
            cls._shift_subtree(child, delta_x)

    @classmethod
    def _flatten_layout_nodes(cls, node, bucket=None):
        if bucket is None:
            bucket = []
        bucket.append(node)
        for child in node.get("children", []):
            cls._flatten_layout_nodes(child, bucket)
        return bucket

    @classmethod
    def _branch_roots_for_pair(cls, left_node, right_node):
        left_path = []
        current = left_node
        while current is not None:
            left_path.append(current)
            current = current.get("_parent")
        right_path = []
        current = right_node
        while current is not None:
            right_path.append(current)
            current = current.get("_parent")

        left_path.reverse()
        right_path.reverse()
        split_index = 0
        max_shared = min(len(left_path), len(right_path))
        while split_index < max_shared and left_path[split_index] is right_path[split_index]:
            split_index += 1

        if split_index == len(left_path):
            return left_node, right_path[split_index] if split_index < len(right_path) else right_node
        if split_index == len(right_path):
            return left_path[split_index] if split_index < len(left_path) else left_node, right_node

        return left_path[split_index], right_path[split_index]

    @classmethod
    def _apply_preferred_position_relaxation(cls, node, strength: float = 0.18):
        total_shift = 0.0
        for child in node.get("children", []):
            preferred_x = float(child.get("_preferred_x_center", child.get("_x_center", 0.0)))
            current_x = float(child.get("_x_center", 0.0))
            delta_x = (preferred_x - current_x) * strength
            if abs(delta_x) > 0.01:
                cls._shift_subtree(child, delta_x)
                total_shift += abs(delta_x)
            total_shift += cls._apply_preferred_position_relaxation(child, strength=strength)
        return total_shift

    @classmethod
    def _rectangles_overlap(cls, left_node, right_node, button_height: float, margin: float):
        left_half_width = float(left_node.get("_button_width", 0.0)) / 2.0
        right_half_width = float(right_node.get("_button_width", 0.0)) / 2.0
        dx = abs(float(left_node.get("_x_center", 0.0)) - float(right_node.get("_x_center", 0.0)))
        dy = abs(float(left_node.get("_y_center", 0.0)) - float(right_node.get("_y_center", 0.0)))
        min_dx = left_half_width + right_half_width + margin
        min_dy = button_height + margin
        return dx < min_dx and dy < min_dy, max(0.0, min_dx - dx)

    @classmethod
    def _resolve_layout_collisions(cls, root_node, scale: float):
        button_height = cls.BASE_BUTTON_HEIGHT * scale
        margin = cls.COLLISION_MARGIN * scale
        for _ in range(cls.LAYOUT_RELAXATION_PASSES):
            relaxation_shift = cls._apply_preferred_position_relaxation(
                root_node,
                strength=cls.LAYOUT_RELAXATION_STRENGTH,
            )
            collisions = 0
            layout_nodes = sorted(
                cls._flatten_layout_nodes(root_node, []),
                key=lambda current: (float(current.get("_y_center", 0.0)), float(current.get("_x_center", 0.0))),
            )
            vertical_threshold = button_height + margin
            for index, left_node in enumerate(layout_nodes):
                left_y = float(left_node.get("_y_center", 0.0))
                for right_node in layout_nodes[index + 1:]:
                    right_y = float(right_node.get("_y_center", 0.0))
                    if (right_y - left_y) >= vertical_threshold:
                        break

                    collides, overlap_x = cls._rectangles_overlap(left_node, right_node, button_height, margin)
                    if not collides:
                        continue

                    collisions += 1
                    branch_left, branch_right = cls._branch_roots_for_pair(left_node, right_node)
                    shift_amount = (overlap_x / 2.0) + max(1.0, margin * 0.12)
                    if float(branch_left.get("_x_center", 0.0)) <= float(branch_right.get("_x_center", 0.0)):
                        cls._shift_subtree(branch_left, -shift_amount)
                        cls._shift_subtree(branch_right, shift_amount)
                    else:
                        cls._shift_subtree(branch_left, shift_amount)
                        cls._shift_subtree(branch_right, -shift_amount)

            if collisions == 0 and relaxation_shift < 0.25:
                break

    def _start_schema_build(self):
        def worker():
            stats = {"sampled_large_arrays": 0}
            try:
                summary = self._build_structure_summary(self._json_data, stats=stats)
                display_tree = self._build_display_tree(summary, label="root", is_root=True)
            except Exception as exc:
                self.after(0, lambda: self._fail_schema_build(str(exc)))
                return

            self._json_data = None
            self.after(0, lambda: self._finish_schema_build(display_tree, stats))

        threading.Thread(target=worker, daemon=True, name="json-structure-summary").start()

    def _finish_schema_build(self, display_tree, stats: dict):
        if not self.winfo_exists():
            return
        self._schema_tree = display_tree
        sampled_large_arrays = int((stats or {}).get("sampled_large_arrays") or 0)
        if sampled_large_arrays:
            self.status_label.configure(
                text=(
                    "Estrutura consolidada. "
                    f"{sampled_large_arrays} lista(s) grande(s) foram resumidas por amostragem estrutural."
                ),
                text_color="#475569",
            )
        else:
            self.status_label.configure(
                text="Estrutura consolidada. Listas repetitivas aparecem em uma unica ramificacao.",
                text_color="#475569",
            )
        self.add_button.configure(state="normal")
        self._render_structure()

    def _fail_schema_build(self, error_message: str):
        if not self.winfo_exists():
            return
        self.status_label.configure(
            text=f"Falha ao consolidar a estrutura JSON: {error_message}",
            text_color="#b91c1c",
        )

    def _render_structure(self):
        previous_xview = self.canvas.xview()
        previous_yview = self.canvas.yview()
        self.canvas.delete("all")
        self._node_layout = []

        root_node = self._schema_tree
        if not root_node:
            return

        button_height = int(self.BASE_BUTTON_HEIGHT * self._zoom_scale)
        y_gap = self.BASE_Y_GAP * self._zoom_scale
        padding_x = self.BASE_PADDING_X * self._zoom_scale
        padding_y = self.BASE_PADDING_Y * self._zoom_scale
        button_font = ctk.CTkFont(size=max(10, int(12 * self._zoom_scale)), weight="bold")

        self._measure_subtree(root_node, scale=self._zoom_scale)
        root_node["_parent"] = None
        root_center = padding_x + (root_node["_subtree_width"] / 2)
        self._layout_tree(root_node, root_center, 0, self._node_layout, scale=self._zoom_scale)
        self._resolve_layout_collisions(root_node, scale=self._zoom_scale)
        self._node_layout = self._flatten_layout_nodes(root_node, [])

        for node in self._node_layout:
            parent_x = node["_x_center"]
            parent_y = node["_y_center"]
            for child in node.get("children", []):
                child_x = child["_x_center"]
                child_y = child["_y_center"]
                mid_y = parent_y + (y_gap / 2) - max(8, 10 * self._zoom_scale)
                self.canvas.create_line(
                    parent_x,
                    parent_y + (button_height / 2),
                    parent_x,
                    mid_y,
                    child_x,
                    mid_y,
                    child_x,
                    child_y - (button_height / 2),
                    fill="#94a3b8",
                    width=max(1, int(2 * self._zoom_scale)),
                    smooth=False,
                )

        for node in self._node_layout:
            button = ctk.CTkButton(
                self.canvas,
                text=node["label"],
                width=int(node.get("_button_width") or self._estimate_button_width(node["label"], self._zoom_scale)),
                height=button_height,
                font=button_font,
                corner_radius=10,
                command=lambda: None,
            )
            node_width = int(node.get("_button_width") or self._estimate_button_width(node["label"], self._zoom_scale))
            self.canvas.create_window(
                node["_x_center"],
                node["_y_center"],
                window=button,
                width=node_width,
                height=button_height,
            )

        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(
                scrollregion=(
                    bbox[0] - padding_x,
                    bbox[1] - padding_y,
                    bbox[2] + padding_x,
                    bbox[3] + padding_y,
                )
            )
            self.canvas.xview_moveto(previous_xview[0] if previous_xview else 0)
            self.canvas.yview_moveto(previous_yview[0] if previous_yview else 0)

    def _focus_canvas(self, _event=None):
        try:
            self.canvas.focus_set()
        except Exception:
            pass

    def _start_pan(self, event):
        self._focus_canvas()
        self.canvas.configure(cursor="fleur")
        self.canvas.scan_mark(event.x, event.y)

    def _drag_pan(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _end_pan(self, _event):
        self.canvas.configure(cursor="hand2")

    def _handle_zoom(self, event):
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return

        factor = 1.1 if delta > 0 else 1 / 1.1
        next_zoom = max(0.55, min(2.4, self._zoom_scale * factor))
        if abs(next_zoom - self._zoom_scale) < 0.001:
            return

        self._zoom_scale = next_zoom
        self._render_structure()

    def _confirm_add(self):
        self.result = "add"
        self.close()

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class RawImportReviewDialog(ctk.CTkToplevel):
    PAGE_SIZE = 80
    SUMMARY_BOX_FALLBACK_HEIGHT = 118

    def __init__(
        self,
        master,
        file_name: str,
        total_records: int,
        header_candidates,
        noise_candidates,
        preview_count: int | None = None,
        record_refs=None,
        sheet_context=None,
        require_header_per_sheet: bool = False,
    ):
        super().__init__(master)

        self.withdraw()
        self.title("Revisar header e noise")
        self.geometry("1120x760")
        self.minsize(980, 660)
        self.result = None
        self._total_records = total_records
        self._role_panels = {}
        self._header_candidate_vars = {}
        self._noise_candidate_vars = {}
        self._header_manual_enabled = tk.BooleanVar(value=False)
        self._noise_manual_enabled = tk.BooleanVar(value=False)
        self._first_line_headers_enabled = tk.BooleanVar(value=False)
        self._summary_resize_after_id = None
        self._last_summary_box_height = None
        self._record_refs = list(record_refs or [])
        self._record_refs_by_number = {
            record["record_number"]: record
            for record in self._record_refs
        }
        self._sheet_context = list(sheet_context or [])
        self._sheet_info_by_key = {
            str(sheet.get("sheet_key", "")).lower(): sheet
            for sheet in self._sheet_context
            if sheet.get("sheet_key")
        }
        self._manual_source_lookup = self._build_manual_source_lookup(self._record_refs)
        self._require_header_per_sheet = require_header_per_sheet

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="Revisao do JSONB Raw",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        summary_text = (
            f"Arquivo: {file_name}\n"
            f"Registros Raw detectados: {total_records}\n"
            f"Sugestoes automaticas calculadas sobre as primeiras {preview_count or total_records} linhas importadas.\n"
            "Os numeros manuais usam o indice # mostrado abaixo, global para todos os arquivos e abas desta revisao."
        )
        sheet_labels = ""
        if self._sheet_context:
            sheet_labels = ", ".join(
                f"{sheet['sheet_key']}={sheet['sheet_name']}"
                for sheet in self._sheet_context
            )
        requirement_text = ""
        if self._require_header_per_sheet:
            requirement_text = "Para continuar, selecione exatamente 1 header por aba e headers iguais entre as abas."
        self.summary_box = ctk.CTkScrollableFrame(
            self,
            corner_radius=12,
            height=self.SUMMARY_BOX_FALLBACK_HEIGHT,
        )
        self.summary_box.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        self.summary_box.grid_columnconfigure(0, weight=1)

        self.summary_label = ctk.CTkLabel(
            self.summary_box,
            text=summary_text,
            justify="left",
            wraplength=1000
        )
        self.summary_label.grid(row=0, column=0, padx=12, pady=(8, 6), sticky="w")

        self.summary_sheet_title_label = None
        self.summary_sheet_values_label = None
        self.summary_requirement_label = None

        if sheet_labels:
            self.summary_sheet_title_label = ctk.CTkLabel(
                self.summary_box,
                text="Abas importadas nesta revisao:",
                justify="left",
                wraplength=1000,
            )
            self.summary_sheet_title_label.grid(row=1, column=0, padx=12, pady=(0, 2), sticky="w")

            self.summary_sheet_values_label = ctk.CTkLabel(
                self.summary_box,
                text=sheet_labels + ".",
                justify="left",
                wraplength=1000,
            )
            self.summary_sheet_values_label.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="w")

        if requirement_text:
            self.summary_requirement_label = ctk.CTkLabel(
                self.summary_box,
                text=requirement_text,
                justify="left",
                wraplength=1000,
            )
            self.summary_requirement_label.grid(row=3, column=0, padx=12, pady=(0, 8), sticky="w")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.header_manual_entry = self._build_role_panel(
            parent=content,
            column=0,
            title="Header",
            description=(
                "Sugestoes: linhas com todos os campos preenchidos e pelo menos 70% dos valores em string. "
                "As linhas marcadas aqui nao entram no banco e viram as chaves das demais linhas."
            ),
            candidates=header_candidates,
            candidate_vars=self._header_candidate_vars,
            manual_enabled_var=self._header_manual_enabled,
            manual_placeholder=self._manual_placeholder("2 ou 2-3 ou 2, 5, 8-10"),
        )
        self.noise_manual_entry = self._build_role_panel(
            parent=content,
            column=1,
            title="Noise",
            description=(
                "Sugestoes: linhas com pelo menos uma celula string contendo 3 ou mais palavras. "
                "As linhas marcadas aqui nao entram no banco e serao salvas nas notas da versao."
            ),
            candidates=noise_candidates,
            candidate_vars=self._noise_candidate_vars,
            manual_enabled_var=self._noise_manual_enabled,
            manual_placeholder=self._manual_placeholder("1 ou 4-6 ou 3, 8, 12-15"),
        )

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Confirm", command=self._confirm).pack(side="right")
        self.bind("<Configure>", self._schedule_summary_box_resize, add="+")
        self.after(0, self._schedule_summary_box_resize)
        self.after(0, lambda: show_centered_dialog(self, master))

    def _schedule_summary_box_resize(self, _event=None):
        if self._summary_resize_after_id is not None:
            try:
                self.after_cancel(self._summary_resize_after_id)
            except Exception:
                pass
        self._summary_resize_after_id = self.after(15, self._apply_dynamic_summary_box_height)

    def _apply_dynamic_summary_box_height(self):
        self._summary_resize_after_id = None
        if not getattr(self, "summary_box", None) or not self.summary_box.winfo_exists():
            return

        try:
            self.update_idletasks()

            target_height = self.summary_label.winfo_y() + self.summary_label.winfo_reqheight() + 6
            if self.summary_sheet_title_label and self.summary_sheet_title_label.winfo_exists():
                target_height = (
                    self.summary_sheet_title_label.winfo_y()
                    + self.summary_sheet_title_label.winfo_reqheight()
                    + 2
                )
            if self.summary_sheet_values_label and self.summary_sheet_values_label.winfo_exists():
                target_height = (
                    self.summary_sheet_values_label.winfo_y()
                    + self._measure_single_line_label_height(self.summary_sheet_values_label)
                    + 4
                )
            elif self.summary_requirement_label and self.summary_requirement_label.winfo_exists():
                target_height = (
                    self.summary_requirement_label.winfo_y()
                    + self.summary_requirement_label.winfo_reqheight()
                    + 8
                )

            target_height = max(int(target_height), 48)
            if target_height != self._last_summary_box_height:
                self.summary_box.configure(height=target_height)
                self._last_summary_box_height = target_height
        except Exception:
            self.summary_box.configure(height=self.SUMMARY_BOX_FALLBACK_HEIGHT)
            self._last_summary_box_height = self.SUMMARY_BOX_FALLBACK_HEIGHT

    @staticmethod
    def _measure_single_line_label_height(reference_label) -> int:
        parent = reference_label.master
        probe_label = ctk.CTkLabel(
            parent,
            text="Ag",
            font=reference_label.cget("font"),
            justify=reference_label.cget("justify"),
        )
        probe_label.update_idletasks()
        measured_height = probe_label.winfo_reqheight()
        probe_label.destroy()
        return max(int(measured_height), 20)

    def _build_role_panel(
        self,
        parent,
        column: int,
        title: str,
        description: str,
        candidates,
        candidate_vars: dict,
        manual_enabled_var,
        manual_placeholder: str,
    ):
        panel = ctk.CTkFrame(parent, corner_radius=12)
        panel.grid(row=0, column=column, padx=(0, 10) if column == 0 else (10, 0), sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        role_key = title.lower()
        candidates_row = 2
        if role_key == "header":
            candidates_row = 3
        panel.grid_rowconfigure(candidates_row, weight=1)

        ctk.CTkLabel(
            panel,
            text=title,
            font=ctk.CTkFont(size=17, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(14, 6), sticky="w")

        ctk.CTkLabel(
            panel,
            text=description,
            justify="left",
            wraplength=470
        ).grid(row=1, column=0, padx=16, pady=(0, 10), sticky="w")

        if role_key == "header":
            header_mode_box = ctk.CTkFrame(panel, fg_color="transparent")
            header_mode_box.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="ew")
            header_mode_box.grid_columnconfigure(0, weight=1)

            ctk.CTkCheckBox(
                header_mode_box,
                text="Todos os headers estao na primeira linha",
                variable=self._first_line_headers_enabled,
                command=self._toggle_first_line_headers_mode,
            ).grid(row=0, column=0, sticky="w")

            ctk.CTkLabel(
                header_mode_box,
                text=(
                    "Ao marcar, a primeira linha de cada aba ou arquivo importado sera usada "
                    "como header automaticamente."
                ),
                justify="left",
                text_color="#6b7280",
                wraplength=470,
            ).grid(row=1, column=0, pady=(6, 0), sticky="w")

        candidates_box = ctk.CTkScrollableFrame(panel, corner_radius=10)
        candidates_box.grid(row=candidates_row, column=0, padx=16, pady=(0, 12), sticky="nsew")
        candidates_box.grid_columnconfigure(0, weight=1)

        for candidate in candidates:
            candidate_vars[candidate["record_number"]] = tk.BooleanVar(value=False)

        navigation = ctk.CTkFrame(panel, fg_color="transparent")
        navigation.grid(row=candidates_row + 1, column=0, padx=16, pady=(0, 8), sticky="ew")
        navigation.grid_columnconfigure(1, weight=1)

        prev_button = ctk.CTkButton(
            navigation,
            text="Anterior",
            width=90,
            command=lambda role_key=title.lower(): self._change_role_page(role_key, -1),
        )
        prev_button.grid(row=0, column=0, sticky="w")

        page_label = ctk.CTkLabel(navigation, text="")
        page_label.grid(row=0, column=1, sticky="n")

        jump_box = ctk.CTkFrame(navigation, fg_color="transparent")
        jump_box.grid(row=0, column=2, padx=(10, 10), sticky="e")

        ctk.CTkLabel(jump_box, text="Pagina").pack(side="left", padx=(0, 6))
        page_entry = ctk.CTkEntry(jump_box, width=64)
        page_entry.pack(side="left")
        page_entry.bind("<Return>", lambda _event, role_key=title.lower(): self._jump_role_page(role_key))
        page_entry.bind("<KP_Enter>", lambda _event, role_key=title.lower(): self._jump_role_page(role_key))

        jump_button = ctk.CTkButton(
            jump_box,
            text="Ir",
            width=54,
            command=lambda role_key=title.lower(): self._jump_role_page(role_key),
        )
        jump_button.pack(side="left", padx=(6, 0))

        next_button = ctk.CTkButton(
            navigation,
            text="Proxima",
            width=90,
            command=lambda role_key=title.lower(): self._change_role_page(role_key, 1),
        )
        next_button.grid(row=0, column=3, sticky="e")

        manual_box = ctk.CTkFrame(panel, fg_color="transparent")
        manual_box.grid(row=candidates_row + 2, column=0, padx=16, pady=(0, 14), sticky="ew")
        manual_box.grid_columnconfigure(0, weight=1)

        manual_toggle = ctk.CTkCheckBox(
            manual_box,
            text="Adicionar linhas manualmente",
            variable=manual_enabled_var,
            command=lambda: self._toggle_manual_entry(manual_enabled_var, manual_entry),
        )
        manual_toggle.grid(row=0, column=0, pady=(0, 8), sticky="w")

        manual_entry = ctk.CTkEntry(
            manual_box,
            placeholder_text=manual_placeholder,
            state="disabled",
        )
        manual_entry.grid(row=1, column=0, sticky="ew")

        ctk.CTkLabel(
            manual_box,
            text="Use o # global mostrado na lista. Aceita numeros isolados e intervalos separados por virgula.",
            text_color="#6b7280",
            justify="left",
        ).grid(row=2, column=0, pady=(6, 0), sticky="w")

        self._role_panels[role_key] = {
            "candidates": list(candidates),
            "candidate_vars": candidate_vars,
            "candidates_box": candidates_box,
            "page_label": page_label,
            "page_entry": page_entry,
            "prev_button": prev_button,
            "next_button": next_button,
            "jump_button": jump_button,
            "manual_toggle": manual_toggle,
            "manual_entry": manual_entry,
            "page_index": 0,
        }
        self._render_role_page(role_key)
        if role_key == "header":
            self._refresh_header_panel_state()

        return manual_entry

    def _manual_placeholder(self, default_text: str) -> str:
        return f"Ex.: {default_text}"

    def _change_role_page(self, role_key: str, delta: int):
        state = self._role_panels[role_key]
        total_pages = self._get_role_total_pages(state)
        state["page_index"] = max(0, min(state["page_index"] + delta, total_pages - 1))
        self._render_role_page(role_key)

    def _jump_role_page(self, role_key: str):
        state = self._role_panels[role_key]
        page_entry = state.get("page_entry")
        raw_value = page_entry.get().strip() if page_entry else ""
        if not raw_value:
            return

        if not re.fullmatch(r"\d+", raw_value):
            messagebox.showerror("Error", "Digite um numero de pagina valido.", parent=self)
            if page_entry:
                page_entry.focus_set()
            return

        requested_page = int(raw_value)
        total_pages = self._get_role_total_pages(state)
        if requested_page < 1 or requested_page > total_pages:
            messagebox.showerror(
                "Error",
                f"A pagina precisa estar entre 1 e {total_pages}.",
                parent=self,
            )
            if page_entry:
                page_entry.focus_set()
            return

        state["page_index"] = requested_page - 1
        self._render_role_page(role_key)

    def _get_role_total_pages(self, state: dict) -> int:
        candidate_count = len(state["candidates"])
        if candidate_count == 0:
            return 1
        return max(1, (candidate_count + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _render_role_page(self, role_key: str):
        state = self._role_panels[role_key]
        candidates_box = state["candidates_box"]
        for child in candidates_box.winfo_children():
            child.destroy()

        candidates = state["candidates"]
        total_pages = self._get_role_total_pages(state)
        page_index = state["page_index"]
        state["page_label"].configure(text=f"Pagina {page_index + 1} de {total_pages}")
        page_entry = state.get("page_entry")
        if page_entry is not None:
            page_entry.delete(0, "end")
            page_entry.insert(0, str(page_index + 1))
        state["prev_button"].configure(state="normal" if page_index > 0 else "disabled")
        state["next_button"].configure(state="normal" if page_index < total_pages - 1 else "disabled")

        if not candidates:
            ctk.CTkLabel(
                candidates_box,
                text="Nenhuma linha sugerida automaticamente.",
                justify="left",
                wraplength=420
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        start = page_index * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, len(candidates))
        page_candidates = candidates[start:end]
        checkbox_state = "disabled" if role_key == "header" and self._first_line_headers_enabled.get() else "normal"

        for row_index, candidate in enumerate(page_candidates):
            item = ctk.CTkFrame(candidates_box, corner_radius=10)
            item.grid(row=row_index, column=0, padx=4, pady=4, sticky="ew")
            item.grid_columnconfigure(1, weight=1)

            ctk.CTkCheckBox(
                item,
                text="",
                variable=state["candidate_vars"][candidate["record_number"]],
                width=24,
                state=checkbox_state,
            ).grid(row=0, column=0, padx=(10, 8), pady=10, sticky="n")
            ctk.CTkLabel(
                item,
                text=self._format_candidate_text(candidate),
                justify="left",
                wraplength=400,
                anchor="w"
            ).grid(row=0, column=1, padx=(0, 10), pady=10, sticky="w")

    @staticmethod
    def _format_candidate_text(candidate: dict) -> str:
        values_preview = " | ".join(
            "(null)" if value is None else str(value)
            for value in candidate.get("values", [])
        )
        source_label = candidate.get("source_label") or "origem nao informada"
        return f"#{candidate['record_number']} - {source_label}\n{values_preview}"

    @staticmethod
    def _toggle_manual_entry(enabled_var, entry):
        entry.configure(state="normal" if enabled_var.get() else "disabled")
        if not enabled_var.get():
            entry.delete(0, "end")

    def _toggle_first_line_headers_mode(self):
        if self._first_line_headers_enabled.get():
            self._header_manual_enabled.set(False)

        self._render_role_page("header")
        self._refresh_header_panel_state()

    def _refresh_header_panel_state(self):
        state = self._role_panels.get("header")
        if not state:
            return

        automatic_mode = self._first_line_headers_enabled.get()
        total_pages = self._get_role_total_pages(state)
        page_index = state["page_index"]

        state["prev_button"].configure(
            state="disabled" if automatic_mode or page_index == 0 else "normal"
        )
        state["next_button"].configure(
            state="disabled" if automatic_mode or page_index >= total_pages - 1 else "normal"
        )
        state["jump_button"].configure(state="disabled" if automatic_mode else "normal")
        state["page_entry"].configure(state="disabled" if automatic_mode else "normal")
        state["manual_toggle"].configure(state="disabled" if automatic_mode else "normal")
        self._toggle_manual_entry(self._header_manual_enabled, state["manual_entry"])

    def _collect_first_line_header_rows(self) -> set[int]:
        if not self._record_refs:
            raise ValueError("Nao ha registros suficientes para selecionar headers automaticamente.")

        if self._sheet_context:
            selected = set()
            missing = []
            for sheet in self._sheet_context:
                sheet_key = str(sheet.get("sheet_key") or "").strip().lower()
                sheet_name = str(sheet.get("sheet_name") or sheet_key.upper()).strip() or sheet_key.upper()
                record_number = self._manual_source_lookup.get((sheet_key, 1))
                if record_number is None:
                    missing.append(f"{sheet_key.upper()} ({sheet_name})")
                    continue
                selected.add(record_number)

            if missing:
                raise ValueError(
                    "Nao foi possivel usar a primeira linha como header em todas as abas ou arquivos. "
                    "Primeira linha ausente nos registros importaveis de: "
                    + ", ".join(missing)
                    + "."
                )
            return selected

        first_record = next(
            (
                record
                for record in self._record_refs
                if int(record.get("source_row_number") or 0) == 1
            ),
            None,
        )
        if first_record is None and self._record_refs:
            first_record = self._record_refs[0]
        if first_record is None:
            raise ValueError("Nao foi possivel localizar a primeira linha para selecionar o header automaticamente.")
        return {int(first_record["record_number"])}

    def _parse_manual_selection(self, raw_value: str, selection_label: str) -> set[int]:
        selected = set()
        content = raw_value.strip()
        if not content:
            return selected

        for chunk in content.split(","):
            token = chunk.strip()
            if not token:
                continue

            sheet_range_match = re.fullmatch(r"#?(\d+)\s*(?:-\s*#?(\d+))?\s*([A-Za-z]+)", token)
            if sheet_range_match:
                start = int(sheet_range_match.group(1))
                end = int(sheet_range_match.group(2) or start)
                sheet_key = sheet_range_match.group(3).lower()
                if start > end:
                    raise ValueError(
                        f"Intervalo invalido em {selection_label}: {token}. Use inicio menor ou igual ao fim."
                    )
                selected.update(
                    self._resolve_sheet_manual_range(sheet_key, start, end, selection_label)
                )
                continue

            range_match = re.fullmatch(r"#?(\d+)\s*-\s*#?(\d+)", token)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                if start > end:
                    raise ValueError(
                        f"Intervalo invalido em {selection_label}: {token}. Use inicio menor ou igual ao fim."
                    )
                for number in range(start, end + 1):
                    self._validate_manual_number(number, selection_label)
                    selected.add(number)
                continue

            if not re.fullmatch(r"#?\d+", token):
                raise ValueError(
                    f"Formato invalido em {selection_label}: {token}. Use exemplos como #5, 2-5 ou 2, 4, 6-10."
                )

            number = int(token.lstrip("#"))
            self._validate_manual_number(number, selection_label)
            selected.add(number)

        return selected

    @staticmethod
    def _build_manual_source_lookup(records) -> dict:
        lookup = {}
        for record in records:
            sheet_key = record.get("sheet_key")
            source_row_number = record.get("source_row_number")
            if not sheet_key or source_row_number is None:
                continue
            lookup[(str(sheet_key).lower(), int(source_row_number))] = record["record_number"]
        return lookup

    def _resolve_sheet_manual_range(
        self,
        sheet_key: str,
        start: int,
        end: int,
        selection_label: str,
    ) -> set[int]:
        if sheet_key not in self._sheet_info_by_key:
            known = ", ".join(sheet["sheet_key"] for sheet in self._sheet_context) or "nenhuma"
            raise ValueError(
                f"A aba {sheet_key.upper()} em {selection_label} nao esta entre as abas importadas. "
                f"Abas validas: {known}."
            )

        selected = set()
        for source_row_number in range(start, end + 1):
            record_number = self._manual_source_lookup.get((sheet_key, source_row_number))
            if record_number is None:
                sheet_name = self._sheet_info_by_key[sheet_key].get("sheet_name", sheet_key.upper())
                raise ValueError(
                    f"A linha {source_row_number} da aba {sheet_key.upper()} ({sheet_name}) "
                    f"em {selection_label} nao existe nos registros importaveis."
                )
            selected.add(record_number)
        return selected

    def _validate_manual_number(self, number: int, selection_label: str):
        if number < 1 or number > self._total_records:
            raise ValueError(
                f"O numero {number} em {selection_label} esta fora do intervalo permitido (1 a {self._total_records})."
            )

    @staticmethod
    def _collect_checked_rows(candidate_vars: dict) -> set[int]:
        return {
            row_number
            for row_number, var in candidate_vars.items()
            if var.get()
        }

    @staticmethod
    def _normalize_header_values(values, column_count: int) -> tuple:
        normalized = []
        for column_index in range(column_count):
            value = values[column_index] if column_index < len(values) else None
            if value is None:
                normalized.append("")
            else:
                normalized.append(str(value).strip())
        return tuple(normalized)

    def _validate_header_per_sheet(self, header_rows: set[int]):
        header_records = [
            self._record_refs_by_number[number]
            for number in sorted(header_rows)
            if number in self._record_refs_by_number
        ]
        header_rows_by_sheet = {
            sheet["sheet_key"]: []
            for sheet in self._sheet_context
        }
        for record in header_records:
            sheet_key = record.get("sheet_key")
            if sheet_key in header_rows_by_sheet:
                header_rows_by_sheet[sheet_key].append(record)

        missing_sheets = [
            f"{sheet['sheet_key']} ({sheet['sheet_name']})"
            for sheet in self._sheet_context
            if not header_rows_by_sheet.get(sheet["sheet_key"])
        ]
        repeated_sheets = [
            f"{sheet_key} ({len(records)} headers)"
            for sheet_key, records in header_rows_by_sheet.items()
            if len(records) > 1
        ]
        if missing_sheets or repeated_sheets:
            details = []
            if missing_sheets:
                details.append("sem header: " + ", ".join(missing_sheets))
            if repeated_sheets:
                details.append("com mais de um header: " + ", ".join(repeated_sheets))
            raise ValueError(
                "Selecione exatamente 1 header para cada aba importada (" + "; ".join(details) + ")."
            )

        column_count = max(
            [len(record.get("values", [])) for record in header_records],
            default=0,
        )
        if column_count == 0:
            raise ValueError("Os headers selecionados nao possuem colunas.")

        first_sheet = self._sheet_context[0]
        first_record = header_rows_by_sheet[first_sheet["sheet_key"]][0]
        first_header = self._normalize_header_values(first_record.get("values", []), column_count)
        different_sheets = []
        for sheet in self._sheet_context[1:]:
            record = header_rows_by_sheet[sheet["sheet_key"]][0]
            header = self._normalize_header_values(record.get("values", []), column_count)
            if header != first_header:
                different_sheets.append(f"{sheet['sheet_key']} ({sheet['sheet_name']})")

        if different_sheets:
            raise ValueError(
                "Os headers selecionados nao sao iguais entre as abas. "
                f"Divergencias em: {', '.join(different_sheets)}."
            )

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        try:
            if self._first_line_headers_enabled.get():
                header_rows = self._collect_first_line_header_rows()
            else:
                header_rows = self._collect_checked_rows(self._header_candidate_vars)
            noise_rows = self._collect_checked_rows(self._noise_candidate_vars)

            if not self._first_line_headers_enabled.get() and self._header_manual_enabled.get():
                header_rows.update(
                    self._parse_manual_selection(self.header_manual_entry.get(), "header")
                )

            if self._noise_manual_enabled.get():
                noise_rows.update(
                    self._parse_manual_selection(self.noise_manual_entry.get(), "noise")
                )

            overlap = sorted(header_rows & noise_rows)
            if overlap:
                joined = ", ".join(str(item) for item in overlap)
                messagebox.showerror(
                    "Error",
                    f"As mesmas linhas nao podem ser header e noise ao mesmo tempo: {joined}.",
                    parent=self,
                )
                return

            if self._require_header_per_sheet:
                self._validate_header_per_sheet(header_rows)

            self.result = {
                "header_row_numbers": sorted(header_rows),
                "noise_row_numbers": sorted(noise_rows),
            }
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc), parent=self)


class DumpRestoreDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        restore_info: dict,
        default_target_full_table_name: str,
        conflict_message: str | None = None,
        dialog_title: str = "Restaurar dump",
        heading_text: str | None = None,
        summary_text: str | None = None,
        target_table_label: str = "Nome da tabela",
        default_version_title: str | None = None,
        confirm_button_text: str = "Restaurar",
    ):
        super().__init__(master)

        heading_text = heading_text or dialog_title
        if summary_text is None:
            summary_text = (
                f"Base: {restore_info['database_name']}\n"
                f"Tabela de origem: {restore_info['schema_name']}.{restore_info['table_name']}\n"
                f"Versao do dump: {restore_info['version_code']} - {restore_info['version_title']}"
            )
        if default_version_title is None:
            default_version_title = f"Restauracao do dump {restore_info['version_code']}"

        self.title(dialog_title)
        self.geometry("620x380")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=heading_text,
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 14), sticky="w")

        ctk.CTkLabel(
            self,
            text=summary_text,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="ew")

        if conflict_message:
            ctk.CTkLabel(
                self,
                text=conflict_message,
                justify="left",
                anchor="w",
                text_color="#b91c1c",
                wraplength=560,
            ).grid(row=2, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(self, text=target_table_label).grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.target_table_entry = ctk.CTkEntry(self)
        self.target_table_entry.grid(row=3, column=1, padx=20, pady=8, sticky="ew")
        self.target_table_entry.insert(0, default_target_full_table_name)

        ctk.CTkLabel(self, text="Titulo da versao").grid(row=4, column=0, padx=20, pady=8, sticky="w")
        self.version_title_entry = ctk.CTkEntry(self)
        self.version_title_entry.grid(row=4, column=1, padx=20, pady=8, sticky="ew")
        self.version_title_entry.insert(0, default_version_title)

        ctk.CTkLabel(self, text="Seu nome").grid(row=5, column=0, padx=20, pady=8, sticky="w")
        self.author_entry = ctk.CTkEntry(self)
        self.author_entry.grid(row=5, column=1, padx=20, pady=8, sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text=confirm_button_text, command=self._confirm).pack(side="right")

    def _cancel(self):
        self.result = None
        self.destroy()

    def _confirm(self):
        target_full_table_name = self.target_table_entry.get().strip()
        version_title = self.version_title_entry.get().strip()
        requested_by = self.author_entry.get().strip()

        if not target_full_table_name:
            messagebox.showerror("Error", "Digite o nome da tabela para restauracao.", parent=self)
            return

        if not version_title:
            messagebox.showerror("Error", "Digite o titulo da versao de restauracao.", parent=self)
            return

        if not requested_by:
            messagebox.showerror("Error", "Digite seu nome.", parent=self)
            return

        self.result = {
            "target_full_table_name": target_full_table_name,
            "version_title": version_title,
            "requested_by": requested_by,
        }
        self.destroy()


class DumpArtifactsDialog(ctk.CTkToplevel):
    def __init__(self, master, on_refresh=None, on_delete=None, on_restore=None):
        super().__init__(master)

        self.title("Manage dumps")
        self.geometry("980x760")
        self.minsize(840, 620)
        self._on_refresh = on_refresh
        self._on_delete = on_delete
        self._on_restore = on_restore
        self._dump_items = []
        self._expanded_keys = set()
        self._busy = False

        self.transient(master)
        self.grab_set()
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text="Arquivos de recuperacao",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        header_actions = ctk.CTkFrame(self, fg_color="transparent")
        header_actions.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        header_actions.grid_columnconfigure(1, weight=1)

        self.refresh_button = ctk.CTkButton(
            header_actions,
            text="Refresh",
            width=110,
            command=self._request_refresh,
        )
        self.refresh_button.grid(row=0, column=0, sticky="w")

        self.status_label = ctk.CTkLabel(
            header_actions,
            text="Carregando lista de dumps...",
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=0, column=1, padx=(12, 0), sticky="ew")

        self.tree_frame = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.tree_frame.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self.tree_frame.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(footer, text="Close", command=self.close).pack(side="right")

    def _request_refresh(self):
        if self._busy:
            return
        if self._on_refresh:
            self._on_refresh()

    def set_busy(self, busy: bool, status_text: str | None = None):
        self._busy = busy
        self.refresh_button.configure(state="disabled" if busy else "normal")
        if status_text is not None:
            self.set_status(status_text)
        self._render_tree()

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def set_dump_items(self, dump_items):
        self._dump_items = list(dump_items)
        if not self._expanded_keys:
            for item in self._dump_items:
                current_parts = []
                for part in item.get("tree_parts", [])[:-1]:
                    current_parts.append(part)
                    self._expanded_keys.add("/".join(current_parts))

        total = len(self._dump_items)
        self.set_status(f"{total} arquivo(s) encontrados.")
        self._render_tree()

    def _render_tree(self):
        for widget in self.tree_frame.winfo_children():
            widget.destroy()

        if not self._dump_items:
            ctk.CTkLabel(
                self.tree_frame,
                text="Nenhum dump encontrado.",
                justify="left",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        tree = self._build_tree()
        row_counter = {"value": 0}
        self._render_node(tree, [], depth=0, row_counter=row_counter)

    def _build_tree(self):
        root = {"children": {}, "files": []}
        for item in self._dump_items:
            node = root
            parts = item.get("tree_parts", [])
            for folder_part in parts[:-1]:
                node = node["children"].setdefault(folder_part, {"children": {}, "files": []})
            node["files"].append(item)
        return root

    def _render_node(self, node, path_parts, depth: int, row_counter: dict):
        for folder_name in sorted(node["children"]):
            current_parts = path_parts + [folder_name]
            folder_key = "/".join(current_parts)
            expanded = folder_key in self._expanded_keys

            row = ctk.CTkFrame(self.tree_frame, fg_color="transparent")
            row.grid(row=row_counter["value"], column=0, padx=8, pady=2, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            row_counter["value"] += 1

            indent = max(depth * 18, 0)
            ctk.CTkButton(
                row,
                text=f"{'[-]' if expanded else '[+]'} {folder_name}",
                anchor="w",
                fg_color="transparent",
                text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"],
                hover_color=ctk.ThemeManager.theme["CTkFrame"]["fg_color"],
                command=lambda key=folder_key: self._toggle_folder(key),
            ).grid(row=0, column=0, padx=(indent, 0), sticky="ew")

            if expanded:
                self._render_node(
                    node["children"][folder_name],
                    current_parts,
                    depth + 1,
                    row_counter,
                )

        for item in sorted(node["files"], key=lambda current: current["file_name"].lower()):
            row = ctk.CTkFrame(self.tree_frame, corner_radius=10)
            row.grid(row=row_counter["value"], column=0, padx=8, pady=4, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            row_counter["value"] += 1

            info_box = ctk.CTkFrame(row, fg_color="transparent")
            info_box.grid(row=0, column=0, padx=(depth * 18 + 16, 8), pady=8, sticky="ew")
            info_box.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                info_box,
                text=f"{item['file_name']} - {item.get('size_label', 'tamanho desconhecido')}",
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                info_box,
                text=item["full_path"],
                justify="left",
                anchor="w",
                text_color="#6b7280",
                wraplength=690,
            ).grid(row=1, column=0, sticky="ew")

            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.grid(row=0, column=1, padx=(0, 10), pady=10, sticky="e")

            ctk.CTkButton(
                actions,
                text="Restaurar",
                width=90,
                state=(
                    "disabled"
                    if self._busy or not item.get("can_restore")
                    else "normal"
                ),
                command=lambda current=item: self._request_restore(current),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                actions,
                text="Delete",
                width=82,
                fg_color="#dc2626",
                hover_color="#b91c1c",
                state="disabled" if self._busy else "normal",
                command=lambda current=item: self._request_delete(current),
            ).pack(side="left")

    def _toggle_folder(self, folder_key: str):
        if folder_key in self._expanded_keys:
            self._expanded_keys.remove(folder_key)
        else:
            self._expanded_keys.add(folder_key)
        self._render_tree()

    def _request_delete(self, dump_item: dict):
        if self._busy:
            return
        if self._on_delete:
            self._on_delete(dump_item)

    def _request_restore(self, dump_item: dict):
        if self._busy:
            return
        if self._on_restore:
            self._on_restore(dump_item)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class OperationProgressDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        heading: str,
        subject_text: str,
        stages,
        initial_stage_text: str,
        initial_message: str,
        cancel_button_text: str,
        on_cancel=None,
    ):
        super().__init__(master)

        self.title(title)
        self.geometry("660x470")
        self.resizable(False, False)
        self._closed = False
        self._cancel_requested = False
        self._stage_labels = {}
        self._stages = list(stages)
        self._stage_names = dict(self._stages)
        self._on_cancel = on_cancel

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.request_cancel)
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            self,
            text=heading,
            font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            self,
            text=subject_text,
            justify="left",
            wraplength=610
        ).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")

        self.current_stage_label = ctk.CTkLabel(
            self,
            text=f"Current stage: {initial_stage_text}",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.current_stage_label.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="w")

        progress_row = ctk.CTkFrame(self, fg_color="transparent")
        progress_row.grid(row=3, column=0, padx=20, pady=(0, 8), sticky="ew")
        progress_row.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(progress_row, height=18)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.progress_bar.set(0)

        self.percent_label = ctk.CTkLabel(progress_row, text="0%")
        self.percent_label.grid(row=0, column=1, sticky="e")

        self.status_label = ctk.CTkLabel(
            self,
            text=initial_message,
            justify="left",
            wraplength=610
        )
        self.status_label.grid(row=4, column=0, padx=20, pady=(0, 12), sticky="w")

        stages_box = ctk.CTkFrame(self, corner_radius=12)
        stages_box.grid(row=5, column=0, padx=20, pady=(0, 12), sticky="nsew")
        stages_box.grid_columnconfigure(0, weight=1)

        for row_index, (stage_key, stage_label) in enumerate(self._stages):
            label = ctk.CTkLabel(
                stages_box,
                text=f"[0%] {stage_label}",
                anchor="w",
                text_color="#6b7280"
            )
            label.grid(row=row_index, column=0, padx=16, pady=7, sticky="ew")
            self._stage_labels[stage_key] = label

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, padx=20, pady=(0, 20), sticky="e")

        self.cancel_button = ctk.CTkButton(
            buttons,
            text=cancel_button_text,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self.request_cancel
        )
        self.cancel_button.pack(side="right")

    def update_progress(self, stage_key: str, progress: float, message: str):
        if self._closed:
            return

        bounded_progress = max(0.0, min(progress, 100.0))
        self.progress_bar.set(bounded_progress / 100.0)
        self.percent_label.configure(text=f"{int(round(bounded_progress))}%")
        self.status_label.configure(text=message)
        self.current_stage_label.configure(text=f"Current stage: {self._stage_names.get(stage_key, stage_key)}")

        reached_current = True
        for current_key, current_label in self._stages:
            label = self._stage_labels[current_key]
            if current_key == stage_key:
                reached_current = False
                label.configure(
                    text=f"[{int(round(bounded_progress)):>3}%] {current_label}",
                    text_color="#d97706"
                )
            elif reached_current:
                label.configure(text=f"[100%] {current_label}", text_color="#15803d")
            else:
                label.configure(text=f"[0%] {current_label}", text_color="#6b7280")

    def mark_finalizing(self, message: str = "Finalizing committed operation..."):
        if self._closed:
            return

        self._cancel_requested = True
        self.cancel_button.configure(state="disabled", text="Finalizing...")
        self.status_label.configure(text=message)

    def mark_cancelling(self, message: str = "Cancellation request sent."):
        if self._closed:
            return

        self._cancel_requested = True
        self.cancel_button.configure(state="disabled", text="Cancelling...")
        self.status_label.configure(text=message)

    def mark_cancelled(self, message: str):
        if self._closed:
            return

        self.mark_cancelling(message)
        self.after(500, self.close)

    def mark_completed(self, message: str):
        if self._closed:
            return

        for _stage_key, current_label in self._stages:
            self._stage_labels[_stage_key].configure(text=f"[100%] {current_label}", text_color="#15803d")

        self.progress_bar.set(1)
        self.percent_label.configure(text="100%")
        self.current_stage_label.configure(text="Current stage: completed")
        self.status_label.configure(text=message)
        self.cancel_button.configure(state="disabled", text="Completed")
        self.after(700, self.close)

    def request_cancel(self):
        if self._closed or self._cancel_requested:
            return

        self.mark_cancelling()
        if self._on_cancel:
            self._on_cancel()

    def close(self):
        if self._closed:
            return

        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class RawImportProgressDialog(OperationProgressDialog):
    STAGES = [
        ("schema", "1. Preparar ambiente"),
        ("source", "2. Ler arquivo"),
        ("payload", "3. Montar carga"),
        ("database", "4. Importar no banco"),
        ("review", "5. Revisar header/noise"),
        ("version", "6. Registrar versao"),
        ("refresh", "7. Atualizar interface"),
    ]

    def __init__(self, master, file_name: str, table_name: str, on_cancel=None):
        super().__init__(
            master,
            title="Importacao Raw em andamento",
            heading="Importando Raw",
            subject_text=f"Arquivo: {file_name}\nTabela: {table_name}",
            stages=self.STAGES,
            initial_stage_text="preparando importacao",
            initial_message="Aguardando inicio da importacao...",
            cancel_button_text="Cancelar operacao",
            on_cancel=on_cancel,
        )


class RawExpandProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar expansao"),
        ("alter", "2. Preparar destino"),
        ("convert", "3. Converter valores"),
        ("version", "4. Registrar versao"),
        ("refresh", "5. Atualizar interface"),
    ]

    def __init__(
        self,
        master,
        table_name: str,
        raw_key: str,
        focus_text: str,
        focus_label: str = "Column",
        on_cancel=None,
    ):
        super().__init__(
            master,
            title="Expansao Raw em andamento",
            heading="Expandindo Raw",
            subject_text=f"Tabela: {table_name}\nChave: {raw_key}\n{focus_label}: {focus_text}",
            stages=self.STAGES,
            initial_stage_text="preparando expansao",
            initial_message="Aguardando inicio da expansao...",
            cancel_button_text="Cancelar expansao",
            on_cancel=on_cancel,
        )


class RawGroupProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar agrupamento"),
        ("group", "2. Agrupar e reescrever raw"),
        ("version", "3. Registrar versao"),
        ("refresh", "4. Atualizar interface"),
    ]

    def __init__(self, master, table_name: str, focus_text: str, focus_label: str = "Chave", on_cancel=None):
        super().__init__(
            master,
            title="Agrupamento Raw em andamento",
            heading="Agrupando Raw",
            subject_text=f"Tabela: {table_name}\n{focus_label}: {focus_text}",
            stages=self.STAGES,
            initial_stage_text="preparando agrupamento",
            initial_message="Aguardando inicio do agrupamento...",
            cancel_button_text="Cancelar agrupamento",
            on_cancel=on_cancel,
        )


class SqlExecutionProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar execucao"),
        ("execute", "2. Rodar SQL"),
        ("version", "3. Registrar versao"),
        ("refresh", "4. Atualizar interface"),
    ]

    def __init__(self, master, database_name: str, table_name: str, on_cancel=None):
        super().__init__(
            master,
            title="Execucao SQL em andamento",
            heading="Rodando SQL",
            subject_text=f"Base: {database_name}\nTabela vinculada: {table_name}",
            stages=self.STAGES,
            initial_stage_text="preparando execucao",
            initial_message="Aguardando inicio da execucao SQL...",
            cancel_button_text="Cancelar execucao",
            on_cancel=on_cancel,
        )


class SqlExpansionProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Prepare expansion"),
        ("preflight", "2. Validate source and destinations"),
        ("execute", "3. Insert into destinations"),
        ("version", "4. Register versions"),
        ("refresh", "5. Refresh interface"),
    ]

    def __init__(
        self,
        master,
        database_name: str,
        source_table_name: str,
        destination_count: int,
        on_cancel=None,
    ):
        super().__init__(
            master,
            title="Cross-table expansion in progress",
            heading="Expanding data",
            subject_text=(
                f"Database: {database_name}\n"
                f"Read-only source: {source_table_name}\n"
                f"Destinations: {destination_count}"
            ),
            stages=self.STAGES,
            initial_stage_text="preparing expansion",
            initial_message="Waiting for expansion to start...",
            cancel_button_text="Cancel expansion",
            on_cancel=on_cancel,
        )


class ExpansionTimingReportDialog(ctk.CTkToplevel):
    STAGE_LABELS = {
        "prepare": "1. Preparation",
        "preflight": "2. Preflight validation",
        "execute": "3. PostgreSQL expansion",
        "version": "4. Version registration",
        "refresh": "5. Interface refresh",
    }

    def __init__(self, master, report: dict):
        super().__init__(master)
        self._report = dict(report or {})
        self._report_text = self.build_report_text(self._report)

        self.title("Expansion performance report")
        self.geometry("920x720")
        self.minsize(760, 560)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(10, lambda: center_window(self))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self,
            text="Expansion performance report",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 6), sticky="w")

        measured_total = self.measured_total_seconds(self._report)
        source_rows = int(self._report.get("source_rows") or 0)
        inserted_rows = int(self._report.get("total_rows_inserted") or 0)
        ctk.CTkLabel(
            self,
            text=(
                f"Measured processing total: {self._format_duration(measured_total)}   |   "
                f"Source rows: {source_rows:,}   |   Inserted rows: {inserted_rows:,}"
            ),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#d97706",
        ).grid(row=1, column=0, padx=20, pady=(0, 4), sticky="w")

        ctk.CTkLabel(
            self,
            text=(
                "Local steps use per-thread CPU time. Database steps are timed on "
                "the Linux/PostgreSQL server, so SSH connection and return transit "
                "are not included."
            ),
            justify="left",
            wraplength=870,
            text_color="#64748b",
        ).grid(row=2, column=0, padx=20, pady=(0, 10), sticky="w")

        self.report_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Cascadia Code", size=12),
            wrap="none",
        )
        self.report_box.grid(
            row=3,
            column=0,
            padx=20,
            pady=(0, 12),
            sticky="nsew",
        )
        self.report_box.insert("1.0", self._report_text)
        self.report_box.configure(state="disabled")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(
            footer,
            text="Export TXT report",
            command=self._export_report,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            footer,
            text="Close",
            command=self.close,
        ).pack(side="left")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        value = max(float(seconds or 0.0), 0.0)
        if value < 0.001:
            return f"{value * 1_000_000:.0f} us"
        if value < 1:
            return f"{value * 1000:.3f} ms"
        if value < 60:
            return f"{value:.3f} s"
        minutes, remainder = divmod(value, 60)
        return f"{int(minutes)}m {remainder:06.3f}s"

    @classmethod
    def measured_total_seconds(cls, report: dict) -> float:
        return sum(
            max(float(item.get("seconds") or 0.0), 0.0)
            for item in (report.get("items") or [])
            if item.get("include_in_total", True)
        )

    @classmethod
    def build_report_text(cls, report: dict) -> str:
        lines = [
            "EXPANSION PERFORMANCE REPORT",
            "=" * 80,
            f"Started:       {report.get('started_at') or '-'}",
            f"Completed:     {report.get('completed_at') or '-'}",
            f"Database:      {report.get('database_name') or '-'}",
            f"Source:        {report.get('source_table_name') or '-'}",
            "Raw schemas:   " + ", ".join(report.get("raw_schemas") or ["-"]),
            f"Source rows:   {int(report.get('source_rows') or 0):,}",
            f"Inserted rows: {int(report.get('total_rows_inserted') or 0):,}",
            "",
            "MEASUREMENT MODEL",
            "- Local Python work: per-thread CPU time; blocking and SSH waits excluded.",
            "- Remote commands: clocked by the Linux server; SSH connection/return excluded.",
            "- Expansion SQL: clocked inside PostgreSQL with clock_timestamp().",
            "- Diagnostic envelope entries are shown but excluded from totals.",
        ]

        destination_rows = report.get("destination_rows") or []
        if destination_rows:
            lines.extend(["", "ROWS BY DESTINATION"])
            for item in destination_rows:
                lines.append(
                    f"- {item.get('table_name') or '-'}: "
                    f"{int(item.get('rows') or 0):,}"
                )

        items = list(report.get("items") or [])
        known_stages = list(cls.STAGE_LABELS)
        extra_stages = [
            stage
            for stage in dict.fromkeys(
                str(item.get("stage") or "other") for item in items
            )
            if stage not in known_stages
        ]
        for stage in known_stages + extra_stages:
            stage_items = [
                item
                for item in items
                if str(item.get("stage") or "other") == stage
            ]
            if not stage_items:
                continue
            stage_label = cls.STAGE_LABELS.get(stage, stage.replace("_", " ").title())
            lines.extend(["", stage_label.upper(), "-" * 80])
            stage_total = 0.0
            for index, item in enumerate(stage_items, start=1):
                duration = max(float(item.get("seconds") or 0.0), 0.0)
                included = item.get("include_in_total", True)
                if included:
                    stage_total += duration
                suffix_parts = [str(item.get("measurement") or "unknown")]
                if item.get("rows") is not None:
                    suffix_parts.append(f"rows={int(item['rows']):,}")
                if item.get("destination"):
                    suffix_parts.append(f"destination={item['destination']}")
                if not included:
                    suffix_parts.append("diagnostic only; excluded from totals")
                lines.append(
                    f"{index:>2}. {item.get('name') or 'Unnamed step'}"
                )
                lines.append(
                    f"    {cls._format_duration(duration):>14} | "
                    + " | ".join(suffix_parts)
                )
                if item.get("details"):
                    lines.append(f"    {item['details']}")
            lines.append(
                f"Stage subtotal: {cls._format_duration(stage_total)}"
            )

        lines.extend(
            [
                "",
                "=" * 80,
                "MEASURED PROCESSING TOTAL: "
                + cls._format_duration(cls.measured_total_seconds(report)),
                "=" * 80,
            ]
        )
        return "\n".join(lines) + "\n"

    def _export_report(self):
        timestamp = re.sub(
            r"[^0-9]",
            "",
            str(self._report.get("completed_at") or ""),
        )[:14]
        source_name = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            str(self._report.get("source_table_name") or "source"),
        ).strip("_") or "source"
        suggested_name = f"expansion_timing_{source_name}_{timestamp or 'report'}.txt"
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Export expansion performance report",
            defaultextension=".txt",
            initialfile=suggested_name,
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(self._report_text, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(
                "Export failed",
                f"Could not export the timing report.\n\n{exc}",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Report exported",
            f"The timing report was saved to:\n{target}",
            parent=self,
        )

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class SqlQueryProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar consulta"),
        ("execute", "2. Rodar consulta"),
        ("result", "3. Carregar resultado"),
    ]

    def __init__(self, master, database_name: str, table_name: str, on_cancel=None):
        super().__init__(
            master,
            title="Consulta SQL em andamento",
            heading="Rodando consulta SQL",
            subject_text=f"Base: {database_name}\nTabela vinculada: {table_name}",
            stages=self.STAGES,
            initial_stage_text="preparando consulta",
            initial_message="Aguardando inicio da consulta SQL...",
            cancel_button_text="Cancelar consulta",
            on_cancel=on_cancel,
        )


class ColumnDeletionProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar exclusao"),
        ("alter", "2. Excluir coluna"),
        ("version", "3. Registrar versao"),
        ("refresh", "4. Atualizar interface"),
    ]

    def __init__(self, master, table_name: str, column_name: str, on_cancel=None):
        super().__init__(
            master,
            title="Exclusao de coluna em andamento",
            heading="Excluindo coluna",
            subject_text=f"Tabela: {table_name}\nColuna: {column_name}",
            stages=self.STAGES,
            initial_stage_text="preparando exclusao",
            initial_message="Aguardando inicio da exclusao...",
            cancel_button_text="Cancelar exclusao",
            on_cancel=on_cancel,
        )


class TableConstraintProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar alteracao"),
        ("execute", "2. Criar constraint"),
        ("version", "3. Registrar versao"),
        ("refresh", "4. Atualizar interface"),
    ]

    def __init__(
        self,
        master,
        table_name: str,
        column_name: str,
        constraint_label: str,
        reference_text: str | None = None,
        on_cancel=None,
    ):
        subject_lines = [
            f"Tabela: {table_name}",
            f"Coluna: {column_name}",
            f"Constraint: {constraint_label}",
        ]
        if reference_text:
            subject_lines.append(f"Referencia: {reference_text}")

        super().__init__(
            master,
            title="Criacao de constraint em andamento",
            heading="Criando constraint",
            subject_text="\n".join(subject_lines),
            stages=self.STAGES,
            initial_stage_text="preparando alteracao",
            initial_message="Aguardando inicio da alteracao estrutural...",
            cancel_button_text="Cancelar alteracao",
            on_cancel=on_cancel,
        )


class TableDeletionProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar exclusao"),
        ("dump", "2. Preservar historico"),
        ("drop", "3. Excluir tabela"),
        ("audit", "4. Registrar auditoria"),
        ("refresh", "5. Atualizar interface"),
    ]

    def __init__(self, master, database_name: str, table_name: str, on_cancel=None):
        super().__init__(
            master,
            title="Exclusao de tabela em andamento",
            heading="Excluindo tabela",
            subject_text=f"Base: {database_name}\nTabela: {table_name}",
            stages=self.STAGES,
            initial_stage_text="preparando exclusao",
            initial_message="Aguardando inicio da exclusao...",
            cancel_button_text="Cancelar exclusao",
            on_cancel=on_cancel,
        )


class RestoreVersionProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar restauracao"),
        ("drop", "2. Remover estado atual"),
        ("replay", "3. Reaplicar versoes"),
        ("version", "4. Registrar nova versao"),
        ("refresh", "5. Atualizar interface"),
    ]

    def __init__(self, master, table_name: str, version_code: str, on_cancel=None):
        super().__init__(
            master,
            title="Restauracao de versao em andamento",
            heading="Restaurando versao",
            subject_text=f"Tabela: {table_name}\nVersao alvo: {version_code}",
            stages=self.STAGES,
            initial_stage_text="preparando restauracao",
            initial_message="Aguardando inicio da restauracao...",
            cancel_button_text="Cancelar restauracao",
            on_cancel=on_cancel,
        )


class RestoreVersionToNewTableProgressDialog(OperationProgressDialog):
    STAGES = [
        ("prepare", "1. Preparar restauracao"),
        ("drop", "2. Preparar tabela destino"),
        ("replay", "3. Reaplicar versoes"),
        ("version", "4. Registrar nova versao"),
        ("refresh", "5. Atualizar interface"),
    ]

    def __init__(self, master, table_name: str, version_code: str, on_cancel=None):
        super().__init__(
            master,
            title="Restauracao em nova tabela em andamento",
            heading="Restaurando em nova tabela",
            subject_text=f"Tabela destino: {table_name}\nVersao alvo: {version_code}",
            stages=self.STAGES,
            initial_stage_text="preparando restauracao",
            initial_message="Aguardando inicio da restauracao...",
            cancel_button_text="Cancelar restauracao",
            on_cancel=on_cancel,
        )
