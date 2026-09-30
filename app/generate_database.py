#!/usr/bin/env python3
"""Create a generic deterministic PostgreSQL expansion-challenge dataset.

The generator intentionally lives outside the DB Manager application.  It only
depends on Python's standard library and on the PostgreSQL ``psql`` client.

Typical use (libpq environment variables and .pgpass are honored)::

    python generate_database.py --rows <row_num> --host <linux_server_ip> \
        --ssh_port <linux_ssh_port> --ssh_user <linux_ssh_user> --postgres_port 5432 \
        --user <postgres_user>

The generated database is always named ``kaggle_challenge``.  Existing
databases are preserved unless ``--replace`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence


DATABASE_NAME = "kaggle_challenge"
DEFAULT_CONTROL_DATABASE = "postgres_data_manager"
DEFAULT_CONTROL_SCHEMA = "app_control"
PASSWORD_ENV_VAR = "PG_KAGGLE_CHALLENGE_PASS"
GENERATOR_ACTOR = "challenge_generator"
GENERATOR_WORKSTATION = "generate_database.py"
FIXED_SEED = 0x4441544153455431  # ASCII "DATASET1" encoded as an integer.
GENERATOR_VERSION = 6
MASK_64 = (1 << 64) - 1
RAW_HASH_BLOCK_SIZE = 500_000
RAW_SCHEMA_NAME = "group001"
DEFAULT_CHUNK_ROWS = 500_000
MAX_ROWS = 999_000_000
REFERENCE_ROWS = 789_169
REFERENCE_ID_START = 1_000_000 - REFERENCE_ROWS

RAW_INGESTED_START = datetime(1999, 3, 12, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
RAW_INGESTED_END = datetime(2027, 3, 12, 23, 59, 0, tzinfo=timezone(timedelta(hours=-3)))
RAW_INGESTED_SECONDS = int((RAW_INGESTED_END - RAW_INGESTED_START).total_seconds()) + 1

VALUE_TIMESTAMP_START = datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
VALUE_TIMESTAMP_WINDOW_MS = 30 * 24 * 60 * 60 * 1_000

AXIS_SHARES = {
    0: 0.013305,
    2: 86.443896,
    3: 5.481241,
    4: 2.732824,
    5: 1.339516,
    6: 3.175466,
    7: 0.543360,
    8: 0.023033,
    9: 0.246358,
    10: 0.000572,
    12: 0.000286,
    14: 0.000143,
}

SPEED_PROFILES = {
    0: (56.0, 86.0, 126.0),
    2: (73.0, 98.0, 130.0),
    3: (64.0, 80.0, 101.0),
    4: (65.0, 78.0, 95.0),
    5: (62.0, 76.0, 94.0),
    6: (62.0, 74.0, 89.0),
    7: (63.0, 76.0, 91.0),
    8: (57.0, 76.0, 91.0),
    9: (60.0, 76.0, 89.0),
    10: (40.0, 60.0, 85.0),
}

LENGTH_PROFILES = {
    0: (5.0, 6.51, 21.6),
    2: (3.48, 4.21, 6.78),
    3: (5.93, 9.53, 15.48),
    4: (8.66, 14.9, 17.54),
    5: (11.64, 15.97, 18.79),
    6: (12.04, 15.08, 21.26),
    7: (15.2, 17.39, 27.04),
    8: (16.3, 21.34, 27.96),
    9: (17.32, 23.59, 27.98),
    10: (10.0, 25.0, 33.0),
}

TEXT_COLUMN_2_TOKENS = ("CA", "CB", "DA", "DB")
TEXT_COLUMN_2_WEIGHTS = (31.558090, 31.235622, 20.232080, 16.974208)

TEXT_COLUMN_3_TOKENS = tuple(
    [f"A{chr(ord('A') + index)}" for index in range(26)] + ["BA"]
)
TEXT_COLUMN_3_WEIGHTS = (
    77.216789, 6.726620, 3.564888, 2.898636, 2.048688, 1.921503,
    1.215908, 1.154247, 0.749230, 0.578697, 0.446648, 0.420038,
    0.216886, 0.171249, 0.170962, 0.111018, 0.089415, 0.080116,
    0.075824, 0.075395, 0.022604, 0.018026, 0.011874, 0.011302,
    0.001574, 0.001574, 0.000286,
)

POSITION_TYPE_WEIGHTS = {
    1: (98.646141, 1.335831, 0.018029),
    2: (90.458026, 9.472293, 0.069682),
    3: (16.808223, 77.947857, 5.243920),
    4: (15.700367, 42.071732, 42.227901),
    5: (14.656214, 21.113110, 64.230676),
    6: (13.462918, 16.665471, 69.871611),
    7: (5.045710, 64.820675, 30.133615),
    8: (0.634921, 34.497354, 64.867725),
    9: (0.115674, 31.000578, 68.883748),
    10: (0.0, 42.857143, 57.142857),
    11: (0.0, 33.333333, 66.666667),
    12: (0.0, 33.333333, 66.666667),
    13: (0.0, 0.0, 100.0),
    14: (0.0, 0.0, 100.0),
}

WEIGHT_PROFILES = {
    1: (0.2, 0.43, 0.62, 0.78, 1.28, 5.71, 7.21),
    2: (0.19, 0.29, 0.43, 0.58, 1.07, 8.35, 11.3),
    3: (0.16, 0.58, 3.31, 5.18, 7.338, 10.18, 13.9),
    4: (0.48, 1.78, 3.15, 5.82, 7.99, 10.31, 12.45),
    5: (1.37, 2.2, 4.62, 7.39, 8.89, 11.14, 13.22),
    6: (1.768, 2.8, 5.76, 7.36, 8.68, 10.89, 12.643),
    7: (1.109, 1.81, 5.037, 7.64, 8.82, 11.116, 13.001),
    8: (1.246, 2.54, 7.08, 8.16, 9.04, 10.22, 11.31),
    9: (1.643, 3.434, 6.58, 7.63, 8.34, 9.702, 10.856),
    10: (1.0, 2.0, 4.0, 7.0, 9.0, 12.0, 14.0),
    11: (1.5, 3.0, 5.0, 7.5, 9.0, 12.0, 14.0),
    12: (1.5, 3.0, 5.0, 7.5, 9.0, 12.0, 14.0),
    13: (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0),
    14: (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0),
}

DISTANCE_PROFILES = {
    1: (1.44, 2.32, 2.48, 2.61, 3.02, 4.8, 6.69),
    2: (1.02, 1.2, 1.28, 1.37, 6.38, 9.23, 10.46),
    3: (0.86, 1.19, 1.27, 4.79, 5.92, 8.31, 10.415),
    4: (1.13, 1.17, 1.22, 1.25, 1.31, 5.067, 8.48),
    5: (1.15, 1.19, 1.23, 1.27, 2.35, 5.06, 7.89),
    6: (1.14, 1.18, 1.22, 1.26, 3.21, 7.26, 9.15),
    7: (1.13, 1.17, 1.22, 1.26, 4.88, 7.81, 8.091),
    8: (1.15, 1.18, 1.22, 1.25, 1.28, 1.33, 1.37),
    9: (0.24, 0.3, 0.8, 1.27, 1.4, 5.3, 6.6),
    10: (1.1, 1.15, 1.2, 1.3, 1.5, 5.0, 7.5),
    11: (1.1, 1.15, 1.2, 1.25, 1.4, 1.5, 1.6),
    12: (1.1, 1.15, 1.2, 1.25, 1.4, 1.5, 1.6),
    13: (1.1, 1.15, 1.2, 1.25, 1.4, 1.5, 1.6),
}

PIECEWISE_PROBABILITIES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)
THREE_QUANTILE_PROBABILITIES = (0.05, 0.50, 0.95)
POSITION_TYPE_TOKENS = ("X", "Y", "Z")

BASE_VALUE_KEYS = (
    "id_column_1",
    "text_column_1",
    "datetime_column_1",
    "integer_column_1",
    "text_column_2",
    "numeric_column_1",
    "text_column_3",
    "numeric_column_2",
)

POSITION_VALUE_KEYS = tuple(
    (
        f"integer_column_{position + 1}",
        f"text_column_{position + 3}",
        f"numeric_column_{2 * position + 1}",
        f"numeric_column_{2 * position + 2}",
    )
    for position in range(1, 15)
)

ALL_VALUE_KEYS = BASE_VALUE_KEYS + tuple(
    key for position_keys in POSITION_VALUE_KEYS for key in position_keys
)

if len(ALL_VALUE_KEYS) != 64:
    raise AssertionError("The challenge payload must contain exactly 64 keys.")


def _mix64(value: int) -> int:
    value &= MASK_64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK_64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK_64
    return (value ^ (value >> 31)) & MASK_64


class RowRandom:
    """Small, stable PRNG whose output does not depend on Python's random module."""

    __slots__ = ("state",)

    def __init__(self, row_key: int):
        self.state = _mix64(FIXED_SEED ^ ((row_key + 1) * 0xD1342543DE82EF95))

    def uint64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK_64
        return _mix64(self.state)

    def random(self) -> float:
        return (self.uint64() >> 11) * (1.0 / (1 << 53))

    def integer(self, minimum: int, maximum: int) -> int:
        return minimum + (self.uint64() % (maximum - minimum + 1))


def _coprime_multiplier(modulus: int, candidate: int) -> int:
    if modulus <= 1:
        return 0
    value = candidate % modulus
    if value == 0:
        value = 1
    while math.gcd(value, modulus) != 1:
        value = (value + 1) % modulus
        if value == 0:
            value = 1
    return value


@dataclass(frozen=True)
class AffinePermutation:
    modulus: int
    multiplier: int
    increment: int

    @classmethod
    def build(cls, modulus: int, salt: int) -> "AffinePermutation":
        if modulus <= 1:
            return cls(modulus=max(modulus, 1), multiplier=0, increment=0)
        multiplier = _coprime_multiplier(modulus, _mix64(FIXED_SEED ^ salt) | 1)
        increment = _mix64(FIXED_SEED ^ (salt << 1)) % modulus
        return cls(modulus=modulus, multiplier=multiplier, increment=increment)

    def apply(self, index: int) -> int:
        if self.modulus <= 1:
            return 0
        return (self.multiplier * index + self.increment) % self.modulus


def _weighted_choice(rng: RowRandom, tokens: Sequence[str], weights: Sequence[float]) -> str:
    target = rng.random() * sum(weights)
    running = 0.0
    for token, weight in zip(tokens, weights):
        running += weight
        if target < running:
            return token
    return tokens[-1]


def _piecewise_sample(u: float, probabilities: Sequence[float], values: Sequence[float]) -> float:
    if u <= probabilities[0]:
        return values[0]
    if u >= probabilities[-1]:
        return values[-1]
    right = bisect_right(probabilities, u)
    left = right - 1
    fraction = (u - probabilities[left]) / (probabilities[right] - probabilities[left])
    return values[left] + fraction * (values[right] - values[left])


def _profile_for_axis(profiles: dict[int, tuple[float, ...]], axis_count: int) -> tuple[float, ...]:
    return profiles[10 if axis_count >= 10 else axis_count]


def _to_cents(value: float) -> int:
    return int(math.floor(value * 100.0 + 0.5 + 1e-9))


def _render_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    whole, fraction = divmod(absolute, 100)
    if fraction == 0:
        return f"{sign}{whole}"
    if fraction % 10 == 0:
        return f"{sign}{whole}.{fraction // 10}"
    return f"{sign}{whole}.{fraction:02d}"


def _format_offset_timestamp(value: datetime, include_milliseconds: bool) -> str:
    base = value.strftime("%Y-%m-%d %H:%M:%S")
    if include_milliseconds:
        fraction = f"{value.microsecond // 1_000:03d}".rstrip("0")
        if fraction:
            base += f".{fraction}"
    return base + "-03"


def _constant_text_value() -> str:
    rng = RowRandom(0x54455854)
    digits_1 = "".join(str(rng.integer(0, 9)) for _ in range(3))
    letters = "".join(chr(ord("A") + rng.integer(0, 25)) for _ in range(3))
    digits_2 = "".join(str(rng.integer(0, 9)) for _ in range(4))
    return digits_1 + letters + digits_2


CONSTANT_TEXT_COLUMN_1 = _constant_text_value()
VALUE_TIMESTAMP_PERMUTATION = AffinePermutation.build(
    VALUE_TIMESTAMP_WINDOW_MS,
    0x54494D455354414D,
)
FIXED_ROW_PERMUTATION = AffinePermutation.build(
    MAX_ROWS,
    0x524F575348554646,
)


def _axis_count_for_raw_id(raw_id: int) -> int:
    """Sample the axis profile deterministically without depending on total rows."""
    rng = RowRandom(0x4158495300000000 + raw_id)
    target = rng.random() * sum(AXIS_SHARES.values())
    running = 0.0
    for axis_count, share in sorted(AXIS_SHARES.items()):
        running += share
        if target < running:
            return axis_count
    return max(AXIS_SHARES)


def _value_timestamp(logical_index: int) -> str:
    offset_ms = VALUE_TIMESTAMP_PERMUTATION.apply(logical_index)
    value = VALUE_TIMESTAMP_START + timedelta(milliseconds=offset_ms)
    return _format_offset_timestamp(value, include_milliseconds=True)


def _raw_ingested_timestamp(rng: RowRandom) -> str:
    offset = rng.uint64() % RAW_INGESTED_SECONDS
    value = RAW_INGESTED_START + timedelta(seconds=offset)
    return _format_offset_timestamp(value, include_milliseconds=False)


def _generate_values(logical_index: int, axis_count: int) -> dict[str, str]:
    rng = RowRandom(logical_index)
    values: dict[str, str] = {key: "" for key in ALL_VALUE_KEYS}

    speed_profile = _profile_for_axis(SPEED_PROFILES, axis_count)
    if rng.random() < 0.005:
        speed = rng.integer(2, 246)
    else:
        speed = int(math.floor(_piecewise_sample(
            rng.random(), THREE_QUANTILE_PROBABILITIES, speed_profile
        ) + 0.5))

    length_profile = _profile_for_axis(LENGTH_PROFILES, axis_count)
    length_cents = _to_cents(_piecewise_sample(
        rng.random(), THREE_QUANTILE_PROBABILITIES, length_profile
    ))

    values["id_column_1"] = str(REFERENCE_ID_START + logical_index)
    values["text_column_1"] = CONSTANT_TEXT_COLUMN_1
    values["datetime_column_1"] = _value_timestamp(logical_index)
    values["integer_column_1"] = str(speed)
    values["text_column_2"] = _weighted_choice(
        rng, TEXT_COLUMN_2_TOKENS, TEXT_COLUMN_2_WEIGHTS
    )
    values["numeric_column_1"] = _render_cents(length_cents)
    values["text_column_3"] = _weighted_choice(
        rng, TEXT_COLUMN_3_TOKENS, TEXT_COLUMN_3_WEIGHTS
    )

    weight_sum_cents = 0
    for position, keys in enumerate(POSITION_VALUE_KEYS, start=1):
        if position > axis_count:
            continue

        integer_key, text_key, weight_key, distance_key = keys
        weight_cents = _to_cents(_piecewise_sample(
            rng.random(), PIECEWISE_PROBABILITIES, WEIGHT_PROFILES[position]
        ))
        weight_sum_cents += weight_cents

        values[integer_key] = str(position)
        values[text_key] = _weighted_choice(
            rng, POSITION_TYPE_TOKENS, POSITION_TYPE_WEIGHTS[position]
        )
        values[weight_key] = _render_cents(weight_cents)
        if position == axis_count:
            values[distance_key] = "0"
        else:
            distance_cents = _to_cents(_piecewise_sample(
                rng.random(), PIECEWISE_PROBABILITIES, DISTANCE_PROFILES[position]
            ))
            values[distance_key] = _render_cents(distance_cents)

    values["numeric_column_2"] = _render_cents(weight_sum_cents)
    return values


def _block_hashes(rows: int) -> list[str]:
    block_count = math.ceil(rows / RAW_HASH_BLOCK_SIZE)
    return [
        hashlib.sha256(
            f"postgres-data-challenge:{FIXED_SEED}:raw-hash-block:{block_index}".encode("ascii")
        ).hexdigest()
        for block_index in range(block_count)
    ]


@dataclass(frozen=True)
class GeneratedRow:
    raw_id: int
    raw_hash: str
    raw_ingested_at: str
    raw_schema: str
    raw_tab: str
    raw_json: str
    axis_count: int
    logical_index: int

    def copy_values(self) -> tuple[object, ...]:
        return (
            self.raw_id,
            self.raw_hash,
            self.raw_ingested_at,
            self.raw_schema,
            self.raw_tab,
            self.raw_json,
        )


@dataclass(frozen=True)
class DatasetGenerator:
    rows: int
    row_permutation: AffinePermutation
    hashes: tuple[str, ...]

    @classmethod
    def build(cls, rows: int) -> "DatasetGenerator":
        if rows <= 0 or rows > MAX_ROWS:
            raise ValueError(f"rows must be between 1 and {MAX_ROWS:,}")
        return cls(
            rows=rows,
            row_permutation=FIXED_ROW_PERMUTATION,
            hashes=tuple(_block_hashes(rows)),
        )

    def row_at(self, physical_index: int) -> GeneratedRow:
        if physical_index < 0 or physical_index >= self.rows:
            raise IndexError(physical_index)
        raw_id = physical_index + 1
        logical_index = self.row_permutation.apply(physical_index)
        axis_count = _axis_count_for_raw_id(raw_id)
        metadata_rng = RowRandom(0x100000000 + physical_index)
        tab_draw = metadata_rng.random()
        raw_tab = "Plan1" if tab_draw < 0.45 else "Sheet1" if tab_draw < 0.90 else "My beautiful sheet"
        values = _generate_values(logical_index, axis_count)
        raw_json = json.dumps(
            {"values": values},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return GeneratedRow(
            raw_id=raw_id,
            raw_hash=self.hashes[physical_index // RAW_HASH_BLOCK_SIZE],
            raw_ingested_at=_raw_ingested_timestamp(metadata_rng),
            raw_schema=RAW_SCHEMA_NAME,
            raw_tab=raw_tab,
            raw_json=raw_json,
            axis_count=axis_count,
            logical_index=logical_index,
        )

    def __iter__(self) -> Iterator[GeneratedRow]:
        for physical_index in range(self.rows):
            yield self.row_at(physical_index)


def iter_generated_rows(rows: int) -> Iterator[GeneratedRow]:
    yield from DatasetGenerator.build(rows)


SCHEMA_SQL = r"""
CREATE SCHEMA challenge_metadata;

CREATE TABLE public.raw_data (
    raw_id bigint NOT NULL PRIMARY KEY,
    raw_hash text,
    raw_ingested_at timestamptz,
    raw_schema text,
    raw_tab text,
    raw jsonb
)
WITH (
    autovacuum_vacuum_scale_factor = '0.01',
    autovacuum_vacuum_threshold = '100000',
    autovacuum_vacuum_insert_scale_factor = '0.01',
    autovacuum_vacuum_insert_threshold = '100000',
    autovacuum_analyze_scale_factor = '0.001',
    autovacuum_analyze_threshold = '100000'
);

CREATE INDEX raw_data_raw_schema_raw_id_idx
    ON public.raw_data (raw_schema, raw_id);

-- Generic relational destination table 3.
CREATE TABLE public.table3 (
    id_column_1 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    text_column_1 character varying,
    text_column_2 character varying,
    numeric_column_1 numeric,
    text_column_3 character varying,
    text_column_4 character varying
);

-- Generic relational destination table 4.
CREATE TABLE public.table4 (
    id_column_1 smallint NOT NULL PRIMARY KEY,
    text_column_1 character varying,
    text_column_2 character varying
);

-- Generic relational destination table 1.
CREATE TABLE public.table1 (
    id_column_1 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    id_column_2 integer,
    text_column_1 character varying,
    datetime_column_1 timestamp without time zone,
    text_column_2 character varying,
    integer_column_1 smallint,
    integer_column_2 smallint,
    integer_column_3 integer,
    text_column_3 character varying,
    id_column_3 bigint NOT NULL UNIQUE,
    id_column_4 bigint,
    id_column_5 bigint,
    integer_column_4 integer,
    integer_column_5 integer,
    id_column_6 integer,
    integer_column_6 integer,
    integer_column_7 integer,
    CONSTRAINT table1_id_column_3_fkey
        FOREIGN KEY (id_column_3) REFERENCES public.raw_data(raw_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT table1_id_column_4_fkey
        FOREIGN KEY (id_column_4) REFERENCES public.table3(id_column_1)
        ON UPDATE CASCADE ON DELETE CASCADE
);

-- Generic relational destination table 2.
CREATE TABLE public.table2 (
    id_column_1 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    id_column_2 bigint NOT NULL,
    integer_column_1 smallint NOT NULL,
    integer_column_2 integer,
    text_column_1 character varying,
    integer_column_3 integer,
    integer_column_4 integer,
    integer_column_5 integer,
    integer_column_6 integer,
    CONSTRAINT table2_text_column_1_check
        CHECK (text_column_1 IS NULL OR text_column_1 IN ('X', 'Y', 'Z')),
    CONSTRAINT table2_id_column_2_integer_column_1_key
        UNIQUE (id_column_2, integer_column_1),
    CONSTRAINT table2_id_column_2_fkey
        FOREIGN KEY (id_column_2) REFERENCES public.table1(id_column_1)
        ON UPDATE CASCADE ON DELETE CASCADE
);

-- Generic relational destination table 5.
CREATE TABLE public.table5 (
    id_column_1 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    numeric_column_1 numeric,
    numeric_column_2 numeric,
    integer_column_1 integer,
    integer_column_2 integer,
    id_column_2 bigint NOT NULL,
    id_column_3 bigint,
    CONSTRAINT table5_id_column_2_fkey
        FOREIGN KEY (id_column_2) REFERENCES public.table1(id_column_1)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE UNIQUE INDEX table5_id_column_3_not_null_key
    ON public.table5 (id_column_3) WHERE id_column_3 IS NOT NULL;
CREATE UNIQUE INDEX table5_id_column_2_when_id_column_3_null_key
    ON public.table5 (id_column_2) WHERE id_column_3 IS NULL;

-- Generic relational destination table 6.
CREATE TABLE public.table6 (
    id_column_1 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    id_column_2 bigint NOT NULL,
    id_column_3 smallint,
    CONSTRAINT table6_id_column_2_id_column_3_key UNIQUE (id_column_2, id_column_3),
    CONSTRAINT table6_id_column_2_fkey
        FOREIGN KEY (id_column_2) REFERENCES public.table1(id_column_1)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT table6_id_column_3_fkey
        FOREIGN KEY (id_column_3) REFERENCES public.table4(id_column_1)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE challenge_metadata.generation_progress (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generator_version integer NOT NULL,
    fixed_seed numeric(20, 0) NOT NULL,
    requested_rows bigint NOT NULL,
    last_committed_raw_id bigint NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('loading', 'loaded', 'complete')),
    chunk_rows integer NOT NULL CHECK (chunk_rows > 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (last_committed_raw_id BETWEEN 0 AND requested_rows)
);

COMMENT ON TABLE public.raw_data IS 'Deterministic Raw source for a generic PostgreSQL expansion challenge.';
COMMENT ON TABLE public.table1 IS 'Empty generic relational destination table 1.';
COMMENT ON TABLE public.table2 IS 'Empty generic relational destination table 2.';
COMMENT ON TABLE public.table3 IS 'Empty generic relational destination table 3.';
COMMENT ON TABLE public.table4 IS 'Empty generic relational destination table 4.';
COMMENT ON TABLE public.table5 IS 'Empty generic relational destination table 5.';
COMMENT ON TABLE public.table6 IS 'Empty generic relational destination table 6.';
"""


def _connection_arguments(args: argparse.Namespace, database: str) -> list[str]:
    command = [args.psql, "-X", "-q", "-w", "-v", "ON_ERROR_STOP=1"]
    if args.host:
        command.extend(["-h", args.host])
    if args.port:
        command.extend(["-p", str(args.port)])
    if args.user:
        command.extend(["-U", args.user])
    command.extend(["-d", database])
    return command


def _psql_environment(args: argparse.Namespace) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PGPASSWORD", None)
    password = args.user_pass
    if password is None:
        password = environment.get(PASSWORD_ENV_VAR)
    environment.pop(PASSWORD_ENV_VAR, None)
    if password is not None:
        environment["PGPASSWORD"] = password
    return environment


def _run_psql(args: argparse.Namespace, database: str, sql: str, tuples_only: bool = False) -> str:
    command = _connection_arguments(args, database)
    if tuples_only:
        command.extend(["-A", "-t"])
    completed = subprocess.run(
        command,
        input=sql,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_psql_environment(args),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "psql failed")
    return completed.stdout.strip()


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _database_exists(args: argparse.Namespace, database_name: str = DATABASE_NAME) -> bool:
    result = _run_psql(
        args,
        args.admin_database,
        f"SELECT 1 FROM pg_database WHERE datname = {_sql_literal(database_name)};",
        tuples_only=True,
    )
    return result == "1"


def _ensure_manager_control_store(args: argparse.Namespace) -> None:
    if not _database_exists(args, args.control_database):
        _run_psql(
            args,
            args.admin_database,
            (
                f"CREATE DATABASE {_sql_identifier(args.control_database)} "
                f"OWNER {_sql_identifier(args.user)};"
            ),
        )

    control_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('raw_id_counters')}"
    )
    dictionary_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('data_dictionary')}"
    )
    dictionary_usage_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('data_dictionary_usage')}"
    )
    versions_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('table_versions')}"
    )
    dependencies_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('table_version_dependencies')}"
    )
    _run_psql(
        args,
        args.control_database,
        (
            f"CREATE SCHEMA IF NOT EXISTS {_sql_identifier(args.control_schema)};\n"
            f"CREATE TABLE IF NOT EXISTS {control_table} (\n"
            "    database_name text NOT NULL,\n"
            "    schema_name text NOT NULL,\n"
            "    table_name text NOT NULL,\n"
            "    next_raw_id bigint NOT NULL,\n"
            "    updated_at timestamptz NOT NULL DEFAULT now(),\n"
            "    PRIMARY KEY (database_name, schema_name, table_name)\n"
            ");\n"
            f"CREATE TABLE IF NOT EXISTS {dictionary_table} (\n"
            "    standard_name text PRIMARY KEY,\n"
            "    definition text NOT NULL,\n"
            "    units text NOT NULL,\n"
            "    value_domain text NOT NULL,\n"
            "    aliases text,\n"
            "    data_type text\n"
            ");\n"
            f"CREATE TABLE IF NOT EXISTS {dictionary_usage_table} (\n"
            "    database_name text NOT NULL,\n"
            "    schema_name text NOT NULL,\n"
            "    table_name text NOT NULL,\n"
            "    column_name text NOT NULL,\n"
            f"    standard_name text NOT NULL REFERENCES {dictionary_table} (standard_name)\n"
            "        ON UPDATE CASCADE ON DELETE CASCADE,\n"
            "    linked_at timestamptz NOT NULL DEFAULT now(),\n"
            "    linked_by text,\n"
            "    PRIMARY KEY (database_name, schema_name, table_name, column_name)\n"
            ");\n"
            f"CREATE TABLE IF NOT EXISTS {versions_table} (\n"
            "    id bigserial PRIMARY KEY,\n"
            "    database_name text NOT NULL,\n"
            "    schema_name text NOT NULL,\n"
            "    table_name text NOT NULL,\n"
            "    version_code text NOT NULL,\n"
            "    version_title text NOT NULL,\n"
            "    created_by text NOT NULL,\n"
            "    workstation_name text NOT NULL,\n"
            "    created_at timestamptz NOT NULL DEFAULT now(),\n"
            "    sql_recipe text NOT NULL,\n"
            "    restored_from_version text,\n"
            "    version_history_log text,\n"
            "    version_history_format text DEFAULT 'full',\n"
            "    raw_dump_path text,\n"
            "    raw_hash text,\n"
            "    raw_ingested_at timestamptz,\n"
            "    raw_schema text,\n"
            "    operation_kind text NOT NULL DEFAULT 'standard',\n"
            "    UNIQUE (database_name, schema_name, table_name, version_code)\n"
            ");\n"
            f"CREATE TABLE IF NOT EXISTS {dependencies_table} (\n"
            "    id bigserial PRIMARY KEY,\n"
            "    table_version_id bigint NOT NULL\n"
            f"        REFERENCES {versions_table} (id) ON DELETE CASCADE,\n"
            "    dependency_kind text NOT NULL,\n"
            "    source_database_name text NOT NULL,\n"
            "    source_schema_name text NOT NULL,\n"
            "    source_table_name text NOT NULL,\n"
            "    dependency_payload jsonb NOT NULL,\n"
            "    created_at timestamptz NOT NULL DEFAULT now(),\n"
            "    UNIQUE (table_version_id, dependency_kind, source_database_name, "
            "source_schema_name, source_table_name)\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS table_version_dependencies_version_idx "
            f"ON {dependencies_table} (table_version_id);\n"
            f"ALTER TABLE {dictionary_table} ADD COLUMN IF NOT EXISTS data_type text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS version_history_log text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS version_history_format text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS raw_dump_path text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS raw_hash text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS raw_ingested_at timestamptz;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS raw_schema text;\n"
            f"ALTER TABLE {versions_table} ADD COLUMN IF NOT EXISTS operation_kind text;\n"
            f"ALTER TABLE {versions_table} ALTER COLUMN version_history_format "
            "SET DEFAULT 'full';\n"
            f"UPDATE {versions_table} SET version_history_log = sql_recipe "
            "WHERE version_history_log IS NULL;\n"
            f"UPDATE {versions_table} SET version_history_format = CASE "
            "WHEN COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') "
            "THEN 'entry' ELSE 'full' END WHERE version_history_format IS NULL;\n"
            f"UPDATE {versions_table} SET operation_kind = 'standard' "
            "WHERE operation_kind IS NULL OR btrim(operation_kind) = '';\n"
            f"ALTER TABLE {versions_table} ALTER COLUMN operation_kind SET DEFAULT 'standard';\n"
            f"ALTER TABLE {versions_table} ALTER COLUMN operation_kind SET NOT NULL;"
        ),
    )


def _sync_manager_raw_id_counter(args: argparse.Namespace, next_raw_id: int) -> None:
    control_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('raw_id_counters')}"
    )
    _run_psql(
        args,
        args.control_database,
        (
            f"INSERT INTO {control_table} "
            "(database_name, schema_name, table_name, next_raw_id, updated_at) VALUES "
            f"({_sql_literal(DATABASE_NAME)}, 'public', 'raw_data', {next_raw_id}, now()) "
            "ON CONFLICT (database_name, schema_name, table_name) DO UPDATE SET "
            "next_raw_id = EXCLUDED.next_raw_id, updated_at = now();"
        ),
    )


def _extract_table_creation_sql(table_name: str) -> str:
    marker = f"CREATE TABLE public.{table_name} ("
    start = SCHEMA_SQL.find(marker)
    if start < 0:
        raise RuntimeError(f"Could not locate the DDL for public.{table_name}.")
    end = SCHEMA_SQL.find("\n);", start)
    if end < 0:
        raise RuntimeError(f"Could not locate the end of the DDL for public.{table_name}.")
    return SCHEMA_SQL[start:end + len("\n);")].strip()


def _table_column_metadata(table_name: str) -> list[tuple[str, str, str]]:
    type_pattern = (
        r"timestamp without time zone|character varying|bigint|smallint|integer|numeric"
    )
    type_names = {
        "timestamp without time zone": "timestamp",
        "character varying": "varchar",
    }
    table_sql = _extract_table_creation_sql(table_name)
    return [
        (table_name, column_name, type_names.get(postgres_type, postgres_type))
        for column_name, postgres_type in re.findall(
            rf"^    ([a-z][a-z0-9_]*) ({type_pattern})\b",
            table_sql,
            flags=re.MULTILINE,
        )
    ]


def _relational_column_metadata() -> list[tuple[str, str, str]]:
    metadata = []
    for table_number in range(1, 7):
        table_name = f"table{table_number}"
        metadata.extend(_table_column_metadata(table_name))
    return metadata


def _relational_foreign_keys() -> list[tuple[tuple[str, str], tuple[str, str]]]:
    relationships = []
    foreign_key_pattern = re.compile(
        r"FOREIGN KEY\s*\(\s*([a-z][a-z0-9_]*)\s*\)\s*"
        r"REFERENCES\s+public\.([a-z][a-z0-9_]*)\s*"
        r"\(\s*([a-z][a-z0-9_]*)\s*\)",
        flags=re.IGNORECASE,
    )
    for table_number in range(1, 7):
        source_table = f"table{table_number}"
        table_sql = _extract_table_creation_sql(source_table)
        for source_column, target_table, target_column in foreign_key_pattern.findall(
            table_sql
        ):
            relationships.append(
                ((source_table, source_column), (target_table, target_column))
            )
    return relationships


def _data_dictionary_seed() -> tuple[list[dict], list[dict], list[str]]:
    relational_metadata = _relational_column_metadata()
    columns = {
        (table_name, column_name): data_type
        for table_name, column_name, data_type in relational_metadata
    }
    canonical_targets = {}
    for source, target in _relational_foreign_keys():
        if source not in columns:
            raise RuntimeError(
                f"Foreign-key source public.{source[0]}.{source[1]} is not a known column."
            )
        if target not in columns:
            target_columns = {
                (table_name, column_name): data_type
                for table_name, column_name, data_type in _table_column_metadata(target[0])
            }
            if target not in target_columns:
                raise RuntimeError(
                    f"Foreign-key target public.{target[0]}.{target[1]} is not a known column."
                )
            columns[target] = target_columns[target]
        canonical_targets[source] = target

    def resolve_canonical(column: tuple[str, str]) -> tuple[str, str]:
        current = column
        visited = set()
        while current in canonical_targets:
            if current in visited:
                raise RuntimeError("A cycle was found in the generic dictionary relationships.")
            visited.add(current)
            current = canonical_targets[current]
        return current

    groups = {}
    for column, data_type in columns.items():
        canonical = resolve_canonical(column)
        groups.setdefault(canonical, []).append((column[0], column[1], data_type))

    family_labels = {
        "id": "identifier",
        "text": "text",
        "integer": "integer",
        "numeric": "numeric",
        "datetime": "date/time",
    }
    entries = []
    usages = []
    for canonical, members in sorted(groups.items()):
        canonical_type = columns[canonical]
        member_types = {data_type for _table, _column, data_type in members}
        if member_types != {canonical_type}:
            formatted_members = ", ".join(
                f"public.{table_name}.{column_name} ({data_type})"
                for table_name, column_name, data_type in sorted(members)
            )
            raise RuntimeError(
                "Related dictionary columns must use the same PostgreSQL type: "
                + formatted_members
            )

        standard_name = f"{DATABASE_NAME}_{canonical[0]}_{canonical[1]}"
        sorted_members = sorted(members)
        if len(sorted_members) > 1:
            related_columns = ", ".join(
                f"public.{table_name}.{column_name}"
                for table_name, column_name, _data_type in sorted_members
            )
            definition = f"Generic identifier shared by related columns: {related_columns}."
        else:
            table_name, column_name, _data_type = sorted_members[0]
            family = column_name.split("_", 1)[0]
            position = column_name.rsplit("_", 1)[-1]
            definition = (
                f"Generic {family_labels.get(family, family)} column {position} in "
                f"public.{table_name}."
            )
        entries.append(
            {
                "standard_name": standard_name,
                "definition": definition,
                "units": "not specified",
                "value_domain": (
                    f"Values compatible with PostgreSQL type {canonical_type} "
                    "and the related table constraints."
                ),
                "data_type": canonical_type,
            }
        )
        for table_name, column_name, _data_type in sorted_members:
            usages.append(
                {
                    "table_name": table_name,
                    "column_name": column_name,
                    "standard_name": standard_name,
                }
            )

    active_standard_names = {entry["standard_name"] for entry in entries}
    previous_individual_names = {
        f"{DATABASE_NAME}_{table_name}_{column_name}"
        for table_name, column_name, _data_type in relational_metadata
    }
    obsolete_standard_names = sorted(previous_individual_names - active_standard_names)
    return entries, usages, obsolete_standard_names


def _sync_manager_data_dictionary(args: argparse.Namespace) -> None:
    dictionary_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('data_dictionary')}"
    )
    usage_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('data_dictionary_usage')}"
    )
    entries, usages, obsolete_standard_names = _data_dictionary_seed()
    dictionary_values = []
    usage_values = []
    for entry in entries:
        dictionary_values.append(
            "("
            + ", ".join(
                [
                    _sql_literal(entry["standard_name"]),
                    _sql_literal(entry["definition"]),
                    _sql_literal(entry["units"]),
                    _sql_literal(entry["value_domain"]),
                    "NULL",
                    _sql_literal(entry["data_type"]),
                ]
            )
            + ")"
        )
    for usage in usages:
        usage_values.append(
            "("
            + ", ".join(
                [
                    _sql_literal(DATABASE_NAME),
                    _sql_literal("public"),
                    _sql_literal(usage["table_name"]),
                    _sql_literal(usage["column_name"]),
                    _sql_literal(usage["standard_name"]),
                    _sql_literal(GENERATOR_ACTOR),
                ]
            )
            + ")"
        )

    if not dictionary_values or not usage_values:
        raise RuntimeError("No generic relational columns were found for the data dictionary.")

    obsolete_cleanup = ""
    if obsolete_standard_names:
        obsolete_values = ", ".join(
            _sql_literal(standard_name) for standard_name in obsolete_standard_names
        )
        obsolete_cleanup = (
            f"DELETE FROM {dictionary_table} AS dictionary\n"
            f"WHERE dictionary.standard_name IN ({obsolete_values})\n"
            f"  AND NOT EXISTS (SELECT 1 FROM {usage_table} AS usage "
            "WHERE usage.standard_name = dictionary.standard_name);\n"
        )

    sql = (
        "BEGIN;\n"
        f"DELETE FROM {usage_table} WHERE database_name = {_sql_literal(DATABASE_NAME)};\n"
        + obsolete_cleanup
        + f"INSERT INTO {dictionary_table} "
        "(standard_name, definition, units, value_domain, aliases, data_type) VALUES\n"
        + ",\n".join(dictionary_values)
        + "\nON CONFLICT (standard_name) DO UPDATE SET\n"
        "    definition = EXCLUDED.definition,\n"
        "    units = EXCLUDED.units,\n"
        "    value_domain = EXCLUDED.value_domain,\n"
        "    aliases = EXCLUDED.aliases,\n"
        "    data_type = EXCLUDED.data_type;\n"
        f"INSERT INTO {usage_table} "
        "(database_name, schema_name, table_name, column_name, standard_name, linked_by) VALUES\n"
        + ",\n".join(usage_values)
        + "\nON CONFLICT (database_name, schema_name, table_name, column_name) "
        "DO UPDATE SET\n"
        "    standard_name = EXCLUDED.standard_name,\n"
        "    linked_at = now(),\n"
        "    linked_by = EXCLUDED.linked_by;\n"
        "COMMIT;"
    )
    _run_psql(args, args.control_database, sql)


def _register_manager_initial_versions(args: argparse.Namespace) -> None:
    versions_table = (
        f"{_sql_identifier(args.control_schema)}."
        f"{_sql_identifier('table_versions')}"
    )
    version_rows = []
    raw_table_ddl = _extract_table_creation_sql("raw_data")
    version_rows.append(
        (
            "raw_data",
            "0000",
            f"Synthetic Raw scope {RAW_SCHEMA_NAME}",
            raw_table_ddl,
            RAW_SCHEMA_NAME,
        )
    )

    for table_number in range(1, 7):
        table_name = f"table{table_number}"
        version_rows.append(
            (
                table_name,
                "0000",
                "Synthetic initial schema",
                _extract_table_creation_sql(table_name),
                None,
            )
        )

    values = []
    for table_name, version_code, version_title, sql_recipe, raw_schema in version_rows:
        values.append(
            "("
            + ", ".join(
                [
                    _sql_literal(DATABASE_NAME),
                    _sql_literal("public"),
                    _sql_literal(table_name),
                    _sql_literal(version_code),
                    _sql_literal(version_title),
                    _sql_literal(GENERATOR_ACTOR),
                    _sql_literal(GENERATOR_WORKSTATION),
                    _sql_literal(sql_recipe),
                    "NULL",
                    _sql_literal(sql_recipe),
                    _sql_literal("entry"),
                    "NULL",
                    "NULL",
                    "NULL",
                    _sql_literal(raw_schema) if raw_schema else "NULL",
                    _sql_literal("standard"),
                ]
            )
            + ")"
        )

    sql = (
        "BEGIN;\n"
        f"DELETE FROM {versions_table} WHERE database_name = {_sql_literal(DATABASE_NAME)};\n"
        f"INSERT INTO {versions_table} (\n"
        "    database_name, schema_name, table_name, version_code, version_title,\n"
        "    created_by, workstation_name, sql_recipe, restored_from_version,\n"
        "    version_history_log, version_history_format, raw_dump_path, raw_hash,\n"
        "    raw_ingested_at, raw_schema, operation_kind\n"
        ") VALUES\n"
        + ",\n".join(values)
        + ";\nCOMMIT;"
    )
    _run_psql(args, args.control_database, sql)


def _create_database(args: argparse.Namespace) -> None:
    if args.admin_database == DATABASE_NAME:
        raise RuntimeError(
            f"--admin-database must be different from {DATABASE_NAME!r}; "
            "use an existing maintenance database such as 'postgres'."
        )
    if _database_exists(args):
        if not args.replace:
            raise RuntimeError(
                f"Database {DATABASE_NAME!r} already exists. "
                "Use --replace only if you intend to drop and regenerate it."
            )
        _run_psql(
            args,
            args.admin_database,
            (
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{DATABASE_NAME}' AND pid <> pg_backend_pid();\n"
                f'DROP DATABASE "{DATABASE_NAME}";'
            ),
        )
    _run_psql(args, args.admin_database, f'CREATE DATABASE "{DATABASE_NAME}";')


def _create_schema(args: argparse.Namespace) -> None:
    _run_psql(args, DATABASE_NAME, SCHEMA_SQL)


def _initialize_generation(args: argparse.Namespace) -> None:
    _run_psql(
        args,
        DATABASE_NAME,
        (
            "BEGIN;\n"
            "INSERT INTO challenge_metadata.generation_progress "
            "(singleton, generator_version, fixed_seed, requested_rows, "
            "last_committed_raw_id, status, chunk_rows) VALUES "
            f"(true, {GENERATOR_VERSION}, {FIXED_SEED}, {args.rows}, 0, "
            f"'loading', {args.chunk_rows});\n"
            "COMMIT;"
        ),
    )


@dataclass(frozen=True)
class GenerationProgress:
    generator_version: int
    fixed_seed: int
    requested_rows: int
    last_committed_raw_id: int
    status: str
    chunk_rows: int


def _read_generation_progress(args: argparse.Namespace) -> GenerationProgress:
    result = _run_psql(
        args,
        DATABASE_NAME,
        (
            "SELECT generator_version, fixed_seed, requested_rows, "
            "last_committed_raw_id, status, chunk_rows "
            "FROM challenge_metadata.generation_progress WHERE singleton;"
        ),
        tuples_only=True,
    )
    fields = result.split("|")
    if len(fields) != 6:
        raise RuntimeError(
            "The database has no valid challenge_metadata.generation_progress row. "
            "It cannot be resumed safely."
        )
    return GenerationProgress(
        generator_version=int(fields[0]),
        fixed_seed=int(fields[1]),
        requested_rows=int(fields[2]),
        last_committed_raw_id=int(fields[3]),
        status=fields[4],
        chunk_rows=int(fields[5]),
    )


def _validate_resume(args: argparse.Namespace) -> GenerationProgress:
    if not _database_exists(args):
        raise RuntimeError(
            f"Database {DATABASE_NAME!r} does not exist; there is nothing to resume."
        )
    progress = _read_generation_progress(args)
    mismatches = []
    if progress.generator_version != GENERATOR_VERSION:
        mismatches.append(
            f"generator version {progress.generator_version} != {GENERATOR_VERSION}"
        )
    if progress.fixed_seed != FIXED_SEED:
        mismatches.append(f"seed {progress.fixed_seed} != {FIXED_SEED}")
    if progress.requested_rows != args.rows:
        mismatches.append(
            f"requested rows {progress.requested_rows:,} != --rows {args.rows:,}"
        )
    if progress.status not in {"loading", "loaded", "complete"}:
        mismatches.append(f"invalid status {progress.status!r}")
    if not 0 <= progress.last_committed_raw_id <= args.rows:
        mismatches.append(
            f"invalid last committed raw_id {progress.last_committed_raw_id:,}"
        )
    if progress.status == "loading" and progress.last_committed_raw_id == args.rows:
        mismatches.append("status is 'loading' even though every raw_id is committed")
    if progress.status in {"loaded", "complete"} and progress.last_committed_raw_id != args.rows:
        mismatches.append(
            f"status is {progress.status!r} before every raw_id is committed"
        )
    if mismatches:
        raise RuntimeError("Resume validation failed: " + "; ".join(mismatches))
    return progress


def _copy_chunk(
    args: argparse.Namespace,
    dataset: DatasetGenerator,
    first_raw_id: int,
    last_raw_id: int,
) -> None:
    command = _connection_arguments(args, DATABASE_NAME)
    command.extend(["-f", "-"])

    with tempfile.TemporaryFile(mode="w+b") as error_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=error_file,
            env=_psql_environment(args),
        )
        if process.stdin is None:
            raise RuntimeError("Could not open psql stdin for COPY.")

        stream = io.TextIOWrapper(process.stdin, encoding="utf-8", newline="", write_through=False)
        writer = csv.writer(stream, lineterminator="\n")
        try:
            stream.write("BEGIN;\n")
            stream.write("SET LOCAL statement_timeout = 0;\n")
            stream.write("SET LOCAL lock_timeout = 0;\n")
            stream.write(
                "DO $progress$\n"
                "DECLARE current_raw_id bigint; current_status text;\n"
                "BEGIN\n"
                "  SELECT last_committed_raw_id, status "
                "INTO current_raw_id, current_status\n"
                "  FROM challenge_metadata.generation_progress "
                "WHERE singleton FOR UPDATE;\n"
                "  IF current_raw_id IS NULL "
                f"OR current_raw_id <> {first_raw_id - 1} "
                "OR current_status <> 'loading' THEN\n"
                "    RAISE EXCEPTION 'Generation progress changed or is not resumable';\n"
                "  END IF;\n"
                "END;\n"
                "$progress$;\n"
            )
            stream.write(
                "\\copy public.raw_data "
                "(raw_id, raw_hash, raw_ingested_at, raw_schema, raw_tab, raw) "
                "FROM STDIN WITH (FORMAT csv)\n"
            )

            for physical_index in range(first_raw_id - 1, last_raw_id):
                generated_row = dataset.row_at(physical_index)
                writer.writerow(generated_row.copy_values())

            stream.write("\\.\n")
            next_status = "loaded" if last_raw_id == args.rows else "loading"
            stream.write(
                "UPDATE challenge_metadata.generation_progress SET "
                f"last_committed_raw_id = {last_raw_id}, "
                f"status = '{next_status}', chunk_rows = {args.chunk_rows}, "
                "updated_at = clock_timestamp() WHERE singleton;\n"
            )
            stream.write("COMMIT;\n")
            stream.flush()
            stream.close()
        except BaseException as exc:
            try:
                try:
                    stream.close()
                except (BrokenPipeError, OSError):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
            if isinstance(exc, (BrokenPipeError, OSError)):
                error_file.seek(0)
                error_text = error_file.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(error_text or "psql COPY connection failed") from exc
            raise
        else:
            return_code = process.wait()

        if return_code != 0:
            error_file.seek(0)
            error_text = error_file.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(error_text or f"psql COPY failed with exit code {return_code}")


def _load_raw_data(args: argparse.Namespace, start_raw_id: int = 1) -> None:
    dataset = DatasetGenerator.build(args.rows)
    started_at = time.perf_counter()
    starting_committed = start_raw_id - 1
    first_raw_id = start_raw_id
    while first_raw_id <= args.rows:
        last_raw_id = min(first_raw_id + args.chunk_rows - 1, args.rows)
        _copy_chunk(args, dataset, first_raw_id, last_raw_id)
        _sync_manager_raw_id_counter(args, last_raw_id + 1)
        elapsed = max(time.perf_counter() - started_at, 1e-9)
        generated_this_run = last_raw_id - starting_committed
        rate = generated_this_run / elapsed
        remaining = (args.rows - last_raw_id) / rate if rate else 0.0
        print(
            f"Committed {last_raw_id:,}/{args.rows:,} rows "
            f"({100.0 * last_raw_id / args.rows:5.1f}%) | "
            f"{rate:,.0f} rows/s | ETA {remaining / 60:,.1f} min",
            flush=True,
        )
        first_raw_id = last_raw_id + 1


def _finalize_database(args: argparse.Namespace) -> None:
    _run_psql(
        args,
        DATABASE_NAME,
        (
            "DO $progress$\n"
            "BEGIN\n"
            "  IF NOT EXISTS (SELECT 1 FROM challenge_metadata.generation_progress "
            f"WHERE singleton AND last_committed_raw_id = {args.rows} "
            "AND status IN ('loaded', 'complete')) THEN\n"
            "    RAISE EXCEPTION 'Raw generation has not finished';\n"
            "  END IF;\n"
            f"  IF (SELECT count(*) FROM public.raw_data) <> {args.rows} THEN\n"
            "    RAISE EXCEPTION 'Raw row count does not match the requested row count';\n"
            "  END IF;\n"
            "END;\n"
            "$progress$;\n"
            "ANALYZE public.raw_data;"
        ),
    )
    _sync_manager_raw_id_counter(args, args.rows + 1)
    _sync_manager_data_dictionary(args)
    _register_manager_initial_versions(args)
    _run_psql(args, DATABASE_NAME, "DROP SCHEMA challenge_metadata CASCADE;")


def _estimate_copy_bytes(rows: int, sample_size: int = 1_000) -> int:
    dataset = DatasetGenerator.build(rows)
    selected = min(rows, sample_size)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    for index in range(selected):
        writer.writerow(dataset.row_at(index).copy_values())
    average = len(buffer.getvalue().encode("utf-8")) / selected
    return int(math.ceil(average * rows))


def _resolve_psql(value: str | None) -> str:
    if value:
        path = Path(value)
        if path.exists():
            return str(path)
        located = shutil.which(value)
        if located:
            return located
        raise argparse.ArgumentTypeError(f"psql executable not found: {value}")
    located = shutil.which("psql")
    if not located:
        raise argparse.ArgumentTypeError(
            "psql was not found in PATH. Use --psql with the full executable path."
        )
    return located


def _resolve_ssh(value: str | None) -> str:
    if value:
        path = Path(value)
        if path.exists():
            return str(path)
        located = shutil.which(value)
        if located:
            return located
        raise argparse.ArgumentTypeError(f"ssh executable not found: {value}")
    located = shutil.which("ssh")
    if not located:
        raise argparse.ArgumentTypeError(
            "ssh was not found in PATH. Install the Windows OpenSSH Client or use "
            "--ssh_executable with the full executable path."
        )
    return located


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def _optional_ssh_tunnel(args: argparse.Namespace) -> Iterator[argparse.Namespace]:
    if args.ssh_port is None:
        yield args
        return

    ssh_executable = _resolve_ssh(args.ssh_executable)
    local_port = _available_loopback_port()
    ssh_target = f"{args.ssh_user}@{args.host}" if args.ssh_user else args.host
    forward_spec = f"127.0.0.1:{local_port}:127.0.0.1:{args.port}"
    command = [
        ssh_executable,
        "-N",
        "-T",
        "-p",
        str(args.ssh_port),
        "-L",
        forward_spec,
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        ssh_target,
    ]

    print(
        f"Opening SSH tunnel via {ssh_target}:{args.ssh_port} to "
        f"PostgreSQL 127.0.0.1:{args.port}...",
        flush=True,
    )
    print("OpenSSH may request the Linux account password or host-key confirmation.", flush=True)
    process = subprocess.Popen(command)
    deadline = time.monotonic() + 120

    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(f"SSH tunnel exited before it was ready (status {return_code}).")
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "SSH tunnel was not ready after 120 seconds. Check the SSH address, "
                        "port, credentials, and host-key prompt."
                    )
                time.sleep(0.15)

        tunneled_args = argparse.Namespace(**vars(args))
        tunneled_args.host = "127.0.0.1"
        tunneled_args.port = local_port
        print(f"SSH tunnel ready on 127.0.0.1:{local_port}.", flush=True)
        yield tunneled_args
    finally:
        _stop_process(process)


def _tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if port <= 0 or port > 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_row_count(value: str) -> int:
    try:
        rows = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--rows must be an integer") from exc
    if rows <= 0:
        raise argparse.ArgumentTypeError("--rows must be greater than zero")
    if rows > MAX_ROWS:
        raise argparse.ArgumentTypeError(
            f"--rows cannot exceed the generator limit of {MAX_ROWS:,}"
        )
    return rows


def _positive_chunk_rows(value: str) -> int:
    try:
        chunk_rows = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--chunk_rows must be an integer") from exc
    if chunk_rows <= 0:
        raise argparse.ArgumentTypeError("--chunk_rows must be greater than zero")
    return chunk_rows


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create kaggle_challenge and fill public.raw_data with a fixed-seed, "
            "deterministic expansion benchmark dataset."
        )
    )
    parser.add_argument("--rows", required=True, type=_positive_row_count)
    parser.add_argument(
        "--host",
        default=os.environ.get("PGHOST"),
        required=not bool(os.environ.get("PGHOST")),
        help=(
            "Linux server hostname or IP address. With --ssh_port it is the SSH host; "
            "otherwise it is the PostgreSQL host (or set PGHOST)."
        ),
    )
    parser.add_argument(
        "--postgres_port",
        "--postgres-port",
        "--port",
        dest="port",
        type=_tcp_port,
        default=os.environ.get("PGPORT", "5432"),
        help=(
            "PostgreSQL TCP port (default: 5432). This is not the Linux server's "
            "SSH port."
        ),
    )
    parser.add_argument(
        "--ssh_port",
        "--ssh-port",
        dest="ssh_port",
        type=_tcp_port,
        default=None,
        help=(
            "Open an automatic SSH tunnel through this port (usually 22). If omitted, "
            "connect directly to the PostgreSQL port."
        ),
    )
    parser.add_argument(
        "--ssh_user",
        "--ssh-user",
        dest="ssh_user",
        default=None,
        help="Linux SSH account. If omitted, OpenSSH uses its configured/default username.",
    )
    parser.add_argument(
        "--ssh_executable",
        "--ssh-executable",
        dest="ssh_executable",
        default=None,
        help="Path or command name for the OpenSSH client (default: ssh from PATH).",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("PGUSER"),
        required=not bool(os.environ.get("PGUSER")),
        help="Dedicated PostgreSQL role that will own the generated database (or set PGUSER).",
    )
    parser.add_argument(
        "--admin_database",
        "--admin-database",
        dest="admin_database",
        default=os.environ.get("PGDATABASE", "postgres"),
        help="Existing database used to issue CREATE DATABASE (default: postgres).",
    )
    parser.add_argument(
        "--control_database",
        "--control-database",
        dest="control_database",
        default=os.environ.get("PDM_CONTROL_DB", DEFAULT_CONTROL_DATABASE),
        help=(
            "DB Manager control database that owns raw_id_counters "
            f"(default: {DEFAULT_CONTROL_DATABASE})."
        ),
    )
    parser.add_argument(
        "--control_schema",
        "--control-schema",
        dest="control_schema",
        default=os.environ.get("PDM_CONTROL_SCHEMA", DEFAULT_CONTROL_SCHEMA),
        help=(
            "DB Manager control schema that owns raw_id_counters "
            f"(default: {DEFAULT_CONTROL_SCHEMA})."
        ),
    )
    parser.add_argument(
        "--user_pass",
        "--user-pass",
        dest="user_pass",
        default=None,
        help=(
            "PostgreSQL password. This is passed to psql through PGPASSWORD and is not "
            f"stored in the generated database. Prefer {PASSWORD_ENV_VAR} or .pgpass "
            "on shared systems."
        ),
    )
    parser.add_argument(
        "--chunk_rows",
        "--chunk-rows",
        dest="chunk_rows",
        type=_positive_chunk_rows,
        default=DEFAULT_CHUNK_ROWS,
        help=f"Rows committed per transaction (default: {DEFAULT_CHUNK_ROWS:,}).",
    )
    parser.add_argument(
        "--psql",
        default=None,
        help="Path or command name for the psql client.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--replace",
        action="store_true",
        help="Drop an existing kaggle_challenge database before generation.",
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help="Resume after the last transaction recorded in generation_progress.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.ssh_user and args.ssh_port is None:
        parser.error("--ssh_user requires --ssh_port")
    if args.ssh_executable and args.ssh_port is None:
        parser.error("--ssh_executable requires --ssh_port")
    if not str(args.control_database).strip():
        parser.error("--control_database cannot be empty")
    if not str(args.control_schema).strip():
        parser.error("--control_schema cannot be empty")
    if args.control_database == DATABASE_NAME:
        parser.error(f"--control_database must be different from {DATABASE_NAME!r}")
    try:
        args.psql = _resolve_psql(args.psql)
        estimated_copy_bytes = _estimate_copy_bytes(args.rows)
        print(f"Fixed seed: {FIXED_SEED}")
        print(f"Rows: {args.rows:,}")
        print(f"Estimated COPY stream: {estimated_copy_bytes / (1024 ** 3):,.1f} GiB")
        print("PostgreSQL data and WAL require additional disk beyond the COPY estimate.")
        print(f"Transaction chunk: {args.chunk_rows:,} rows")

        with _optional_ssh_tunnel(args) as connection_args:
            print(
                f"PostgreSQL target: {connection_args.user}@{connection_args.host}:"
                f"{connection_args.port}/{DATABASE_NAME}"
            )
            _ensure_manager_control_store(connection_args)

            if connection_args.resume:
                progress = _validate_resume(connection_args)
                _sync_manager_raw_id_counter(
                    connection_args,
                    progress.last_committed_raw_id + 1,
                )
                if progress.status == "complete":
                    print(
                        f"Database {DATABASE_NAME!r} is already complete with "
                        f"{progress.last_committed_raw_id:,} committed rows; "
                        "cleaning legacy generation metadata."
                    )
                start_raw_id = progress.last_committed_raw_id + 1
                print(
                    f"Resuming after raw_id {progress.last_committed_raw_id:,} "
                    f"(previous chunk size: {progress.chunk_rows:,}).",
                    flush=True,
                )
            else:
                print("Creating database and empty relational destinations...", flush=True)
                _create_database(connection_args)
                _create_schema(connection_args)
                _initialize_generation(connection_args)
                _sync_manager_raw_id_counter(connection_args, 1)
                start_raw_id = 1

            if start_raw_id <= connection_args.rows:
                print("Streaming deterministic rows through PostgreSQL COPY...", flush=True)
                _load_raw_data(connection_args, start_raw_id=start_raw_id)
            print("Collecting PostgreSQL statistics...", flush=True)
            _finalize_database(connection_args)
            print(
                f"Database {DATABASE_NAME!r} is ready: public.raw_data has "
                f"{connection_args.rows:,} rows; public.table1..table6 are empty."
            )
            return 0
    except KeyboardInterrupt:
        print(
            "Generation interrupted. Previously committed chunks were preserved; "
            "rerun the same command with --resume.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
