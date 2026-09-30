"""Render the animated GIFs used by the README (docs/assets/*.gif).

Only facts already recorded in reports/ and docs/ are drawn: 100,000 official
rows, the six destinations and their row counts, the measured medians and every
individual run. Illustrative diagrams are labelled as such. Rendering is
supersampled 2x for anti-aliasing. Usage: python scripts/build_assets.py
"""
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/assets'
FONTS = Path('C:/Windows/Fonts')
BG, PANEL, LINE = (11, 15, 25), (20, 27, 43), (44, 56, 82)
TEXT, MUTED = (232, 238, 250), (139, 152, 178)
CYAN, AMBER, GREEN, RED, VIOLET = (56, 189, 248), (251, 191, 36), (52, 211, 153), (248, 113, 113), (167, 139, 250)
S = 2  # supersampling factor
TABLES = [('table3', 4), ('table4', 26), ('table1', 100000), ('table5', 100000), ('table6', 100000), ('table2', 232386)]

def font(size, bold=False):
    return ImageFont.truetype(str(FONTS / ('bahnschrift.ttf')), size * S) if not bold else ImageFont.truetype(str(FONTS / 'bahnschrift.ttf'), size * S)

def mono(size):
    return ImageFont.truetype(str(FONTS / 'consola.ttf'), size * S)

def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def seg(t, a, b):
    """Progress of t inside [a, b], eased."""
    return ease((t - a) / (b - a))

class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.img = Image.new('RGB', (w * S, h * S), BG)
        self.d = ImageDraw.Draw(self.img)
        for y in range(0, h * S, 4):  # subtle vertical gradient
            k = y / (h * S)
            self.d.line([(0, y), (w * S, y)], fill=(int(11 + 9 * k), int(15 + 10 * k), int(25 + 20 * k)), width=4)

    def rect(self, x, y, w, h, fill=PANEL, outline=LINE, r=10, width=1):
        self.d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=r * S, fill=fill, outline=outline, width=width * S)

    def check(self, cx, cy, size, fill):
        k = size * 0.32
        self.line([(cx - k, cy), (cx - k * 0.3, cy + k * 0.8), (cx + k, cy - k * 0.8)], fill=fill, width=max(2, size // 8))

    def cross(self, cx, cy, size, fill):
        k = size * 0.3
        self.line([(cx - k, cy - k), (cx + k, cy + k)], fill=fill, width=max(2, size // 8))
        self.line([(cx - k, cy + k), (cx + k, cy - k)], fill=fill, width=max(2, size // 8))

    def text(self, x, y, s, size=16, fill=TEXT, anchor='la', mono_font=False):
        if s in ('✓', '✕'):
            (self.check if s == '✓' else self.cross)(x, y, size, fill)
            return
        if s.startswith('✓ '):
            self.check(x + size * 0.4, y + size * 0.62, size, fill)
            x += size * 1.1
            s = s[2:]
        self.d.text((x * S, y * S), s, font=mono(size) if mono_font else font(size), fill=fill, anchor=anchor)

    def line(self, pts, fill=LINE, width=2):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=width * S, joint='curve')

    def dot(self, x, y, r=4, fill=CYAN):
        self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=fill)

    def frame(self):
        return self.img.resize((self.w, self.h), Image.LANCZOS)

def save(frames, name, ms=60, hold_last=900):
    OUT.mkdir(parents=True, exist_ok=True)
    quant = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    durations = [ms] * len(quant)
    durations[-1] = hold_last
    quant[0].save(OUT / name, save_all=True, append_images=quant[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(name, len(frames), 'frames', round((OUT / name).stat().st_size / 1024), 'KiB')

def title(c, head, sub):
    c.text(40, 26, head, 28)
    c.text(40, 62, sub, 15, MUTED)

def chip(c, x, y, s, fill=CYAN):
    w = int(len(s) * 8.6 + 26)
    c.rect(x, y, w, 26, fill=(fill[0] // 6 + 10, fill[1] // 6 + 12, fill[2] // 6 + 20), outline=fill, r=13)
    c.text(x + w / 2, y + 13, s, 13, fill, 'mm')
    return w

# --------------------------------------------------------------------------- header
def header():
    W, H, N = 1280, 380, 72
    frames = []
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        for gx in range(0, W, 40):  # faint grid
            c.line([(gx, 0), (gx, H)], fill=(17, 23, 38), width=1)
        for gy in range(0, H, 40):
            c.line([(0, gy), (W, gy)], fill=(17, 23, 38), width=1)
        # data stream: rows enter the forge and leave as six columns
        fx = 190
        for k in range(26):
            p = (t + k / 26) % 1
            x = 40 + p * (fx - 60)
            y = 300 - 8 * (k % 3) + math.sin(p * 9 + k) * 6
            c.dot(x, y, 3, MUTED if p < .9 else CYAN)
        c.rect(fx - 30, 250, 70, 100, fill=(28, 20, 12), outline=AMBER, r=14, width=2)
        glow = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 3)
        c.text(fx + 5, 300, 'JSON', 15, (int(200 + 55 * glow), int(150 + 40 * glow), 60), 'mm', True)
        for j in range(6):
            y0 = 236 + j * 20
            c.line([(fx + 40, 300), (fx + 120, y0 + 8)], fill=(70, 62, 40), width=1)
            for k in range(6):
                p = (t * 1.3 + k / 6 + j * .07) % 1
                x = fx + 40 + p * 260
                yy = 300 + (y0 + 8 - 300) * min(1, p * 3)
                c.dot(x, yy, 2.5, [CYAN, GREEN, VIOLET, AMBER, CYAN, GREEN][j])
            c.rect(fx + 300, y0, 120, 16, fill=PANEL, outline=LINE, r=5)
            c.text(fx + 310, y0 + 8, TABLES[j][0], 12, MUTED, 'lm', True)
        # text block
        c.text(640, 60, 'QueryFoundry', 76, TEXT)
        c.d.rectangle([640 * S, 160 * S, 940 * S, 164 * S], fill=AMBER)
        c.text(642, 184, 'Fast, verifiable, recoverable database expansion', 23, TEXT)
        c.text(642, 222, 'Kaggle Python Database Performance Optimization', 15, MUTED)
        c.text(642, 244, 'Windows GUI  ·  PostgreSQL 17 on Linux  ·  SSH', 15, MUTED)
        x = 642
        for label, col in (('100k official rows', CYAN), ('6 equivalent tables', GREEN)):
            x += chip(c, x, 285, label, col) + 10
        x = 642
        for label, col in (('durable receipts', VIOLET), ('no official score claimed', AMBER)):
            x += chip(c, x, 320, label, col) + 10
        c.text(642, 356, 'Independent extension of the organizer application. Apache-2.0.', 14, MUTED)
        frames.append(c.frame())
    save(frames, 'header.gif', 55, 55)

# --------------------------------------------------------------------------- pipeline
def pipeline():
    W, H, N = 1100, 520, 80
    frames = []
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        title(c, '1 · One expansion, six destinations', 'Official GUI worker: validate, then one REPEATABLE READ transaction, then register six versions')
        c.rect(40, 130, 200, 300, r=14)
        c.text(140, 150, 'raw_data', 22, CYAN, 'ma')
        c.text(140, 182, '100,000 rows', 15, MUTED, 'ma')
        c.text(140, 204, 'raw -> values (JSON)', 13, MUTED, 'ma', True)
        for r in range(12):
            y = 230 + r * 15
            c.rect(60, y, 160, 9, fill=(34, 48, 78), outline=(34, 48, 78), r=3)
            k = (t * 30 - r * 0.9)
            if 0 < k < 1:
                c.rect(60, y, 160, 9, fill=CYAN, outline=CYAN, r=3)
        c.rect(300, 100, 40, 360, fill=(24, 30, 48), r=6)
        c.text(320, 478, 'ONE TRANSACTION', 13, AMBER, 'mm', True)
        for j, (name, rows) in enumerate(TABLES):
            y = 115 + j * 58
            start = 0.08 + j * 0.10
            p = seg(t, start, start + 0.5)
            c.line([(240, 280), (300, 280)], fill=LINE, width=2)
            c.line([(340, 280), (400, y + 22)], fill=(60, 74, 104), width=2)
            if 0 < p < 1:
                bx, by = 340 + 60 * p, 280 + (y + 22 - 280) * p
                c.dot(bx, by, 5, AMBER)
            c.rect(400, y, 420, 44, r=10)
            c.text(416, y + 22, f'{j + 1}. public.{name}', 16, TEXT, 'lm', True)
            shown = int(rows * seg(t, start + .1, start + .55))
            c.text(800, y + 22, f'{shown:,}', 18, GREEN if shown == rows else MUTED, 'rm', True)
            c.rect(560, y + 30, 200, 5, fill=(30, 40, 62), outline=(30, 40, 62), r=2)
            c.rect(560, y + 30, max(2, int(200 * seg(t, start + .1, start + .55))), 5, fill=GREEN, outline=GREEN, r=2)
        v = seg(t, 0.82, 0.98)
        c.rect(860, 130, 200, 300, r=14, outline=VIOLET if v > 0 else LINE)
        c.text(960, 150, 'control DB', 22, VIOLET, 'ma')
        c.text(960, 182, 'table_versions', 14, MUTED, 'ma', True)
        for j in range(6):
            on = v * 6 > j
            c.rect(880, 215 + j * 30, 160, 22, fill=(38, 30, 66) if on else (24, 30, 48), outline=VIOLET if on else LINE, r=6)
            c.text(960, 226 + j * 30, ('v0001 ' + TABLES[j][0]) if on else '···', 13, TEXT if on else MUTED, 'mm', True)
        c.text(40, 502, 'Row counts from reports/durable-recovery.json (100,000-row official generator, fixed seed).', 13, MUTED)
        frames.append(c.frame())
    save(frames, 'pipeline.gif', 60, 1200)

# --------------------------------------------------------------------------- payload_once
def payload():
    W, H, N = 1100, 520, 72
    frames = []
    fields = ['text_column_1', 'text_column_2', 'integer_column_2', 'datetime_column_1']
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        title(c, '2 · payload_once: parse the JSON once per row', 'Only JSON extraction changes. Casts, filters, joins, order and outputs stay identical. (illustrative diagram)')
        for side, (x0, label, col) in enumerate(((40, 'Original recipe', RED), (570, 'payload_once (LATERAL + OFFSET 0)', GREEN))):
            c.rect(x0, 120, 490, 330, r=14, outline=col)
            c.text(x0 + 245, 140, label, 18, col, 'ma', True)
            c.rect(x0 + 20, 190, 130, 60, fill=(24, 34, 58), r=8)
            c.text(x0 + 85, 220, 'one row', 15, TEXT, 'mm')
            c.text(x0 + 85, 238, "raw->'values'", 11, MUTED, 'mm', True)
            for j, f in enumerate(fields):
                y = 150 + j * 58
                c.rect(x0 + 330, y + 40, 140, 34, fill=PANEL, r=8)
                c.text(x0 + 400, y + 57, f, 11, MUTED, 'mm', True)
                if side == 0:
                    ph = (t * 3 + j * .25) % 1
                    c.line([(x0 + 150, 220), (x0 + 330, y + 57)], fill=(120, 60, 66), width=1)
                    c.rect(x0 + 205, y + 46 + 0, 70, 22, fill=(58, 28, 36), outline=RED, r=6)
                    c.text(x0 + 240, y + 57, 'parse', 12, RED, 'mm', True)
                    c.dot(x0 + 150 + 180 * ph, 220 + (y + 57 - 220) * ph, 4, RED)
                else:
                    ph = (t * 3 + j * .25) % 1
                    c.line([(x0 + 260, 220), (x0 + 330, y + 57)], fill=(40, 110, 80), width=1)
                    c.dot(x0 + 260 + 70 * ph, 220 + (y + 57 - 220) * ph, 4, GREEN)
            if side == 1:
                c.line([(x0 + 150, 220), (x0 + 200, 220)], fill=(40, 110, 80), width=2)
                c.rect(x0 + 200, 195, 60, 50, fill=(20, 58, 46), outline=GREEN, r=8)
                c.text(x0 + 230, 220, 'parse', 12, GREEN, 'mm', True)
                c.text(x0 + 230, 262, 'once', 12, GREEN, 'mm', True)
            else:
                c.text(x0 + 245, 425, 'each referenced field re-reads the JSON', 14, MUTED, 'mm')
        c.text(815, 425, 'the payload is extracted once and reused', 14, MUTED, 'mm')
        c.text(40, 484, 'Only the six known organizer recipes are rewritten; custom recipes keep their original SQL.', 13, MUTED)
        frames.append(c.frame())
    save(frames, 'payload-once.gif', 60, 900)

# --------------------------------------------------------------------------- recovery
def recovery():
    W, H, N = 1100, 560, 96
    frames = []
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        title(c, '3 · Durable recovery without a distributed transaction', 'Effects and their receipt commit together, in each database. A retry with the same job UUID replays the stored result.')
        c.rect(40, 130, 300, 230, r=14, outline=CYAN)
        c.text(190, 148, 'data database', 20, CYAN, 'ma', True)
        c.rect(860, 130, 200, 230, r=14, outline=VIOLET)
        c.text(960, 148, 'control database', 20, VIOLET, 'ma', True)
        c.rect(60, 190, 260, 40, fill=(24, 34, 58), r=8)
        c.text(190, 210, 'six tables (rows)', 15, TEXT, 'mm', True)
        c.rect(60, 250, 260, 40, fill=(24, 34, 58), r=8)
        c.text(190, 270, 'receipt (job UUID, sha256)', 14, TEXT, 'mm', True)
        commit = t > .28
        if commit:
            for y in (190, 250):
                c.rect(60, y, 260, 40, fill=(16, 60, 48), outline=GREEN, r=8)
            c.text(190, 210, 'six tables (rows)', 15, TEXT, 'mm', True)
            c.text(190, 270, 'receipt (job UUID, sha256)', 14, TEXT, 'mm', True)
            c.check(78, 322, 16, GREEN)
            c.text(206, 322, 'COMMIT, same transaction', 15, GREEN, 'mm', True)
        # client
        c.rect(430, 130, 270, 230, r=14)
        c.text(565, 148, 'GUI client (SSH)', 18, TEXT, 'ma', True)
        steps = [(.0, .28, 'send expansion'), (.28, .45, 'reply lost'), (.45, .70, 'retry same job UUID'), (.70, 1.0, 'register six versions')]
        for k, (a, b, label) in enumerate(steps):
            on = a <= t < b or (k == 3 and t >= a)
            done = t >= b and k < 3
            col = (RED if k == 1 else AMBER if k == 2 else CYAN) if on else (GREEN if done else MUTED)
            c.dot(460, 200 + k * 36, 7, col)
            c.text(480, 200 + k * 36, label, 15, TEXT if (on or done) else MUTED, 'lm', True)
        # packets
        if t < .28:
            p = seg(t, 0, .28); c.dot(430 - 90 * p, 240 + 0 * p, 6, CYAN)
        elif .28 <= t < .45:
            p = seg(t, .28, .45); c.dot(340 + 90 * p, 300, 6, RED if p > .5 else GREEN)
            if p > .5: c.cross(390, 318, 26, RED)
        elif .45 <= t < .70:
            p = seg(t, .45, .55); c.dot(430 - 90 * p, 240, 6, AMBER)
            q = seg(t, .55, .70)
            c.dot(340 + 90 * q, 300, 6, GREEN)
            if t > .58:
                c.text(190, 344, 'receipt found -> stored result, 0 new rows', 13, AMBER, 'mm', True)
        else:
            p = seg(t, .7, .95); c.dot(700 + 160 * p, 240, 6, VIOLET)
            v = seg(t, .85, 1.0)
            for j in range(6):
                on = v * 6 > j
                c.rect(880, 190 + j * 26, 160, 20, fill=(38, 30, 66) if on else (24, 30, 48), outline=VIOLET if on else LINE, r=5)
        c.rect(40, 400, 1020, 110, r=12)
        c.text(60, 418, 'Verified failpoints (reports/durable-recovery.json)', 16, TEXT, 'la', True)
        for k, s in enumerate(('pre-commit failure rolls back rows and receipt', 'lost data reply returns the committed result', 'new connection finalizes versions without repeating data', 'changed request under the same UUID is rejected')):
            c.text(60 + (k % 2) * 510, 448 + (k // 2) * 26, '✓ ' + s, 13, GREEN, 'la')
        c.text(40, 530, 'Known limits: full expansion repeats after a pre-commit failure (no chunk checkpoints); no cross-database atomicity.', 12, MUTED)
        frames.append(c.frame())
    save(frames, 'recovery.gif', 60, 1500)

# --------------------------------------------------------------------------- benchmark
def benchmark():
    W, H, N = 1100, 560, 70
    plain = json.loads((ROOT / 'reports/gui-benchmark.json').read_text())['median_seconds']
    durable = json.loads((ROOT / 'reports/durable-gui/gui-benchmark.json').read_text())['median_seconds']
    runs = {}
    for f in (ROOT / 'reports/durable-gui/gui-runs.json',):
        for r in json.loads(f.read_text()):
            runs.setdefault(r['mode'], []).append(r['measured_processing_total_seconds'])
    frames = []
    top, base = 170, 470
    scale = (base - top) / 18.0
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        title(c, '4 · Honest, local measurements', 'MEASURED PROCESSING TOTAL, full Windows GUI path, 100,000 official rows, 3 alternating repeats per variant')
        for s in (0, 5, 10, 15):
            y = base - s * scale
            c.line([(90, y), (1040, y)], fill=(30, 40, 62), width=1)
            c.text(80, y, f'{s} s', 12, MUTED, 'rm')
        groups = [('Without receipts', plain['baseline'], plain['payload_once'], None, 130),
                  ('Durable series (original without receipts, candidate with)', durable['baseline'], durable['payload_once'], runs, 600)]
        for name, a, b, rr, x0 in groups:
            c.text(x0 + 150, 500, name, 13, MUTED, 'ma')
            for k, (val, col, lab) in enumerate(((a, RED, 'original'), (b, GREEN, 'payload_once'))):
                x = x0 + k * 170
                h = val * scale * seg(t, .05 + k * .1, .6)
                c.rect(x, base - h, 110, h, fill=col, outline=col, r=8)
                c.text(x + 55, base - h - 22, f'{val * seg(t, .05 + k * .1, .6):.3f} s', 17, TEXT, 'mm', True)
                c.text(x + 55, base + 18, lab, 13, TEXT, 'mm')
                if rr and t > .65:
                    key = 'baseline' if k == 0 else 'payload_once'
                    for n, v in enumerate(rr[key]):
                        c.dot(x + 130 + n * 0, base - v * scale, 5, (255, 255, 255))
        if t > .65:
            c.dot(846, 150, 5, (255, 255, 255)); c.text(860, 150, 'individual runs', 13, MUTED, 'lm')
        c.rect(40, 520, 1020, 30, fill=(40, 30, 12), outline=AMBER, r=8)
        c.text(550, 535, 'Original 3-run range overlaps the candidate\'s. No official score, no 300M-row claim. Reduction: 7.1 % / 6.9 % medians.', 13, AMBER, 'mm')
        frames.append(c.frame())
    save(frames, 'benchmark.gif', 60, 1800)

# --------------------------------------------------------------------------- integrity
def integrity():
    W, H, N = 1100, 520, 80
    frames = []
    for i in range(N):
        t = i / N
        c = Canvas(W, H)
        title(c, '5 · Guardrails: nothing official is altered', 'Extensions live in dbperf/. The organizer application, generator and fixed seed stay byte-identical.')
        c.rect(40, 130, 480, 330, r=14, outline=CYAN)
        c.text(60, 148, 'app/ · 32 organizer files (SHA-256)', 17, CYAN, 'la', True)
        n = int(32 * seg(t, .05, .6))
        for k in range(32):
            x, y = 70 + (k % 8) * 55, 200 + (k // 8) * 55
            ok = k < n
            c.rect(x, y, 44, 44, fill=(16, 60, 48) if ok else (24, 30, 48), outline=GREEN if ok else LINE, r=8)
            if ok: c.text(x + 22, y + 22, '✓', 20, GREEN, 'mm', True)
        c.text(60, 425, f'verify_upstream.py: {n}/32 unchanged', 15, GREEN if n == 32 else MUTED, 'la', True)
        c.rect(560, 130, 500, 150, r=14, outline=AMBER)
        c.text(580, 148, 'Six-table equivalence', 17, AMBER, 'la', True)
        c.text(580, 182, '(query EXCEPT ALL reference)', 13, MUTED, 'la', True)
        c.text(580, 202, 'UNION ALL (reference EXCEPT ALL query) = 0 rows', 13, MUTED, 'la', True)
        for j, (name, _) in enumerate(TABLES):
            on = seg(t, .3, .9) * 6 > j
            c.rect(580 + j * 78, 235, 68, 30, fill=(16, 60, 48) if on else (24, 30, 48), outline=GREEN if on else LINE, r=8)
            c.text(614 + j * 78, 250, name, 12, TEXT if on else MUTED, 'mm', True)
        c.rect(560, 300, 500, 160, r=14, outline=VIOLET)
        c.text(580, 318, 'Source protections kept', 17, VIOLET, 'la', True)
        for k, s in enumerate(('source / destination guards', 'raw_hash manifest re-checked in the snapshot', 'row counters and version records', 'exact six destinations, fixed order')):
            on = seg(t, .5, 1.0) * 4 > k
            c.text(580, 352 + k * 26, ('✓ ' if on else '· ') + s, 14, TEXT if on else MUTED, 'la')
        frames.append(c.frame())
    save(frames, 'integrity.gif', 60, 1800)

if __name__ == '__main__':
    header(); pipeline(); payload(); recovery(); benchmark(); integrity()
