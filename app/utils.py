import json
import os
import re
from datetime import datetime
from pathlib import Path

from config import APP_NAME, LOCAL_STORAGE_DIR


def sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "sem_titulo"


def version_to_int(version_code: str) -> int:
    return int(version_code)


def int_to_version(n: int) -> str:
    return f"{n:04d}"


def split_table_name(full_table_name: str) -> tuple[str, str]:
    if "." in full_table_name:
        return full_table_name.split(".", 1)
    return "public", full_table_name


def format_version_history_entry(
    version_code: str,
    version_title: str,
    created_by: str,
    workstation_name: str,
    sql_recipe: str,
    restored_from_version: str | None = None,
    notes: str | None = None,
) -> str:
    lines = [
        f"=== VERSÃO {version_code} ===",
        f"Título: {version_title}",
        f"Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Autor: {created_by}",
        f"Computador: {workstation_name}",
    ]

    if restored_from_version:
        lines.append(f"Restaurada de: {restored_from_version}")

    if notes:
        lines.extend([
            "",
            "NOTAS:",
            notes.strip(),
        ])

    lines.extend([
        "",
        "SQL:",
        sql_recipe.strip(),
    ])

    return "\n".join(lines).strip()


CONNECTION_CREDENTIALS_FILE = "connection_credentials.bin"
DPAPI_ENTROPY = APP_NAME.encode("utf-8")


def _credentials_file_path() -> Path:
    base_dir = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    target_dir = base_dir / LOCAL_STORAGE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / CONNECTION_CREDENTIALS_FILE


def _protect_bytes(payload: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted credential storage requires Windows.")

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def build_blob(raw_bytes: bytes):
        if not raw_bytes:
            return DATA_BLOB(0, None), None

        buffer = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
        blob = DATA_BLOB(
            len(raw_bytes),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer

    crypt_protect = ctypes.windll.crypt32.CryptProtectData
    crypt_protect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt_protect.restype = wintypes.BOOL

    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    input_blob, _input_buffer = build_blob(payload)
    entropy_blob, _entropy_buffer = build_blob(DPAPI_ENTROPY)
    output_blob = DATA_BLOB()

    if not crypt_protect(
        ctypes.byref(input_blob),
        APP_NAME,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            local_free(output_blob.pbData)


def _unprotect_bytes(payload: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted credential storage requires Windows.")

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def build_blob(raw_bytes: bytes):
        if not raw_bytes:
            return DATA_BLOB(0, None), None

        buffer = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
        blob = DATA_BLOB(
            len(raw_bytes),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer

    crypt_unprotect = ctypes.windll.crypt32.CryptUnprotectData
    crypt_unprotect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt_unprotect.restype = wintypes.BOOL

    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    input_blob, _input_buffer = build_blob(payload)
    entropy_blob, _entropy_buffer = build_blob(DPAPI_ENTROPY)
    description = wintypes.LPWSTR()
    output_blob = DATA_BLOB()

    if not crypt_unprotect(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            local_free(description)
        if output_blob.pbData:
            local_free(output_blob.pbData)


def load_saved_connection_credentials() -> dict | None:
    credentials_path = _credentials_file_path()
    if not credentials_path.exists():
        return None

    try:
        encrypted_payload = credentials_path.read_bytes()
        decrypted_payload = _unprotect_bytes(encrypted_payload)
        document = json.loads(decrypted_payload.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(document, dict):
        return None

    return {
        "server_option": "other",
        "custom_host": str(document.get("custom_host") or "").strip(),
        "ssh_port": int(document.get("ssh_port") or document.get("port") or 22),
        "postgres_port": int(document.get("postgres_port") or 5432),
        "ssh_username": str(document.get("ssh_username") or "").strip(),
        "ssh_password": str(document.get("ssh_password") or ""),
        "sql_username": str(document.get("sql_username") or "").strip(),
        "sql_password": str(document.get("sql_password") or ""),
        "remember_credentials": True,
    }


def save_connection_credentials(payload: dict):
    serializable_payload = {
        "server_option": "other",
        "custom_host": str(payload.get("custom_host") or "").strip(),
        "ssh_port": int(payload.get("ssh_port") or 22),
        "postgres_port": int(payload.get("postgres_port") or 5432),
        "ssh_username": str(payload.get("ssh_username") or "").strip(),
        "ssh_password": str(payload.get("ssh_password") or ""),
        "sql_username": str(payload.get("sql_username") or "").strip(),
        "sql_password": str(payload.get("sql_password") or ""),
    }
    raw_payload = json.dumps(serializable_payload, ensure_ascii=False).encode("utf-8")
    encrypted_payload = _protect_bytes(raw_payload)
    _credentials_file_path().write_bytes(encrypted_payload)


def clear_saved_connection_credentials():
    credentials_path = _credentials_file_path()
    if credentials_path.exists():
        credentials_path.unlink()
