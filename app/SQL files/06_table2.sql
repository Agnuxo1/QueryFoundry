INSERT INTO public.table2 (
    id_column_2,
    integer_column_1,
    integer_column_2,
    text_column_1,
    integer_column_3,
    integer_column_4,
    integer_column_5,
    integer_column_6
)
SELECT
    main_row.id_column_1,
    position_data.position_number,
    ROUND(
        NULLIF(BTRIM(position_data.weight_text), '')::numeric * 1000
    )::integer,
    UPPER(BTRIM(position_data.type_text)),
    NULL::integer,
    NULL::integer,
    NULL::integer,
    CASE
        WHEN NULLIF(BTRIM(position_data.gap_text), '')::numeric > 0
        THEN ROUND(
            NULLIF(BTRIM(position_data.gap_text), '')::numeric * 1000
        )::integer
        ELSE NULL::integer
    END
FROM {{source}} AS source_row
CROSS JOIN LATERAL (
    VALUES
        (1::smallint, source_row.raw -> 'values' ->> 'integer_column_2', source_row.raw -> 'values' ->> 'text_column_4', source_row.raw -> 'values' ->> 'numeric_column_3', source_row.raw -> 'values' ->> 'numeric_column_4'),
        (2::smallint, source_row.raw -> 'values' ->> 'integer_column_3', source_row.raw -> 'values' ->> 'text_column_5', source_row.raw -> 'values' ->> 'numeric_column_5', source_row.raw -> 'values' ->> 'numeric_column_6'),
        (3::smallint, source_row.raw -> 'values' ->> 'integer_column_4', source_row.raw -> 'values' ->> 'text_column_6', source_row.raw -> 'values' ->> 'numeric_column_7', source_row.raw -> 'values' ->> 'numeric_column_8'),
        (4::smallint, source_row.raw -> 'values' ->> 'integer_column_5', source_row.raw -> 'values' ->> 'text_column_7', source_row.raw -> 'values' ->> 'numeric_column_9', source_row.raw -> 'values' ->> 'numeric_column_10'),
        (5::smallint, source_row.raw -> 'values' ->> 'integer_column_6', source_row.raw -> 'values' ->> 'text_column_8', source_row.raw -> 'values' ->> 'numeric_column_11', source_row.raw -> 'values' ->> 'numeric_column_12'),
        (6::smallint, source_row.raw -> 'values' ->> 'integer_column_7', source_row.raw -> 'values' ->> 'text_column_9', source_row.raw -> 'values' ->> 'numeric_column_13', source_row.raw -> 'values' ->> 'numeric_column_14'),
        (7::smallint, source_row.raw -> 'values' ->> 'integer_column_8', source_row.raw -> 'values' ->> 'text_column_10', source_row.raw -> 'values' ->> 'numeric_column_15', source_row.raw -> 'values' ->> 'numeric_column_16'),
        (8::smallint, source_row.raw -> 'values' ->> 'integer_column_9', source_row.raw -> 'values' ->> 'text_column_11', source_row.raw -> 'values' ->> 'numeric_column_17', source_row.raw -> 'values' ->> 'numeric_column_18'),
        (9::smallint, source_row.raw -> 'values' ->> 'integer_column_10', source_row.raw -> 'values' ->> 'text_column_12', source_row.raw -> 'values' ->> 'numeric_column_19', source_row.raw -> 'values' ->> 'numeric_column_20'),
        (10::smallint, source_row.raw -> 'values' ->> 'integer_column_11', source_row.raw -> 'values' ->> 'text_column_13', source_row.raw -> 'values' ->> 'numeric_column_21', source_row.raw -> 'values' ->> 'numeric_column_22'),
        (11::smallint, source_row.raw -> 'values' ->> 'integer_column_12', source_row.raw -> 'values' ->> 'text_column_14', source_row.raw -> 'values' ->> 'numeric_column_23', source_row.raw -> 'values' ->> 'numeric_column_24'),
        (12::smallint, source_row.raw -> 'values' ->> 'integer_column_13', source_row.raw -> 'values' ->> 'text_column_15', source_row.raw -> 'values' ->> 'numeric_column_25', source_row.raw -> 'values' ->> 'numeric_column_26'),
        (13::smallint, source_row.raw -> 'values' ->> 'integer_column_14', source_row.raw -> 'values' ->> 'text_column_16', source_row.raw -> 'values' ->> 'numeric_column_27', source_row.raw -> 'values' ->> 'numeric_column_28'),
        (14::smallint, source_row.raw -> 'values' ->> 'integer_column_15', source_row.raw -> 'values' ->> 'text_column_17', source_row.raw -> 'values' ->> 'numeric_column_29', source_row.raw -> 'values' ->> 'numeric_column_30')
) AS position_data(position_number, declared_number_text, type_text, weight_text, gap_text)
JOIN public.table1 AS main_row
  ON main_row.id_column_3 = source_row.raw_id
WHERE NULLIF(BTRIM(position_data.declared_number_text), '')::smallint
        = position_data.position_number
  AND NULLIF(BTRIM(position_data.weight_text), '')::numeric >= 0
  AND UPPER(BTRIM(position_data.type_text)) IN ('X', 'Y', 'Z');
