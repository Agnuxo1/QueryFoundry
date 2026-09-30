import sqlite3
from pathlib import Path


def find_first_sqlite():
    pasta_atual = Path(__file__).resolve().parent
    extensoes = ["*.sqlite", "*.db", "*.sqlite3"]

    for ext in extensoes:
        arquivos = sorted(pasta_atual.glob(ext))
        if arquivos:
            return arquivos[0]

    return None


def get_tables(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    return [row[0] for row in cursor.fetchall()]


def get_columns(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info('{table_name}')")
    return cursor.fetchall()  # cid, name, type, notnull, dflt_value, pk


def get_row_count(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    return cursor.fetchone()[0]


def get_sample_rows(conn, table_name, limit=3):
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM "{table_name}" LIMIT {limit}')
    return cursor.fetchall()


def summarize_table(conn, table_name):
    print(f"\n{'=' * 70}")
    print(f"TABELA: {table_name}")

    columns = get_columns(conn, table_name)
    row_count = get_row_count(conn, table_name)

    print(f"Quantidade de linhas: {row_count}")
    print("Colunas:")
    for col in columns:
        cid, name, col_type, notnull, default, pk = col
        extras = []
        if pk:
            extras.append("PK")
        if notnull:
            extras.append("NOT NULL")
        extra_txt = f" [{' | '.join(extras)}]" if extras else ""
        print(f"  - {name} ({col_type or 'SEM TIPO'}){extra_txt}")

    sample_rows = get_sample_rows(conn, table_name)

    if sample_rows:
        col_names = [col[1] for col in columns]
        print("\nExemplo de registros:")
        for i, row in enumerate(sample_rows, start=1):
            preview = {col_names[j]: row[j] for j in range(len(col_names))}
            print(f"  Registro {i}: {preview}")
    else:
        print("\nTabela vazia.")

    print("\nResumo:")
    print(f"  - Tabela '{table_name}' possui {len(columns)} colunas e {row_count} linhas.")
    if columns:
        print(f"  - Primeiras colunas: {', '.join(col[1] for col in columns[:5])}")


def main():
    db_file = find_first_sqlite()

    if not db_file:
        print("Nenhum arquivo .sqlite, .db ou .sqlite3 foi encontrado na mesma pasta do script.")
        return

    print(f"Arquivo encontrado: {db_file.name}")

    try:
        conn = sqlite3.connect(db_file)
        tables = get_tables(conn)

        if not tables:
            print("O banco foi aberto, mas não há tabelas visíveis.")
            return

        print(f"Tabelas encontradas: {', '.join(tables)}")

        for table in tables:
            summarize_table(conn, table)

        conn.close()

    except Exception as e:
        print(f"Erro ao ler o banco: {e}")


if __name__ == "__main__":
    main()