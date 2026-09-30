"""Read-only Linux PostgreSQL sampling; observations are lower bounds, not peaks.

SQL TEMP relations are not necessarily visible from another SQL connection.
Count their filesystem names, including TOAST, forks and relation segments.
No SQL queries, database credentials or source content are collected.
"""
import re

RESOURCE_COMMAND = (
    'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.stat; '
    'echo QF_FILES; '
    "find -L /var/lib/postgresql/data/base /var/lib/postgresql/data/pg_tblspc "
    "-type f -printf '%p\\t%s\\t%b\\n' 2>/dev/null; "
    'echo QF_SCAN_EXIT:$?'
)
TEMP_RELATION = re.compile(r't[0-9]+_[0-9]+(?:_(?:fsm|vm|init))?(?:\.[0-9]+)?\Z')


def category(path):
    parts = path.replace('\\', '/').split('/')
    if len(parts) < 2:
        return None
    # Shared/parallel filesets have nested directories and arbitrary leaf names.
    if 'pgsql_tmp' in parts[:-1]:
        return 'executor_work_files'
    if parts[-2].isdigit() and TEMP_RELATION.fullmatch(parts[-1]):
        return 'sql_temp_relations'
    return None


def parse_sample(output, started, finished):
    lines = output.splitlines()
    boundary = lines.index('QF_FILES')
    if not lines[-1].startswith('QF_SCAN_EXIT:'):
        raise ValueError('Incomplete filesystem scan')
    stats = dict(line.split() for line in lines[1:boundary])
    sizes = {name: {'apparent_bytes': 0, 'allocated_bytes': 0, 'files': 0}
             for name in ('executor_work_files', 'sql_temp_relations')}
    for line in lines[boundary + 1:-1]:
        path, apparent, blocks = line.rsplit('\t', 2)
        name = category(path)
        if name is not None:
            sizes[name]['apparent_bytes'] += int(apparent)
            # GNU find %b reports 512-byte blocks, regardless of filesystem size.
            sizes[name]['allocated_bytes'] += int(blocks) * 512
            sizes[name]['files'] += 1
    return {'started_monotonic_seconds': started,
            'finished_monotonic_seconds': finished,
            'scan_complete': int(lines[-1].split(':')[1]) == 0,
            'working_set_bytes': max(0, int(lines[0]) - int(stats.get('inactive_file', 0))),
            'categories': sizes,
            'ephemeral_apparent_bytes': sum(x['apparent_bytes'] for x in sizes.values()),
            'ephemeral_allocated_bytes': sum(x['allocated_bytes'] for x in sizes.values())}


def summarize_samples(samples, errors=0):
    complete = [s for s in samples if s['scan_complete']]
    starts = [s['started_monotonic_seconds'] for s in samples]
    return {
        'method': 'Linux cgroup and GNU find; PGDATA/base and followed PGDATA/pg_tblspc',
        'requested_wait_between_scans_seconds': 0.5,
        'sample_count': len(samples),
        'complete_scan_count': len(complete),
        'failed_scan_count': len(samples) - len(complete) + errors,
        'max_observed_start_gap_seconds': max((b-a for a, b in zip(starts, starts[1:])), default=None),
        'max_observed_scan_duration_seconds': max((s['finished_monotonic_seconds']-s['started_monotonic_seconds'] for s in samples), default=None),
        'sampled_peak_ephemeral_apparent_bytes': max((s['ephemeral_apparent_bytes'] for s in complete), default=None),
        'sampled_peak_ephemeral_allocated_bytes': max((s['ephemeral_allocated_bytes'] for s in complete), default=None),
        'sampled_peak_executor_work_files_apparent_bytes': max((s['categories']['executor_work_files']['apparent_bytes'] for s in complete), default=None),
        'sampled_peak_sql_temp_relations_apparent_bytes': max((s['categories']['sql_temp_relations']['apparent_bytes'] for s in complete), default=None),
        'exact_peak': False,
        'global_intermediate_disk_peak_bytes': None,
        'limitations': ['Sequential, non-atomic scans can miss short-lived files or fail during deletion.',
                       'Category maxima occur independently; the combined maximum is computed per scan.',
                       'Persistent receipts, client caches/staging, WAL and volume provisioning are excluded.',
                       'Instrumentation overhead and real Linux calibration remain to be measured.'],
    }
