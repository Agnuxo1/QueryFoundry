import tkinter as tk
import tkinter.font as tkfont
import textwrap
import time
from datetime import datetime
from tkinter import ttk

import customtkinter as ctk


class HoverPreview:
    def __init__(self, widget):
        self.widget = widget
        self.tipwindow = None
        self.content_label = None
        self.title_label = None
        self.current_text = None
        self.current_title = None

    def _build_wrapped_text(self, text: str):
        font = tkfont.Font(family="Consolas", size=10)
        screen_width = self.widget.winfo_screenwidth()
        screen_height = self.widget.winfo_screenheight()
        max_width = min(int(screen_width * 0.55), 820)
        max_height = int(screen_height * 0.65)
        min_width = 180
        avg_char_width = max(font.measure("0"), 7)
        max_chars = max(24, (max_width - 32) // avg_char_width)

        wrapped_lines = []
        for line in str(text).splitlines() or [""]:
            wrapped_lines.append(
                textwrap.fill(
                    line or " ",
                    width=max_chars,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
            )

        wrapped_text = "\n".join(wrapped_lines)
        line_widths = [font.measure(line) for line in wrapped_text.splitlines() or [""]]
        preferred_width = max(line_widths + [min_width - 24]) + 24
        return wrapped_text, max(min_width, min(preferred_width, max_width)), max_height

    def _format_geometry(self, width: int, height: int, x_root: int, y_root: int) -> str:
        screen_width = self.widget.winfo_screenwidth()
        screen_height = self.widget.winfo_screenheight()
        x = min(x_root + 16, max(screen_width - width - 24, 0))
        y = min(y_root + 16, max(screen_height - height - 48, 0))
        return f"{width}x{height}+{x}+{y}"

    def show(self, text: str, x_root: int, y_root: int, title: str | None = None):
        if not text:
            self.hide()
            return

        wrapped_text, preferred_width, max_height = self._build_wrapped_text(text)

        if self.tipwindow and self.current_text == text and self.current_title == title:
            self.tipwindow.update_idletasks()
            height = min(self.tipwindow.winfo_reqheight(), max_height)
            self.tipwindow.geometry(self._format_geometry(preferred_width, height, x_root, y_root))
            return

        self.hide()

        self.tipwindow = tk.Toplevel(self.widget)
        self.tipwindow.wm_overrideredirect(True)
        self.tipwindow.attributes("-topmost", True)
        self.tipwindow.configure(background="#111827", padx=1, pady=1)

        container = tk.Frame(
            self.tipwindow,
            background="#111827",
            borderwidth=0,
        )
        container.pack(fill="both", expand=True)

        if title:
            self.title_label = tk.Label(
                container,
                text=title,
                justify="left",
                anchor="w",
                background="#1f2937",
                foreground="white",
                padx=10,
                pady=6,
                font=("Segoe UI", 9, "bold"),
            )
            self.title_label.pack(fill="x")

        self.content_label = tk.Label(
            container,
            text=wrapped_text,
            justify="left",
            anchor="w",
            background="#111827",
            foreground="white",
            padx=10,
            pady=8,
            font=("Consolas", 10),
            wraplength=max(preferred_width - 20, 120),
        )
        self.content_label.pack(fill="both", expand=True)

        self.tipwindow.update_idletasks()
        height = min(self.tipwindow.winfo_reqheight(), max_height)
        self.tipwindow.wm_geometry(self._format_geometry(preferred_width, height, x_root, y_root))

        self.current_text = text
        self.current_title = title

    def hide(self):
        if self.tipwindow is not None:
            self.tipwindow.destroy()
            self.tipwindow = None
            self.content_label = None
            self.title_label = None
            self.current_text = None
            self.current_title = None


class ReadOnlyTable(ctk.CTkFrame):
    OVERSCROLL_COLUMN = "__pgdm_table_overscroll__"
    CELL_PREVIEW_CHAR_LIMIT = 180

    @staticmethod
    def _trace_table_open(trace_context: dict | None, message: str):
        if not trace_context:
            return

        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        request_id = trace_context.get("request_id", "?")
        table_name = trace_context.get("table_name", "?")
        elapsed_ms = (time.perf_counter() - trace_context["started_at"]) * 1000
        print(
            f"[table-open #{request_id} {stamp} +{elapsed_ms:8.1f} ms {table_name}] {message}",
            flush=True,
        )

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=12)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header_tooltips = {}
        self.active_filters = {}
        self.attention_columns = set()
        self.column_constraints = {}
        self.on_filter = None
        self.tooltip = HoverPreview(self)
        self._table_font = tkfont.nametofont("TkDefaultFont")
        self._heading_font = tkfont.nametofont("TkHeadingFont")
        self._header_marker_cache = {}
        self.manual_column_widths = {}
        self._visible_columns = []
        self._full_row_values = {}
        self._pending_freeze_columns = None
        self._ignore_header_click_until = 0

        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        self.table_container = ctk.CTkFrame(self, fg_color="transparent")
        self.table_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.table_container.grid_rowconfigure(0, weight=1)
        self.table_container.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(self.table_container, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.v_scroll = ttk.Scrollbar(
            self.table_container, orient="vertical", command=self.tree.yview
        )
        self.v_scroll.grid(row=0, column=1, sticky="ns")

        self.h_scroll = ttk.Scrollbar(
            self.table_container, orient="horizontal", command=self.tree.xview
        )
        self.h_scroll.grid(row=1, column=0, sticky="ew")

        self.tree.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )

        self.tree.tag_configure("odd", background="#f7f9fc")
        self.tree.tag_configure("even", background="#ffffff")

        self.tree.bind("<Motion>", self._on_mouse_move)
        self.tree.bind("<Leave>", lambda _e: self.tooltip.hide())
        self.tree.bind("<ButtonRelease-1>", self._handle_left_button_release)
        self.tree.bind("<Double-Button-1>", self._auto_fit_column_on_double_click)

    def set_title(self, title: str):
        self.title_label.configure(text=title)

    @staticmethod
    def _darken_hex_color(color: str, factor: float = 0.7) -> str:
        normalized = str(color or "").strip()
        if not normalized.startswith("#") or len(normalized) != 7:
            return normalized or "#111827"
        red = int(normalized[1:3], 16)
        green = int(normalized[3:5], 16)
        blue = int(normalized[5:7], 16)
        return "#{:02x}{:02x}{:02x}".format(
            max(0, min(255, int(red * factor))),
            max(0, min(255, int(green * factor))),
            max(0, min(255, int(blue * factor))),
        )

    def _build_header_marker_image(self, colors):
        marker_key = tuple(colors or [])
        cached_image = self._header_marker_cache.get(marker_key)
        if cached_image is not None:
            return cached_image
        if not marker_key:
            self._header_marker_cache[marker_key] = ""
            return ""

        image_width = max(10, len(marker_key) * 9 + 2)
        image = tk.PhotoImage(width=image_width, height=10)
        for index, color in enumerate(marker_key):
            start_x = 1 + index * 9
            fill_color = color or "#1f2937"
            outline_color = self._darken_hex_color(fill_color, factor=0.6)
            fill_pixels = {
                (start_x + 4, 1),
                (start_x + 3, 2), (start_x + 4, 2), (start_x + 5, 2),
                (start_x + 2, 3), (start_x + 3, 3), (start_x + 4, 3), (start_x + 5, 3), (start_x + 6, 3),
                (start_x + 2, 4), (start_x + 3, 4), (start_x + 4, 4), (start_x + 5, 4), (start_x + 6, 4),
                (start_x + 2, 5), (start_x + 3, 5), (start_x + 4, 5), (start_x + 5, 5), (start_x + 6, 5),
                (start_x + 3, 6), (start_x + 4, 6), (start_x + 5, 6),
                (start_x + 4, 7),
            }
            outline_pixels = {
                (start_x + 4, 0),
                (start_x + 2, 2), (start_x + 6, 2),
                (start_x + 1, 4), (start_x + 7, 4),
                (start_x + 2, 6), (start_x + 6, 6),
                (start_x + 4, 8),
            }
            for x_pos, y_pos in fill_pixels:
                image.put(fill_color, (x_pos, y_pos))
            for x_pos, y_pos in outline_pixels:
                image.put(outline_color, (x_pos, y_pos))
        self._header_marker_cache[marker_key] = image
        return image

    def _resolve_header_marker_colors(self, column_name: str) -> tuple:
        colors = []
        constraint_info = dict(self.column_constraints.get(column_name) or {})
        if constraint_info.get("is_primary_key"):
            colors.append("#16a34a")
        if constraint_info.get("is_foreign_key"):
            colors.append("#facc15")
        if column_name in self.attention_columns:
            colors.append("#dc2626")
        return tuple(colors)

    def set_data(
        self,
        headers,
        rows,
        header_tooltips=None,
        active_filters=None,
        on_filter=None,
        attention_columns=None,
        column_constraints=None,
        trace_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        self._trace_table_open(
            trace_context,
            f"ReadOnlyTable.set_data() iniciou; columns={len(headers or [])}; rows={len(rows or [])}",
        )
        clear_started_at = time.perf_counter()
        self.tree.delete(*self.tree.get_children())
        self._remember_column_widths()
        self._full_row_values = {}
        self.header_tooltips = header_tooltips or {}
        self.active_filters = active_filters or {}
        self.attention_columns = set(attention_columns or [])
        self.column_constraints = column_constraints or {}
        self.on_filter = on_filter
        self._trace_table_open(
            trace_context,
            f"ReadOnlyTable.set_data(): limpeza inicial em {(time.perf_counter() - clear_started_at) * 1000:.1f} ms",
        )

        if not headers:
            self._visible_columns = []
            self._pending_freeze_columns = None
            self.tree["displaycolumns"] = ()
            self.tree["columns"] = ["info"]
            self.tree["displaycolumns"] = ["info"]
            self.tree.heading("info", text="Informacao")
            self.tree.heading("info", anchor="center")
            self.tree.column("info", width=320, anchor="center", stretch=True)
            self.tree.insert("", "end", values=("Nenhum dado disponivel.",))
            self._trace_table_open(
                trace_context,
                f"ReadOnlyTable.set_data() finalizou sem headers em {(time.perf_counter() - started_at) * 1000:.1f} ms",
            )
            return

        self._visible_columns = list(headers)
        tree_columns = list(headers)
        tree_columns.append(self.OVERSCROLL_COLUMN)
        column_anchors = self._build_column_anchors(headers, rows)

        header_setup_started_at = time.perf_counter()
        self.tree["displaycolumns"] = ()
        self.tree["columns"] = tree_columns
        self.tree["displaycolumns"] = tree_columns

        for col in headers:
            header_image = self._build_header_marker_image(self._resolve_header_marker_colors(col))
            heading_kwargs = {
                "text": self._format_heading_text(col),
                "anchor": "center",
                "command": lambda current=col: self._request_filter(current),
                "image": header_image,
            }
            self.tree.heading(col, **heading_kwargs)
            width = self.manual_column_widths.get(col)
            if width is None:
                role_labels = []
                constraint_info = dict(self.column_constraints.get(col) or {})
                if constraint_info.get("is_primary_key"):
                    role_labels.append("PK")
                if constraint_info.get("is_foreign_key"):
                    role_labels.append("FK")
                extra_padding = 16 if header_image else 0
                if role_labels:
                    extra_padding += 12 + len("|".join(role_labels)) * 9
                width = max(120, min(320, len(str(col)) * 10 + 30 + extra_padding))
            self.tree.column(
                col,
                width=width,
                minwidth=80,
                anchor=column_anchors.get(col, "center"),
                stretch=False,
            )

        if headers:
            self.tree.heading(self.OVERSCROLL_COLUMN, text="", anchor="center")
            self.tree.column(
                self.OVERSCROLL_COLUMN,
                width=180,
                minwidth=180,
                anchor="center",
                stretch=False,
            )
        self._trace_table_open(
            trace_context,
            f"ReadOnlyTable.set_data(): configuração de colunas em {(time.perf_counter() - header_setup_started_at) * 1000:.1f} ms",
        )


        if not rows:
            empty_row = [""] * len(tree_columns)
            empty_row[0] = "(sem registros)"
            self.tree.insert("", "end", values=empty_row, tags=("even",))
            fit_started_at = time.perf_counter()
            self._fit_then_freeze_if_needed(headers)
            self._trace_table_open(
                trace_context,
                (
                    "ReadOnlyTable.set_data(): grid sem registros finalizada em "
                    f"{(time.perf_counter() - fit_started_at) * 1000:.1f} ms; "
                    f"total={(time.perf_counter() - started_at) * 1000:.1f} ms"
                ),
            )
            return

        rows_insert_started_at = time.perf_counter()
        for idx, row in enumerate(rows):
            full_row = ["" if value is None else str(value) for value in row]
            display_row = [self._format_preview_cell_value(value) for value in full_row]
            display_row.append("")
            tag = "even" if idx % 2 == 0 else "odd"
            item_id = self.tree.insert("", "end", values=display_row, tags=(tag,))
            self._full_row_values[item_id] = full_row
        self._trace_table_open(
            trace_context,
            f"ReadOnlyTable.set_data(): inserção das linhas em {(time.perf_counter() - rows_insert_started_at) * 1000:.1f} ms",
        )

        fit_started_at = time.perf_counter()
        self._fit_then_freeze_if_needed(headers)
        self._trace_table_open(
            trace_context,
            (
                f"ReadOnlyTable.set_data(): ajuste final em {(time.perf_counter() - fit_started_at) * 1000:.1f} ms; "
                f"total={(time.perf_counter() - started_at) * 1000:.1f} ms"
            ),
        )

    def _fit_then_freeze_if_needed(self, headers):
        if any(column_name in self.manual_column_widths for column_name in headers):
            self._pending_freeze_columns = None
            return

        self.update_idletasks()
        available_width = max(self.tree.winfo_width() - 24, 0)
        data_width = sum(int(self.tree.column(column_name, "width")) for column_name in headers)
        if available_width <= 0 or data_width >= available_width:
            self._pending_freeze_columns = None
            return

        self._pending_freeze_columns = list(headers)
        self.tree.column(self.OVERSCROLL_COLUMN, width=0, minwidth=0, stretch=False)
        for column_name in headers:
            self.tree.column(column_name, stretch=True)

        self.after_idle(self._freeze_fitted_columns)

    def _freeze_fitted_columns(self):
        columns = self._pending_freeze_columns
        self._pending_freeze_columns = None
        if not columns:
            return

        self.update_idletasks()
        for column_name in columns:
            try:
                width = int(self.tree.column(column_name, "width"))
            except (tk.TclError, ValueError):
                continue

            if width > 0:
                self.manual_column_widths[column_name] = width
                self.tree.column(column_name, width=width, stretch=False)

        self.tree.column(self.OVERSCROLL_COLUMN, width=180, minwidth=180, stretch=False)

    def _remember_column_widths(self, _event=None):
        for column_name in self._visible_columns:
            try:
                width = int(self.tree.column(column_name, "width"))
            except (tk.TclError, ValueError):
                continue

            if width > 0:
                self.manual_column_widths[column_name] = width

    def _handle_left_button_release(self, event):
        self._remember_column_widths(event)

        if not self.on_filter:
            return

        if self.tree.identify_region(event.x, event.y) != "cell":
            return

        column_name = self._resolve_column_name(event.x)
        row_id = self.tree.identify_row(event.y)
        if not column_name or not row_id:
            return

        if column_name not in self.attention_columns:
            return

        self.tooltip.hide()
        self.after_idle(lambda current=column_name: self.on_filter(current))

    def _build_column_anchors(self, headers, rows):
        anchors = {}
        for column_index, column_name in enumerate(headers):
            anchors[column_name] = "center"
            for row in rows or []:
                if column_index >= len(row):
                    continue
                value = row[column_index]
                if value is not None and len(str(value)) > 25:
                    anchors[column_name] = "w"
                    break
        return anchors

    @classmethod
    def _format_preview_cell_value(cls, value) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""

        single_line = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        if len(single_line) <= cls.CELL_PREVIEW_CHAR_LIMIT:
            return single_line
        return single_line[: cls.CELL_PREVIEW_CHAR_LIMIT - 3].rstrip() + "..."

    def _auto_fit_column_on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "separator":
            return

        column_name = self._resolve_column_name(max(event.x - 2, 0))
        if not column_name:
            column_name = self._resolve_column_name(event.x)
        if not column_name:
            return

        width = self._measure_column_width(column_name)
        self.manual_column_widths[column_name] = width
        self.tree.column(column_name, width=width, stretch=False)
        self._ignore_header_click_until = self.tree.tk.call("clock", "milliseconds") + 350

    def _measure_column_width(self, column_name: str) -> int:
        heading_text = self.tree.heading(column_name, "text") or column_name
        max_width = self._heading_font.measure(str(heading_text)) + 32
        if self._resolve_header_marker_colors(column_name):
            max_width += 16

        try:
            column_index = list(self.tree["columns"]).index(column_name)
        except ValueError:
            return max(80, min(max_width, 640))

        for item_id in self.tree.get_children():
            values = self.tree.item(item_id, "values")
            if column_index >= len(values):
                continue

            value_width = self._table_font.measure(str(values[column_index])) + 28
            max_width = max(max_width, value_width)

        return max(80, min(max_width, 640))

    def _on_mouse_move(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            self._show_header_tooltip(event)
            return

        if region == "cell":
            self._show_cell_tooltip(event)
            return

        self.tooltip.hide()

    def _show_header_tooltip(self, event):
        column_name = self._resolve_column_name(event.x)
        if not column_name:
            self.tooltip.hide()
            return

        tip_text = self.header_tooltips.get(column_name)
        if column_name in self.attention_columns:
            attention_text = (
                "Coluna sem vinculo no data dictionary. "
                "Clique no cabecalho ou em qualquer celula da coluna para abrir as acoes; "
                "a primeira opcao permite vincular ao dicionario."
            )
            tip_text = f"{tip_text}\n{attention_text}" if tip_text else attention_text
        elif self.on_filter:
            filter_text = "Clique para abrir acoes da coluna."
            if column_name in self.active_filters:
                filter_text = "Filtro ativo. Clique para abrir acoes da coluna."
            tip_text = f"{tip_text}\n{filter_text}" if tip_text else filter_text

        if tip_text:
            self.tooltip.show(tip_text, event.x_root, event.y_root, title=f"Tipo da coluna: {column_name}")
        else:
            self.tooltip.hide()

    def _show_cell_tooltip(self, event):
        row_id = self.tree.identify_row(event.y)
        column_name = self._resolve_column_name(event.x)
        if not row_id or not column_name:
            self.tooltip.hide()
            return

        try:
            values = self._full_row_values.get(row_id) or self.tree.item(row_id, "values")
            column_index = list(self.tree["columns"]).index(column_name)
        except ValueError:
            self.tooltip.hide()
            return

        if column_index < 0 or column_index >= len(values):
            self.tooltip.hide()
            return

        cell_value = values[column_index]
        display_text = cell_value if str(cell_value).strip() else "(vazio)"
        self.tooltip.show(
            str(display_text),
            event.x_root,
            event.y_root,
            title=f"Conteudo completo: {column_name}",
        )

    def _resolve_column_name(self, x_pos: int):
        column_id = self.tree.identify_column(x_pos)
        if not column_id:
            self.tooltip.hide()
            return None

        try:
            column_index = int(column_id.replace("#", "")) - 1
        except ValueError:
            self.tooltip.hide()
            return None

        columns = list(self.tree["columns"])
        if column_index < 0 or column_index >= len(columns):
            self.tooltip.hide()
            return None

        column_name = columns[column_index]
        if column_name == self.OVERSCROLL_COLUMN:
            self.tooltip.hide()
            return None

        return column_name

    def _format_heading_text(self, column_name: str) -> str:
        status_labels = []
        constraint_info = dict(self.column_constraints.get(column_name) or {})
        if constraint_info.get("is_primary_key"):
            status_labels.append("PK")
        if constraint_info.get("is_foreign_key"):
            status_labels.append("FK")
        suffix = f" [{'|'.join(status_labels)}]" if status_labels else ""
        marker = (
            "●▼"
            if column_name in self.active_filters
            else "▼"
        )
        return f"{column_name}{suffix}  {marker}"

    def _request_filter(self, column_name: str):
        now = int(self.tree.tk.call("clock", "milliseconds"))
        if now < self._ignore_header_click_until:
            return

        if self.on_filter:
            self.on_filter(column_name)
