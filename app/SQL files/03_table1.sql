INSERT INTO public.table1 (
    id_column_2,
    text_column_1,
    datetime_column_1,
    text_column_2,
    integer_column_1,
    integer_column_2,
    integer_column_3,
    text_column_3,
    id_column_3,
    id_column_4,
    id_column_5,
    integer_column_4,
    integer_column_5,
    id_column_6,
    integer_column_6,
    integer_column_7
)
SELECT
    NULLIF(BTRIM(source_row.raw -> 'values' ->> 'id_column_1'), '')::integer,
    NULL::character varying,
    NULLIF(BTRIM(source_row.raw -> 'values' ->> 'datetime_column_1'), '')::timestamptz
        AT TIME ZONE 'America/Sao_Paulo',
    NULL::character varying,
    GREATEST(
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_2'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_3'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_4'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_5'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_6'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_7'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_8'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_9'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_10'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_11'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_12'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_13'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_14'), '')::smallint,
        NULLIF(BTRIM(source_row.raw -> 'values' ->> 'integer_column_15'), '')::smallint
    ),
    NULL::smallint,
    CASE
        WHEN NULLIF(BTRIM(source_row.raw -> 'values' ->> 'numeric_column_2'), '')::numeric > 0
        THEN ROUND(
            NULLIF(BTRIM(source_row.raw -> 'values' ->> 'numeric_column_2'), '')::numeric * 1000
        )::integer
        ELSE NULL::integer
    END,
    NULL::character varying,
    source_row.raw_id,
    dimension_row.id_column_1,
    NULL::bigint,
    NULL::integer,
    NULL::integer,
    NULL::integer,
    NULL::integer,
    CASE
        WHEN NULLIF(BTRIM(source_row.raw -> 'values' ->> 'numeric_column_1'), '')::numeric > 0
        THEN ROUND(
            NULLIF(BTRIM(source_row.raw -> 'values' ->> 'numeric_column_1'), '')::numeric * 1000
        )::integer
        ELSE NULL::integer
    END
FROM {{source}} AS source_row
JOIN public.table3 AS dimension_row
  ON dimension_row.text_column_1 = BTRIM(source_row.raw -> 'values' ->> 'text_column_1')
 AND dimension_row.text_column_2 = 'SYNTHETIC/REFERENCE'
 AND dimension_row.numeric_column_1 IS NULL
 AND dimension_row.text_column_3 = LEFT(
        UPPER(BTRIM(source_row.raw -> 'values' ->> 'text_column_2')),
        1
    )
 AND dimension_row.text_column_4 = RIGHT(
        UPPER(BTRIM(source_row.raw -> 'values' ->> 'text_column_2')),
        1
    );
