"""
Mapa dos módulos:
- config.py
    Constantes globais (host, usuário, base de controle, paths remotos).
- utils.py
    Helpers puros de SQL, versionamento e parsing de nomes.
- ui/widgets.py
    Componentes visuais reutilizáveis.
- ui/dialogs.py
    Diálogos de confirmação e coleta de justificativa/senha.
- services/postgres_service.py
    Toda a lógica de SSH, psql, pg_dump e auditoria/versionamento.
"""

import os
import json
import socket
import threading
import time
import traceback
from datetime import datetime
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from config import (
    APP_GEOMETRY,
    APP_TITLE,
    CONTROL_DB,
    CONTROL_SCHEMA,
    DATA_DICTIONARY_TABLE,
    DATA_DICTIONARY_USAGE_TABLE,
    LEGACY_CONTROL_SCHEMA,
)
from filter.facet_service import FilterFacetService
from services.pdf_export import build_data_dictionary_pdf
from services.postgres_service import OperationCancelledError, PostgresAdminService
from ui.dialogs import (
    AdminActionDialog,
    ColumnDeletionProgressDialog,
    ColumnActionDialog,
    ColumnFilterDialog,
    ColumnValidationDialog,
    ConnectionDialog,
    CreateColumnsDialog,
    CreateDatabaseUserDialog,
    CreateTableDialog,
    DataDictionaryEntriesDialog,
    DataDictionaryUsageEntriesDialog,
    DatabaseOperationsDialog,
    EditDataDictionaryEntryDialog,
    EditDataDictionaryUsageEntryDialog,
    DangerConfirmDialog,
    DumpArtifactsDialog,
    DumpRestoreDialog,
    ExcelSheetSelectionDialog,
    ExpansionTimingReportDialog,
    ForeignKeyReferenceDialog,
    JsonStructureDialog,
    RawExpandColumnDialog,
    RawExpandChildTableDialog,
    RawGroupConfigDialog,
    RawGroupColumnsConfigDialog,
    RawExpandKeysDialog,
    RawExpandMultiValueModeDialog,
    RawExpandProgressDialog,
    RawGroupProgressDialog,
    RawImportReviewDialog,
    RawImportProgressDialog,
    RawSourceSelectionDialog,
    RenameDatabaseUserDialog,
    RestoreVersionProgressDialog,
    RestoreVersionToNewTableProgressDialog,
    RolePermissionsDialog,
    RootPasswordDialog,
    RawExpandTypeMismatchDialog,
    SqlExecutionDialog,
    SqlExpansionDialog,
    SqlQueryResultDialog,
    SqlExpressionDialog,
    SqlExecutionProgressDialog,
    SqlExpansionProgressDialog,
    SqlQueryProgressDialog,
    TableDeletionProgressDialog,
    TableConstraintProgressDialog,
    TableSelectionDialog,
    LinkDataDictionaryDialog,
    UserSelectionDialog,
    VersionHistoryDialog,
    VersionInfoDialog,
    center_window,
)
from utils import (
    clear_saved_connection_credentials,
    load_saved_connection_credentials,
    save_connection_credentials,
    split_table_name,
)
from ui.widgets import ReadOnlyTable
from ui.theme import apply_orange_theme


class App(ctk.CTk):
    LEGACY_CONTROL_SCHEMA = LEGACY_CONTROL_SCHEMA
    DATA_DICTIONARY_TABLE = DATA_DICTIONARY_TABLE
    DATA_DICTIONARY_USAGE_TABLE = DATA_DICTIONARY_USAGE_TABLE

    @staticmethod
    def _build_table_open_trace_context(table_name: str) -> dict:
        return {
            "request_id": int(time.time() * 1000),
            "table_name": table_name,
            "started_at": time.perf_counter(),
        }

    def _trace_table_open(self, trace_context: dict | None, message: str):
        if not trace_context:
            return

        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        elapsed_ms = (time.perf_counter() - trace_context["started_at"]) * 1000
        request_id = trace_context.get("request_id", "?")
        table_name = trace_context.get("table_name", "?")
        print(
            f"[table-open #{request_id} {stamp} +{elapsed_ms:8.1f} ms {table_name}] {message}",
            flush=True,
        )

    def __init__(self):
        apply_orange_theme()
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(APP_GEOMETRY)

        self._setup_ttk_style()

        # Serviço centralizado: comunicação remota e SQL passa por ele.
        self.service = PostgresAdminService()
        self.facet_service = FilterFacetService(self.service)

        # Estado de navegação da UI.
        self.current_db = None
        self.selected_table_name = None
        self.selected_version_record = None
        self._version_button_map = []
        self.can_create_db = False
        self.raw_progress_dialog = None
        self.raw_import_cancel_event = None
        self.raw_import_finalizing = False
        self.raw_expand_progress_dialog = None
        self.raw_expand_cancel_event = None
        self.raw_group_progress_dialog = None
        self.raw_group_cancel_event = None
        self.constraint_progress_dialog = None
        self.constraint_change_cancel_event = None
        self.column_delete_progress_dialog = None
        self.column_delete_cancel_event = None
        self.table_delete_progress_dialog = None
        self.table_delete_cancel_event = None
        self.restore_version_progress_dialog = None
        self.restore_version_cancel_event = None
        self.sql_execution_progress_dialog = None
        self.sql_execution_cancel_event = None
        self.cross_table_expansion_progress_dialog = None
        self.expansion_timing_report_dialog = None
        self.cross_table_expansion_cancel_event = None
        self.cross_table_expansion_finalizing = False
        self.dump_artifacts_dialog = None
        self.data_dictionary_entries_dialog = None
        self.data_dictionary_usage_entries_dialog = None
        self.user_selection_dialog = None
        self.database_operations_dialog = None
        self.table_preview_filters = {}
        self.table_numeric_columns = set()
        self.table_column_types = {}
        self.table_column_constraints = {}
        self.table_dictionary_unlinked_columns = set()
        self._last_data_dictionary_pdf_version = 0
        self._table_action_buttons_busy = False

        self._build_layout()
        self.clear_table_selection(initial=True)
        self.after(10, lambda: center_window(self))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _restore_version_to_new_table_thread(
        self,
        database_name: str,
        version,
        target_full_table_name: str,
        new_title: str,
        author: str,
    ):
        try:
            cancel_event = self.restore_version_cancel_event
            target_schema_name, target_table_name = split_table_name(target_full_table_name)
            self.ui(
                self.set_status,
                f"Restaurando versao {version['version_code']} em {target_full_table_name}...",
                "loading",
            )
            self.ui(self.update_restore_version_progress, "prepare", 5, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.service.restore_table_version_to_new_table(
                database_name,
                version,
                target_full_table_name,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_restore_version_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            restore_title = new_title or f"Restauracao de {version['version_code']} em nova tabela"
            restore_recipe = self.service.build_restore_recipe(version["version_code"])
            restore_notes = (
                f"Tabela criada por restauracao da versao {version['version_code']} "
                f"de {version['schema_name']}.{version['table_name']}."
            )
            self.ui(
                self.update_restore_version_progress,
                "version",
                10,
                "Preparando registro da nova versao...",
            )
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=target_schema_name,
                table_name=target_table_name,
                version_title=restore_title,
                requested_by=author,
                workstation_name=socket.gethostname(),
                sql_recipe=restore_recipe,
                restored_from_version=version["version_code"],
                version_notes=restore_notes,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_restore_version_progress,
                    stage_key,
                    progress,
                    message,
                ),
                cancel_event=cancel_event,
            )

            self.ui(
                self.update_restore_version_progress,
                "refresh",
                5,
                "Marcando cache local de filtros como stale...",
            )
            self._mark_table_filter_cache_stale_safe(database_name, target_full_table_name)
            self.ui(
                self.update_restore_version_progress,
                "refresh",
                35,
                "Carregando nova tabela restaurada...",
            )

            tables = None
            details = None
            versions = None
            if self.current_db == database_name:
                tables = self.service.list_tables(database_name)
                details = self.service.get_table_details(database_name, target_full_table_name)
                self.ui(
                    self.update_restore_version_progress,
                    "refresh",
                    70,
                    "Carregando historico de versoes...",
                )
                versions = self.service.list_table_versions(database_name, target_full_table_name)
                if versions:
                    versions[0].update(
                        self.service.get_table_version_detail(
                            database_name,
                            versions[0]["schema_name"],
                            versions[0]["table_name"],
                            versions[0]["version_code"],
                        )
                    )

            def apply_restore():
                if self.current_db != database_name:
                    return
                if tables is not None:
                    self.populate_table_buttons(tables)
                if details is not None and versions is not None:
                    self.table_preview_filters = {}
                    self._apply_table_details(target_full_table_name, details, versions)

            self.ui(apply_restore)
            self.ui(self.update_restore_version_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_restore_version_progress, "Restauracao concluida com sucesso.")
            self.ui(
                self.set_status,
                f"Versao restaurada em nova tabela: {target_full_table_name} ({next_version})",
                "success",
            )
        except OperationCancelledError:
            self.ui(self.mark_restore_version_progress_cancelled, "Restauracao cancelada pelo usuario.")
            self.ui(self.set_status, "Restauracao em nova tabela cancelada", "error")
        except Exception as e:
            diagnostic = traceback.format_exc()

            print(
                "\n"
                "============================================================\n"
                "ERRO COMPLETO DURANTE RESTAURAÇÃO\n"
                "============================================================\n"
                f"{diagnostic}"
                "============================================================\n",
                flush=True,
            )

            self.ui(self.close_restore_version_progress)
            self.ui(self.set_status, "Erro ao restaurar versão", "error")
            self.ui(
                self.show_error,
                "Erro ao restaurar versão",
                (
                    f"{type(e).__name__}: {e}\n\n"
                    "O traceback completo foi enviado ao terminal do PyCharm."
                ),
            )
        finally:
            self.clear_restore_version_cancellation_scope()

    # ------------------------------------------------------------------
    # Infra e utilitários de UI
    # ------------------------------------------------------------------

    def _setup_ttk_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Treeview",
            background="#ffffff",
            foreground="#1f2937",
            fieldbackground="#ffffff",
            bordercolor="#d5dbe3",
            borderwidth=1,
            rowheight=30,
            relief="solid"
        )

        style.map(
            "Treeview",
            background=[("selected", "#ffedd5")],
            foreground=[("selected", "#111827")]
        )

        style.configure(
            "Treeview.Heading",
            background="#f97316",
            foreground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10, "bold"),
            padding=(8, 8)
        )

        style.map(
            "Treeview.Heading",
            background=[("active", "#c2410c")]
        )

    def ui(self, func, *args, **kwargs):
        self.after(0, lambda: func(*args, **kwargs))

    def ui_sync(self, func, *args, **kwargs):
        done = threading.Event()
        result = {}

        def runner():
            try:
                result["value"] = func(*args, **kwargs)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        self.after(0, runner)
        done.wait()

        if "error" in result:
            raise result["error"]

        return result.get("value")

    def run_async(self, target, *args):
        target_name = getattr(target, "__name__", str(target))

        def runner():
            target(*args)

        thread_name = f"async-{target_name[:24]}"
        threading.Thread(target=runner, daemon=True, name=thread_name).start()

    def set_status(self, text: str, state: str = "error"):
        self.status_label.configure(text=text)

        if state == "success":
            color = "#1faa59"
        elif state == "loading":
            color = "#d4a017"
        else:
            color = "#d11a2a"

        self.status_dot.configure(text_color=color)

    def set_version_preview(self, text: str, trace_context: dict | None = None):
        started_at = time.perf_counter()
        self.version_preview_box.configure(state="normal")
        self.version_preview_box.delete("1.0", "end")
        self.version_preview_box.insert("end", text)
        self.version_preview_box.configure(state="disabled")
        self._trace_table_open(
            trace_context,
            f"set_version_preview() concluido em {(time.perf_counter() - started_at) * 1000:.1f} ms; chars={len(text)}",
        )

    @staticmethod
    def align_button_text_left(button):
        text_label = getattr(button, "_text_label", None)
        if text_label:
            text_label.configure(justify="left", anchor="w")

    def _is_control_metadata_table(self, full_table_name: str | None) -> bool:
        if not full_table_name or self.current_db != CONTROL_DB:
            return False

        schema_name, _table_name = split_table_name(full_table_name)
        return schema_name in {CONTROL_SCHEMA, self.LEGACY_CONTROL_SCHEMA}

    def _matches_selected_control_table(self, full_table_name: str | None, expected_table_name: str) -> bool:
        if not full_table_name or self.current_db != CONTROL_DB:
            return False

        schema_name, table_name = split_table_name(full_table_name)
        return schema_name == CONTROL_SCHEMA and table_name == expected_table_name

    def _is_data_dictionary_table(self, full_table_name: str | None = None) -> bool:
        return self._matches_selected_control_table(
            full_table_name or self.selected_table_name,
            self.DATA_DICTIONARY_TABLE,
        )

    def _is_data_dictionary_usage_table(self, full_table_name: str | None = None) -> bool:
        return self._matches_selected_control_table(
            full_table_name or self.selected_table_name,
            self.DATA_DICTIONARY_USAGE_TABLE,
        )

    def _sync_special_table_action_buttons(self):
        is_dictionary_table = self._is_data_dictionary_table()
        is_usage_table = self._is_data_dictionary_usage_table()
        button_state = "disabled" if self._table_action_buttons_busy else "normal"

        if is_dictionary_table:
            self.dictionary_edit_button.grid()
            self.dictionary_edit_button.configure(state=button_state)
            self.dictionary_pdf_button.grid()
            self.dictionary_pdf_button.configure(state=button_state)
        else:
            self.dictionary_edit_button.grid_remove()
            self.dictionary_edit_button.configure(state="disabled")
            self.dictionary_pdf_button.grid_remove()
            self.dictionary_pdf_button.configure(state="disabled")

        if is_usage_table:
            self.usage_refresh_button.grid()
            self.usage_refresh_button.configure(state=button_state)
            self.usage_edit_button.grid()
            self.usage_edit_button.configure(state=button_state)
        else:
            self.usage_refresh_button.grid_remove()
            self.usage_refresh_button.configure(state="disabled")
            self.usage_edit_button.grid_remove()
            self.usage_edit_button.configure(state="disabled")

    def validate_admin_password(self, typed_password: str) -> bool:
        typed = str(typed_password or "")
        if not typed:
            return False

        return typed in {
            str(self.service.ssh_password or ""),
            str(self.service.sql_password or ""),
        }

    def show_error(self, title: str, message: str):
        normalized_message = str(message or "").strip()
        if not normalized_message:
            normalized_message = "Falha sem detalhe adicional. Consulte o terminal/log para o traceback."
        messagebox.showerror(title, normalized_message, parent=self)

    def show_warning(self, title: str, message: str):
        messagebox.showwarning(title, message, parent=self)

    def show_info(self, title: str, message: str):
        messagebox.showinfo(title, message, parent=self)

    def ask_yes_no(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, parent=self)

    def ask_open_filename(self, **kwargs):
        return filedialog.askopenfilename(parent=self, **kwargs)

    def ask_open_filenames(self, **kwargs):
        return list(filedialog.askopenfilenames(parent=self, **kwargs))

    def ask_save_filename(self, **kwargs):
        return filedialog.asksaveasfilename(parent=self, **kwargs)

    def ask_text_input(self, title: str, text: str):
        dialog = ctk.CTkInputDialog(text=text, title=title)
        dialog.after(10, lambda: center_window(dialog))
        return dialog.get_input()

    def _next_data_dictionary_pdf_version(self) -> str:
        version_number = int(datetime.now().strftime("%Y%m%d%H%M"))
        if version_number <= self._last_data_dictionary_pdf_version:
            version_number = self._last_data_dictionary_pdf_version + 1
        self._last_data_dictionary_pdf_version = version_number
        return str(version_number)

    def set_raw_import_busy(self, busy: bool):
        self.set_table_action_buttons_busy(busy)

    def set_sql_execution_busy(self, busy: bool):
        self.set_table_action_buttons_busy(busy)

    def set_table_action_buttons_busy(self, busy: bool):
        self._table_action_buttons_busy = busy
        if busy:
            self.raw_button.configure(state="disabled")
            self.create_columns_button.configure(state="disabled")
            self.sql_button.configure(state="disabled")
            self.expand_button.configure(state="disabled")
            self.dictionary_edit_button.configure(state="disabled")
            self.dictionary_pdf_button.configure(state="disabled")
            self.usage_refresh_button.configure(state="disabled")
            self.usage_edit_button.configure(state="disabled")
            return

        state = "normal" if self.selected_table_name and not self._is_control_metadata_table(self.selected_table_name) else "disabled"
        self.raw_button.configure(state=state)
        self.create_columns_button.configure(state=state)
        self.sql_button.configure(state=state)
        self.expand_button.configure(state=state)
        self._sync_special_table_action_buttons()

    def open_raw_progress(self, file_name: str, table_name: str):
        self.close_raw_progress()
        self.raw_progress_dialog = RawImportProgressDialog(
            self,
            file_name=file_name,
            table_name=table_name,
            on_cancel=self.request_cancel_raw_import,
        )
        self.raw_progress_dialog.update_progress("schema", 0, "Preparando importacao...")

    def update_raw_progress(self, stage_key: str, progress: float, message: str):
        if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists():
            self.raw_progress_dialog.update_progress(stage_key, progress, message)

    def complete_raw_progress(self, message: str):
        if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists():
            self.raw_progress_dialog.mark_completed(message)
            self.after(800, self.close_raw_progress)

    def mark_raw_progress_finalizing(self, message: str):
        if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists():
            self.raw_progress_dialog.mark_finalizing(message)

    def mark_raw_progress_cancelling(self, message: str):
        if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists():
            self.raw_progress_dialog.mark_cancelling(message)

    def mark_raw_progress_cancelled(self, message: str):
        if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists():
            self.raw_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_raw_progress)

    def close_raw_progress(self):
        if self.raw_progress_dialog:
            try:
                if self.raw_progress_dialog.winfo_exists():
                    self.raw_progress_dialog.close()
            except Exception:
                pass
        self.raw_progress_dialog = None

    def begin_raw_import_cancellation_scope(self):
        self.raw_import_finalizing = False
        self.raw_import_cancel_event = threading.Event()

    def begin_raw_import_finalization(self):
        self.raw_import_finalizing = True
        self.ui(
            self.mark_raw_progress_finalizing,
            "Carga confirmada no banco. Finalizando dump e versionamento; esta etapa nao pode ser cancelada.",
        )

    def request_cancel_raw_import(self):
        if self.raw_import_finalizing:
            self.mark_raw_progress_finalizing(
                "Carga ja confirmada no banco. Aguarde a conclusao do dump e do versionamento."
            )
            return
        if not self.raw_import_cancel_event or self.raw_import_cancel_event.is_set():
            return

        self.raw_import_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_raw_progress_cancelling("Solicitacao de cancelamento enviada. Encerrando operacao...")

    def clear_raw_import_cancellation_scope(self):
        self.raw_import_finalizing = False
        self.raw_import_cancel_event = None

    def open_sql_execution_progress(self, database_name: str, table_name: str):
        self.close_sql_execution_progress()
        self.sql_execution_progress_dialog = SqlExecutionProgressDialog(
            self,
            database_name=database_name,
            table_name=table_name,
            on_cancel=self.request_cancel_sql_execution,
        )
        self.sql_execution_progress_dialog.update_progress("prepare", 0, "Preparando execucao SQL...")

    def open_sql_query_progress(self, database_name: str, table_name: str):
        self.close_sql_execution_progress()
        self.sql_execution_progress_dialog = SqlQueryProgressDialog(
            self,
            database_name=database_name,
            table_name=table_name,
            on_cancel=self.request_cancel_sql_execution,
        )
        self.sql_execution_progress_dialog.update_progress("prepare", 0, "Preparando consulta SQL...")

    def update_sql_execution_progress(self, stage_key: str, progress: float, message: str):
        if self.sql_execution_progress_dialog and self.sql_execution_progress_dialog.winfo_exists():
            self.sql_execution_progress_dialog.update_progress(stage_key, progress, message)

    def complete_sql_execution_progress(self, message: str):
        if self.sql_execution_progress_dialog and self.sql_execution_progress_dialog.winfo_exists():
            self.sql_execution_progress_dialog.mark_completed(message)
            self.after(800, self.close_sql_execution_progress)

    def mark_sql_execution_progress_cancelling(self, message: str):
        if self.sql_execution_progress_dialog and self.sql_execution_progress_dialog.winfo_exists():
            self.sql_execution_progress_dialog.mark_cancelling(message)

    def mark_sql_execution_progress_cancelled(self, message: str):
        if self.sql_execution_progress_dialog and self.sql_execution_progress_dialog.winfo_exists():
            self.sql_execution_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_sql_execution_progress)

    def close_sql_execution_progress(self):
        if self.sql_execution_progress_dialog:
            try:
                if self.sql_execution_progress_dialog.winfo_exists():
                    self.sql_execution_progress_dialog.close()
            except Exception:
                pass
        self.sql_execution_progress_dialog = None

    def begin_sql_execution_cancellation_scope(self):
        self.sql_execution_cancel_event = threading.Event()

    def request_cancel_sql_execution(self):
        if not self.sql_execution_cancel_event or self.sql_execution_cancel_event.is_set():
            return

        self.sql_execution_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_sql_execution_progress_cancelling(
            "Solicitacao de cancelamento enviada. Encerrando execucao SQL..."
        )

    def clear_sql_execution_cancellation_scope(self):
        self.sql_execution_cancel_event = None

    def open_cross_table_expansion_progress(
        self,
        database_name: str,
        source_table_name: str,
        destination_count: int,
    ):
        self.close_cross_table_expansion_progress()
        self.cross_table_expansion_progress_dialog = SqlExpansionProgressDialog(
            self,
            database_name=database_name,
            source_table_name=source_table_name,
            destination_count=destination_count,
            on_cancel=self.request_cancel_cross_table_expansion,
        )
        self.cross_table_expansion_progress_dialog.update_progress(
            "prepare",
            0,
            "Preparing cross-table expansion...",
        )

    def update_cross_table_expansion_progress(
        self,
        stage_key: str,
        progress: float,
        message: str,
    ):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog and dialog.winfo_exists():
            dialog.update_progress(stage_key, progress, message)

    def complete_cross_table_expansion_progress(
        self,
        message: str,
        timing_report: dict | None = None,
    ):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog and dialog.winfo_exists():
            dialog.mark_completed(message)
            self.after(800, self.close_cross_table_expansion_progress)
        if timing_report:
            self.after(
                900,
                lambda: self.open_expansion_timing_report(timing_report),
            )

    def open_expansion_timing_report(self, timing_report: dict):
        dialog = self.expansion_timing_report_dialog
        if dialog:
            try:
                if dialog.winfo_exists():
                    dialog.close()
            except Exception:
                pass
        self.expansion_timing_report_dialog = ExpansionTimingReportDialog(
            self,
            timing_report,
        )

    def mark_cross_table_expansion_finalizing(self, message: str):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog and dialog.winfo_exists():
            dialog.mark_finalizing(message)

    def mark_cross_table_expansion_cancelling(self, message: str):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog and dialog.winfo_exists():
            dialog.mark_cancelling(message)

    def mark_cross_table_expansion_cancelled(self, message: str):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog and dialog.winfo_exists():
            dialog.mark_cancelled(message)
            self.after(700, self.close_cross_table_expansion_progress)

    def close_cross_table_expansion_progress(self):
        dialog = self.cross_table_expansion_progress_dialog
        if dialog:
            try:
                if dialog.winfo_exists():
                    dialog.close()
            except Exception:
                pass
        self.cross_table_expansion_progress_dialog = None

    def begin_cross_table_expansion_cancellation_scope(self):
        self.cross_table_expansion_finalizing = False
        self.cross_table_expansion_cancel_event = threading.Event()

    def begin_cross_table_expansion_finalization(self):
        self.cross_table_expansion_finalizing = True
        self.ui(
            self.mark_cross_table_expansion_finalizing,
            (
                "Data committed to the destinations. Finalizing atomic version "
                "registration; this stage cannot be cancelled."
            ),
        )

    def request_cancel_cross_table_expansion(self):
        if self.cross_table_expansion_finalizing:
            self.mark_cross_table_expansion_finalizing(
                "The data has already been committed. Wait for version registration."
            )
            return
        cancel_event = self.cross_table_expansion_cancel_event
        if not cancel_event or cancel_event.is_set():
            return
        cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_cross_table_expansion_cancelling(
            "Cancellation request sent. Rolling back the transaction..."
        )

    def clear_cross_table_expansion_cancellation_scope(self):
        self.cross_table_expansion_finalizing = False
        self.cross_table_expansion_cancel_event = None

    def open_raw_expand_progress(
        self,
        table_name: str,
        raw_key: str,
        focus_text: str,
        focus_label: str = "Column",
    ):
        self.close_raw_expand_progress()
        self.raw_expand_progress_dialog = RawExpandProgressDialog(
            self,
            table_name=table_name,
            raw_key=raw_key,
            focus_text=focus_text,
            focus_label=focus_label,
            on_cancel=self.request_cancel_raw_expand,
        )
        self.raw_expand_progress_dialog.update_progress("prepare", 0, "Preparando expansao...")

    def update_raw_expand_progress(self, stage_key: str, progress: float, message: str):
        if self.raw_expand_progress_dialog and self.raw_expand_progress_dialog.winfo_exists():
            self.raw_expand_progress_dialog.update_progress(stage_key, progress, message)

    def complete_raw_expand_progress(self, message: str):
        if self.raw_expand_progress_dialog and self.raw_expand_progress_dialog.winfo_exists():
            self.raw_expand_progress_dialog.mark_completed(message)
            self.after(800, self.close_raw_expand_progress)

    def mark_raw_expand_progress_cancelling(self, message: str):
        if self.raw_expand_progress_dialog and self.raw_expand_progress_dialog.winfo_exists():
            self.raw_expand_progress_dialog.mark_cancelling(message)

    def mark_raw_expand_progress_cancelled(self, message: str):
        if self.raw_expand_progress_dialog and self.raw_expand_progress_dialog.winfo_exists():
            self.raw_expand_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_raw_expand_progress)

    def close_raw_expand_progress(self):
        if self.raw_expand_progress_dialog:
            try:
                if self.raw_expand_progress_dialog.winfo_exists():
                    self.raw_expand_progress_dialog.close()
            except Exception:
                pass
        self.raw_expand_progress_dialog = None

    def begin_raw_expand_cancellation_scope(self):
        self.raw_expand_cancel_event = threading.Event()

    def request_cancel_raw_expand(self):
        if not self.raw_expand_cancel_event or self.raw_expand_cancel_event.is_set():
            return

        self.raw_expand_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_raw_expand_progress_cancelling("Solicitacao de cancelamento enviada. Encerrando expansao...")

    def clear_raw_expand_cancellation_scope(self):
        self.raw_expand_cancel_event = None

    @staticmethod
    def _build_expand_field_option_label(source_kind: str, field_name: str) -> str:
        prefix = "Column" if source_kind == "column" else "Raw"
        return f"{prefix}: {field_name}"

    def _load_expand_field_options(
        self,
        database_name: str,
        table_name: str,
        include_raw: bool = True,
    ) -> list[dict]:
        options = []
        seen_labels = set()
        has_raw_column = False

        for column in self.service.get_table_column_definitions(database_name, table_name):
            column_name = str(column.get("name") or "").strip()
            if column_name == "raw":
                has_raw_column = True
                continue
            if not column_name or column_name.endswith("_fail"):
                continue
            label = self._build_expand_field_option_label("column", column_name)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            options.append(
                {
                    "label": label,
                    "source_kind": "column",
                    "name": column_name,
                    "type": column.get("type"),
                }
            )

        if include_raw and has_raw_column:
            for raw_key in self.service.get_raw_value_keys(database_name, table_name):
                raw_key_name = str(raw_key or "").strip()
                if not raw_key_name:
                    continue
                label = self._build_expand_field_option_label("raw", raw_key_name)
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                options.append(
                    {
                        "label": label,
                        "source_kind": "raw",
                        "name": raw_key_name,
                        "type": "raw",
                    }
                )

        return options

    def _default_child_type_conversion_sql(self, target_type: str) -> str:
        normalized_type = self.service._normalize_data_type_name(target_type)
        if normalized_type == "varchar":
            return "NULLIF(btrim(input_text), '')"
        if normalized_type == "boolean":
            return (
                "CASE\n"
                "    WHEN normalized_text IN ('true', 't', '1', 'sim', 's', 'yes', 'y') THEN true\n"
                "    WHEN normalized_text IN ('false', 'f', '0', 'nao', 'n', 'no') THEN false\n"
                "    ELSE NULL\n"
                "END"
            )
        if normalized_type == "date":
            return "to_timestamp(input_text, 'DD/MM/YYYY')::date"
        if normalized_type == "time":
            return "to_timestamp(input_text, 'HH24:MI:SS')::time"
        if normalized_type == "timestamp":
            return "to_timestamp(input_text, 'DD/MM/YYYY HH24:MI:SS')::timestamp"
        if normalized_type == "timestamptz":
            return "to_timestamp(input_text, 'DD/MM/YYYY HH24:MI:SS')"
        return f"NULLIF(btrim(input_text), '')::{target_type}"

    def _resolve_child_expand_type_conflicts(self, table_name: str, child_table_name: str, expand_config: dict):
        value_mappings = [dict(item) for item in (expand_config or {}).get("value_mappings") or []]
        if not value_mappings:
            return dict(expand_config or {})

        resolved_mappings = []
        for mapping_index, mapping in enumerate(value_mappings, start=1):
            child_value_source = dict(mapping.get("child_value_source") or {})
            child_column_label = str(
                child_value_source.get("label")
                or child_value_source.get("name")
                or f"valor {mapping_index}"
            ).strip()
            child_db_type = str(child_value_source.get("type") or "").strip()
            if not child_db_type:
                resolved_mappings.append(mapping)
                continue

            selected_type = str(mapping.get("column_type") or "").strip().lower()
            selected_compare_type = (
                self.service.resolve_supported_expand_column_type(selected_type)
                or self.service._normalize_data_type_name(selected_type)
            )
            child_compare_type = (
                self.service.resolve_supported_expand_column_type(child_db_type)
                or self.service._normalize_data_type_name(child_db_type)
            )
            if not child_compare_type or child_compare_type == selected_compare_type:
                mapping.pop("type_conversion_mode", None)
                mapping.pop("conversion_sql", None)
                resolved_mappings.append(mapping)
                continue

            raw_key = str(mapping.get("raw_key") or "").strip() or "(sem header)"
            allow_child_type_conversion = bool(
                self.service.resolve_supported_expand_column_type(child_db_type)
                and not (
                    child_compare_type in RawExpandColumnDialog.DATE_TIME_TYPES
                    and not list(mapping.get("date_formats") or [])
                )
            )
            resolution_mode = self.ask_raw_expand_type_mismatch_resolution(
                raw_key=raw_key,
                child_column_label=child_column_label,
                selected_type=selected_type or "(vazio)",
                child_type=child_db_type,
                allow_child_type_conversion=allow_child_type_conversion,
            )
            if not resolution_mode:
                return None

            if resolution_mode == "child_type":
                resolved_child_type = self.service.resolve_supported_expand_column_type(child_db_type)
                if not resolved_child_type:
                    self.show_error(
                        "Error",
                        (
                            f"A coluna {child_column_label} usa o tipo {child_db_type}, "
                            "que nao possui conversao automatica suportada neste fluxo."
                        ),
                    )
                    return None
                mapping["column_type"] = resolved_child_type
                mapping["type_conversion_mode"] = "child_type"
                mapping.pop("conversion_sql", None)
                resolved_mappings.append(mapping)
                continue

            sql_expression = self.ask_sql_expression(
                title=f"SQL de conversao: {raw_key}",
                database_name=self.current_db or "",
                table_name=table_name,
                target_type=child_db_type,
                context_label=(
                    f"Tabela filha: {child_table_name}   |   Coluna: {child_column_label}\n"
                    f"Header raw: {raw_key}"
                ),
                initial_sql=self._default_child_type_conversion_sql(child_db_type),
            )
            if not sql_expression:
                return None

            resolved_child_type = self.service.resolve_supported_expand_column_type(child_db_type)
            if resolved_child_type:
                mapping["column_type"] = resolved_child_type
            mapping["type_conversion_mode"] = "custom_sql"
            mapping["conversion_sql"] = sql_expression
            resolved_mappings.append(mapping)

        resolved_config = dict(expand_config or {})
        resolved_config["value_mappings"] = resolved_mappings
        primary_mapping = resolved_mappings[0]
        for field_name in (
            "child_value_source",
            "column_name",
            "column_type",
            "false_values",
            "true_values",
            "date_formats",
            "dictionary",
            "type_conversion_mode",
            "conversion_sql",
        ):
            if field_name in primary_mapping:
                resolved_config[field_name] = primary_mapping[field_name]
            else:
                resolved_config.pop(field_name, None)
        return resolved_config

    def open_raw_group_progress(self, table_name: str, focus_text: str, focus_label: str = "Chave"):
        self.close_raw_group_progress()
        self.raw_group_progress_dialog = RawGroupProgressDialog(
            self,
            table_name=table_name,
            focus_text=focus_text,
            focus_label=focus_label,
            on_cancel=self.request_cancel_raw_group,
        )
        self.raw_group_progress_dialog.update_progress("prepare", 0, "Preparando agrupamento...")

    def update_raw_group_progress(self, stage_key: str, progress: float, message: str):
        if self.raw_group_progress_dialog and self.raw_group_progress_dialog.winfo_exists():
            self.raw_group_progress_dialog.update_progress(stage_key, progress, message)

    def complete_raw_group_progress(self, message: str):
        if self.raw_group_progress_dialog and self.raw_group_progress_dialog.winfo_exists():
            self.raw_group_progress_dialog.mark_completed(message)
            self.after(800, self.close_raw_group_progress)

    def mark_raw_group_progress_cancelling(self, message: str):
        if self.raw_group_progress_dialog and self.raw_group_progress_dialog.winfo_exists():
            self.raw_group_progress_dialog.mark_cancelling(message)

    def mark_raw_group_progress_cancelled(self, message: str):
        if self.raw_group_progress_dialog and self.raw_group_progress_dialog.winfo_exists():
            self.raw_group_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_raw_group_progress)

    def close_raw_group_progress(self):
        if self.raw_group_progress_dialog:
            try:
                if self.raw_group_progress_dialog.winfo_exists():
                    self.raw_group_progress_dialog.close()
            except Exception:
                pass
        self.raw_group_progress_dialog = None

    def begin_raw_group_cancellation_scope(self):
        self.raw_group_cancel_event = threading.Event()

    def request_cancel_raw_group(self):
        if not self.raw_group_cancel_event or self.raw_group_cancel_event.is_set():
            return

        self.raw_group_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_raw_group_progress_cancelling("Solicitacao de cancelamento enviada. Encerrando agrupamento...")

    def clear_raw_group_cancellation_scope(self):
        self.raw_group_cancel_event = None

    def open_constraint_progress(
        self,
        table_name: str,
        column_name: str,
        constraint_label: str,
        reference_text: str | None = None,
    ):
        self.close_constraint_progress()
        self.constraint_progress_dialog = TableConstraintProgressDialog(
            self,
            table_name=table_name,
            column_name=column_name,
            constraint_label=constraint_label,
            reference_text=reference_text,
            on_cancel=self.request_cancel_constraint_change,
        )
        self.constraint_progress_dialog.update_progress("prepare", 0, "Preparando alteracao estrutural...")

    def update_constraint_progress(self, stage_key: str, progress: float, message: str):
        if self.constraint_progress_dialog and self.constraint_progress_dialog.winfo_exists():
            self.constraint_progress_dialog.update_progress(stage_key, progress, message)

    def complete_constraint_progress(self, message: str):
        if self.constraint_progress_dialog and self.constraint_progress_dialog.winfo_exists():
            self.constraint_progress_dialog.mark_completed(message)
            self.after(800, self.close_constraint_progress)

    def mark_constraint_progress_cancelling(self, message: str):
        if self.constraint_progress_dialog and self.constraint_progress_dialog.winfo_exists():
            self.constraint_progress_dialog.mark_cancelling(message)

    def mark_constraint_progress_cancelled(self, message: str):
        if self.constraint_progress_dialog and self.constraint_progress_dialog.winfo_exists():
            self.constraint_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_constraint_progress)

    def close_constraint_progress(self):
        if self.constraint_progress_dialog:
            try:
                if self.constraint_progress_dialog.winfo_exists():
                    self.constraint_progress_dialog.close()
            except Exception:
                pass
        self.constraint_progress_dialog = None

    def begin_constraint_change_cancellation_scope(self):
        self.constraint_change_cancel_event = threading.Event()

    def request_cancel_constraint_change(self):
        if not self.constraint_change_cancel_event or self.constraint_change_cancel_event.is_set():
            return

        self.constraint_change_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_constraint_progress_cancelling(
            "Solicitacao de cancelamento enviada. Encerrando alteracao estrutural..."
        )

    def clear_constraint_change_cancellation_scope(self):
        self.constraint_change_cancel_event = None

    def open_column_delete_progress(self, table_name: str, column_name: str):
        self.close_column_delete_progress()
        self.column_delete_progress_dialog = ColumnDeletionProgressDialog(
            self,
            table_name=table_name,
            column_name=column_name,
            on_cancel=self.request_cancel_column_delete,
        )
        self.column_delete_progress_dialog.update_progress("prepare", 0, "Preparando exclusao...")

    def update_column_delete_progress(self, stage_key: str, progress: float, message: str):
        if self.column_delete_progress_dialog and self.column_delete_progress_dialog.winfo_exists():
            self.column_delete_progress_dialog.update_progress(stage_key, progress, message)

    def complete_column_delete_progress(self, message: str):
        if self.column_delete_progress_dialog and self.column_delete_progress_dialog.winfo_exists():
            self.column_delete_progress_dialog.mark_completed(message)
            self.after(800, self.close_column_delete_progress)

    def mark_column_delete_progress_cancelling(self, message: str):
        if self.column_delete_progress_dialog and self.column_delete_progress_dialog.winfo_exists():
            self.column_delete_progress_dialog.mark_cancelling(message)

    def mark_column_delete_progress_cancelled(self, message: str):
        if self.column_delete_progress_dialog and self.column_delete_progress_dialog.winfo_exists():
            self.column_delete_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_column_delete_progress)

    def close_column_delete_progress(self):
        if self.column_delete_progress_dialog:
            try:
                if self.column_delete_progress_dialog.winfo_exists():
                    self.column_delete_progress_dialog.close()
            except Exception:
                pass
        self.column_delete_progress_dialog = None

    def begin_column_delete_cancellation_scope(self):
        self.column_delete_cancel_event = threading.Event()

    def request_cancel_column_delete(self):
        if not self.column_delete_cancel_event or self.column_delete_cancel_event.is_set():
            return

        self.column_delete_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_column_delete_progress_cancelling("Solicitacao de cancelamento enviada. Encerrando exclusao...")

    def clear_column_delete_cancellation_scope(self):
        self.column_delete_cancel_event = None

    def open_table_delete_progress(self, database_name: str, table_name: str):
        self.close_table_delete_progress()
        self.table_delete_progress_dialog = TableDeletionProgressDialog(
            self,
            database_name=database_name,
            table_name=table_name,
            on_cancel=self.request_cancel_table_delete,
        )
        self.table_delete_progress_dialog.update_progress("prepare", 0, "Preparando exclusao...")

    def update_table_delete_progress(self, stage_key: str, progress: float, message: str):
        if self.table_delete_progress_dialog and self.table_delete_progress_dialog.winfo_exists():
            self.table_delete_progress_dialog.update_progress(stage_key, progress, message)

    def complete_table_delete_progress(self, message: str):
        if self.table_delete_progress_dialog and self.table_delete_progress_dialog.winfo_exists():
            self.table_delete_progress_dialog.mark_completed(message)
            self.after(800, self.close_table_delete_progress)

    def mark_table_delete_progress_cancelling(self, message: str):
        if self.table_delete_progress_dialog and self.table_delete_progress_dialog.winfo_exists():
            self.table_delete_progress_dialog.mark_cancelling(message)

    def mark_table_delete_progress_cancelled(self, message: str):
        if self.table_delete_progress_dialog and self.table_delete_progress_dialog.winfo_exists():
            self.table_delete_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_table_delete_progress)

    def close_table_delete_progress(self):
        if self.table_delete_progress_dialog:
            try:
                if self.table_delete_progress_dialog.winfo_exists():
                    self.table_delete_progress_dialog.close()
            except Exception:
                pass
        self.table_delete_progress_dialog = None

    def begin_table_delete_cancellation_scope(self):
        self.table_delete_cancel_event = threading.Event()

    def request_cancel_table_delete(self):
        if not self.table_delete_cancel_event or self.table_delete_cancel_event.is_set():
            return

        self.table_delete_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_table_delete_progress_cancelling(
            "Solicitacao de cancelamento enviada. Encerrando exclusao..."
        )

    def clear_table_delete_cancellation_scope(self):
        self.table_delete_cancel_event = None

    def open_restore_version_progress(self, table_name: str, version_code: str):
        self.close_restore_version_progress()
        self.restore_version_progress_dialog = RestoreVersionProgressDialog(
            self,
            table_name=table_name,
            version_code=version_code,
            on_cancel=self.request_cancel_restore_version,
        )
        self.restore_version_progress_dialog.update_progress("prepare", 0, "Preparando restauracao...")

    def open_restore_version_to_new_table_progress(self, table_name: str, version_code: str):
        self.close_restore_version_progress()
        self.restore_version_progress_dialog = RestoreVersionToNewTableProgressDialog(
            self,
            table_name=table_name,
            version_code=version_code,
            on_cancel=self.request_cancel_restore_version,
        )
        self.restore_version_progress_dialog.update_progress("prepare", 0, "Preparando restauracao...")

    def update_restore_version_progress(self, stage_key: str, progress: float, message: str):
        if self.restore_version_progress_dialog and self.restore_version_progress_dialog.winfo_exists():
            self.restore_version_progress_dialog.update_progress(stage_key, progress, message)

    def complete_restore_version_progress(self, message: str):
        if self.restore_version_progress_dialog and self.restore_version_progress_dialog.winfo_exists():
            self.restore_version_progress_dialog.mark_completed(message)
            self.after(800, self.close_restore_version_progress)

    def mark_restore_version_progress_cancelling(self, message: str):
        if self.restore_version_progress_dialog and self.restore_version_progress_dialog.winfo_exists():
            self.restore_version_progress_dialog.mark_cancelling(message)

    def mark_restore_version_progress_cancelled(self, message: str):
        if self.restore_version_progress_dialog and self.restore_version_progress_dialog.winfo_exists():
            self.restore_version_progress_dialog.mark_cancelled(message)
            self.after(700, self.close_restore_version_progress)

    def close_restore_version_progress(self):
        if self.restore_version_progress_dialog:
            try:
                if self.restore_version_progress_dialog.winfo_exists():
                    self.restore_version_progress_dialog.close()
            except Exception:
                pass
        self.restore_version_progress_dialog = None

    def begin_restore_version_cancellation_scope(self):
        self.restore_version_cancel_event = threading.Event()

    def request_cancel_restore_version(self):
        if not self.restore_version_cancel_event or self.restore_version_cancel_event.is_set():
            return

        self.restore_version_cancel_event.set()
        self.service.cancel_active_commands()
        self.mark_restore_version_progress_cancelling(
            "Solicitacao de cancelamento enviada. Encerrando restauracao..."
        )

    def clear_restore_version_cancellation_scope(self):
        self.restore_version_cancel_event = None

    # ------------------------------------------------------------------
    # Construção da interface
    # ------------------------------------------------------------------

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_data_panel()
        self._build_versions_panel()

    def _build_header(self):
        self.header = ctk.CTkFrame(self, corner_radius=14)
        self.header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=16)
        self.header.grid_columnconfigure(2, weight=1)

        self.connect_button = ctk.CTkButton(
            self.header,
            text="Connect",
            width=140,
            command=self.connect_vm
        )
        self.connect_button.grid(row=0, column=0, padx=(12, 8), pady=12)

        self.status_dot = ctk.CTkLabel(
            self.header,
            text="●",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#d11a2a"
        )
        self.status_dot.grid(row=0, column=1, padx=(6, 4), pady=12)

        self.status_label = ctk.CTkLabel(
            self.header,
            text="Disconnected",
            anchor="w"
        )
        self.status_label.grid(row=0, column=2, sticky="ew", padx=(0, 12), pady=12)

        self.users_permissions_button = ctk.CTkButton(
            self.header,
            text="👤",
            font=ctk.CTkFont(size=24),
            width=42,
            command=self.open_users_permissions_flow,
            state="disabled",
        )
        self.users_permissions_button.grid(row=0, column=3, padx=(0, 12), pady=12)

        self.database_operations_button = ctk.CTkButton(
            self.header,
            text="\u23F1",
            font=ctk.CTkFont(size=22),
            width=42,
            command=self.open_database_operations_flow,
            state="disabled",
        )
        self.database_operations_button.grid(row=0, column=4, padx=(0, 12), pady=12)

    def _build_sidebar(self):
        self.sidebar_panel = ctk.CTkFrame(self, corner_radius=14, width=340)
        self.sidebar_panel.grid(row=1, column=0, sticky="ns", padx=(16, 8), pady=(0, 16))
        self.sidebar_panel.grid_rowconfigure(0, weight=1)
        self.sidebar_panel.grid_rowconfigure(1, weight=1)

        self._build_database_panel()
        self._build_tables_panel()

    def _build_database_panel(self):
        self.db_panel = ctk.CTkFrame(self.sidebar_panel, corner_radius=14)
        self.db_panel.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 8))
        self.db_panel.grid_rowconfigure(1, weight=1)

        self.db_header = ctk.CTkFrame(self.db_panel, fg_color="transparent")
        self.db_header.grid(row=0, column=0, padx=12, pady=(12, 8), sticky="ew")
        self.db_header.grid_columnconfigure(0, weight=1)

        self.db_title = ctk.CTkLabel(
            self.db_header,
            text="Databases",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.db_title.grid(row=0, column=0, sticky="w")

        self.add_db_button = ctk.CTkButton(
            self.db_header,
            text="+",
            width=32,
            height=32,
            corner_radius=16,
            command=self.create_database_flow,
            state="disabled"
        )
        self.add_db_button.grid(row=0, column=1, sticky="e")

        self.db_buttons_frame = ctk.CTkScrollableFrame(self.db_panel, width=300, height=300)
        self.db_buttons_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")

    def _build_tables_panel(self):
        self.tables_panel = ctk.CTkFrame(self.sidebar_panel, corner_radius=14)
        self.tables_panel.grid(row=1, column=0, sticky="nsew", padx=0, pady=(8, 0))
        self.tables_panel.grid_rowconfigure(1, weight=1)

        self.tables_header = ctk.CTkFrame(self.tables_panel, fg_color="transparent")
        self.tables_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.tables_header.grid_columnconfigure(0, weight=1)

        self.tables_title = ctk.CTkLabel(
            self.tables_header,
            text="Tables",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.tables_title.grid(row=0, column=0, sticky="w")

        self.add_table_button = ctk.CTkButton(
            self.tables_header,
            text="+",
            width=32,
            height=32,
            corner_radius=16,
            command=self.create_table_flow,
            state="disabled"
        )
        self.add_table_button.grid(row=0, column=1, sticky="e")

        self.tables_buttons_frame = ctk.CTkScrollableFrame(self.tables_panel, width=300, height=300)
        self.tables_buttons_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")

    def _build_data_panel(self):
        self.data_panel = ctk.CTkFrame(self, corner_radius=14)
        self.data_panel.grid(row=1, column=1, sticky="nsew", padx=(8, 8), pady=(0, 16))
        self.data_panel.grid_rowconfigure(1, weight=1)
        self.data_panel.grid_columnconfigure(0, weight=1)

        self.data_header = ctk.CTkFrame(self.data_panel, fg_color="transparent")
        self.data_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.data_header.grid_columnconfigure(9, weight=1)

        self.back_button = ctk.CTkButton(
            self.data_header,
            text="← Clear selection",
            width=130,
            command=self.clear_table_selection
        )
        self.back_button.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.raw_button = ctk.CTkButton(
            self.data_header,
            text="⊕ Raw",
            width=110,
            command=self.import_raw_file_flow,
            state="disabled"
        )
        self.raw_button.grid(row=0, column=1, sticky="w", padx=(0, 10))

        self.create_columns_button = ctk.CTkButton(
            self.data_header,
            text="Create",
            width=110,
            command=self.create_columns_flow,
            state="disabled"
        )
        self.create_columns_button.grid(row=0, column=2, sticky="w", padx=(0, 10))

        self.sql_button = ctk.CTkButton(
            self.data_header,
            text="SQL",
            width=110,
            command=self.execute_sql_flow,
            state="disabled"
        )
        self.sql_button.grid(row=0, column=3, sticky="w", padx=(0, 10))

        self.expand_button = ctk.CTkButton(
            self.data_header,
            text="Expand",
            width=110,
            command=self.execute_cross_table_expansion_flow,
            state="disabled",
        )
        self.expand_button.grid(row=0, column=4, sticky="w")

        self.dictionary_edit_button = ctk.CTkButton(
            self.data_header,
            text="Edit",
            width=110,
            command=self.open_data_dictionary_editor_flow,
            state="disabled",
        )
        self.dictionary_edit_button.grid(row=0, column=5, sticky="w", padx=(10, 0))
        self.dictionary_edit_button.grid_remove()

        self.dictionary_pdf_button = ctk.CTkButton(
            self.data_header,
            text="Generate PDF",
            width=120,
            command=self.export_data_dictionary_pdf_flow,
            state="disabled",
        )
        self.dictionary_pdf_button.grid(row=0, column=6, sticky="w", padx=(10, 0))
        self.dictionary_pdf_button.grid_remove()

        self.usage_refresh_button = ctk.CTkButton(
            self.data_header,
            text="Refresh",
            width=120,
            command=self.refresh_data_dictionary_usage_flow,
            state="disabled",
        )
        self.usage_refresh_button.grid(row=0, column=7, sticky="w", padx=(10, 0))
        self.usage_refresh_button.grid_remove()

        self.usage_edit_button = ctk.CTkButton(
            self.data_header,
            text="Edit usage",
            width=130,
            command=self.open_data_dictionary_usage_editor_flow,
            state="disabled",
        )
        self.usage_edit_button.grid(row=0, column=8, sticky="w", padx=(10, 0))
        self.usage_edit_button.grid_remove()

        self.table_detail_frame = ctk.CTkFrame(self.data_panel, corner_radius=12)
        self.table_detail_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.table_detail_frame.grid_rowconfigure(0, weight=1)
        self.table_detail_frame.grid_columnconfigure(0, weight=1)

        self.data_table = ReadOnlyTable(self.table_detail_frame, "First 50 rows")
        self.data_table.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    def _build_versions_panel(self):
        self.versions_panel = ctk.CTkFrame(self, corner_radius=14, width=360)
        self.versions_panel.grid(row=1, column=2, sticky="ns", padx=(8, 16), pady=(0, 16))
        self.versions_panel.grid_rowconfigure(1, weight=1)
        self.versions_panel.grid_rowconfigure(3, weight=1)
        self.versions_panel.grid_columnconfigure(0, weight=1)

        header_row = ctk.CTkFrame(self.versions_panel, fg_color="transparent")
        header_row.grid(row=0, column=0, padx=12, pady=(12, 8), sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        self.versions_title = ctk.CTkLabel(
            header_row,
            text="Versions",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.versions_title.grid(row=0, column=0, sticky="w")

        self.dump_manager_button = ctk.CTkButton(
            header_row,
            text=".dump",
            width=90,
            command=self.open_dump_manager_flow,
            state="disabled"
        )
        self.dump_manager_button.grid(row=0, column=1, sticky="e")

        self.versions_buttons_frame = ctk.CTkScrollableFrame(self.versions_panel, width=320, height=260)
        self.versions_buttons_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")

        self.version_preview_title = ctk.CTkLabel(
            self.versions_panel,
            text="Version preview",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.version_preview_title.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        self.version_preview_box = ctk.CTkTextbox(self.versions_panel, width=320)
        self.version_preview_box.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.version_preview_box.configure(state="disabled")

        self.version_history_button = ctk.CTkButton(
            self.versions_panel,
            text="View full history",
            command=self.open_selected_version_history_flow,
            state="disabled"
        )
        self.version_history_button.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="ew")

        self.restore_version_button = ctk.CTkButton(
            self.versions_panel,
            text="Restore this version",
            command=self.restore_selected_version_flow,
            state="disabled"
        )
        self.restore_version_button.grid(row=5, column=0, padx=12, pady=(0, 8), sticky="ew")

        self.restore_version_new_table_button = ctk.CTkButton(
            self.versions_panel,
            text="Restore to a new table",
            command=self.restore_selected_version_to_new_table_flow,
            state="disabled"
        )
        self.restore_version_new_table_button.grid(row=6, column=0, padx=12, pady=(0, 12), sticky="ew")

    # ------------------------------------------------------------------
    # Limpeza / renderização de listas
    # ------------------------------------------------------------------

    def clear_db_buttons(self):
        for widget in self.db_buttons_frame.winfo_children():
            widget.destroy()

    def clear_table_buttons(self):
        for widget in self.tables_buttons_frame.winfo_children():
            widget.destroy()

    def clear_version_buttons(self):
        self._version_button_map = []
        for widget in self.versions_buttons_frame.winfo_children():
            widget.destroy()

    def clear_table_selection(self, initial=False):
        self.selected_table_name = None
        self.selected_version_record = None
        self.table_preview_filters = {}
        self.table_numeric_columns = set()
        self.table_column_types = {}
        self.table_column_constraints = {}
        self.table_dictionary_unlinked_columns = set()
        self.version_history_button.configure(state="disabled")
        self.restore_version_button.configure(state="disabled")
        self.restore_version_new_table_button.configure(state="disabled")
        self.raw_button.configure(state="disabled")
        self.create_columns_button.configure(state="disabled")
        self.sql_button.configure(state="disabled")
        self.expand_button.configure(state="disabled")
        self._sync_special_table_action_buttons()
        self.clear_version_buttons()

        self.data_table.set_title("First 50 rows")
        self.data_table.set_data([], [])
        self.set_version_preview("Selecione uma tabela para ver o histórico de versões.")

        if initial:
            self.tables_title.configure(text="Tables")
        elif self.current_db:
            self.tables_title.configure(text=f"Tables in {self.current_db}")
        else:
            self.tables_title.configure(text="Tables")

    def populate_database_buttons(self, dbs):
        self.clear_db_buttons()

        if not dbs:
            ctk.CTkLabel(self.db_buttons_frame, text="No databases found.").pack(fill="x", padx=4, pady=4)
            return

        for db_name in dbs:
            row = ctk.CTkFrame(self.db_buttons_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=4)

            btn = ctk.CTkButton(
                row,
                text=db_name,
                anchor="w",
                command=lambda name=db_name: self.load_tables(name)
            )
            btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

            del_btn = ctk.CTkButton(
                row,
                text="X",
                width=32,
                height=32,
                corner_radius=16,
                fg_color="#dc2626",
                hover_color="#b91c1c",
                command=lambda name=db_name: self.delete_database_flow(name)
            )
            del_btn.pack(side="right")

    def populate_table_buttons(self, tables):
        self.clear_table_buttons()
        self.tables_title.configure(text=f"Tables in {self.current_db}" if self.current_db else "Tables")

        if not tables:
            ctk.CTkLabel(self.tables_buttons_frame, text="No tables found.").pack(fill="x", padx=4, pady=4)
            return

        for table_name in tables:
            row = ctk.CTkFrame(self.tables_buttons_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=4)

            is_protected_control_table = self._is_control_metadata_table(table_name)

            btn = ctk.CTkButton(
                row,
                text=table_name,
                anchor="w",
                command=lambda name=table_name: self.load_table_details(name)
            )
            btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

            del_btn = ctk.CTkButton(
                row,
                text="X",
                width=32,
                height=32,
                corner_radius=16,
                fg_color="#dc2626",
                hover_color="#b91c1c",
                command=lambda name=table_name: self.delete_table_flow(name)
            )
            del_btn.pack(side="right")
            if is_protected_control_table:
                del_btn.configure(state="disabled")

    def populate_version_buttons(self, versions, trace_context: dict | None = None):
        started_at = time.perf_counter()
        self._trace_table_open(
            trace_context,
            f"populate_version_buttons() iniciou; version_count={len(versions)}",
        )
        self.clear_version_buttons()
        self.selected_version_record = None
        self.version_history_button.configure(state="disabled")
        self.restore_version_button.configure(state="disabled")
        self.restore_version_new_table_button.configure(state="disabled")

        if not versions:
            ctk.CTkLabel(self.versions_buttons_frame, text="No versions found.").pack(fill="x", padx=4, pady=4)
            self.set_version_preview("No version available.", trace_context=trace_context)
            self._trace_table_open(
                trace_context,
                f"populate_version_buttons() finalizado em {(time.perf_counter() - started_at) * 1000:.1f} ms",
            )
            return

        buttons_started_at = time.perf_counter()
        for version in versions:
            text = (
                f"{version['version_code']} — {version['version_title']}\n"
                f"{version['created_at']} • {version['created_by']}"
            )
            btn = ctk.CTkButton(
                self.versions_buttons_frame,
                text=text,
                anchor="w",
                height=54
            )
            self.align_button_text_left(btn)
            btn.configure(command=lambda v=version, b=btn: self.select_version(v, b))
            btn.pack(fill="x", padx=4, pady=4)
            self._version_button_map.append((btn, version))

        self._trace_table_open(
            trace_context,
            f"populate_version_buttons(): botões criados em {(time.perf_counter() - buttons_started_at) * 1000:.1f} ms",
        )

        if not self._version_button_map:
            self._trace_table_open(trace_context, "populate_version_buttons(): nenhum botão criado; abortando seleção inicial")
            return

        first_selection_started_at = time.perf_counter()
        self.select_version(versions[0], self._version_button_map[0][0], trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"populate_version_buttons(): primeira versão selecionada em {(time.perf_counter() - first_selection_started_at) * 1000:.1f} ms",
        )
        self._trace_table_open(
            trace_context,
            f"populate_version_buttons() finalizado em {(time.perf_counter() - started_at) * 1000:.1f} ms",
        )

    @staticmethod
    def _version_has_full_detail(version) -> bool:
        return bool(version) and "sql_recipe" in version

    def _build_version_preview_text(self, version):
        preview = (
            f"Versão selecionada: {version['version_code']}\n"
            f"Título: {version['version_title']}\n"
            f"Data: {version['created_at']}\n"
            f"Autor: {version['created_by']}\n"
        )

        if version["restored_from_version"]:
            preview += f"Restaurada de: {version['restored_from_version']}\n"

        if version.get("raw_dump_path"):
            preview += f"Raw dump: {version['raw_dump_path']}\n"

        preview += "\nHistórico completo disponível sob demanda."
        return preview

    def _build_version_history_text(self, version):
        preview = (
            f"Versão selecionada: {version['version_code']}\n"
            f"Título: {version['version_title']}\n"
            f"Data: {version['created_at']}\n"
            f"Autor: {version['created_by']}\n"
        )

        if version["restored_from_version"]:
            preview += f"Restaurada de: {version['restored_from_version']}\n"

        if version.get("raw_hash"):
            preview += f"Raw hash: {version['raw_hash']}\n"

        if version.get("raw_ingested_at"):
            preview += f"Raw ingested at: {version['raw_ingested_at']}\n"

        if version.get("raw_schema"):
            preview += f"Raw schema: {version['raw_schema']}\n"

        if version.get("raw_dump_path"):
            preview += f"Raw dump: {version['raw_dump_path']}\n"

        preview += "\nHistórico acumulado\n" + "-" * 70 + "\n"
        history_log = version.get("full_version_history_log")
        if history_log is None:
            history_log = version.get("version_history_log", version.get("sql_recipe", ""))
        preview += history_log
        return preview

    def _fetch_version_detail_thread(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        version_code: str,
        on_success=None,
        error_status: str = "Erro ao carregar detalhes da versão",
    ):
        try:
            detail = self.service.get_table_version_detail(
                database_name,
                schema_name,
                table_name,
                version_code,
            )

            def apply_detail():
                if not self.selected_version_record or self.selected_version_record["version_code"] != version_code:
                    return

                self.selected_version_record.update(detail)
                self.set_version_preview(self._build_version_preview_text(self.selected_version_record))
                if on_success:
                    on_success(self.selected_version_record)

            self.ui(apply_detail)
        except Exception as e:
            self.ui(self.set_status, error_status, "error")
            self.ui(self.show_error, "Error", str(e))

    def _fetch_version_history_thread(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        version_code: str,
    ):
        try:
            detail = self.service.get_table_version_detail(
                database_name,
                schema_name,
                table_name,
                version_code,
            )
            history_log = self.service.get_table_version_history_log(
                database_name,
                schema_name,
                table_name,
                version_code=version_code,
            )

            def apply_history():
                if not self.selected_version_record or self.selected_version_record["version_code"] != version_code:
                    return

                self.selected_version_record.update(detail)
                self.selected_version_record["full_version_history_log"] = history_log
                self.set_version_preview(self._build_version_preview_text(self.selected_version_record))
                self._show_version_history_dialog(self.selected_version_record)

            self.ui(apply_history)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar historico da versao", "error")
            self.ui(self.show_error, "Error", str(e))

    def _with_selected_version_detail(self, on_success, loading_message: str):
        if not self.current_db or not self.selected_version_record:
            self.show_error("Error", "Nenhuma versão selecionada.")
            return

        version = self.selected_version_record
        if self._version_has_full_detail(version):
            on_success(version)
            return

        self.set_status(loading_message, "loading")
        self.run_async(
            self._fetch_version_detail_thread,
            self.current_db,
            version["schema_name"],
            version["table_name"],
            version["version_code"],
            on_success,
        )

    def _show_version_history_dialog(self, version):
        dialog = VersionHistoryDialog(
            self,
            title=f"Histórico completo da versão {version['version_code']}",
            content=self._build_version_history_text(version),
        )
        self.wait_window(dialog)
        if self.selected_table_name:
            self.set_status(f"Tabela carregada: {self.selected_table_name}", "success")

    def open_selected_version_history_flow(self):
        if not self.current_db or not self.selected_version_record:
            self.show_error("Error", "Nenhuma versão selecionada.")
            return

        version_code = self.selected_version_record["version_code"]
        self.set_status(f"Carregando historico completo da versao {version_code}...", "loading")
        self.run_async(
            self._fetch_version_history_thread,
            self.current_db,
            self.selected_version_record["schema_name"],
            self.selected_version_record["table_name"],
            version_code,
        )

    def select_version(self, version, selected_button=None, trace_context: dict | None = None):
        started_at = time.perf_counter()
        self.selected_version_record = version
        self.version_history_button.configure(state="normal")
        restore_state = "disabled" if self._is_control_metadata_table(self.selected_table_name) else "normal"
        self.restore_version_button.configure(state=restore_state)
        self.restore_version_new_table_button.configure(state=restore_state)

        for btn, _item in self._version_button_map:
            if btn == selected_button:
                btn.configure(fg_color="#ea580c", hover_color="#c2410c")
            else:
                btn.configure(
                    fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"],
                    hover_color=ctk.ThemeManager.theme["CTkButton"]["hover_color"]
                )

        self.set_version_preview(self._build_version_preview_text(version), trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"select_version({version['version_code']}) finalizado em {(time.perf_counter() - started_at) * 1000:.1f} ms",
        )

    # ------------------------------------------------------------------
    # Diálogos auxiliares
    # ------------------------------------------------------------------

    def ask_admin_action(self, title: str):
        dialog = AdminActionDialog(self, title=title, workstation_name=socket.gethostname())
        self.wait_window(dialog)
        return dialog.result

    def ask_connection_info(self):
        dialog = ConnectionDialog(
            self,
            saved_credentials=load_saved_connection_credentials(),
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_final_delete_confirmation(
        self,
        object_label: str,
        execution_message: str | None = None,
        confirmation_text: str | None = None,
    ) -> bool:
        final_message = execution_message or "Essa acao criara um backup .dump e depois executara a exclusao."
        dialog = DangerConfirmDialog(
            self,
            title="Confirmacao final de exclusao",
            message=(
                f"Voce esta prestes a excluir permanentemente:\n\n{object_label}\n\n"
                f"{final_message}"
            ),
            confirmation_text=confirmation_text,
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_dump_delete_confirmation(self, dump_path: str, confirmation_text: str | None = None) -> bool:
        dialog = DangerConfirmDialog(
            self,
            title="Confirmacao final de exclusao",
            message=(
                "Voce esta prestes a excluir permanentemente o arquivo de recuperacao:\n\n"
                f"{dump_path}\n\n"
                "Essa acao removera o dump remoto e podera impedir futuras restauracoes que dependam dele."
            ),
            confirmation_text=confirmation_text,
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_version_info(self, title: str, version_label: str = "Titulo da versao"):
        dialog = VersionInfoDialog(self, title=title, version_label=version_label)
        self.wait_window(dialog)
        return dialog.result

    def ask_sql_execution_info(self, database_name: str, table_name: str):
        dialog = SqlExecutionDialog(self, database_name=database_name, table_name=table_name)
        self.wait_window(dialog)
        return dialog.result

    def ask_cross_table_expansion_info(
        self,
        database_name: str,
        source_table_name: str,
        available_tables,
        available_raw_schemas,
    ):
        dialog = SqlExpansionDialog(
            self,
            database_name=database_name,
            source_table_name=source_table_name,
            available_tables=available_tables,
            available_raw_schemas=available_raw_schemas,
        )
        self.wait_window(dialog)
        return dialog.result

    def show_sql_query_results(self, database_name: str, table_name: str, result: dict):
        dialog = SqlQueryResultDialog(
            self,
            database_name=database_name,
            table_name=table_name,
            headers=result.get("headers") or [],
            rows=result.get("rows") or [],
            message=str(result.get("message") or "").strip(),
            query_kind=str(result.get("query_kind") or "select"),
        )
        self.wait_window(dialog)

    def ask_raw_expand_type_mismatch_resolution(
        self,
        raw_key: str,
        child_column_label: str,
        selected_type: str,
        child_type: str,
        allow_child_type_conversion: bool = True,
    ):
        dialog = RawExpandTypeMismatchDialog(
            self,
            raw_key=raw_key,
            child_column_label=child_column_label,
            selected_type=selected_type,
            child_type=child_type,
            allow_child_type_conversion=allow_child_type_conversion,
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_sql_expression(
        self,
        title: str,
        database_name: str,
        table_name: str,
        target_type: str,
        context_label: str,
        initial_sql: str = "",
    ):
        dialog = SqlExpressionDialog(
            self,
            title=title,
            database_name=database_name,
            table_name=table_name,
            target_type=target_type,
            context_label=context_label,
            initial_sql=initial_sql,
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_dump_restore_info(
        self,
        restore_info: dict,
        default_target_full_table_name: str,
        conflict_message: str | None = None,
    ):
        dialog = DumpRestoreDialog(
            self,
            restore_info=restore_info,
            default_target_full_table_name=default_target_full_table_name,
            conflict_message=conflict_message,
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_restore_version_to_new_table_info(
        self,
        version: dict,
        default_target_full_table_name: str,
        conflict_message: str | None = None,
    ):
        summary_text = (
            f"Base: {self.current_db}\n"
            f"Tabela de origem: {version['schema_name']}.{version['table_name']}\n"
            f"Versao alvo: {version['version_code']} - {version['version_title']}\n"
            "A tabela original nao sera alterada. A restauracao sera criada em public."
        )
        dialog = DumpRestoreDialog(
            self,
            restore_info={
                "database_name": self.current_db or "",
                "schema_name": version["schema_name"],
                "table_name": version["table_name"],
                "version_code": version["version_code"],
                "version_title": version["version_title"],
            },
            default_target_full_table_name=default_target_full_table_name,
            conflict_message=conflict_message,
            dialog_title="Restaurar versao em nova tabela",
            heading_text="Restaurar versao em nova tabela",
            summary_text=summary_text,
            target_table_label="Nome da nova tabela em public",
            default_version_title=f"Restauracao da versao {version['version_code']} em nova tabela",
            confirm_button_text="Criar tabela restaurada",
        )
        self.wait_window(dialog)
        return dialog.result

    def ask_create_table_info(self):
        dialog = CreateTableDialog(self)
        self.wait_window(dialog)
        return dialog.result

    def ask_create_columns_info(self):
        dialog = CreateColumnsDialog(
            self,
            dictionary_lookup_callback=self.lookup_data_dictionary_entry,
        )
        self.wait_window(dialog)
        return dialog.result

    def lookup_data_dictionary_entry(self, search_term: str):
        return self.service.find_data_dictionary_entry(search_term)

    def _sync_data_dictionary_for_columns(
        self,
        database_name: str,
        table_name: str,
        columns,
        requested_by: str,
    ):
        normalized_columns = []
        for column_payload in columns or []:
            column_name = str((column_payload or {}).get("column_name") or "").strip()
            if not column_name or column_name in self.service.RAW_SYSTEM_COLUMNS:
                continue
            if not (column_payload or {}).get("dictionary"):
                continue
            normalized_columns.append(
                {
                    "column_name": column_name,
                    "column_type": str((column_payload or {}).get("column_type") or "").strip(),
                    "dictionary": dict((column_payload or {}).get("dictionary") or {}),
                }
            )

        if not normalized_columns:
            return

        self.service.sync_data_dictionary_entries(
            database_name=database_name,
            full_table_name=table_name,
            columns=normalized_columns,
            requested_by=requested_by,
        )

    def open_table_column_dictionary_link_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de vincular colunas ao data dictionary.")
            return

        table_name = self.selected_table_name
        dialog = LinkDataDictionaryDialog(
            self,
            column_name=column_name,
            column_type=self.table_column_types.get(column_name, "varchar"),
            dictionary_lookup_callback=self.lookup_data_dictionary_entry,
        )
        self.wait_window(dialog)

        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.set_status(f"Vinculando {column_name} ao data dictionary...", "loading")
        self.run_async(
            self._link_table_column_dictionary_thread,
            self.current_db,
            table_name,
            column_name,
            dict(dialog.result["dictionary"]),
            dialog.result["requested_by"],
            self._copy_table_preview_filters(),
        )

    def _link_table_column_dictionary_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        dictionary_payload: dict,
        requested_by: str,
        filters: dict,
    ):
        try:
            self.ui(self.set_status, f"Vinculando {column_name} ao data dictionary...", "loading")
            self.service.sync_data_dictionary_entries(
                database_name=database_name,
                full_table_name=table_name,
                columns=[
                    {
                        "column_name": column_name,
                        "column_type": self.table_column_types.get(column_name, "varchar"),
                        "dictionary": dictionary_payload,
                    }
                ],
                requested_by=requested_by,
            )
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            resolved_standard_name = (
                str(dictionary_payload.get("existing_standard_name") or "").strip()
                or str(dictionary_payload.get("standard_name") or "").strip()
            )
            self.ui(self._apply_table_preview_details, table_name, details)
            if resolved_standard_name:
                self.ui(
                    self.set_status,
                    f"Coluna vinculada ao data dictionary: {column_name} -> {resolved_standard_name}",
                    "success",
                )
            else:
                self.ui(
                    self.set_status,
                    f"Coluna vinculada ao data dictionary: {column_name}",
                    "success",
                )
        except Exception as e:
            self.ui(self.set_status, "Erro ao vincular coluna ao data dictionary", "error")
            self.ui(self.show_error, "Error", str(e))

    def open_data_dictionary_editor_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de gerenciar o data dictionary.")
            return
        if not self._is_data_dictionary_table():
            return

        if self.data_dictionary_entries_dialog and self.data_dictionary_entries_dialog.winfo_exists():
            self.data_dictionary_entries_dialog.focus()
            self.data_dictionary_entries_dialog.lift()
            self.refresh_data_dictionary_entries_dialog(self.data_dictionary_entries_dialog)
            return

        dialog = DataDictionaryEntriesDialog(
            self,
            [],
            on_delete=self.delete_data_dictionary_entry_flow,
            on_edit=self.edit_data_dictionary_entry_flow,
        )
        dialog.bind(
            "<Destroy>",
            lambda event, current=dialog: self._handle_data_dictionary_entries_dialog_destroy(event, current),
        )
        self.data_dictionary_entries_dialog = dialog
        self.refresh_data_dictionary_entries_dialog(dialog)

    def export_data_dictionary_pdf_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de gerar o PDF do data dictionary.")
            return
        if not self._is_data_dictionary_table():
            return

        version_number = self._next_data_dictionary_pdf_version()
        generated_at = datetime.now()
        output_path = self.ask_save_filename(
            title="Gerar PDF do data dictionary",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"data_dictionary_v{version_number}.pdf",
        )
        if not output_path:
            if self.selected_table_name:
                self.set_status(f"Tabela carregada: {self.selected_table_name}", "success")
            return

        self.set_table_action_buttons_busy(True)
        self.set_status("Gerando PDF do data dictionary...", "loading")
        self.run_async(
            self._export_data_dictionary_pdf_thread,
            output_path,
            version_number,
            generated_at,
        )

    def _export_data_dictionary_pdf_thread(
        self,
        output_path: str,
        version_number: str,
        generated_at: datetime,
    ):
        try:
            entries = self.service.list_data_dictionary_entries()
            build_data_dictionary_pdf(
                output_path=output_path,
                entries=entries,
                version_number=version_number,
                generated_at=generated_at,
                source_label=f"{CONTROL_SCHEMA}.{self.DATA_DICTIONARY_TABLE}",
            )
            file_name = os.path.basename(output_path)
            self.ui(
                self.set_status,
                f"PDF gerado: {file_name} | versao {version_number}",
                "success",
            )
        except Exception as e:
            self.ui(self.set_status, "Erro ao gerar PDF do data dictionary", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_table_action_buttons_busy, False)

    def _handle_data_dictionary_entries_dialog_destroy(self, event, dialog):
        if event.widget == dialog and self.data_dictionary_entries_dialog == dialog:
            self.data_dictionary_entries_dialog = None

    def refresh_data_dictionary_entries_dialog(self, dialog=None):
        target_dialog = dialog or self.data_dictionary_entries_dialog
        if not target_dialog or not target_dialog.winfo_exists():
            return

        target_dialog.set_busy(True, "Carregando entradas do data dictionary...")
        self.run_async(self._load_data_dictionary_entries_thread, target_dialog)

    def _load_data_dictionary_entries_thread(self, dialog):
        try:
            entries = self.service.list_data_dictionary_entries()

            def apply_entries():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_entries(entries)
                dialog.set_busy(False)
                dialog.set_status(f"{len(entries)} entrada(s) encontradas.")

            self.ui(apply_entries)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao carregar entradas do data dictionary.")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def delete_data_dictionary_entry_flow(self, dialog, entry: dict):
        standard_name = str((entry or {}).get("standard_name") or "").strip()
        if not dialog or not dialog.winfo_exists() or not standard_name:
            return

        if not self.ask_yes_no(
            "Excluir entrada do data dictionary",
            f'Deseja excluir a entrada "{standard_name}" do data dictionary?',
        ):
            return

        dialog.set_busy(True, f'Excluindo entrada "{standard_name}"...')
        self.run_async(self._delete_data_dictionary_entry_thread, dialog, standard_name)

    def _delete_data_dictionary_entry_thread(self, dialog, standard_name: str):
        try:
            deleted_entry = self.service.delete_data_dictionary_entry(standard_name)
            entries = self.service.list_data_dictionary_entries()

            def apply_success():
                if dialog and dialog.winfo_exists():
                    dialog.set_entries(entries)
                    dialog.set_busy(False)
                    dialog.set_status(f'Entrada excluida: {deleted_entry["standard_name"]}')
                if self.current_db == CONTROL_DB and self._is_data_dictionary_table():
                    self.load_table_details(self.selected_table_name)
                self.set_status(
                    f'Data dictionary atualizado: {deleted_entry["standard_name"]}',
                    "success",
                )

            self.ui(apply_success)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao excluir entrada do data dictionary.")
                self.set_status("Erro ao excluir entrada do data dictionary", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def edit_data_dictionary_entry_flow(self, dialog, entry: dict):
        standard_name = str((entry or {}).get("standard_name") or "").strip()
        if not dialog or not dialog.winfo_exists() or not standard_name:
            return

        dialog.set_busy(True, f'Carregando entrada "{standard_name}" para edicao...')
        self.run_async(self._prepare_data_dictionary_entry_edit_thread, dialog, standard_name)

    def _prepare_data_dictionary_entry_edit_thread(self, dialog, standard_name: str):
        try:
            context = self.service.get_data_dictionary_entry_edit_context(standard_name)

            def present_dialog():
                if not dialog or not dialog.winfo_exists():
                    return

                dialog.set_busy(False)
                edit_dialog = EditDataDictionaryEntryDialog(
                    self,
                    entry=context["entry"],
                    inferred_data_type=context.get("effective_data_type") or "",
                    detected_usage_types=context.get("detected_usage_types") or [],
                    stale_usage_count=int(context.get("stale_usage_count") or 0),
                )
                self.wait_window(edit_dialog)
                if not edit_dialog.result:
                    dialog.set_status(f"{len(dialog._entries)} entrada(s) encontradas.")
                    return

                dialog.set_busy(True, f'Atualizando entrada "{standard_name}"...')
                self.run_async(
                    self._update_data_dictionary_entry_thread,
                    dialog,
                    standard_name,
                    dict(edit_dialog.result),
                )

            self.ui(present_dialog)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao carregar entrada para edicao.")
                self.set_status("Erro ao preparar edicao do data dictionary", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def _update_data_dictionary_entry_thread(self, dialog, standard_name: str, edit_request: dict):
        try:
            requested_by = str(edit_request.get("requested_by") or "").strip()
            dictionary_payload = dict(edit_request.get("dictionary") or {})
            result = self.service.update_data_dictionary_entry(
                existing_standard_name=standard_name,
                dictionary_payload=dictionary_payload,
                requested_by=requested_by,
            )
            entries = self.service.list_data_dictionary_entries()

            def apply_success():
                if dialog and dialog.winfo_exists():
                    dialog.set_entries(entries)
                    dialog.set_busy(False)
                    dialog.set_status(
                        f'Entrada atualizada: {result["standard_name"]} | usos afetados: {result["usage_count"]}'
                    )

                for affected_table in result.get("affected_tables") or []:
                    self.facet_service.clear_table(affected_table["database_name"], affected_table["full_table_name"])

                if self.current_db == CONTROL_DB and self.selected_table_name and (
                    self._is_data_dictionary_table() or self._is_data_dictionary_usage_table()
                ):
                    self.load_table_details(self.selected_table_name)
                elif self.current_db and self.selected_table_name:
                    for affected_table in result.get("affected_tables") or []:
                        if (
                            affected_table["database_name"] == self.current_db
                            and affected_table["full_table_name"] == self.selected_table_name
                        ):
                            self.load_table_details(self.selected_table_name)
                            break

                self.set_status(
                    (
                        f'Data dictionary atualizado: {result["standard_name"]}'
                        + (
                            f' | colunas convertidas: {result["altered_column_count"]}'
                            if int(result.get("altered_column_count") or 0)
                            else ""
                        )
                    ),
                    "success",
                )

            self.ui(apply_success)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao atualizar entrada do data dictionary.")
                self.set_status("Erro ao atualizar entrada do data dictionary", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def open_data_dictionary_usage_editor_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de gerenciar o data dictionary usage.")
            return
        if not self._is_data_dictionary_usage_table():
            return

        if self.data_dictionary_usage_entries_dialog and self.data_dictionary_usage_entries_dialog.winfo_exists():
            self.data_dictionary_usage_entries_dialog.focus()
            self.data_dictionary_usage_entries_dialog.lift()
            self.refresh_data_dictionary_usage_entries_dialog(self.data_dictionary_usage_entries_dialog)
            return

        dialog = DataDictionaryUsageEntriesDialog(
            self,
            [],
            on_edit=self.edit_data_dictionary_usage_entry_flow,
        )
        dialog.bind(
            "<Destroy>",
            lambda event, current=dialog: self._handle_data_dictionary_usage_entries_dialog_destroy(event, current),
        )
        self.data_dictionary_usage_entries_dialog = dialog
        self.refresh_data_dictionary_usage_entries_dialog(dialog)

    def _handle_data_dictionary_usage_entries_dialog_destroy(self, event, dialog):
        if event.widget == dialog and self.data_dictionary_usage_entries_dialog == dialog:
            self.data_dictionary_usage_entries_dialog = None

    def refresh_data_dictionary_usage_entries_dialog(self, dialog=None):
        target_dialog = dialog or self.data_dictionary_usage_entries_dialog
        if not target_dialog or not target_dialog.winfo_exists():
            return

        target_dialog.set_busy(True, "Carregando entradas do data dictionary usage...")
        self.run_async(self._load_data_dictionary_usage_entries_thread, target_dialog)

    def _load_data_dictionary_usage_entries_thread(self, dialog):
        try:
            entries = self.service.list_data_dictionary_usage_entries()

            def apply_entries():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_entries(entries)
                dialog.set_busy(False)
                dialog.set_status(f"{len(entries)} entrada(s) encontradas.")

            self.ui(apply_entries)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao carregar entradas do data dictionary usage.")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def edit_data_dictionary_usage_entry_flow(self, dialog, entry: dict):
        if not dialog or not dialog.winfo_exists() or not entry:
            return

        database_name = str((entry or {}).get("database_name") or "").strip()
        schema_name = str((entry or {}).get("schema_name") or "").strip()
        table_name = str((entry or {}).get("table_name") or "").strip()
        column_name = str((entry or {}).get("column_name") or "").strip()
        if not (database_name and schema_name and table_name and column_name):
            return

        edit_dialog = EditDataDictionaryUsageEntryDialog(self, entry=entry)
        self.wait_window(edit_dialog)
        if not edit_dialog.result:
            dialog.set_status(f"{len(dialog._entries)} entrada(s) encontradas.")
            return

        dialog.set_busy(True, f"Atualizando usage: {schema_name}.{table_name}.{column_name}...")
        self.run_async(
            self._update_data_dictionary_usage_entry_thread,
            dialog,
            dict(entry),
            dict(edit_dialog.result),
        )

    def _update_data_dictionary_usage_entry_thread(self, dialog, entry: dict, edit_request: dict):
        try:
            result = self.service.update_data_dictionary_usage_entry(
                database_name=str((entry or {}).get("database_name") or "").strip(),
                schema_name=str((entry or {}).get("schema_name") or "").strip(),
                table_name=str((entry or {}).get("table_name") or "").strip(),
                current_column_name=str((entry or {}).get("column_name") or "").strip(),
                target_column_name=str((edit_request or {}).get("column_name") or "").strip(),
                requested_by=str((edit_request or {}).get("requested_by") or "").strip(),
            )
            entries = self.service.list_data_dictionary_usage_entries()

            def apply_success():
                if dialog and dialog.winfo_exists():
                    dialog.set_entries(entries)
                    dialog.set_busy(False)
                    dialog.set_status(
                        f'Usage atualizado: {result["previous_column_name"]} -> {result["column_name"]}'
                    )

                self.facet_service.clear_table(result["database_name"], result["full_table_name"])

                if self.current_db == CONTROL_DB and self.selected_table_name and (
                    self._is_data_dictionary_table() or self._is_data_dictionary_usage_table()
                ):
                    self.load_table_details(self.selected_table_name)
                elif (
                    self.current_db == result["database_name"]
                    and self.selected_table_name == result["full_table_name"]
                ):
                    self.load_table_details(self.selected_table_name)

                self.set_status(
                    (
                        f'Data dictionary usage atualizado: {result["standard_name"]}'
                        f' | coluna: {result["previous_column_name"]} -> {result["column_name"]}'
                    ),
                    "success",
                )

            self.ui(apply_success)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao atualizar entrada do data dictionary usage.")
                self.set_status("Erro ao atualizar data dictionary usage", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def refresh_data_dictionary_usage_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de atualizar o data dictionary usage.")
            return
        if not self.current_db or not self.selected_table_name or not self._is_data_dictionary_usage_table():
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        filters = self._copy_table_preview_filters()
        self.set_table_action_buttons_busy(True)
        self.set_status("Atualizando data dictionary usage...", "loading")
        self.run_async(
            self._refresh_data_dictionary_usage_thread,
            database_name,
            table_name,
            filters,
        )

    def _refresh_data_dictionary_usage_thread(self, database_name: str, table_name: str, filters: dict):
        try:
            refresh_result = self.service.reconcile_data_dictionary_usage()
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            if refresh_result["removed_rows"]:
                status_message = (
                    f"Usage atualizado: {refresh_result['removed_rows']} vinculo(s) orfao(s) removido(s)."
                )
            else:
                status_message = "Usage atualizado: nenhum vinculo orfao encontrado."

            def apply_refresh():
                self.set_table_action_buttons_busy(False)
                if self.current_db == database_name and self.selected_table_name == table_name:
                    self._apply_table_details(table_name, details, versions)
                self.set_status(status_message, "success")

            self.ui(apply_refresh)
        except Exception as e:
            def apply_error():
                self.set_table_action_buttons_busy(False)
                self.set_status("Erro ao atualizar data dictionary usage", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def ask_raw_import_review(
        self,
        file_name: str,
        total_records: int,
        review_candidates: dict,
        parent=None,
        preview_count: int | None = None,
        record_refs=None,
        sheet_context=None,
        require_header_per_sheet: bool = False,
    ):
        dialog_parent = parent or self
        dialog = RawImportReviewDialog(
            dialog_parent,
            file_name=file_name,
            total_records=total_records,
            header_candidates=review_candidates.get("header_candidates", []),
            noise_candidates=review_candidates.get("noise_candidates", []),
            preview_count=preview_count,
            record_refs=record_refs,
            sheet_context=sheet_context,
            require_header_per_sheet=require_header_per_sheet,
        )
        dialog.wait_window()
        return dialog.result

    def ask_excel_sheet_selection(self, file_name: str, sheet_names):
        dialog = ExcelSheetSelectionDialog(self, file_name=file_name, sheet_names=sheet_names)
        self.wait_window(dialog)
        return dialog.result

    def ask_raw_source_selection(self):
        dialog = RawSourceSelectionDialog(self)
        self.wait_window(dialog)
        return dialog.result

    def show_json_structure_dialog(self, file_name: str, json_data):
        dialog = JsonStructureDialog(self, file_name=file_name, json_data=json_data)
        self.wait_window(dialog)
        return dialog.result

    def ask_create_database_user(self, parent=None):
        dialog_parent = parent or self
        parent_grab_released = False

        if parent is not None:
            try:
                if parent.winfo_exists():
                    parent.grab_release()
                    parent_grab_released = True
            except Exception:
                parent_grab_released = False

        dialog = CreateDatabaseUserDialog(dialog_parent)
        self.wait_window(dialog)
        result = dialog.result

        if parent_grab_released:
            try:
                if parent.winfo_exists():
                    parent.grab_set()
                    parent.focus_force()
            except Exception:
                pass

        return result

    def ask_rename_database_user(self, current_user_name: str, parent=None):
        dialog_parent = parent or self
        parent_grab_released = False

        if parent is not None:
            try:
                if parent.winfo_exists():
                    parent.grab_release()
                    parent_grab_released = True
            except Exception:
                parent_grab_released = False

        dialog = RenameDatabaseUserDialog(dialog_parent, current_user_name)
        self.wait_window(dialog)
        result = dialog.result

        if parent_grab_released:
            try:
                if parent.winfo_exists():
                    parent.grab_set()
                    parent.focus_force()
            except Exception:
                pass

        return result

    def ask_root_password(self, parent=None):
        dialog_parent = parent or self
        parent_grab_released = False

        if parent is not None:
            try:
                if parent.winfo_exists():
                    parent.grab_release()
                    parent_grab_released = True
            except Exception:
                parent_grab_released = False

        dialog = RootPasswordDialog(dialog_parent)
        self.wait_window(dialog)
        result = dialog.result

        if parent_grab_released:
            try:
                if parent.winfo_exists():
                    parent.grab_set()
                    parent.focus_force()
            except Exception:
                pass

        return result

    @staticmethod
    def _format_database_operation_label(session: dict) -> str:
        pid = int(session.get("pid") or 0)
        database_name = str(session.get("database_name") or "").strip() or "(sem base)"
        user_name = str(session.get("user_name") or "").strip() or "(sem usuario)"
        return f"PID {pid} | {database_name} | {user_name}"

    def open_database_operations_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de consultar operacoes do PostgreSQL.")
            return

        if self.database_operations_dialog and self.database_operations_dialog.winfo_exists():
            self.database_operations_dialog.focus()
            self.database_operations_dialog.lift()
            self.refresh_database_operations_dialog(
                self.database_operations_dialog,
                "Atualizando operacoes do PostgreSQL...",
            )
            return

        self.database_operations_button.configure(state="disabled")
        self.set_status("Carregando operacoes do PostgreSQL...", "loading")
        self.run_async(self._load_database_operations_thread)

    def _load_database_operations_thread(self):
        try:
            snapshot = self.service.list_database_operations()

            def show_dialog():
                self.database_operations_button.configure(state="normal")
                self.set_status("Operacoes do PostgreSQL carregadas", "success")
                dialog = DatabaseOperationsDialog(
                    self,
                    sessions=snapshot.get("sessions") or [],
                    summary=snapshot.get("summary") or {},
                    collected_at=str(snapshot.get("collected_at") or ""),
                    on_refresh=self.refresh_database_operations_dialog,
                    on_cancel_query=self.cancel_database_operation_query_flow,
                    on_terminate_session=self.terminate_database_session_flow,
                )
                dialog.bind(
                    "<Destroy>",
                    lambda event, current=dialog: self._handle_database_operations_dialog_destroy(event, current),
                )
                self.database_operations_dialog = dialog

            self.ui(show_dialog)
        except Exception as e:
            self.ui(self.database_operations_button.configure, state="normal")
            self.ui(self.set_status, "Erro ao carregar operacoes do PostgreSQL", "error")
            self.ui(self.show_error, "Error", str(e))

    def _handle_database_operations_dialog_destroy(self, event, dialog):
        if event.widget == dialog and self.database_operations_dialog == dialog:
            self.database_operations_dialog = None

    def refresh_database_operations_dialog(self, dialog=None, message: str | None = None):
        target_dialog = dialog or self.database_operations_dialog
        try:
            if not target_dialog or not target_dialog.winfo_exists():
                return
        except Exception:
            return

        target_dialog.set_busy(True, message or "Atualizando operacoes do PostgreSQL...")
        self.run_async(self._refresh_database_operations_dialog_thread, target_dialog)

    def _refresh_database_operations_dialog_thread(self, dialog):
        try:
            snapshot = self.service.list_database_operations()

            def apply_snapshot():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_snapshot(
                    sessions=snapshot.get("sessions") or [],
                    summary=snapshot.get("summary") or {},
                    collected_at=str(snapshot.get("collected_at") or ""),
                    message="Selecione uma sessao para ver detalhes, cancelar a query ou encerrar a conexao.",
                )
                dialog.set_busy(False)
                self.set_status("Operacoes do PostgreSQL atualizadas", "success")

            self.ui(apply_snapshot)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao atualizar operacoes do PostgreSQL.")
                self.set_status("Erro ao atualizar operacoes do PostgreSQL", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def cancel_database_operation_query_flow(self, dialog, session: dict):
        session_label = self._format_database_operation_label(session)
        if not messagebox.askyesno(
            "Cancelar query",
            f"Deseja cancelar a query desta sessao?\n\n{session_label}",
            parent=dialog,
        ):
            return

        dialog.set_busy(True, f"Cancelando query de {session_label}...")
        self.run_async(self._cancel_database_operation_query_thread, dialog, session)

    def _cancel_database_operation_query_thread(self, dialog, session: dict, root_password: str | None = None):
        pid = int(session.get("pid") or 0)
        session_label = self._format_database_operation_label(session)
        try:
            cancelled = self.service.cancel_backend_query(pid, root_password=root_password)
            if not cancelled:
                raise RuntimeError(
                    "O PostgreSQL nao confirmou o cancelamento. A query pode ter terminado antes da solicitacao."
                )
            self.ui(self.set_status, f"Query cancelada: {session_label}", "success")
            self.ui(
                self.refresh_database_operations_dialog,
                dialog,
                f"Atualizando operacoes apos cancelar {session_label}...",
            )
        except Exception as exc:
            if root_password or not self.service.is_permission_denied_error(exc):
                def apply_error():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Erro ao cancelar query.")
                    self.set_status("Erro ao cancelar query do PostgreSQL", "error")
                    self.show_error("Error", str(exc))

                self.ui(apply_error)
                return

            root_password = self.ui_sync(self.ask_root_password, dialog)
            if not root_password:
                def apply_cancel():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Cancelamento da query abortado.")
                    self.set_status("Cancelamento da query abortado", "error")

                self.ui(apply_cancel)
                return

            self._cancel_database_operation_query_thread(dialog, session, root_password=root_password)

    def terminate_database_session_flow(self, dialog, session: dict):
        session_label = self._format_database_operation_label(session)
        if not messagebox.askyesno(
            "Encerrar sessao",
            f"Deseja encerrar esta sessao do PostgreSQL?\n\n{session_label}",
            parent=dialog,
        ):
            return

        dialog.set_busy(True, f"Encerrando sessao {session_label}...")
        self.run_async(self._terminate_database_session_thread, dialog, session)

    def _terminate_database_session_thread(self, dialog, session: dict, root_password: str | None = None):
        pid = int(session.get("pid") or 0)
        session_label = self._format_database_operation_label(session)
        try:
            terminated = self.service.terminate_backend_session(pid, root_password=root_password)
            if not terminated:
                raise RuntimeError(
                    "O PostgreSQL nao confirmou o encerramento. A sessao pode ter sido finalizada antes da solicitacao."
                )
            self.ui(self.set_status, f"Sessao encerrada: {session_label}", "success")
            self.ui(
                self.refresh_database_operations_dialog,
                dialog,
                f"Atualizando operacoes apos encerrar {session_label}...",
            )
        except Exception as exc:
            if root_password or not self.service.is_permission_denied_error(exc):
                def apply_error():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Erro ao encerrar sessao.")
                    self.set_status("Erro ao encerrar sessao do PostgreSQL", "error")
                    self.show_error("Error", str(exc))

                self.ui(apply_error)
                return

            root_password = self.ui_sync(self.ask_root_password, dialog)
            if not root_password:
                def apply_cancel():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Encerramento da sessao abortado.")
                    self.set_status("Encerramento da sessao abortado", "error")

                self.ui(apply_cancel)
                return

            self._terminate_database_session_thread(dialog, session, root_password=root_password)

    def open_users_permissions_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de gerenciar usuarios.")
            return

        self.users_permissions_button.configure(state="disabled")
        self.set_status("Carregando usuarios do PostgreSQL...", "loading")
        self.run_async(self._load_database_users_thread)

    def _load_database_users_thread(self):
        try:
            users = self.service.list_database_users()

            def show_dialog():
                self.users_permissions_button.configure(state="normal")
                self.set_status("Usuarios carregados", "success")
                dialog = UserSelectionDialog(
                    self,
                    users,
                    on_select=self.open_role_permissions_flow,
                    on_create=self.create_database_user_flow,
                    on_edit=self.rename_database_user_flow,
                    on_delete=self.delete_database_user_flow,
                )
                dialog.bind("<Destroy>", lambda event, current=dialog: self._handle_user_dialog_destroy(event, current))
                self.user_selection_dialog = dialog

            self.ui(show_dialog)
        except Exception as e:
            self.ui(self.users_permissions_button.configure, state="normal")
            self.ui(self.set_status, "Erro ao carregar usuarios", "error")
            self.ui(self.show_error, "Error", str(e))

    def _handle_user_dialog_destroy(self, event, dialog):
        if event.widget == dialog and self.user_selection_dialog == dialog:
            self.user_selection_dialog = None

    def refresh_user_selection_dialog(self, dialog, message: str | None = None):
        try:
            if not dialog or not dialog.winfo_exists():
                return
        except Exception:
            return

        if message:
            dialog.set_busy(True, message)
        self.run_async(self._refresh_user_selection_dialog_thread, dialog)

    def _refresh_user_selection_dialog_thread(self, dialog):
        try:
            users = self.service.list_database_users()

            def apply_users():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_users(users)
                dialog.set_busy(False, "Selecione um usuario para gerenciar permissoes.")

            self.ui(apply_users)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao atualizar usuarios.")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def create_database_user_flow(self, dialog):
        user_info = self.ask_create_database_user(parent=dialog)
        if not user_info:
            return

        dialog.set_busy(True, f"Criando usuario {user_info['user_name']}...")
        self.run_async(self._create_database_user_thread, dialog, user_info)

    def _create_database_user_thread(self, dialog, user_info: dict, root_password: str | None = None):
        user_name = user_info["user_name"]
        try:
            self.service.create_database_user(
                user_name,
                user_info["password"],
                root_password=root_password,
            )
            self.ui(self.set_status, f"Usuario criado: {user_name}", "success")
            self.ui(self.refresh_user_selection_dialog, dialog, f"Atualizando usuarios apos criar {user_name}...")
        except Exception as exc:
            if root_password or not self.service.is_permission_denied_error(exc):
                def apply_error():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Erro ao criar usuario.")
                    self.set_status("Erro ao criar usuario", "error")
                    self.show_error("Error", str(exc))

                self.ui(apply_error)
                return

            root_password = self.ui_sync(self.ask_root_password, dialog)
            if not root_password:
                def apply_cancel():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Criacao cancelada.")
                    self.set_status("Criacao de usuario cancelada", "error")

                self.ui(apply_cancel)
                return

            self._create_database_user_thread(dialog, user_info, root_password=root_password)

    def rename_database_user_flow(self, dialog, current_user_name: str):
        new_user_name = self.ask_rename_database_user(current_user_name, parent=dialog)
        if not new_user_name or new_user_name == current_user_name:
            return

        dialog.set_busy(True, f"Renomeando usuario {current_user_name}...")
        self.run_async(self._rename_database_user_thread, dialog, current_user_name, new_user_name)

    def _rename_database_user_thread(
        self,
        dialog,
        current_user_name: str,
        new_user_name: str,
        root_password: str | None = None,
    ):
        try:
            self.service.rename_database_user(
                current_user_name,
                new_user_name,
                root_password=root_password,
            )
            self.ui(self.set_status, f"Usuario renomeado: {new_user_name}", "success")
            self.ui(
                self.refresh_user_selection_dialog,
                dialog,
                f"Atualizando usuarios apos renomear {current_user_name}...",
            )
        except Exception as exc:
            if root_password or not self.service.is_permission_denied_error(exc):
                def apply_error():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Erro ao renomear usuario.")
                    self.set_status("Erro ao renomear usuario", "error")
                    self.show_error("Error", str(exc))

                self.ui(apply_error)
                return

            root_password = self.ui_sync(self.ask_root_password, dialog)
            if not root_password:
                def apply_cancel():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Edicao cancelada.")
                    self.set_status("Edicao de usuario cancelada", "error")

                self.ui(apply_cancel)
                return

            self._rename_database_user_thread(
                dialog,
                current_user_name,
                new_user_name,
                root_password=root_password,
            )

    def delete_database_user_flow(self, dialog, user_name: str):
        if not messagebox.askyesno(
            "Excluir usuario",
            f"Deseja excluir o usuario {user_name}?",
            parent=dialog,
        ):
            return

        dialog.set_busy(True, f"Excluindo usuario {user_name}...")
        self.run_async(self._delete_database_user_thread, dialog, user_name)

    def _delete_database_user_thread(self, dialog, user_name: str, root_password: str | None = None):
        try:
            self.service.delete_database_user(user_name, root_password=root_password)
            self.ui(self.set_status, f"Usuario excluido: {user_name}", "success")
            self.ui(self.refresh_user_selection_dialog, dialog, f"Atualizando usuarios apos excluir {user_name}...")
        except Exception as exc:
            if root_password or not self.service.is_permission_denied_error(exc):
                def apply_error():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Erro ao excluir usuario.")
                    self.set_status("Erro ao excluir usuario", "error")
                    self.show_error("Error", str(exc))

                self.ui(apply_error)
                return

            root_password = self.ui_sync(self.ask_root_password, dialog)
            if not root_password:
                def apply_cancel():
                    if dialog and dialog.winfo_exists():
                        dialog.set_busy(False, "Exclusao cancelada.")
                    self.set_status("Exclusao de usuario cancelada", "error")

                self.ui(apply_cancel)
                return

            self._delete_database_user_thread(dialog, user_name, root_password=root_password)

    def open_role_permissions_flow(self, role_name: str):
        self.set_status(f"Carregando permissoes de {role_name}...", "loading")
        self.run_async(self._load_role_permissions_thread, role_name)

    def _load_role_permissions_thread(self, role_name: str):
        try:
            permission_state = self.service.get_role_permission_state(role_name)

            def show_dialog():
                self.set_status(f"Permissoes carregadas: {role_name}", "success")
                RolePermissionsDialog(
                    self,
                    role_name=role_name,
                    permissions=permission_state["permissions"],
                    assigned_permission_keys=permission_state["assigned_permission_keys"],
                    on_grant=lambda dialog, keys, current_role=role_name: self.apply_role_permission_change_async(
                        dialog,
                        current_role,
                        grant_permission_keys=keys,
                    ),
                    on_revoke=lambda dialog, keys, current_role=role_name: self.apply_role_permission_change_async(
                        dialog,
                        current_role,
                        revoke_permission_keys=keys,
                    ),
                )

            self.ui(show_dialog)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar permissoes", "error")
            self.ui(self.show_error, "Error", str(e))

    def apply_role_permission_change_async(
        self,
        dialog: RolePermissionsDialog,
        role_name: str,
        grant_permission_keys=None,
        revoke_permission_keys=None,
    ):
        grant_keys = list(grant_permission_keys or [])
        revoke_keys = list(revoke_permission_keys or [])
        self.run_async(
            self._apply_role_permission_change_thread,
            dialog,
            role_name,
            grant_keys,
            revoke_keys,
        )

    def _restore_role_permission_dialog_grab(self, dialog: RolePermissionsDialog):
        try:
            if dialog.winfo_exists():
                dialog.grab_set()
                dialog.focus_force()
        except Exception:
            pass

    def _finish_role_permission_success(
        self,
        dialog: RolePermissionsDialog,
        role_name: str,
        grant_keys,
        revoke_keys,
        used_root: bool = False,
    ):
        suffix = " com root" if used_root else ""
        self.set_status(f"Permissoes atualizadas{suffix}: {role_name}", "success")
        if grant_keys:
            dialog.complete_grant(grant_keys)
        if revoke_keys:
            dialog.complete_revoke(revoke_keys)

    def _finish_role_permission_failure(
        self,
        dialog: RolePermissionsDialog,
        status_text: str,
        dialog_text: str,
        error_message: str | None = None,
    ):
        self.set_status(status_text, "error")
        dialog.fail_permission_change(dialog_text)
        if error_message:
            try:
                if dialog.winfo_exists():
                    messagebox.showerror("Error", error_message, parent=dialog)
                    return
            except Exception:
                pass
            self.show_error("Error", error_message)

    def _request_root_password_for_role_permission(
        self,
        dialog: RolePermissionsDialog,
        role_name: str,
        grant_keys,
        revoke_keys,
    ):
        try:
            if not dialog.winfo_exists():
                return
        except Exception:
            return

        def handle_root_password(root_password):
            if not root_password:
                self._finish_role_permission_failure(
                    dialog,
                    "Alteracao de permissoes cancelada",
                    "Alteracao cancelada.",
                )
                return

            dialog.set_permission_change_message("Aplicando permissoes com root...")
            self.run_async(
                self._apply_role_permission_change_with_root_thread,
                dialog,
                role_name,
                grant_keys,
                revoke_keys,
                root_password,
            )

        dialog.request_root_password(handle_root_password)

    def _apply_role_permission_change_with_root_thread(
        self,
        dialog: RolePermissionsDialog,
        role_name: str,
        grant_keys,
        revoke_keys,
        root_password: str,
    ):
        try:
            self.service.apply_role_permission_changes(
                role_name,
                grant_permission_keys=grant_keys,
                revoke_permission_keys=revoke_keys,
                root_password=root_password,
            )
            self.ui(
                self._finish_role_permission_success,
                dialog,
                role_name,
                grant_keys,
                revoke_keys,
                True,
            )
        except Exception as root_exc:
            self.ui(
                self._finish_role_permission_failure,
                dialog,
                "Erro ao alterar permissoes com root",
                "Alteracao nao aplicada.",
                str(root_exc),
            )

    def _apply_role_permission_change_thread(
        self,
        dialog: RolePermissionsDialog,
        role_name: str,
        grant_keys,
        revoke_keys,
    ):
        try:
            self.service.apply_role_permission_changes(
                role_name,
                grant_permission_keys=grant_keys,
                revoke_permission_keys=revoke_keys,
            )
            self.ui(
                self._finish_role_permission_success,
                dialog,
                role_name,
                grant_keys,
                revoke_keys,
                False,
            )
            return
        except Exception as exc:
            is_permission_denied = self.service.is_permission_denied_error(exc)
            if not is_permission_denied:
                self.ui(
                    self._finish_role_permission_failure,
                    dialog,
                    "Erro ao alterar permissoes",
                    "Alteracao nao aplicada.",
                    str(exc),
                )
                return

            self.ui(
                self._request_root_password_for_role_permission,
                dialog,
                role_name,
                grant_keys,
                revoke_keys,
            )

    # ------------------------------------------------------------------
    # Fluxo de conexão
    # ------------------------------------------------------------------

    def connect_vm(self):
        connection_info = self.ask_connection_info()
        if not connection_info:
            return

        self.run_async(self._connect_vm_thread, connection_info)

    def _connect_vm_thread(self, connection_info: dict):
        self.ui(self.connect_button.configure, state="disabled")
        self.ui(self.set_status, "Connecting...", "loading")

        try:
            self.service.connect(
                host=connection_info["ssh_host"],
                ssh_port=connection_info["ssh_port"],
                ssh_username=connection_info["ssh_username"],
                ssh_password=connection_info["ssh_password"],
                postgres_port=connection_info["postgres_port"],
                sql_username=connection_info["sql_username"],
                sql_password=connection_info["sql_password"],
            )
            control_database_created = self.service.ensure_control_database()
            self.service.ensure_remote_admin_dirs()
            self.service.ensure_admin_schema()
            self.can_create_db = self.service.check_can_create_database()

            dbs = self.service.list_databases()
            storage_error = None

            try:
                if connection_info.get("remember_credentials"):
                    save_connection_credentials(connection_info)
                else:
                    clear_saved_connection_credentials()
            except Exception as exc:
                storage_error = str(exc)

            def after_connect():
                self.add_db_button.configure(state="normal" if self.can_create_db else "disabled")
                self.dump_manager_button.configure(state="normal")
                self.users_permissions_button.configure(state="normal")
                self.database_operations_button.configure(state="normal")
                self.populate_database_buttons(dbs)

            self.ui(after_connect)
            self.ui(
                self.set_status,
                (
                    f"Connected to {self.service.ssh_username}@{self.service.ssh_host} "
                    f"| PostgreSQL: {self.service.sql_username}"
                ),
                "success",
            )

            if not self.can_create_db:
                self.ui(
                    self.show_warning,
                    "Database creation permission",
                    (
                        f"The connection succeeded, but user {self.service.sql_username} "
                        "does not have the CREATEDB permission.\n\n"
                        "To enable database creation, run this once on the server:\n"
                        f"ALTER ROLE {self.service.sql_username} CREATEDB;"
                    ),
                )
            if control_database_created:
                self.ui(
                    self.show_info,
                    "Control database created",
                    (
                        f"Database {CONTROL_DB} was created successfully.\n"
                        f"Schema {CONTROL_SCHEMA} and the internal tables are ready."
                    ),
                )
            if storage_error:
                self.ui(
                    self.show_warning,
                    "Credentials not saved",
                    f"The connection succeeded, but the credentials could not be saved: {storage_error}",
                )

        except Exception as e:
            self.ui(self.set_status, "Connection failed", "error")
            self.ui(
                self.show_error,
                "Connection error",
                (
                    f"{e}\n\nRequired control database: {CONTROL_DB}\n"
                    f"The application creates schema {CONTROL_SCHEMA} automatically. "
                    "See README.md for the one-time setup."
                ),
            )
        finally:
            self.ui(self.connect_button.configure, state="normal")

    # ------------------------------------------------------------------
    # Navegação de bases e tabelas
    # ------------------------------------------------------------------

    def load_tables(self, db_name: str):
        self.current_db = db_name
        self.add_table_button.configure(state="normal")
        self.clear_table_selection()
        self.run_async(self._load_tables_thread, db_name)

    def _load_tables_thread(self, db_name: str):
        try:
            self.ui(self.set_status, f"Loading tables from {db_name}...", "loading")
            tables = self.service.list_tables(db_name)
            self.ui(self.populate_table_buttons, tables)
            self.ui(self.set_status, f"Tables loaded: {db_name}", "success")
        except Exception as e:
            self.ui(self.set_status, "Could not load tables", "error")
            self.ui(self.show_error, "Error", str(e))

    def load_table_details(self, table_name: str):
        self.table_preview_filters = {}
        trace_context = self._build_table_open_trace_context(table_name)
        self._trace_table_open(trace_context, "clique recebido; agendando carregamento em background")
        self.run_async(self._load_table_details_thread, table_name, trace_context)

    def _apply_table_details(self, table_name: str, details: dict, versions, trace_context: dict | None = None):
        started_at = time.perf_counter()
        self._trace_table_open(
            trace_context,
            (
                "_apply_table_details() iniciou; "
                f"headers={len(details.get('data_headers', []))}; "
                f"rows={len(details.get('data_rows', []))}; "
                f"versions={len(versions)}"
            ),
        )
        if table_name != self.selected_table_name:
            self.table_preview_filters = {}
        self.selected_table_name = table_name
        self.table_numeric_columns = set(details.get("numeric_columns", []))
        self.table_column_types = dict(details.get("column_types", {}))
        self.table_column_constraints = dict(details.get("column_constraints", {}))
        attention_started_at = time.perf_counter()
        self._refresh_table_dictionary_attention_state(table_name, details)
        self._trace_table_open(
            trace_context,
            f"_apply_table_details(): estado do data dictionary atualizado em {(time.perf_counter() - attention_started_at) * 1000:.1f} ms",
        )
        self.set_table_action_buttons_busy(False)
        title_started_at = time.perf_counter()
        self.data_table.set_title(self._build_preview_title(table_name))
        self._trace_table_open(
            trace_context,
            f"_apply_table_details(): título da preview atualizado em {(time.perf_counter() - title_started_at) * 1000:.1f} ms",
        )
        table_render_started_at = time.perf_counter()
        self.data_table.set_data(
            details["data_headers"],
            details["data_rows"],
            header_tooltips=details["header_types"],
            active_filters=self.table_preview_filters,
            on_filter=self.open_table_column_action_flow,
            attention_columns=self.table_dictionary_unlinked_columns,
            column_constraints=self.table_column_constraints,
            trace_context=trace_context,
        )
        self._trace_table_open(
            trace_context,
            f"_apply_table_details(): grid principal atualizada em {(time.perf_counter() - table_render_started_at) * 1000:.1f} ms",
        )
        versions_started_at = time.perf_counter()
        self.populate_version_buttons(versions, trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"_apply_table_details(): painel de versões atualizado em {(time.perf_counter() - versions_started_at) * 1000:.1f} ms",
        )
        self._trace_table_open(
            trace_context,
            f"_apply_table_details() finalizado em {(time.perf_counter() - started_at) * 1000:.1f} ms",
        )

    def _build_preview_title(self, table_name: str) -> str:
        filter_count = len(self.table_preview_filters)
        if filter_count:
            return f"Primeiras 50 linhas de {table_name} ({filter_count} filtro(s))"
        return f"Primeiras 50 linhas de {table_name}"

    def _apply_table_preview_details(self, table_name: str, details: dict):
        if table_name != self.selected_table_name:
            return

        self.table_numeric_columns = set(details.get("numeric_columns", []))
        self.table_column_types = dict(details.get("column_types", {}))
        self.table_column_constraints = dict(details.get("column_constraints", {}))
        self._refresh_table_dictionary_attention_state(table_name, details)
        self.data_table.set_title(self._build_preview_title(table_name))
        self.data_table.set_data(
            details["data_headers"],
            details["data_rows"],
            header_tooltips=details["header_types"],
            active_filters=self.table_preview_filters,
            on_filter=self.open_table_column_action_flow,
            attention_columns=self.table_dictionary_unlinked_columns,
            column_constraints=self.table_column_constraints,
        )

    def _load_table_details_thread(self, table_name: str, trace_context: dict | None = None):
        try:
            if not self.current_db:
                raise RuntimeError("Nenhuma base selecionada.")

            self._trace_table_open(trace_context, "thread de abertura iniciada")
            self.ui(self.set_status, f"Carregando {table_name}...", "loading")

            details_started_at = time.perf_counter()
            self._trace_table_open(trace_context, "iniciando service.get_table_details()")
            details = self.service.get_table_details(
                self.current_db,
                table_name,
                filters=self.table_preview_filters,
                trace_context=trace_context,
            )
            self._trace_table_open(
                trace_context,
                f"service.get_table_details() concluído em {(time.perf_counter() - details_started_at) * 1000:.1f} ms",
            )
            versions_started_at = time.perf_counter()
            self._trace_table_open(trace_context, "iniciando service.list_table_versions()")
            versions = self.service.list_table_versions(self.current_db, table_name, trace_context=trace_context)
            self._trace_table_open(
                trace_context,
                f"service.list_table_versions() concluído em {(time.perf_counter() - versions_started_at) * 1000:.1f} ms; version_count={len(versions)}",
            )

            self._trace_table_open(trace_context, "carregamento em background concluído; agendando callback da UI")

            def apply_loaded_table():
                ui_started_at = time.perf_counter()
                self._trace_table_open(trace_context, "callback de UI iniciado")
                self._apply_table_details(table_name, details, versions, trace_context=trace_context)
                self.set_status(f"Tabela carregada: {table_name}", "success")
                self._trace_table_open(
                    trace_context,
                    (
                        "callback de UI finalizado em "
                        f"{(time.perf_counter() - ui_started_at) * 1000:.1f} ms; "
                        "abertura concluída"
                    ),
                )

            self.ui(apply_loaded_table)

        except Exception as e:
            self._trace_table_open(trace_context, f"falha na abertura: {type(e).__name__}: {e}")
            traceback.print_exc()
            self.ui(self.set_status, "Erro ao carregar tabela", "error")
            self.ui(self.show_error, "Error", str(e))

    def _copy_table_preview_filters(self):
        return {
            column_name: [dict(item) for item in selected_values]
            for column_name, selected_values in self.table_preview_filters.items()
        }

    def _mark_table_filter_cache_stale_safe(self, database_name: str, full_table_name: str):
        try:
            self.facet_service.mark_table_stale(database_name, full_table_name)
        except Exception:
            pass

    @staticmethod
    def _column_is_raw_protected(column_name: str) -> bool:
        return "raw" in str(column_name or "").lower()

    @staticmethod
    def _column_is_raw_payload(column_name: str) -> bool:
        return str(column_name or "").strip().lower() == "raw"

    def _column_blocks_dictionary_link(self, column_name: str) -> bool:
        return self._column_is_raw_payload(column_name)

    def _refresh_table_dictionary_attention_state(self, table_name: str, details: dict):
        if self._is_control_metadata_table(table_name):
            self.table_dictionary_unlinked_columns = set()
            return

        dictionary_links = dict(details.get("dictionary_links", {}))
        header_names = list(details.get("data_headers") or self.table_column_types.keys())
        self.table_dictionary_unlinked_columns = {
            str(column_name)
            for column_name in header_names
            if str(column_name).strip()
            and not self._column_blocks_dictionary_link(column_name)
            and str(column_name) not in dictionary_links
        }

    def _column_requires_dictionary_link(self, column_name: str) -> bool:
        return str(column_name or "") in self.table_dictionary_unlinked_columns

    @staticmethod
    def _build_raw_group_version_notes(group_config: dict) -> str:
        key_header = str((group_config or {}).get("key_header") or "").strip()
        equal_headers = list((group_config or {}).get("equal_headers") or [])
        exclusive_headers = list((group_config or {}).get("exclusive_headers") or [])
        equal_label = ", ".join(equal_headers) if equal_headers else "(nenhum)"
        exclusive_label = ", ".join(exclusive_headers) if exclusive_headers else "(nenhum)"
        return (
            f"Agrupamento raw por chave: {key_header}\n"
            f"Ignorar (valores unicos): {equal_label}\n"
            f"Exclusivo: {exclusive_label}"
        )

    @staticmethod
    def _build_raw_group_columns_version_notes(group_config: dict) -> str:
        groups = list((group_config or {}).get("groups") or [])
        if not groups:
            return "Agrupamento de colunas raw sem configuracao registrada."

        lines = ["Agrupamento de colunas raw:"]
        for index, group_item in enumerate(groups, start=1):
            master_header = str(group_item.get("master_header") or "").strip() or "(sem mestre)"
            source_headers = [
                str(item or "").strip()
                for item in (group_item.get("source_headers") or [])
                if str(item or "").strip()
            ]
            source_label = ", ".join(source_headers) if source_headers else "(nenhuma)"
            lines.append(f"{index}. Mestre: {master_header} | Colunas agrupadas: {source_label}")
        return "\n".join(lines)

    def open_table_column_action_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return

        is_control_metadata_table = self._is_control_metadata_table(self.selected_table_name)
        constraint_info = dict(self.table_column_constraints.get(column_name) or {})
        table_has_primary_key = any(
            bool((metadata or {}).get("is_primary_key"))
            for metadata in self.table_column_constraints.values()
        )
        can_link_dictionary = (
            (not is_control_metadata_table)
            and self._column_requires_dictionary_link(column_name)
        )
        can_delete = (not is_control_metadata_table) and (not self._column_is_raw_protected(column_name))
        can_expand = (not is_control_metadata_table) and self._column_is_raw_payload(column_name)
        can_group = (not is_control_metadata_table) and self._column_is_raw_payload(column_name)
        can_group_columns = (not is_control_metadata_table) and self._column_is_raw_payload(column_name)
        can_validate = (not is_control_metadata_table) and (not self._column_is_raw_protected(column_name))
        can_make_primary_key = (not is_control_metadata_table) and (not table_has_primary_key)
        can_make_foreign_key = (not is_control_metadata_table) and (not constraint_info.get("is_foreign_key"))
        dialog = ColumnActionDialog(
            self,
            column_name=column_name,
            can_link_dictionary=can_link_dictionary,
            can_delete=can_delete,
            can_expand=can_expand,
            can_group=can_group,
            can_group_columns=can_group_columns,
            can_validate=can_validate,
            can_make_primary_key=can_make_primary_key,
            can_make_foreign_key=can_make_foreign_key,
        )
        self.wait_window(dialog)

        if dialog.result == "link_dictionary":
            self.open_table_column_dictionary_link_flow(column_name)
        elif dialog.result == "filter":
            self.open_table_column_filter_flow(column_name)
        elif dialog.result == "make_primary_key":
            self.open_table_column_primary_key_flow(column_name)
        elif dialog.result == "make_foreign_key":
            self.open_table_column_foreign_key_flow(column_name)
        elif dialog.result == "validate":
            self.open_table_column_validation_flow(column_name)
        elif dialog.result == "delete":
            self.delete_table_column_flow(column_name)
        elif dialog.result == "expand":
            self.open_raw_column_expand_flow(column_name)
        elif dialog.result == "group":
            self.open_raw_column_group_flow(column_name)
        elif dialog.result == "group_columns":
            self.open_raw_column_group_columns_flow(column_name)

    def open_table_column_filter_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return

        table_name = self.selected_table_name
        filters = self._copy_table_preview_filters()
        self.set_status(f"Carregando valores de {column_name}...", "loading")
        self.run_async(
            self._load_table_column_filter_values_thread,
            self.current_db,
            table_name,
            column_name,
            filters,
        )

    def _load_table_column_filter_values_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        filters: dict,
    ):
        try:
            self.facet_service.ensure_cache(
                database_name,
                table_name,
                progress_callback=lambda _stage_key, _progress, message: self.ui(
                    self.set_status,
                    message,
                    "loading",
                ),
            )
            values = self.facet_service.get_values(
                database_name,
                table_name,
                column_name,
                filters=filters,
                is_numeric=column_name in self.table_numeric_columns,
            )
            self.ui(self._show_table_column_filter_dialog, table_name, column_name, values)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar valores do filtro", "error")
            self.ui(self.show_error, "Error", str(e))

    def _show_table_column_filter_dialog(self, table_name: str, column_name: str, values):
        if table_name != self.selected_table_name:
            return

        current_values = self.table_preview_filters.get(column_name)
        if current_values:
            known_value_keys = {
                (bool(item.get("is_null")), str(item.get("value", "")))
                for item in values
            }
            for item in current_values:
                key = (bool(item.get("is_null")), str(item.get("value", "")))
                if key not in known_value_keys:
                    values.append(dict(item))
                    known_value_keys.add(key)

        dialog = ColumnFilterDialog(
            self,
            column_name=column_name,
            values=values,
            current_values=current_values,
            is_numeric=column_name in self.table_numeric_columns,
            on_search=lambda filter_dialog, search_text: self.search_table_column_filter_values(
                filter_dialog,
                table_name,
                column_name,
                search_text,
            ),
        )
        self.wait_window(dialog)

        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        selected_values = dialog.result.get("values")
        if selected_values is None:
            self.table_preview_filters.pop(column_name, None)
        else:
            self.table_preview_filters[column_name] = [dict(item) for item in selected_values]

        self.reload_table_preview_with_filters()

    def open_table_column_primary_key_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return

        table_name = self.selected_table_name
        if any(
            bool((metadata or {}).get("is_primary_key"))
            for metadata in self.table_column_constraints.values()
        ):
            self.show_error("Error", "A tabela selecionada ja possui primary key.")
            return

        version_info = self.ask_version_info(
            f"Definir PK: {column_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.begin_constraint_change_cancellation_scope()
        self.set_table_action_buttons_busy(True)
        self.open_constraint_progress(table_name, column_name, "primary key")
        self.set_status(f"Criando primary key em {column_name}...", "loading")
        self.run_async(
            self._set_table_column_primary_key_thread,
            self.current_db,
            table_name,
            column_name,
            version_info,
            self._copy_table_preview_filters(),
        )

    def _set_table_column_primary_key_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        version_info: dict,
        filters: dict,
    ):
        try:
            cancel_event = self.constraint_change_cancel_event
            self.ui(self.set_status, f"Criando primary key em {column_name}...", "loading")
            self.ui(self.update_constraint_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_constraint_progress, "prepare", 100, "Ambiente pronto para criar a primary key.")
            result = self.service.add_primary_key_constraint(
                database_name,
                table_name,
                column_name,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_constraint_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_constraint_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )
            self.ui(self.update_constraint_progress, "refresh", 15, "Carregando dados atualizados...")
            details = self.service.get_table_details(
                database_name,
                table_name,
                filters=filters,
                cancel_event=cancel_event,
            )
            self.ui(self.update_constraint_progress, "refresh", 55, "Carregando historico de versoes...")
            versions = self.service.list_table_versions(database_name, table_name, cancel_event=cancel_event)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )

            def apply_primary_key():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_primary_key)
            self.ui(
                self.update_constraint_progress,
                "refresh",
                100,
                "Interface atualizada. A tabela ja reflete a nova primary key.",
            )
            self.ui(self.complete_constraint_progress, "Primary key criada com sucesso.")
            self.ui(self.set_status, f"Primary key criada em {next_version}: {column_name}", "success")
        except OperationCancelledError:
            self.ui(self.mark_constraint_progress_cancelled, "Criacao de primary key cancelada pelo usuario.")
            self.ui(self.set_status, "Criacao de primary key cancelada", "error")
        except Exception as e:
            self.ui(self.close_constraint_progress)
            self.ui(self.set_status, "Erro ao criar primary key", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_table_action_buttons_busy, False)
            self.clear_constraint_change_cancellation_scope()

    def open_table_column_foreign_key_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return

        table_name = self.selected_table_name
        try:
            reference_options = self.service.list_foreign_key_reference_options(
                self.current_db,
                table_name,
                column_name,
            )
        except Exception as e:
            self.show_error("Error", str(e))
            return

        if not reference_options:
            self.show_error(
                "Error",
                "Nenhuma referencia compativel foi encontrada para essa coluna. "
                "A FK exige uma coluna PK/UNIQUE de tipo compativel.",
            )
            return

        dialog = ForeignKeyReferenceDialog(self, column_name=column_name, reference_options=reference_options)
        self.wait_window(dialog)
        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        version_info = self.ask_version_info(
            f"Definir FK: {column_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        reference_label = (
            f"{dialog.result['referenced_table_name']}.{dialog.result['referenced_column_name']}"
        )
        self.begin_constraint_change_cancellation_scope()
        self.set_table_action_buttons_busy(True)
        self.open_constraint_progress(table_name, column_name, "foreign key", reference_text=reference_label)
        self.set_status(f"Criando foreign key em {column_name}...", "loading")
        self.run_async(
            self._set_table_column_foreign_key_thread,
            self.current_db,
            table_name,
            column_name,
            dict(dialog.result),
            version_info,
            self._copy_table_preview_filters(),
        )

    def _set_table_column_foreign_key_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        reference_info: dict,
        version_info: dict,
        filters: dict,
    ):
        try:
            cancel_event = self.constraint_change_cancel_event
            self.ui(self.set_status, f"Criando foreign key em {column_name}...", "loading")
            self.ui(self.update_constraint_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_constraint_progress, "prepare", 100, "Ambiente pronto para criar a foreign key.")
            result = self.service.add_foreign_key_constraint(
                database_name,
                table_name,
                column_name,
                reference_info["referenced_table_name"],
                reference_info["referenced_column_name"],
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_constraint_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_constraint_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )
            self.ui(self.update_constraint_progress, "refresh", 15, "Carregando dados atualizados...")
            details = self.service.get_table_details(
                database_name,
                table_name,
                filters=filters,
                cancel_event=cancel_event,
            )
            self.ui(self.update_constraint_progress, "refresh", 55, "Carregando historico de versoes...")
            versions = self.service.list_table_versions(database_name, table_name, cancel_event=cancel_event)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )

            def apply_foreign_key():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            target_label = (
                f"{result['referenced_table_name']}.{result['referenced_column_name']}"
            )
            self.ui(apply_foreign_key)
            self.ui(
                self.update_constraint_progress,
                "refresh",
                100,
                "Interface atualizada. A tabela ja reflete a nova foreign key.",
            )
            self.ui(self.complete_constraint_progress, "Foreign key criada com sucesso.")
            self.ui(self.set_status, f"Foreign key criada em {next_version}: {column_name} -> {target_label}", "success")
        except OperationCancelledError:
            self.ui(self.mark_constraint_progress_cancelled, "Criacao de foreign key cancelada pelo usuario.")
            self.ui(self.set_status, "Criacao de foreign key cancelada", "error")
        except Exception as e:
            self.ui(self.close_constraint_progress)
            self.ui(self.set_status, "Erro ao criar foreign key", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_table_action_buttons_busy, False)
            self.clear_constraint_change_cancellation_scope()

    def open_table_column_validation_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if self._column_is_raw_protected(column_name):
            self.show_error("Error", "Colunas raw nao podem ser validadas por este fluxo.")
            return

        table_name = self.selected_table_name
        dialog = ColumnValidationDialog(
            self,
            column_name=column_name,
            is_numeric=column_name in self.table_numeric_columns,
            data_type=self.table_column_types.get(column_name, ""),
        )
        self.wait_window(dialog)
        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        version_info = self.ask_version_info(
            f"Validar coluna: {column_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.set_status(f"Aplicando validacao QA em {column_name}...", "loading")
        self.run_async(
            self._validate_table_column_thread,
            self.current_db,
            table_name,
            column_name,
            dialog.result,
            version_info,
            self._copy_table_preview_filters(),
        )

    def _validate_table_column_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        validation_config: dict,
        version_info: dict,
        filters: dict,
    ):
        try:
            self.ui(self.set_status, f"Aplicando validacao QA em {column_name}...", "loading")
            self.service.ensure_admin_schema()
            result = self.service.validate_table_column(
                database_name=database_name,
                full_table_name=table_name,
                column_name=column_name,
                validation_config=validation_config,
            )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
            )

            try:
                self.facet_service.mark_table_stale(database_name, table_name)
            except Exception:
                pass

            details = self.service.get_table_details(database_name, table_name, filters=filters)
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_validation():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_validation)
            self.run_async(self._clear_table_filter_cache_thread, database_name, table_name)
            self.ui(
                self.set_status,
                f"Validacao QA registrada em {next_version}: {column_name} -> {result['flag_column_name']}",
                "success",
            )
        except Exception as e:
            self.ui(self.set_status, "Erro ao aplicar validacao QA", "error")
            self.ui(self.show_error, "Error", str(e))

    def search_table_column_filter_values(
        self,
        dialog,
        table_name: str,
        column_name: str,
        search_text: str,
    ):
        if table_name != self.selected_table_name:
            return

        self.run_async(
            self._search_table_column_filter_values_thread,
            dialog,
            self.current_db,
            table_name,
            column_name,
            search_text,
            self._copy_table_preview_filters(),
        )

    def _search_table_column_filter_values_thread(
        self,
        dialog,
        database_name: str,
        table_name: str,
        column_name: str,
        search_text: str,
        filters: dict,
    ):
        try:
            values = self.facet_service.get_values(
                database_name,
                table_name,
                column_name,
                filters=filters,
                search_text=search_text,
                is_numeric=column_name in self.table_numeric_columns,
            )

            def apply_values():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_values(values, preserve_selected=True)
                dialog.set_search_busy(False)

            self.ui(apply_values)
        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_search_busy(False, "Erro ao pesquisar valores.")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def open_raw_column_expand_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if not self._column_is_raw_payload(column_name):
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        self.set_status("Carregando chaves do raw...", "loading")
        self.run_async(
            self._load_raw_column_keys_thread,
            database_name,
            table_name,
            column_name,
        )

    def _load_raw_column_keys_thread(self, database_name: str, table_name: str, column_name: str):
        try:
            keys = self.service.get_raw_value_keys(database_name, table_name)
            self.ui(self._show_raw_column_expand_dialog, table_name, column_name, keys)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar chaves do raw", "error")
            self.ui(self.show_error, "Error", str(e))

    def open_raw_column_group_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if not self._column_is_raw_payload(column_name):
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        self.set_status("Carregando headers do raw...", "loading")
        self.run_async(
            self._load_raw_group_headers_thread,
            database_name,
            table_name,
            column_name,
        )

    def _load_raw_group_headers_thread(self, database_name: str, table_name: str, column_name: str):
        try:
            keys = self.service.get_raw_value_keys(database_name, table_name)
            self.ui(self._show_raw_group_dialog, table_name, column_name, keys)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar headers do raw", "error")
            self.ui(self.show_error, "Error", str(e))

    def _show_raw_group_dialog(self, table_name: str, column_name: str, keys):
        if table_name != self.selected_table_name:
            return

        if not keys:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            self.show_error("Error", "Nenhum header encontrado no raw.")
            return

        dialog = RawGroupConfigDialog(self, column_name=column_name, keys=keys)
        self.wait_window(dialog)
        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        version_info = self.ask_version_info(
            f"Agrupar raw em: {table_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.begin_raw_group_cancellation_scope()
        self.open_raw_group_progress(table_name, dialog.result["key_header"], focus_label="Chave")
        self.set_status(f"Agrupando raw por {dialog.result['key_header']}...", "loading")
        self.run_async(
            self._group_raw_column_thread,
            self.current_db,
            table_name,
            dialog.result,
            version_info,
            self._copy_table_preview_filters(),
        )

    def open_raw_column_group_columns_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if not self._column_is_raw_payload(column_name):
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        self.set_status("Carregando headers do raw...", "loading")
        self.run_async(
            self._load_raw_group_columns_headers_thread,
            database_name,
            table_name,
            column_name,
        )

    def _load_raw_group_columns_headers_thread(self, database_name: str, table_name: str, column_name: str):
        try:
            keys = self.service.get_raw_value_keys(database_name, table_name)
            self.ui(self._show_raw_group_columns_dialog, table_name, column_name, keys)
        except Exception as e:
            self.ui(self.set_status, "Erro ao carregar headers do raw", "error")
            self.ui(self.show_error, "Error", str(e))

    def _show_raw_group_columns_dialog(self, table_name: str, column_name: str, keys):
        if table_name != self.selected_table_name:
            return

        if len(keys or []) < 2:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            self.show_error("Error", "Sao necessarios ao menos dois headers no raw para agrupar colunas.")
            return

        dialog = RawGroupColumnsConfigDialog(self, column_name=column_name, keys=keys)
        self.wait_window(dialog)
        if not dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        version_info = self.ask_version_info(
            f"Agrupar colunas raw em: {table_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        master_headers = [
            str(item.get("master_header") or "").strip()
            for item in dialog.result.get("groups", [])
            if str(item.get("master_header") or "").strip()
        ]
        summary = ", ".join(master_headers[:3])
        if len(master_headers) > 3:
            summary += f" e mais {len(master_headers) - 3}"

        self.begin_raw_group_cancellation_scope()
        self.open_raw_group_progress(table_name, summary or "(sem mestres)", focus_label="Mestres")
        self.set_status("Agrupando colunas raw...", "loading")
        self.run_async(
            self._group_raw_columns_thread,
            self.current_db,
            table_name,
            dialog.result,
            version_info,
            self._copy_table_preview_filters(),
        )

    def _show_raw_column_expand_dialog(self, table_name: str, column_name: str, keys):
        if table_name != self.selected_table_name:
            return

        dialog = RawExpandKeysDialog(self, column_name=column_name, keys=keys)
        self.wait_window(dialog)
        selected_key = dialog.result
        if selected_key:
            self.open_raw_key_expand_config_flow(table_name, selected_key)
            return

        self.set_status(f"Tabela carregada: {table_name}", "success")

    def open_raw_key_expand_config_flow(self, table_name: str, raw_key: str):
        if not self.current_db or table_name != self.selected_table_name:
            return

        try:
            has_multiple_values = self.service.raw_value_key_has_multiple_values(
                self.current_db,
                table_name,
                raw_key,
            )
        except Exception as exc:
            self.show_error("Error", str(exc))
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        expand_mode = "default"
        if has_multiple_values:
            mode_dialog = RawExpandMultiValueModeDialog(self, raw_key=raw_key)
            self.wait_window(mode_dialog)
            selected_mode = mode_dialog.result
            if not selected_mode:
                self.set_status(f"Tabela carregada: {table_name}", "success")
                return
            if selected_mode == "child_table":
                self.open_raw_key_expand_child_table_flow(table_name, raw_key)
                return
            expand_mode = "first_value"

        config_dialog = RawExpandColumnDialog(
            self,
            raw_key=raw_key,
            dictionary_lookup_callback=self.lookup_data_dictionary_entry,
        )
        self.wait_window(config_dialog)
        if not config_dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        expand_config = dict(config_dialog.result)
        expand_config["expand_mode"] = expand_mode

        version_info = self.ask_version_info(
            f"Expandir chave raw: {raw_key}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.begin_raw_expand_cancellation_scope()
        self.open_raw_expand_progress(
            table_name,
            raw_key,
            config_dialog.result["column_name"],
        )
        self.set_status(f"Expandindo chave raw {raw_key}...", "loading")
        self.run_async(
            self._expand_raw_key_thread,
            self.current_db,
            table_name,
            raw_key,
            expand_config,
            version_info,
            self._copy_table_preview_filters(),
        )

    def open_raw_key_expand_child_table_flow(self, table_name: str, raw_key: str):
        if not self.current_db or table_name != self.selected_table_name:
            return

        try:
            child_tables = [
                candidate
                for candidate in self.service.list_tables(self.current_db)
                if candidate != table_name
            ]
        except Exception as exc:
            self.show_error("Error", str(exc))
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return
        if not child_tables:
            self.show_error("Error", "Nenhuma tabela filha disponivel nesta base.")
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        selection_dialog = TableSelectionDialog(
            self,
            title="Selecionar tabela filha",
            heading="Escolha a tabela filha",
            items=child_tables,
        )
        self.wait_window(selection_dialog)
        child_table_name = selection_dialog.result
        if not child_table_name:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        try:
            parent_field_options = self._load_expand_field_options(
                self.current_db,
                table_name,
                include_raw=True,
            )
            multi_value_raw_keys = self.service.get_raw_value_keys_with_multiple_values(
                self.current_db,
                table_name,
            )
            child_field_options = self._load_expand_field_options(
                self.current_db,
                child_table_name,
                include_raw=False,
            )
        except Exception as exc:
            self.show_error("Error", str(exc))
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return
        if not parent_field_options:
            self.show_error("Error", "Nao foi possivel montar as opcoes de chave da tabela atual.")
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return
        if not child_field_options:
            self.show_error("Error", "Nao foi possivel montar as opcoes da tabela filha selecionada.")
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        config_dialog = RawExpandChildTableDialog(
            self,
            raw_key=raw_key,
            child_table_name=child_table_name,
            parent_field_options=parent_field_options,
            child_field_options=child_field_options,
            multi_value_raw_keys=multi_value_raw_keys,
            dictionary_lookup_callback=self.lookup_data_dictionary_entry,
        )
        self.wait_window(config_dialog)
        if not config_dialog.result:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        expand_config = self._resolve_child_expand_type_conflicts(
            table_name,
            child_table_name,
            config_dialog.result,
        )
        if not expand_config:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return
        expand_config["child_table_name"] = child_table_name

        version_info = self.ask_version_info(
            f"Expandir chave raw: {raw_key}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.begin_raw_expand_cancellation_scope()
        self.open_raw_expand_progress(
            table_name,
            raw_key,
            child_table_name,
            focus_label="Tabela filha",
        )
        self.set_status(f"Expandindo chave raw {raw_key} via tabela filha...", "loading")
        self.run_async(
            self._expand_raw_key_thread,
            self.current_db,
            table_name,
            raw_key,
            expand_config,
            version_info,
            self._copy_table_preview_filters(),
        )

    def _expand_raw_key_thread(
        self,
        database_name: str,
        table_name: str,
        raw_key: str,
        expand_config: dict,
        version_info: dict,
        filters: dict,
    ):
        try:
            cancel_event = self.raw_expand_cancel_event
            child_version = None
            child_table_name = None
            result_child_table_name = None
            is_child_expand = expand_config.get("expand_mode") == "child_table"
            self.ui(self.set_status, f"Expandindo chave raw {raw_key}...", "loading")
            self.ui(self.update_raw_expand_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_raw_expand_progress, "prepare", 100, "Ambiente pronto para expansao.")
            if is_child_expand:
                result = self.service.expand_raw_value_key_from_child_table(
                    database_name=database_name,
                    parent_full_table_name=table_name,
                    raw_key=raw_key,
                    child_full_table_name=expand_config["child_table_name"],
                    parent_key_source=expand_config["parent_key_source"],
                    child_key_source=expand_config["child_key_source"],
                    child_value_source=expand_config["child_value_source"],
                    column_name=expand_config["column_name"],
                    column_type=expand_config["column_type"],
                    false_values=expand_config.get("false_values"),
                    true_values=expand_config.get("true_values"),
                    date_formats=expand_config.get("date_formats"),
                    value_mappings=expand_config.get("value_mappings"),
                    cancel_event=cancel_event,
                    progress_callback=lambda stage_key, progress, message: self.ui(
                        self.update_raw_expand_progress,
                        stage_key,
                        progress,
                        message,
                    ),
                )
            else:
                result = self.service.expand_raw_value_key(
                    database_name=database_name,
                    full_table_name=table_name,
                    raw_key=raw_key,
                    column_name=expand_config["column_name"],
                    column_type=expand_config["column_type"],
                    false_values=expand_config.get("false_values"),
                    true_values=expand_config.get("true_values"),
                    date_formats=expand_config.get("date_formats"),
                    first_array_item=expand_config.get("expand_mode") == "first_value",
                    cancel_event=cancel_event,
                    progress_callback=lambda stage_key, progress, message: self.ui(
                        self.update_raw_expand_progress,
                        stage_key,
                        progress,
                        message,
                    ),
                )

            if not is_child_expand:
                self._sync_data_dictionary_for_columns(
                    database_name=database_name,
                    table_name=table_name,
                    columns=[expand_config],
                    requested_by=version_info["requested_by"],
                )

            child_schema_name = str(result.get("child_schema_name") or "").strip()
            result_child_table_name = str(result.get("child_table_name") or "").strip()
            if is_child_expand:
                if child_schema_name and result_child_table_name:
                    child_table_name = f"{child_schema_name}.{result_child_table_name}"
                else:
                    child_table_name = str(expand_config.get("child_table_name") or "").strip()
                self.ui(
                    self.update_raw_expand_progress,
                    "version",
                    15,
                    "Registrando versao da tabela filha...",
                )
                next_version = self.service.register_table_version(
                    database_name=database_name,
                    schema_name=result["schema_name"],
                    table_name=result["table_name"],
                    version_title=version_info["version_title"],
                    requested_by=version_info["requested_by"],
                    workstation_name=socket.gethostname(),
                    sql_recipe=result["sql_recipe"],
                    restored_from_version=None,
                )
                self.ui(
                    self.update_raw_expand_progress,
                    "version",
                    100,
                    "Versao da tabela filha registrada com sucesso.",
                )
                self.ui(
                    self.update_raw_expand_progress,
                    "refresh",
                    0,
                    "Limpando cache local de filtros da tabela filha...",
                )
                if child_table_name:
                    self.facet_service.clear_table(database_name, child_table_name)
                self.ui(self.update_raw_expand_progress, "refresh", 100, "Interface atualizada.")
                self.ui(self.complete_raw_expand_progress, "Expansao concluida com sucesso.")
                mapping_count = int(result.get("mapping_count") or 1)
                target_label = child_table_name or f"{result['schema_name']}.{result['table_name']}"
                self.ui(
                    self.set_status,
                    (
                        (
                            f"Chave raw expandida em {next_version}: {raw_key} -> {target_label}.{result['column_name']}"
                            if mapping_count == 1
                            else f"Chaves raw expandidas em {next_version}: {mapping_count} mapeamentos aplicados na tabela filha {target_label}"
                        )
                    ),
                    "success",
                )
                return

            if child_schema_name and result_child_table_name:
                child_table_name = f"{child_schema_name}.{result_child_table_name}"
                self.ui(
                    self.update_raw_expand_progress,
                    "version",
                    10,
                    "Registrando versao da tabela filha...",
                )
                child_version = self.service.register_table_version(
                    database_name=database_name,
                    schema_name=child_schema_name,
                    table_name=result_child_table_name,
                    version_title=version_info["version_title"],
                    requested_by=version_info["requested_by"],
                    workstation_name=socket.gethostname(),
                    sql_recipe=result.get("child_sql_recipe", result["sql_recipe"]),
                    restored_from_version=None,
                )
                self.ui(
                    self.update_raw_expand_progress,
                    "version",
                    45,
                    f"Tabela filha versionada em {child_version}. Registrando tabela atual...",
                )
            else:
                self.ui(
                    self.update_raw_expand_progress,
                    "version",
                    45,
                    "Registrando versao da tabela atual...",
                )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result.get("parent_sql_recipe", result["sql_recipe"]),
                restored_from_version=None,
            )
            self.ui(self.update_raw_expand_progress, "version", 100, "Versoes registradas com sucesso.")

            self.ui(self.update_raw_expand_progress, "refresh", 0, "Limpando cache local de filtros...")
            self.facet_service.clear_table(database_name, table_name)
            if child_table_name:
                self.facet_service.clear_table(database_name, child_table_name)
            self.ui(self.update_raw_expand_progress, "refresh", 35, "Carregando dados atualizados...")
            details = self.service.get_table_details(database_name, table_name, filters=filters, cancel_event=cancel_event)
            self.ui(self.update_raw_expand_progress, "refresh", 65, "Carregando versoes...")
            versions = self.service.list_table_versions(database_name, table_name, cancel_event=cancel_event)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )

            def apply_expand():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_expand)
            self.ui(self.update_raw_expand_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_raw_expand_progress, "Expansao concluida com sucesso.")
            mapping_count = int(result.get("mapping_count") or 1)
            self.ui(
                self.set_status,
                (
                    (
                        f"Chave raw expandida em {next_version}: {raw_key} -> {result['column_name']}"
                        if mapping_count == 1
                        else f"Chaves raw expandidas em {next_version}: {mapping_count} mapeamentos aplicados"
                    )
                    + (f" | tabela filha versionada em {child_version}" if child_version else "")
                ),
                "success",
            )
        except OperationCancelledError:
            self.ui(self.mark_raw_expand_progress_cancelled, "Expansao cancelada pelo usuario.")
            self.ui(self.set_status, "Expansao Raw cancelada", "error")
        except Exception as e:
            self.ui(self.close_raw_expand_progress)
            self.ui(self.set_status, "Erro ao expandir chave raw", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.clear_raw_expand_cancellation_scope()

    def _group_raw_column_thread(
        self,
        database_name: str,
        table_name: str,
        group_config: dict,
        version_info: dict,
        filters: dict,
    ):
        try:
            key_header = group_config["key_header"]
            cancel_event = self.raw_group_cancel_event
            self.ui(self.set_status, f"Agrupando raw por {key_header}...", "loading")
            self.ui(self.update_raw_group_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_raw_group_progress, "prepare", 100, "Ambiente pronto para o agrupamento.")
            result = self.service.group_raw_rows(
                database_name=database_name,
                full_table_name=table_name,
                key_header=key_header,
                equal_headers=group_config.get("equal_headers"),
                exclusive_headers=group_config.get("exclusive_headers"),
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_group_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                version_notes=self._build_raw_group_version_notes(group_config),
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_group_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            self.ui(self.update_raw_group_progress, "refresh", 0, "Limpando cache local de filtros...")
            self.facet_service.clear_table(database_name, table_name)
            self.ui(self.update_raw_group_progress, "refresh", 35, "Carregando dados atualizados...")
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            self.ui(self.update_raw_group_progress, "refresh", 65, "Carregando versoes...")
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_group():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_group)
            self.ui(self.update_raw_group_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_raw_group_progress, "Agrupamento concluido com sucesso.")
            self.ui(
                self.set_status,
                (
                    f"Raw agrupado em {next_version}: "
                    f"{result['source_row_count']} -> {result['group_row_count']} linhas"
                ),
                "success",
            )
        except OperationCancelledError:
            self.ui(self.mark_raw_group_progress_cancelled, "Agrupamento cancelado pelo usuario.")
            self.ui(self.set_status, "Agrupamento Raw cancelado", "error")
        except Exception as e:
            self.ui(self.close_raw_group_progress)
            self.ui(self.set_status, "Erro ao agrupar raw", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.clear_raw_group_cancellation_scope()

    def _group_raw_columns_thread(
        self,
        database_name: str,
        table_name: str,
        group_config: dict,
        version_info: dict,
        filters: dict,
    ):
        try:
            cancel_event = self.raw_group_cancel_event
            self.ui(self.set_status, "Agrupando colunas raw...", "loading")
            self.ui(self.update_raw_group_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_raw_group_progress, "prepare", 100, "Ambiente pronto para o agrupamento.")
            result = self.service.group_raw_columns(
                database_name=database_name,
                full_table_name=table_name,
                column_groups=group_config.get("groups"),
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_group_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                version_notes=self._build_raw_group_columns_version_notes(group_config),
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_group_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            self.ui(self.update_raw_group_progress, "refresh", 0, "Limpando cache local de filtros...")
            self.facet_service.clear_table(database_name, table_name)
            self.ui(self.update_raw_group_progress, "refresh", 35, "Carregando dados atualizados...")
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            self.ui(self.update_raw_group_progress, "refresh", 65, "Carregando versoes...")
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_group_columns():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_group_columns)
            self.ui(self.update_raw_group_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_raw_group_progress, "Agrupamento de colunas concluido com sucesso.")
            self.ui(
                self.set_status,
                (
                    f"Colunas raw agrupadas em {next_version}: "
                    f"{result['group_count']} mestre(s) aplicados em {result['row_count']} linha(s)"
                ),
                "success",
            )
        except OperationCancelledError:
            self.ui(self.mark_raw_group_progress_cancelled, "Agrupamento cancelado pelo usuario.")
            self.ui(self.set_status, "Agrupamento de colunas Raw cancelado", "error")
        except Exception as e:
            self.ui(self.close_raw_group_progress)
            self.ui(self.set_status, "Erro ao agrupar colunas raw", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.clear_raw_group_cancellation_scope()

    def delete_table_column_flow(self, column_name: str):
        if not self.current_db or not self.selected_table_name:
            return
        if self._column_is_raw_protected(column_name):
            self.show_error("Error", "Colunas raw nao podem ser excluidas por este fluxo.")
            return

        table_name = self.selected_table_name
        version_info = self.ask_version_info(
            f"Excluir coluna: {column_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            return

        if not self.ask_final_delete_confirmation(
            f"Coluna {column_name}",
            (
                "Essa acao executara ALTER TABLE DROP COLUMN e registrara "
                "uma nova versao da tabela."
            ),
        ):
            return

        self.begin_column_delete_cancellation_scope()
        self.open_column_delete_progress(table_name, column_name)
        self.run_async(
            self._delete_table_column_thread,
            self.current_db,
            table_name,
            column_name,
            version_info,
        )

    def _delete_table_column_thread(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        version_info: dict,
    ):
        try:
            cancel_event = self.column_delete_cancel_event
            self.ui(self.set_status, f"Excluindo coluna {column_name}...", "loading")
            self.ui(self.update_column_delete_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_column_delete_progress, "prepare", 100, "Ambiente pronto para a exclusao.")
            self.ui(self.update_column_delete_progress, "alter", 10, "Executando ALTER TABLE DROP COLUMN...")
            result = self.service.drop_table_column(
                database_name,
                table_name,
                column_name,
                cancel_event=cancel_event,
            )
            self.ui(self.update_column_delete_progress, "alter", 70, "Removendo vinculo no data dictionary usage...")
            self.service.remove_data_dictionary_usage_link(
                database_name,
                table_name,
                column_name,
                cancel_event=cancel_event,
            )
            self.ui(
                self.update_column_delete_progress,
                "alter",
                100,
                "Coluna excluida no banco e usage atualizado.",
            )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_column_delete_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            self.ui(self.update_column_delete_progress, "refresh", 0, "Limpando cache local de filtros...")
            self.facet_service.clear_table(database_name, table_name)
            filters = {
                current_column: selected_values
                for current_column, selected_values in self._copy_table_preview_filters().items()
                if current_column != column_name
            }
            self.ui(self.update_column_delete_progress, "refresh", 35, "Carregando dados atualizados...")
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            self.ui(self.update_column_delete_progress, "refresh", 65, "Carregando versoes...")
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_column_delete():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self.table_preview_filters.pop(column_name, None)
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_column_delete)
            self.ui(self.update_column_delete_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_column_delete_progress, "Exclusao concluida com sucesso.")
            self.ui(self.set_status, f"Coluna excluida em {next_version}: {column_name}", "success")
        except OperationCancelledError:
            self.ui(self.mark_column_delete_progress_cancelled, "Exclusao cancelada pelo usuario.")
            self.ui(self.set_status, "Exclusao de coluna cancelada", "error")
        except Exception as e:
            self.ui(self.close_column_delete_progress)
            self.ui(self.set_status, "Erro ao excluir coluna", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.clear_column_delete_cancellation_scope()

    def create_columns_flow(self):
        if not self.current_db or not self.selected_table_name:
            self.show_error("Error", "Selecione uma tabela primeiro.")
            return

        table_name = self.selected_table_name
        create_info = self.ask_create_columns_info()
        if not create_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        version_info = self.ask_version_info(
            f"Criar colunas em: {table_name}",
            version_label="Titulo da nova versao",
        )
        if not version_info:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        self.set_status(f"Criando colunas em {table_name}...", "loading")
        self.run_async(
            self._create_columns_thread,
            self.current_db,
            table_name,
            create_info["columns"],
            version_info,
            self._copy_table_preview_filters(),
        )

    def _create_columns_thread(
        self,
        database_name: str,
        table_name: str,
        columns,
        version_info: dict,
        filters: dict,
    ):
        try:
            self.ui(self.set_status, f"Criando colunas em {table_name}...", "loading")
            self.service.ensure_admin_schema()
            result = self.service.create_table_columns(
                database_name=database_name,
                full_table_name=table_name,
                columns=columns,
            )
            self._sync_data_dictionary_for_columns(
                database_name=database_name,
                table_name=table_name,
                columns=columns,
                requested_by=version_info["requested_by"],
            )

            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_info["version_title"],
                requested_by=version_info["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
            )

            self.facet_service.clear_table(database_name, table_name)
            details = self.service.get_table_details(database_name, table_name, filters=filters)
            versions = self.service.list_table_versions(database_name, table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_column_create():
                if self.current_db != database_name or self.selected_table_name != table_name:
                    return
                self._apply_table_details(table_name, details, versions)

            created_names = ", ".join(column["column_name"] for column in result["columns"])
            self.ui(apply_column_create)
            self.ui(self.set_status, f"Colunas criadas em {next_version}: {created_names}", "success")
        except Exception as e:
            self.ui(self.set_status, "Erro ao criar colunas", "error")
            self.ui(self.show_error, "Error", str(e))

    def execute_cross_table_expansion_flow(self):
        if not self.current_db or not self.selected_table_name:
            self.show_error("Error", "Select the source table first.")
            return

        database_name = self.current_db
        source_table_name = self.selected_table_name
        self.set_table_action_buttons_busy(True)
        self.set_status(
            f"Loading expansion options for {source_table_name}...",
            "loading",
        )
        self.run_async(
            self._prepare_cross_table_expansion_flow_thread,
            database_name,
            source_table_name,
        )

    def _prepare_cross_table_expansion_flow_thread(
        self,
        database_name: str,
        source_table_name: str,
    ):
        try:
            tables = self.service.list_tables(database_name)
            available_tables = [
                table_name
                for table_name in tables
                if table_name != source_table_name
                and not self._is_control_metadata_table(table_name)
            ]
            if not available_tables:
                raise RuntimeError(
                    "No other table is available to receive the expansion."
                )
            raw_schemas = self.service.list_table_raw_schemas(
                database_name,
                source_table_name,
            )
            if not raw_schemas:
                raise RuntimeError(
                    f"Source {source_table_name} has no raw_schema registered "
                    "in version history."
                )
            self.ui(
                self._open_cross_table_expansion_dialog,
                database_name,
                source_table_name,
                available_tables,
                raw_schemas,
            )
        except Exception as exc:
            self.ui(self.set_table_action_buttons_busy, False)
            self.ui(self.set_status, "Could not prepare the expansion", "error")
            self.ui(self.show_error, "Error preparing expansion", str(exc))

    def _open_cross_table_expansion_dialog(
        self,
        database_name: str,
        source_table_name: str,
        available_tables,
        available_raw_schemas,
    ):
        if (
            self.current_db != database_name
            or self.selected_table_name != source_table_name
        ):
            self.set_table_action_buttons_busy(False)
            return

        expansion_request = self.ask_cross_table_expansion_info(
            database_name,
            source_table_name,
            available_tables,
            available_raw_schemas,
        )
        if not expansion_request:
            self.set_table_action_buttons_busy(False)
            self.set_status(f"Tabela carregada: {source_table_name}", "success")
            return

        try:
            expansion_request["raw_schemas"] = (
                self.service.resolve_raw_schema_interval(
                    available_raw_schemas,
                    expansion_request["first_raw_schema"],
                    expansion_request["last_raw_schema"],
                )
            )
        except Exception as exc:
            self.set_table_action_buttons_busy(False)
            self.show_error("Error", str(exc))
            return

        self.begin_cross_table_expansion_cancellation_scope()
        self.open_cross_table_expansion_progress(
            database_name,
            source_table_name,
            len(expansion_request["destinations"]),
        )
        self.run_async(
            self._execute_cross_table_expansion_thread,
            database_name,
            source_table_name,
            expansion_request,
        )

    def _execute_cross_table_expansion_thread(
        self,
        database_name: str,
        source_table_name: str,
        expansion_request: dict,
    ):
        try:
            cancel_event = self.cross_table_expansion_cancel_event
            timing_report = (
                self.service.create_cross_table_expansion_timing_report(
                    database_name,
                    source_table_name,
                    expansion_request.get("raw_schemas") or [],
                    [
                        item.get("table_name")
                        for item in expansion_request.get("destinations") or []
                    ],
                )
            )
            self.ui(
                self.set_status,
                f"Expanding {source_table_name} into the destinations...",
                "loading",
            )
            self.ui(
                self.update_cross_table_expansion_progress,
                "prepare",
                10,
                "Preparing version metadata...",
            )
            with self.service.expansion_timing_scope(
                timing_report,
                "prepare",
                "Ensure control database metadata",
            ):
                self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(
                self.update_cross_table_expansion_progress,
                "prepare",
                100,
                "Environment ready.",
            )

            result = self.service.execute_cross_table_expansion(
                database_name=database_name,
                source_full_table_name=source_table_name,
                raw_schemas=expansion_request["raw_schemas"],
                destinations=expansion_request["destinations"],
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_cross_table_expansion_progress,
                    stage_key,
                    progress,
                    message,
                ),
                post_commit_callback=self.begin_cross_table_expansion_finalization,
                timing_report=timing_report,
            )

            # O COMMIT dos dados ja ocorreu. Daqui em diante a operacao precisa
            # terminar o manifesto/versionamento mesmo que o usuario tente cancelar.
            registered_versions = (
                self.service.register_cross_table_expansion_versions(
                    expansion_result=result,
                    requested_by=expansion_request["requested_by"],
                    workstation_name=socket.gethostname(),
                    cancel_event=None,
                    progress_callback=lambda stage_key, progress, message: self.ui(
                        self.update_cross_table_expansion_progress,
                        stage_key,
                        progress,
                        message,
                    ),
                    timing_report=timing_report,
                )
            )

            self.ui(
                self.update_cross_table_expansion_progress,
                "refresh",
                10,
                "Invalidating destination caches...",
            )
            with self.service.expansion_timing_scope(
                timing_report,
                "refresh",
                "Invalidate destination filter caches",
            ):
                destination_names = [
                    item["table_name"]
                    for item in registered_versions
                ]
                for destination_name in destination_names:
                    self._mark_table_filter_cache_stale_safe(
                        database_name,
                        destination_name,
                    )

            with self.service.expansion_timing_scope(
                timing_report,
                "refresh",
                "Reload database table catalog",
            ):
                tables = self.service.list_tables(database_name)

            def apply_expansion_refresh():
                with self.service.expansion_timing_scope(
                    timing_report,
                    "refresh",
                    "Rebuild table navigation controls",
                ):
                    if self.current_db != database_name:
                        return
                    self.populate_table_buttons(tables)

            self.ui_sync(apply_expansion_refresh)
            self.ui(
                self.update_cross_table_expansion_progress,
                "refresh",
                100,
                "Interface refreshed.",
            )
            with self.service.expansion_timing_scope(
                timing_report,
                "refresh",
                "Build completion summary",
            ):
                version_summary = ", ".join(
                    (
                        f"{item['table_name']}={item['version_code']}"
                    )
                    for item in registered_versions
                )
            timing_report["completed_at"] = (
                datetime.now().astimezone().isoformat(timespec="seconds")
            )
            self.ui(
                self.complete_cross_table_expansion_progress,
                "Expansion and versioning completed successfully.",
                timing_report,
            )
            self.ui(
                self.set_status,
                f"Expansion completed: {version_summary}",
                "success",
            )
        except OperationCancelledError:
            self.ui(
                self.mark_cross_table_expansion_cancelled,
                "Expansion cancelled before the data was committed.",
            )
            self.ui(self.set_status, "Expansion cancelled", "error")
        except Exception as exc:
            was_finalizing = self.cross_table_expansion_finalizing
            self.ui(self.close_cross_table_expansion_progress)
            self.ui(self.set_status, "Cross-table expansion error", "error")
            if was_finalizing:
                error_message = (
                    "The data had already been committed to the destinations, but "
                    "version finalization failed. Do not repeat the expansion "
                    "before reconciling version history.\n\n"
                    f"{exc}"
                )
            else:
                error_message = str(exc)
            self.ui(
                self.show_error,
                "Expansion error",
                error_message,
            )
        finally:
            self.ui(self.set_table_action_buttons_busy, False)
            self.clear_cross_table_expansion_cancellation_scope()

    def execute_sql_flow(self):
        if not self.current_db or not self.selected_table_name:
            self.show_error("Error", "Selecione uma tabela primeiro.")
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        sql_request = self.ask_sql_execution_info(database_name, table_name)
        if not sql_request:
            self.set_status(f"Tabela carregada: {table_name}", "success")
            return

        if sql_request.get("mode") == "execute":
            try:
                inspection = self.service.inspect_managed_sql_script(
                    sql_request["sql"],
                    table_name,
                )
            except Exception as exc:
                self.show_error("SQL invalido", str(exc))
                self.set_status(f"Tabela carregada: {table_name}", "success")
                return

            if inspection.get("contains_delete"):
                delete_confirmed = self.ask_yes_no(
                    "Confirmar DELETE",
                    (
                        "Este script contem DELETE.\n\n"
                        f"O botao SQL registrara a receita e a nova versao somente para {table_name}. "
                        "Se as linhas excluidas forem referenciadas por outra tabela, o PostgreSQL "
                        "podera bloquear a operacao ou uma acao ON DELETE CASCADE, SET NULL ou "
                        "SET DEFAULT podera alterar essa outra tabela sem criar uma versao nela. "
                        "Um replay futuro tambem dependera de essas relacoes continuarem compativeis.\n\n"
                        "Continue somente se voce verificou que as linhas a excluir nao possuem "
                        "relacoes em outras tabelas.\n\n"
                        "Confirma que esta ciente e deseja executar o DELETE?"
                    ),
                )
                if not delete_confirmed:
                    self.set_status("DELETE cancelado antes da execucao", "error")
                    return
                sql_request["delete_relationship_acknowledged"] = True

        self.begin_sql_execution_cancellation_scope()
        self.set_sql_execution_busy(True)
        if sql_request.get("mode") == "query":
            self.open_sql_query_progress(database_name, table_name)
            self.run_async(
                self._query_sql_thread,
                database_name,
                table_name,
                sql_request,
            )
        else:
            self.open_sql_execution_progress(database_name, table_name)
            self.run_async(
                self._execute_sql_thread,
                database_name,
                table_name,
                sql_request,
                self._copy_table_preview_filters(),
            )

    def _query_sql_thread(
        self,
        database_name: str,
        table_name: str,
        sql_request: dict,
    ):
        try:
            cancel_event = self.sql_execution_cancel_event
            self.ui(self.set_status, f"Rodando consulta SQL em {table_name}...", "loading")
            self.ui(self.update_sql_execution_progress, "prepare", 0, "Validando consulta e preparando ambiente remoto...")
            result = self.service.execute_readonly_query(
                database_name=database_name,
                sql_script=sql_request["sql"],
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_sql_execution_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            self.ui(self.update_sql_execution_progress, "result", 100, "Resultado carregado.")
            self.ui(self.close_sql_execution_progress)
            self.ui(self.show_sql_query_results, database_name, table_name, result)
            row_count = int(result.get("displayed_row_count") or 0)
            suffix = " (resultado truncado)" if result.get("truncated") else ""
            self.ui(self.set_status, f"Consulta executada: {row_count} linha(s){suffix}", "success")
        except OperationCancelledError:
            self.ui(self.mark_sql_execution_progress_cancelled, "Consulta SQL cancelada pelo usuario.")
            self.ui(self.set_status, "Consulta SQL cancelada", "error")
        except Exception as e:
            self.ui(self.close_sql_execution_progress)
            self.ui(self.set_status, "Erro ao executar consulta SQL", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_sql_execution_busy, False)
            self.clear_sql_execution_cancellation_scope()

    def _execute_sql_thread(
        self,
        database_name: str,
        table_name: str,
        sql_request: dict,
        filters: dict,
    ):
        try:
            cancel_event = self.sql_execution_cancel_event
            self.ui(self.set_status, f"Rodando SQL em {table_name}...", "loading")
            self.ui(self.update_sql_execution_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_sql_execution_progress, "prepare", 100, "Ambiente pronto para executar SQL.")

            result = self.service.execute_sql_script(
                database_name=database_name,
                full_table_name=table_name,
                sql_script=sql_request["sql"],
                delete_relationship_acknowledged=bool(
                    sql_request.get("delete_relationship_acknowledged")
                ),
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_sql_execution_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            schema_name, pure_table_name = split_table_name(table_name)
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=schema_name,
                table_name=pure_table_name,
                version_title=sql_request["version_title"],
                requested_by=sql_request["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=result["sql_recipe"],
                restored_from_version=None,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_sql_execution_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            self.ui(self.update_sql_execution_progress, "refresh", 5, "Atualizando lista de tabelas...")
            tables = self.service.list_tables(database_name, cancel_event=cancel_event)

            if table_name not in tables:
                self.ui(
                    self.update_sql_execution_progress,
                    "refresh",
                    35,
                    "A tabela vinculada nao existe mais. Limpando cache local...",
                )
                self.facet_service.clear_table(database_name, table_name)

                def apply_removed_table():
                    if self.current_db != database_name:
                        return
                    self.populate_table_buttons(tables)
                    if self.selected_table_name == table_name:
                        self.clear_table_selection()

                self.ui(apply_removed_table)
                self.ui(
                    self.update_sql_execution_progress,
                    "refresh",
                    100,
                    "Interface atualizada. A tabela vinculada nao esta mais disponivel.",
                )
                self.ui(self.complete_sql_execution_progress, "Execucao SQL concluida com sucesso.")
                self.ui(
                    self.set_status,
                    f"SQL executado em {next_version}. A tabela selecionada nao esta mais disponivel.",
                    "success",
                )
                return

            self.ui(
                self.update_sql_execution_progress,
                "refresh",
                20,
                "Pulando refresh completo do cache de filtros. A preview sera atualizada agora.",
            )
            self._mark_table_filter_cache_stale_safe(database_name, table_name)
            active_filters = {
                column_name: [dict(item) for item in selected_values]
                for column_name, selected_values in (filters or {}).items()
            }
            self.ui(self.update_sql_execution_progress, "refresh", 55, "Carregando previa atualizada...")
            try:
                details = self.service.get_table_details(
                    database_name,
                    table_name,
                    filters=active_filters,
                    cancel_event=cancel_event,
                )
            except Exception:
                if not active_filters:
                    raise
                active_filters = {}
                self.ui(
                    self.update_sql_execution_progress,
                    "refresh",
                    70,
                    "Filtros anteriores nao se aplicam mais. Recarregando sem filtros...",
                )
                details = self.service.get_table_details(
                    database_name,
                    table_name,
                    cancel_event=cancel_event,
                )

            self.ui(
                self.update_sql_execution_progress,
                "refresh",
                78,
                "Preview atualizada. Carregando historico de versoes...",
            )
            versions = self.service.list_table_versions(database_name, table_name, cancel_event=cancel_event)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )

            def apply_sql_changes():
                if self.current_db != database_name:
                    return
                self.populate_table_buttons(tables)
                if self.selected_table_name != table_name:
                    return
                self.table_preview_filters = active_filters
                self._apply_table_details(table_name, details, versions)

            self.ui(apply_sql_changes)
            self.ui(
                self.update_sql_execution_progress,
                "refresh",
                100,
                "Interface atualizada. O cache de filtros sera recarregado sob demanda.",
            )
            self.ui(self.complete_sql_execution_progress, "Execucao SQL concluida com sucesso.")
            self.ui(self.set_status, f"SQL executado em {next_version}", "success")
        except OperationCancelledError:
            self.ui(self.mark_sql_execution_progress_cancelled, "Execucao SQL cancelada pelo usuario.")
            self.ui(self.set_status, "Execucao SQL cancelada", "error")
        except Exception as e:
            self.ui(self.close_sql_execution_progress)
            self.ui(self.set_status, "Erro ao executar SQL", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_sql_execution_busy, False)
            self.clear_sql_execution_cancellation_scope()

    def reload_table_preview_with_filters(self):
        if not self.current_db or not self.selected_table_name:
            return

        database_name = self.current_db
        table_name = self.selected_table_name
        filters = self._copy_table_preview_filters()
        self.set_status(f"Aplicando filtro em {table_name}...", "loading")
        self.run_async(
            self._reload_table_preview_with_filters_thread,
            database_name,
            table_name,
            filters,
        )

    def _reload_table_preview_with_filters_thread(self, database_name: str, table_name: str, filters: dict):
        try:
            details = self.service.get_table_details(
                database_name,
                table_name,
                filters=filters,
            )
            self.ui(self._apply_table_preview_details, table_name, details)
            self.ui(self.set_status, f"Tabela filtrada: {table_name}", "success")
        except Exception as e:
            self.ui(self.set_status, "Erro ao aplicar filtro", "error")
            self.ui(self.show_error, "Error", str(e))

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------

    def create_database_flow(self):
        if not self.can_create_db:
            self.show_error(
                "Permissão insuficiente",
                (
                    f"O usuário {self.service.sql_username or 'atual'} ainda não tem CREATEDB. "
                    f"Rode uma vez:\nALTER ROLE {self.service.sql_username or 'seu_usuario'} CREATEDB;"
                )
            )
            return

        name = self.ask_text_input(
            title="Criar base",
            text="Nome da nova base de dados:",
        )

        if not name or not name.strip():
            return

        self.run_async(self._create_database_thread, name.strip())

    def _create_database_thread(self, db_name: str):
        try:
            self.ui(self.set_status, f"Criando base {db_name}...", "loading")
            self.service.ensure_admin_schema()
            self.service.create_database(db_name)

            dbs = self.service.list_databases()
            self.ui(self.populate_database_buttons, dbs)
            self.ui(self.set_status, f"Base criada: {db_name}", "success")

        except Exception as e:
            msg = str(e)
            if "permission denied to create database" in msg.lower():
                role_name = self.service.sql_username or "seu_usuario"
                msg += f"\n\nConceda a permissão com:\nALTER ROLE {role_name} CREATEDB;"
            self.ui(self.set_status, "Erro ao criar base", "error")
            self.ui(self.show_error, "Error", msg)

    def create_table_flow(self):
        if not self.current_db:
            self.show_error("Error", "Selecione uma base primeiro.")
            return

        table_info = self.ask_create_table_info()
        if not table_info:
            return

        self.run_async(
            self._create_table_thread,
            table_info["table_name"].strip(),
            table_info["version_title"].strip(),
            table_info["requested_by"].strip(),
        )

    def _create_table_thread(self, full_table_name: str, version_title: str, requested_by: str):
        try:
            self.ui(self.set_status, f"Criando tabela {full_table_name}...", "loading")
            self.service.ensure_admin_schema()

            created = self.service.create_table(self.current_db, full_table_name)
            self.service.register_table_version(
                database_name=self.current_db,
                schema_name=created["schema_name"],
                table_name=created["table_name"],
                version_title=version_title,
                requested_by=requested_by,
                workstation_name=socket.gethostname(),
                sql_recipe=created["sql_recipe"],
                restored_from_version=None,
                base_history_log="",
            )

            self._load_tables_thread(self.current_db)
            self.ui(self.set_status, f"Tabela criada: {created['schema_name']}.{created['table_name']}", "success")

        except Exception as e:
            self.ui(self.set_status, "Erro ao criar tabela", "error")
            self.ui(self.show_error, "Error", str(e))

    @staticmethod
    def _format_raw_import_batch_label(file_paths) -> str:
        normalized_paths = [path for path in (file_paths or []) if path]
        if not normalized_paths:
            return "Raw"
        if len(normalized_paths) == 1:
            return os.path.basename(normalized_paths[0])
        return f"{len(normalized_paths)} arquivos raw"

    @staticmethod
    def _merge_version_notes(*notes) -> str | None:
        merged_notes = [str(note).strip() for note in notes if str(note or "").strip()]
        if not merged_notes:
            return None
        return "\n\n".join(merged_notes)

    @staticmethod
    def _build_batch_raw_source_notes(raw_source: dict) -> str | None:
        source_files = list(raw_source.get("source_files") or [])
        if len(source_files) <= 1:
            return None

        lines = [f"Batch raw files ({len(source_files)}):"]
        for source_file in source_files:
            file_name = str(source_file.get("file_name") or "").strip()
            if not file_name:
                continue
            line = f"- {file_name}"
            json_data_path = str(source_file.get("json_data_path") or "").strip()
            if json_data_path:
                line += f" -> {json_data_path}"
            lines.append(line)

        return "\n".join(lines) if len(lines) > 1 else None

    def import_raw_file_flow(self):
        if not self.current_db or not self.selected_table_name:
            self.show_error("Error", "Selecione uma tabela primeiro.")
            return

        raw_source_info = self.ask_raw_source_selection()
        if not raw_source_info:
            return

        source_kind = raw_source_info["source_kind"]
        raw_schema = raw_source_info["raw_schema"]

        if source_kind == "json":
            self.open_raw_json_structure_flow(raw_schema)
            return

        self.import_raw_excel_file_flow(raw_schema)

    def import_raw_excel_file_flow(self, raw_schema: str):
        file_paths = self.ask_open_filenames(
            title="Selecionar arquivo bruto",
            filetypes=[
                ("Arquivos suportados", "*.csv *.txt *.xls *.xlsx *.xlsm"),
                ("CSV", "*.csv"),
                ("Texto", "*.txt"),
                ("Excel 97-2003", "*.xls"),
                ("Excel", "*.xlsx *.xlsm"),
            ],
        )
        if not file_paths:
            return

        selected_excel_sheets_by_file = {}
        excel_sheet_counts_by_file = {}
        for file_path in file_paths:
            if not self.service.is_excel_file(file_path):
                continue

            try:
                excel_sheet_names = self.service.get_excel_sheet_names(file_path)
            except Exception as exc:
                self.show_error("Erro ao ler Excel", str(exc))
                return

            if not excel_sheet_names:
                self.show_error("Erro ao ler Excel", "Nenhuma aba foi encontrada no arquivo selecionado.")
                return

            excel_sheet_counts_by_file[file_path] = len(excel_sheet_names)
            selected_excel_sheets = excel_sheet_names
            if len(excel_sheet_names) > 1:
                selected_excel_sheets = self.ask_excel_sheet_selection(
                    os.path.basename(file_path),
                    excel_sheet_names,
                )
                if not selected_excel_sheets:
                    return
            selected_excel_sheets_by_file[file_path] = selected_excel_sheets

        version_info = self.ask_version_info(
            title="Nova versao Raw",
            version_label="Titulo desta versao"
        )
        if not version_info:
            return

        self.begin_raw_import_cancellation_scope()
        self.set_raw_import_busy(True)
        self.open_raw_progress(self._format_raw_import_batch_label(file_paths), self.selected_table_name)

        self.run_async(
            self._import_raw_file_thread,
            file_paths,
            raw_schema,
            version_info["version_title"],
            version_info["requested_by"],
            selected_excel_sheets_by_file,
            excel_sheet_counts_by_file,
        )

    def open_raw_json_structure_flow(self, raw_schema: str):
        file_paths = self.ask_open_filenames(
            title="Selecionar arquivo JSON",
            filetypes=[
                ("JSON", "*.json"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not file_paths:
            return

        preview_file_path = file_paths[0]
        try:
            with open(preview_file_path, "r", encoding="utf-8-sig") as json_file:
                json_data = json.load(json_file)
        except UnicodeDecodeError:
            try:
                with open(preview_file_path, "r", encoding="utf-8") as json_file:
                    json_data = json.load(json_file)
            except Exception as exc:
                self.show_error("Erro ao ler JSON", str(exc))
                return
        except Exception as exc:
            self.show_error("Erro ao ler JSON", str(exc))
            return

        preview_name = os.path.basename(preview_file_path)
        if len(file_paths) > 1:
            preview_name = f"{preview_name} (+{len(file_paths) - 1} arquivos)"
        dialog_result = self.show_json_structure_dialog(preview_name, json_data)
        if dialog_result == "add":
            self.import_raw_json_file_flow(file_paths, raw_schema)
            return

        if self.selected_table_name:
            self.set_status(
                f"Estrutura JSON aberta: {self._format_raw_import_batch_label(file_paths)}",
                "success",
            )

    def import_raw_json_file_flow(self, file_paths, raw_schema: str):
        version_info = self.ask_version_info(
            title="Nova versao Raw",
            version_label="Titulo desta versao"
        )
        if not version_info:
            return

        self.begin_raw_import_cancellation_scope()
        self.set_raw_import_busy(True)
        self.open_raw_progress(self._format_raw_import_batch_label(file_paths), self.selected_table_name)

        self.run_async(
            self._import_raw_json_file_thread,
            list(file_paths),
            raw_schema,
            version_info["version_title"],
            version_info["requested_by"],
        )

    def _import_raw_file_thread(
        self,
        file_paths,
        raw_schema: str,
        version_title: str,
        requested_by: str,
        selected_excel_sheets_by_file=None,
        excel_sheet_counts_by_file=None,
    ):
        try:
            if not self.current_db or not self.selected_table_name:
                raise RuntimeError("Nenhuma tabela selecionada.")

            file_paths = [path for path in (file_paths or []) if path]
            if not file_paths:
                raise RuntimeError("Nenhum arquivo Raw foi selecionado.")

            current_db = self.current_db
            selected_table_name = self.selected_table_name
            batch_label = self._format_raw_import_batch_label(file_paths)
            self.ui(self.set_status, f"Importando Raw de {batch_label}...", "loading")
            cancel_event = self.raw_import_cancel_event
            self.ui(self.update_raw_progress, "schema", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_raw_progress, "schema", 100, "Ambiente pronto para a importacao.")

            review_sources = []
            total_files = len(file_paths)
            for file_index, file_path in enumerate(file_paths, start=1):
                file_name = os.path.basename(file_path)
                selected_excel_sheets = (selected_excel_sheets_by_file or {}).get(file_path)

                def progress_callback(stage_key, progress, message, current_file_name=file_name, current_index=file_index):
                    scaled_progress = progress
                    if total_files > 1 and stage_key == "source":
                        scaled_progress = ((current_index - 1) * 100 + progress) / total_files
                    if total_files > 1:
                        message = f"[{current_index}/{total_files}] {current_file_name}: {message}"
                    self.ui(
                        self.update_raw_progress,
                        stage_key,
                        scaled_progress,
                        message,
                    )

                review_sources.append(
                    self.service.load_raw_source_for_review(
                        file_path,
                        raw_schema=raw_schema,
                        selected_sheet_names=selected_excel_sheets,
                        cancel_event=cancel_event,
                        progress_callback=progress_callback,
                    )
                )

            review_source = (
                review_sources[0]
                if len(review_sources) == 1
                else self.service.combine_raw_sources(review_sources)
            )
            prepared_import = {
                "raw_source": review_source,
                "version_notes": None,
            }

            if review_source["records"]:
                require_header_per_sheet = len(file_paths) > 1 or any(
                    int(sheet_count) > 1
                    for sheet_count in (excel_sheet_counts_by_file or {}).values()
                )
                self.ui(
                    self.update_raw_progress,
                    "review",
                    100,
                    "Analise pronta. Revise as sugestoes de header e noise para continuar.",
                )
                review_info = self.ui_sync(
                    self.ask_raw_import_review,
                    batch_label,
                    review_source.get("total_records", len(review_source["records"])),
                    review_source.get("review_candidates", {}),
                    self.raw_progress_dialog if self.raw_progress_dialog and self.raw_progress_dialog.winfo_exists() else self,
                    review_source.get("preview_count"),
                    review_source.get("records", []),
                    review_source.get("sheets", []),
                    require_header_per_sheet,
                )
                if not review_info:
                    raise OperationCancelledError("Importacao cancelada durante a revisao de header/noise.")

                if (
                    review_info["header_row_numbers"]
                    or review_info["noise_row_numbers"]
                    or require_header_per_sheet
                ):
                    prepared_import = self.service.prepare_raw_source_for_import(
                        review_source,
                        header_row_numbers=review_info["header_row_numbers"],
                        noise_row_numbers=review_info["noise_row_numbers"],
                        require_header_per_sheet=require_header_per_sheet,
                    )
            else:
                self.ui(
                    self.update_raw_progress,
                    "review",
                    100,
                    "Nenhum registro Raw importado para revisar.",
                )

            prepared_import["version_notes"] = self._merge_version_notes(
                prepared_import.get("version_notes"),
                self._build_batch_raw_source_notes(prepared_import["raw_source"]),
            )

            result = self.service.provision_table_from_raw_file(
                database_name=current_db,
                full_table_name=selected_table_name,
                file_path=file_paths[0],
                raw_source=prepared_import["raw_source"],
                cancel_event=cancel_event,
                post_commit_callback=self.begin_raw_import_finalization,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            # O COPY ja foi confirmado. Daqui em diante, interromper deixaria
            # dados persistidos sem o dump e o registro de versao correspondentes.
            cancel_event = None

            next_version_code = self.service.get_next_table_version(
                current_db,
                result["schema_name"],
                result["table_name"],
                cancel_event=cancel_event,
            )
            raw_dump_path = self.service.create_raw_snapshot_dump(
                database_name=current_db,
                full_table_name=selected_table_name,
                raw_source=result["raw_source"],
                cancel_event=cancel_event,
                version_code=next_version_code,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            sql_recipe = self.service.build_raw_version_recipe_from_source(
                database_name=current_db,
                full_table_name=selected_table_name,
                raw_source=result["raw_source"],
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            next_version = self.service.register_table_version(
                database_name=current_db,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_title,
                requested_by=requested_by,
                workstation_name=socket.gethostname(),
                sql_recipe=sql_recipe,
                restored_from_version=None,
                raw_dump_path=raw_dump_path,
                raw_hash=result["raw_source"]["file_hash"],
                raw_ingested_at=result["raw_source"]["ingested_at"],
                raw_schema=result["raw_source"].get("raw_schema"),
                version_notes=prepared_import["version_notes"],
                version_code=next_version_code,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")

            self.ui(
                self.update_raw_progress,
                "refresh",
                0,
                "Pulando refresh completo do cache de filtros. A preview sera atualizada agora.",
            )
            self._mark_table_filter_cache_stale_safe(current_db, selected_table_name)
            active_filters = {}
            self.ui(self.update_raw_progress, "refresh", 35, "Atualizando dados exibidos na interface...")
            details = self.service.get_table_details(
                current_db,
                selected_table_name,
                filters=active_filters,
                cancel_event=cancel_event,
            )
            self.ui(self.update_raw_progress, "refresh", 65, "Dados da tabela atualizados. Carregando versoes...")
            versions = self.service.list_table_versions(current_db, selected_table_name, cancel_event=cancel_event)
            if versions:
                self.ui(self.update_raw_progress, "refresh", 78, "Carregando detalhe da versao mais recente...")
                versions[0].update(
                    self.service.get_table_version_detail(
                        current_db,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )
            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")
            self.ui(self.update_raw_progress, "refresh", 90, "Aplicando atualizacao na tela...")

            def apply_raw_import():
                if self.current_db != current_db or self.selected_table_name != selected_table_name:
                    return
                self.table_preview_filters = active_filters
                self._apply_table_details(selected_table_name, details, versions)

            self.ui(apply_raw_import)
            self.ui(
                self.update_raw_progress,
                "refresh",
                100,
                "Interface atualizada. O cache de filtros sera recarregado sob demanda.",
            )
            self.ui(self.complete_raw_progress, "Importacao concluida com sucesso.")
            self.ui(
                self.set_status,
                (
                    f"Raw importado: {batch_label} "
                    f"({result['row_count']} linhas novas, {result.get('total_row_count', result['row_count'])} no total) "
                    f"em {next_version}"
                ),
                "success",
            )

        except OperationCancelledError:
            self.ui(self.mark_raw_progress_cancelled, "Importacao cancelada pelo usuario.")
            self.ui(self.set_status, "Importacao Raw cancelada", "error")
        except Exception as e:
            traceback.print_exc()
            self.ui(self.close_raw_progress)
            self.ui(self.set_status, "Erro ao importar Raw", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_raw_import_busy, False)
            self.clear_raw_import_cancellation_scope()

    # ------------------------------------------------------------------
    # Exclusão
    # ------------------------------------------------------------------

    def _import_raw_json_file_thread(
        self,
        file_paths,
        raw_schema: str,
        version_title: str,
        requested_by: str,
    ):
        try:
            if not self.current_db or not self.selected_table_name:
                raise RuntimeError("Nenhuma tabela selecionada.")

            file_paths = [path for path in (file_paths or []) if path]
            if not file_paths:
                raise RuntimeError("Nenhum arquivo JSON foi selecionado.")

            current_db = self.current_db
            selected_table_name = self.selected_table_name
            batch_label = self._format_raw_import_batch_label(file_paths)
            self.ui(self.set_status, f"Importando Raw de {batch_label}...", "loading")
            cancel_event = self.raw_import_cancel_event
            self.ui(self.update_raw_progress, "schema", 0, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_raw_progress, "schema", 100, "Ambiente pronto para a importacao.")

            prepared_imports = []
            total_files = len(file_paths)
            for file_index, file_path in enumerate(file_paths, start=1):
                file_name = os.path.basename(file_path)

                def progress_callback(stage_key, progress, message, current_file_name=file_name, current_index=file_index):
                    scaled_progress = progress
                    if total_files > 1 and stage_key == "source":
                        scaled_progress = ((current_index - 1) * 100 + progress) / total_files
                    if total_files > 1:
                        message = f"[{current_index}/{total_files}] {current_file_name}: {message}"
                    self.ui(
                        self.update_raw_progress,
                        stage_key,
                        scaled_progress,
                        message,
                    )

                prepared_imports.append(
                    self.service.prepare_json_raw_source_for_import(
                        file_path,
                        raw_schema=raw_schema,
                        cancel_event=cancel_event,
                        progress_callback=progress_callback,
                    )
                )

            combined_raw_source = (
                prepared_imports[0]["raw_source"]
                if len(prepared_imports) == 1
                else self.service.combine_raw_sources(
                    [prepared_item["raw_source"] for prepared_item in prepared_imports]
                )
            )
            prepared_import = {
                "raw_source": combined_raw_source,
                "version_notes": self._merge_version_notes(
                    *(prepared_item.get("version_notes") for prepared_item in prepared_imports),
                    self._build_batch_raw_source_notes(combined_raw_source),
                ),
            }

            json_path = prepared_import["raw_source"].get("json_data_path")
            if len(file_paths) > 1:
                review_message = f"{len(file_paths)} arquivos JSON convertidos para linhas raw."
            else:
                review_message = "Estrutura JSON convertida para linhas raw."
            if json_path and len(file_paths) == 1:
                review_message = f"Estrutura JSON convertida a partir de {json_path}."
            self.ui(self.update_raw_progress, "review", 100, review_message)

            result = self.service.provision_table_from_raw_file(
                database_name=current_db,
                full_table_name=selected_table_name,
                file_path=file_paths[0],
                raw_source=prepared_import["raw_source"],
                cancel_event=cancel_event,
                post_commit_callback=self.begin_raw_import_finalization,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            # O COPY ja foi confirmado. Daqui em diante, interromper deixaria
            # dados persistidos sem o dump e o registro de versao correspondentes.
            cancel_event = None

            next_version_code = self.service.get_next_table_version(
                current_db,
                result["schema_name"],
                result["table_name"],
                cancel_event=cancel_event,
            )
            raw_dump_path = self.service.create_raw_snapshot_dump(
                database_name=current_db,
                full_table_name=selected_table_name,
                raw_source=result["raw_source"],
                cancel_event=cancel_event,
                version_code=next_version_code,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            sql_recipe = self.service.build_raw_version_recipe_from_source(
                database_name=current_db,
                full_table_name=selected_table_name,
                raw_source=result["raw_source"],
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            next_version = self.service.register_table_version(
                database_name=current_db,
                schema_name=result["schema_name"],
                table_name=result["table_name"],
                version_title=version_title,
                requested_by=requested_by,
                workstation_name=socket.gethostname(),
                sql_recipe=sql_recipe,
                restored_from_version=None,
                raw_dump_path=raw_dump_path,
                raw_hash=result["raw_source"]["file_hash"],
                raw_ingested_at=result["raw_source"]["ingested_at"],
                raw_schema=result["raw_source"].get("raw_schema"),
                version_notes=prepared_import["version_notes"],
                version_code=next_version_code,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_raw_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")

            self.ui(
                self.update_raw_progress,
                "refresh",
                0,
                "Pulando refresh completo do cache de filtros. A preview sera atualizada agora.",
            )
            self._mark_table_filter_cache_stale_safe(current_db, selected_table_name)
            active_filters = {}
            self.ui(self.update_raw_progress, "refresh", 35, "Atualizando dados exibidos na interface...")
            details = self.service.get_table_details(
                current_db,
                selected_table_name,
                filters=active_filters,
                cancel_event=cancel_event,
            )
            self.ui(self.update_raw_progress, "refresh", 65, "Dados da tabela atualizados. Carregando versoes...")
            versions = self.service.list_table_versions(current_db, selected_table_name, cancel_event=cancel_event)
            if versions:
                self.ui(self.update_raw_progress, "refresh", 78, "Carregando detalhe da versao mais recente...")
                versions[0].update(
                    self.service.get_table_version_detail(
                        current_db,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                        cancel_event=cancel_event,
                    )
                )
            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")
            self.ui(self.update_raw_progress, "refresh", 90, "Aplicando atualizacao na tela...")

            def apply_raw_import():
                if self.current_db != current_db or self.selected_table_name != selected_table_name:
                    return
                self.table_preview_filters = active_filters
                self._apply_table_details(selected_table_name, details, versions)

            self.ui(apply_raw_import)
            self.ui(
                self.update_raw_progress,
                "refresh",
                100,
                "Interface atualizada. O cache de filtros sera recarregado sob demanda.",
            )
            self.ui(self.complete_raw_progress, "Importacao concluida com sucesso.")
            self.ui(
                self.set_status,
                (
                    f"Raw importado: {batch_label} "
                    f"({result['row_count']} linhas novas, {result.get('total_row_count', result['row_count'])} no total) "
                    f"em {next_version}"
                ),
                "success",
            )

        except OperationCancelledError:
            self.ui(self.mark_raw_progress_cancelled, "Importacao cancelada pelo usuario.")
            self.ui(self.set_status, "Importacao Raw cancelada", "error")
        except Exception as e:
            traceback.print_exc()
            self.ui(self.close_raw_progress)
            self.ui(self.set_status, "Erro ao importar Raw", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.ui(self.set_raw_import_busy, False)
            self.clear_raw_import_cancellation_scope()

    def delete_database_flow(self, db_name: str):
        if db_name == CONTROL_DB:
            self.show_error(
                "Ação bloqueada",
                f"A base {CONTROL_DB} é usada pela aplicação como base de controle e não pode ser excluída."
            )
            return

        info = self.ask_admin_action(f"Excluir base de dados: {db_name}")
        if not info:
            return

        if not self.validate_admin_password(info["password"]):
            self.show_error("Error", "Senha inválida.")
            return

        if not self.ask_final_delete_confirmation(
            f"Base de dados {db_name}",
            confirmation_text=db_name,
        ):
            return

        self.run_async(self._delete_database_thread, db_name, info)

    def _delete_database_thread(self, db_name: str, info: dict):
        try:
            self.ui(self.set_status, f"Preparando exclusão de {db_name}...", "loading")
            self.service.ensure_remote_admin_dirs()
            self.service.ensure_admin_schema()
            self.service.delete_database(db_name, info)
            self.facet_service.clear_database(db_name)

            dbs = self.service.list_databases()

            def after_delete():
                if self.current_db == db_name:
                    self.current_db = None
                    self.add_table_button.configure(state="disabled")
                    self.clear_table_buttons()
                    self.clear_table_selection()
                self.populate_database_buttons(dbs)

            self.ui(after_delete)
            self.ui(self.set_status, f"Base removida: {db_name}", "success")

        except Exception as e:
            self.ui(self.set_status, "Erro ao excluir base", "error")
            self.ui(self.show_error, "Error", str(e))

    def delete_table_flow(self, full_table_name: str):
        if not self.current_db:
            self.show_error("Error", "Nenhuma base selecionada.")
            return

        info = self.ask_admin_action(f"Excluir tabela: {full_table_name}")
        if not info:
            return

        if not self.validate_admin_password(info["password"]):
            self.show_error("Error", "Senha inválida.")
            return

        if not self.ask_final_delete_confirmation(
            f"Tabela {full_table_name}",
            (
                "Essa acao arquivara o historico existente, se houver, "
                "e depois executara a exclusao."
            ),
            confirmation_text=full_table_name,
        ):
            return

        self.begin_table_delete_cancellation_scope()
        self.open_table_delete_progress(self.current_db, full_table_name)
        self.run_async(self._delete_table_thread, full_table_name, info)

    def _delete_table_thread(self, full_table_name: str, info: dict):
        try:
            if not self.current_db:
                raise RuntimeError("Nenhuma base selecionada.")

            current_db = self.current_db
            cancel_event = self.table_delete_cancel_event
            self.ui(self.set_status, f"Preparando exclusão de {full_table_name}...", "loading")
            self.ui(self.update_table_delete_progress, "prepare", 0, "Preparando ambiente remoto...")
            self.service.ensure_remote_admin_dirs()
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.ui(self.update_table_delete_progress, "prepare", 100, "Ambiente pronto para a exclusao.")
            self.service.delete_table(
                current_db,
                full_table_name,
                info,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_table_delete_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")

            self.ui(self.update_table_delete_progress, "refresh", 0, "Invalidando cache local de filtros...")
            try:
                self.facet_service.mark_table_stale(current_db, full_table_name)
            except Exception:
                pass

            self.ui(self.update_table_delete_progress, "refresh", 20, "Atualizando lista de tabelas...")
            tables = self.service.list_tables(current_db, cancel_event=cancel_event)
            self.ui(self.update_table_delete_progress, "refresh", 70, "Aplicando atualizacao na interface...")

            def after_delete():
                if self.selected_table_name == full_table_name:
                    self.clear_table_selection()
                self.populate_table_buttons(tables)

            self.ui(after_delete)
            self.run_async(self._clear_deleted_table_filter_cache_thread, current_db, full_table_name)
            self.ui(self.update_table_delete_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_table_delete_progress, "Exclusao concluida com sucesso.")
            self.ui(self.set_status, f"Tabela removida: {full_table_name}", "success")

        except OperationCancelledError:
            self.ui(self.mark_table_delete_progress_cancelled, "Exclusao cancelada pelo usuario.")
            self.ui(self.set_status, "Exclusao de tabela cancelada", "error")
        except Exception as e:
            self.ui(self.close_table_delete_progress)
            self.ui(self.set_status, "Erro ao excluir tabela", "error")
            self.ui(self.show_error, "Error", str(e))
        finally:
            self.clear_table_delete_cancellation_scope()

    def _clear_table_filter_cache_thread(self, database_name: str, full_table_name: str):
        try:
            self.facet_service.clear_table(database_name, full_table_name)
            self.facet_service.checkpoint_cache()
        except Exception:
            pass

    def _clear_deleted_table_filter_cache_thread(self, database_name: str, full_table_name: str):
        self._clear_table_filter_cache_thread(database_name, full_table_name)

    def open_dump_manager_flow(self):
        if not self.service.is_connected():
            self.show_error("Error", "Conecte-se a VM antes de gerenciar dumps.")
            return

        if self.dump_artifacts_dialog and self.dump_artifacts_dialog.winfo_exists():
            self.dump_artifacts_dialog.focus()
            self.dump_artifacts_dialog.lift()
            self.refresh_dump_artifacts_dialog(self.dump_artifacts_dialog)
            return

        dialog = DumpArtifactsDialog(
            self,
            on_refresh=lambda: self.refresh_dump_artifacts_dialog(dialog),
            on_restore=lambda dump_item: self.restore_dump_artifact_flow(dump_item, dialog),
            on_delete=lambda dump_item: self.delete_dump_artifact_flow(dump_item, dialog),
        )
        dialog.bind("<Destroy>", lambda event, current=dialog: self._handle_dump_dialog_destroy(event, current))
        self.dump_artifacts_dialog = dialog
        self.refresh_dump_artifacts_dialog(dialog)

    def _handle_dump_dialog_destroy(self, event, dialog):
        if event.widget == dialog and self.dump_artifacts_dialog == dialog:
            self.dump_artifacts_dialog = None

    def refresh_dump_artifacts_dialog(self, dialog=None):
        target_dialog = dialog or self.dump_artifacts_dialog
        if not target_dialog or not target_dialog.winfo_exists():
            return

        target_dialog.set_busy(True, "Carregando lista de dumps remotos...")
        self.run_async(self._load_dump_artifacts_thread, target_dialog)

    def _load_dump_artifacts_thread(self, dialog):
        try:
            items = self.service.list_dump_artifacts()

            def apply_items():
                if not dialog or not dialog.winfo_exists():
                    return
                dialog.set_busy(False)
                dialog.set_dump_items(items)

            self.ui(apply_items)

        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao carregar dumps.")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def restore_dump_artifact_flow(self, dump_item: dict, dialog=None):
        target_dialog = dialog or self.dump_artifacts_dialog
        if target_dialog and target_dialog.winfo_exists():
            target_dialog.set_busy(True, f"Preparando restauracao: {dump_item['file_name']}...")

        self.run_async(self._prepare_dump_restore_thread, dump_item, target_dialog)

    def _prepare_dump_restore_thread(self, dump_item: dict, dialog):
        dump_path = dump_item["full_path"]

        try:
            self.service.ensure_admin_schema()
            restore_info = self.service.get_dump_artifact_restore_info(dump_path)
            if not restore_info:
                raise RuntimeError(
                    "Este dump nao esta vinculado a uma versao restauravel. "
                    "A restauracao pelo gerenciador suporta apenas dumps de raw_versions."
                )

            database_name = restore_info["database_name"]
            if not self.service.database_exists(database_name):
                raise RuntimeError(
                    f"A base {database_name} nao existe. Recrie a base antes de restaurar este dump."
                )

            default_target = restore_info["default_restore_full_table_name"]
            conflict_message = None

            while True:
                if self.service.table_exists(database_name, default_target):
                    conflict_message = (
                        f"A tabela {default_target} ja existe na base {database_name}. "
                        "Escolha outro nome para a restauracao."
                    )

                restore_request = self.ui_sync(
                    self.ask_dump_restore_info,
                    restore_info,
                    default_target,
                    conflict_message,
                )
                if not restore_request:
                    def apply_cancel():
                        if dialog and dialog.winfo_exists():
                            dialog.set_busy(False, "Restauracao cancelada.")

                    self.ui(apply_cancel)
                    return

                target_full_table_name = restore_request["target_full_table_name"].strip()
                if "." not in target_full_table_name:
                    target_full_table_name = f"{restore_info['schema_name']}.{target_full_table_name}"

                if self.service.table_exists(database_name, target_full_table_name):
                    default_target = target_full_table_name
                    conflict_message = (
                        f"A tabela {target_full_table_name} ja existe na base {database_name}. "
                        "Escolha outro nome para a restauracao."
                    )
                    continue

                restore_request["target_full_table_name"] = target_full_table_name
                break

            if dialog and dialog.winfo_exists():
                self.ui(dialog.set_busy, True, f"Restaurando dump remoto: {dump_item['file_name']}...")

            self._restore_dump_artifact_thread(dump_item, restore_info, restore_request, dialog)

        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao preparar restauracao.")
                self.set_status("Erro ao restaurar dump", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def _restore_dump_artifact_thread(self, dump_item: dict, restore_info: dict, restore_request: dict, dialog):
        dump_path = dump_item["full_path"]
        database_name = restore_info["database_name"]
        target_full_table_name = restore_request["target_full_table_name"]
        target_schema_name, target_table_name = split_table_name(target_full_table_name)
        try:
            self.ui(self.set_status, f"Restaurando dump {dump_item['file_name']}...", "loading")
            self.service.restore_dump_artifact_to_table(dump_path, target_full_table_name)

            restore_notes = (
                f"Dump restaurado de {restore_info['schema_name']}.{restore_info['table_name']} "
                f"(versao {restore_info['version_code']}).\n"
                f"Arquivo: {dump_path}"
            )
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=target_schema_name,
                table_name=target_table_name,
                version_title=restore_request["version_title"],
                requested_by=restore_request["requested_by"],
                workstation_name=socket.gethostname(),
                sql_recipe=self.service.build_raw_load_recipe(target_full_table_name),
                restored_from_version=restore_info["version_code"],
                raw_dump_path=dump_path,
                raw_hash=restore_info.get("raw_hash"),
                raw_ingested_at=restore_info.get("raw_ingested_at"),
                raw_schema=restore_info.get("raw_schema"),
                version_notes=restore_notes,
            )
            self._mark_table_filter_cache_stale_safe(database_name, target_full_table_name)

            items = self.service.list_dump_artifacts()
            tables = None
            details = None
            versions = None
            if self.current_db == database_name:
                tables = self.service.list_tables(database_name)
                details = self.service.get_table_details(database_name, target_full_table_name)
                versions = self.service.list_table_versions(database_name, target_full_table_name)
                if versions:
                    versions[0].update(
                        self.service.get_table_version_detail(
                            database_name,
                            versions[0]["schema_name"],
                            versions[0]["table_name"],
                            versions[0]["version_code"],
                        )
                    )

            def apply_success():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False)
                    dialog.set_dump_items(items)
                    dialog.set_status(f"Dump restaurado: {dump_item['file_name']}")

                if self.current_db == database_name and tables is not None:
                    self.populate_table_buttons(tables)
                    if details is not None and versions is not None:
                        self._apply_table_details(target_full_table_name, details, versions)

                self.set_status(
                    f"Dump restaurado em {target_full_table_name} ({next_version})",
                    "success",
                )

            self.ui(apply_success)

        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao restaurar dump.")
                self.set_status("Erro ao restaurar dump", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    def delete_dump_artifact_flow(self, dump_item: dict, dialog=None):
        dump_path = dump_item["full_path"]
        info = self.ask_admin_action(f"Excluir dump: {dump_item['file_name']}")
        if not info:
            return

        if not self.validate_admin_password(info["password"]):
            self.show_error("Error", "Senha invalida.")
            return

        if not self.ask_dump_delete_confirmation(dump_path, confirmation_text=dump_item["file_name"]):
            return

        target_dialog = dialog or self.dump_artifacts_dialog
        if target_dialog and target_dialog.winfo_exists():
            target_dialog.set_busy(True, f"Excluindo dump remoto: {dump_item['file_name']}...")

        self.run_async(self._delete_dump_artifact_thread, dump_item, info, target_dialog)

    def _delete_dump_artifact_thread(self, dump_item: dict, info: dict, dialog):
        dump_path = dump_item["full_path"]
        file_name = dump_item["file_name"]

        try:
            self.ui(self.set_status, f"Excluindo dump {file_name}...", "loading")
            self.service.ensure_admin_schema()
            self.service.delete_dump_artifact(dump_path, info)
            items = self.service.list_dump_artifacts()

            def apply_success():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False)
                    dialog.set_dump_items(items)
                    dialog.set_status(f"Dump excluido: {file_name}")
                self.set_status(f"Dump excluido: {file_name}", "success")

            self.ui(apply_success)

        except Exception as e:
            def apply_error():
                if dialog and dialog.winfo_exists():
                    dialog.set_busy(False, "Erro ao excluir dump.")
                self.set_status("Erro ao excluir dump", "error")
                self.show_error("Error", str(e))

            self.ui(apply_error)

    # ------------------------------------------------------------------
    # Versionamento / restauração
    # ------------------------------------------------------------------

    def restore_selected_version_flow(self):
        if not self.selected_version_record or not self.selected_table_name:
            self.show_error("Error", "Nenhuma versão selecionada.")
            return

        def continue_restore(version):
            confirm = self.ask_yes_no(
                "Restaurar versão",
                (
                    f"Deseja restaurar a versão {version['version_code']}?\n\n"
                    "Isso recriará a estrutura da tabela selecionada e gerará uma nova versão sequencial.\n"
                    "Os dados atuais da tabela serão perdidos."
                )
            )
            if not confirm:
                return

            version_info = self.ask_version_info(
                title="Restaurar versao",
                version_label="Titulo da nova versao restaurada",
            )
            if not version_info:
                return

            database_name = self.current_db
            full_table_name = f"{version['schema_name']}.{version['table_name']}"
            self.begin_restore_version_cancellation_scope()
            self.open_restore_version_progress(full_table_name, version["version_code"])
            self.run_async(
                self._restore_version_thread,
                database_name,
                dict(version),
                version_info["version_title"].strip(),
                version_info["requested_by"].strip(),
                self._copy_table_preview_filters(),
            )

        version_code = self.selected_version_record["version_code"]
        self._with_selected_version_detail(
            continue_restore,
            loading_message=f"Carregando detalhes da versão {version_code}...",
        )

    def restore_selected_version_to_new_table_flow(self):
        if not self.selected_version_record or not self.selected_table_name:
            self.show_error("Error", "Nenhuma versao selecionada.")
            return

        def continue_restore(version):
            database_name = self.current_db
            source_table_name = self.service.resolve_original_table_name(version["table_name"])
            default_target = f"public.{source_table_name}_restore_{version['version_code']}"
            conflict_message = None

            while True:
                restore_request = self.ask_restore_version_to_new_table_info(
                    version,
                    default_target,
                    conflict_message,
                )
                if not restore_request:
                    return

                target_full_table_name = restore_request["target_full_table_name"].strip()
                if not target_full_table_name:
                    self.show_error("Error", "Digite o nome da nova tabela.")
                    return

                if "." in target_full_table_name:
                    target_schema_name, target_table_name = split_table_name(target_full_table_name)
                    if target_schema_name != "public":
                        conflict_message = "A nova tabela deve ser criada no schema public."
                        default_target = target_full_table_name
                        continue
                    if not target_table_name:
                        conflict_message = "Digite um nome valido para a nova tabela."
                        default_target = target_full_table_name
                        continue
                else:
                    target_table_name = target_full_table_name
                    target_full_table_name = f"public.{target_table_name}"

                if self.service.table_exists(database_name, target_full_table_name):
                    default_target = target_full_table_name
                    conflict_message = (
                        f"A tabela {target_full_table_name} ja existe na base {database_name}. "
                        "Escolha outro nome para a restauracao."
                    )
                    continue

                restore_request["target_full_table_name"] = target_full_table_name
                break

            self.begin_restore_version_cancellation_scope()
            self.open_restore_version_to_new_table_progress(
                restore_request["target_full_table_name"],
                version["version_code"],
            )
            self.run_async(
                self._restore_version_to_new_table_thread,
                database_name,
                dict(version),
                restore_request["target_full_table_name"],
                restore_request["version_title"].strip(),
                restore_request["requested_by"].strip(),
            )

        version_code = self.selected_version_record["version_code"]
        self._with_selected_version_detail(
            continue_restore,
            loading_message=f"Carregando detalhes da versao {version_code}...",
        )

    def _restore_version_thread(
        self,
        database_name: str,
        version,
        new_title: str,
        author: str,
        filters: dict,
    ):
        try:
            cancel_event = self.restore_version_cancel_event
            full_table_name = f"{version['schema_name']}.{version['table_name']}"
            self.ui(self.set_status, f"Restaurando versão {version['version_code']}...", "loading")
            self.ui(self.update_restore_version_progress, "prepare", 5, "Preparando ambiente remoto...")
            self.service.ensure_admin_schema(cancel_event=cancel_event)
            self.service.restore_table_version(
                database_name,
                version,
                cancel_event=cancel_event,
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_restore_version_progress,
                    stage_key,
                    progress,
                    message,
                ),
            )

            restore_title = new_title or f"Restauração de {version['version_code']}"
            restore_recipe = self.service.build_restore_recipe(version["version_code"])
            self.ui(
                self.update_restore_version_progress,
                "version",
                10,
                "Preparando registro da nova versao restaurada...",
            )
            next_version = self.service.register_table_version(
                database_name=database_name,
                schema_name=version["schema_name"],
                table_name=version["table_name"],
                version_title=restore_title,
                requested_by=author,
                workstation_name=socket.gethostname(),
                sql_recipe=restore_recipe,
                restored_from_version=version["version_code"],
                progress_callback=lambda stage_key, progress, message: self.ui(
                    self.update_restore_version_progress,
                    stage_key,
                    progress,
                    message,
                ),
                cancel_event=cancel_event,
            )

            self.ui(
                self.update_restore_version_progress,
                "refresh",
                5,
                "Marcando cache local de filtros como stale...",
            )
            self._mark_table_filter_cache_stale_safe(database_name, full_table_name)
            self.ui(
                self.update_restore_version_progress,
                "refresh",
                35,
                "Carregando dados restaurados...",
            )

            active_filters = {
                column_name: [dict(item) for item in selected_values]
                for column_name, selected_values in (filters or {}).items()
            }
            try:
                details = self.service.get_table_details(
                    database_name,
                    full_table_name,
                    filters=active_filters,
                )
            except Exception:
                if not active_filters:
                    raise
                active_filters = {}
                self.ui(
                    self.update_restore_version_progress,
                    "refresh",
                    45,
                    "Filtros anteriores nao se aplicam mais. Recarregando sem filtros...",
                )
                details = self.service.get_table_details(database_name, full_table_name)

            self.ui(
                self.update_restore_version_progress,
                "refresh",
                70,
                "Carregando historico de versoes...",
            )
            versions = self.service.list_table_versions(database_name, full_table_name)
            if versions:
                versions[0].update(
                    self.service.get_table_version_detail(
                        database_name,
                        versions[0]["schema_name"],
                        versions[0]["table_name"],
                        versions[0]["version_code"],
                    )
                )

            def apply_restore():
                if self.current_db != database_name or self.selected_table_name != full_table_name:
                    return
                self.table_preview_filters = active_filters
                self._apply_table_details(full_table_name, details, versions)

            self.ui(apply_restore)
            self.ui(self.update_restore_version_progress, "refresh", 100, "Interface atualizada.")
            self.ui(self.complete_restore_version_progress, "Restauracao concluida com sucesso.")
            self.ui(self.set_status, f"Versão restaurada em {next_version}", "success")
        except OperationCancelledError:
            self.ui(self.mark_restore_version_progress_cancelled, "Restauracao cancelada pelo usuario.")
            self.ui(self.set_status, "Restauracao de versao cancelada", "error")
        except Exception as e:
            diagnostic = traceback.format_exc()

            print(
                "\n"
                "============================================================\n"
                "ERRO COMPLETO DURANTE RESTAURAÇÃO\n"
                "============================================================\n"
                f"{diagnostic}"
                "============================================================\n",
                flush=True,
            )

            self.ui(self.close_restore_version_progress)
            self.ui(self.set_status, "Erro ao restaurar versão", "error")
            self.ui(
                self.show_error,
                "Erro ao restaurar versão",
                (
                    f"{type(e).__name__}: {e}\n\n"
                    "O traceback completo foi enviado ao terminal do PyCharm."
                ),
            )
        finally:
            self.clear_restore_version_cancellation_scope()

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------

    def on_close(self):
        if self.cross_table_expansion_finalizing:
            self.mark_cross_table_expansion_finalizing(
                "Data committed. Wait for destination version registration."
            )
            self.set_status(
                "Finalizing versioning for the committed expansion...",
                "loading",
            )
            return

        if self.raw_import_finalizing:
            self.mark_raw_progress_finalizing(
                "Carga ja confirmada no banco. Aguarde a conclusao do dump e do versionamento."
            )
            self.set_status(
                "Finalizando dump e versionamento da carga Raw confirmada...",
                "loading",
            )
            return

        try:
            self.request_cancel_raw_import()
            self.close_raw_progress()
            self.request_cancel_sql_execution()
            self.close_sql_execution_progress()
            self.request_cancel_cross_table_expansion()
            self.close_cross_table_expansion_progress()
            self.request_cancel_raw_expand()
            self.close_raw_expand_progress()
            self.request_cancel_raw_group()
            self.close_raw_group_progress()
            self.request_cancel_column_delete()
            self.close_column_delete_progress()
            self.request_cancel_table_delete()
            self.close_table_delete_progress()
            self.request_cancel_restore_version()
            self.close_restore_version_progress()
            if self.dump_artifacts_dialog and self.dump_artifacts_dialog.winfo_exists():
                self.dump_artifacts_dialog.close()
            if self.data_dictionary_entries_dialog and self.data_dictionary_entries_dialog.winfo_exists():
                self.data_dictionary_entries_dialog.close()
            if self.data_dictionary_usage_entries_dialog and self.data_dictionary_usage_entries_dialog.winfo_exists():
                self.data_dictionary_usage_entries_dialog.close()
            self.facet_service.close()
            self.service.close()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
