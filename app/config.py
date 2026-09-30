import os


APP_NAME = "PostgreSQL Data Manager"
APP_ID = "postgres_data_manager"


def _environment_value(name: str, default: str) -> str:
    return str(os.getenv(name) or default).strip() or default


# Public defaults can be overridden without editing the source code.
CONTROL_DB = _environment_value("PDM_CONTROL_DB", "postgres_data_manager")
CONTROL_SCHEMA = _environment_value("PDM_CONTROL_SCHEMA", "app_control")
LEGACY_CONTROL_SCHEMA = _environment_value("PDM_LEGACY_CONTROL_SCHEMA", "app_control_legacy")
DATA_DICTIONARY_TABLE = "data_dictionary"
DATA_DICTIONARY_USAGE_TABLE = "data_dictionary_usage"

REMOTE_STORAGE_DIR = _environment_value("PDM_REMOTE_STORAGE_DIR", "postgres_data_manager_storage")
LOCAL_STORAGE_DIR = _environment_value("PDM_LOCAL_STORAGE_DIR", APP_NAME)

APP_TITLE = _environment_value("PDM_APP_TITLE", APP_NAME)
APP_GEOMETRY = "1560x860"
