#!/usr/bin/env python3
"""Build the pixel-art sprites for the desktop pet from the reference artwork.

    python tools/make_sprites.py

Reads  assets/reference/duck-robot.webp
Writes assets/sprites/{robot_body.png, shadow.png, sprites.json, preview.png}  (the duck is not used by the app)

Pipeline
  1. detect the logical pixel grid of the (up-scaled, soft-shaded) reference
  2. downsample to that grid, choosing each block colour by mode (no blur)
  3. cut the duck / robot out of the sheet (drop "?", speech bubble, ground shadow)
  4. blank the robot's screen face and repaint a flat screen colour
  5. quantise to a small palette, reduce to the target height with a palette vote
     (outline-biased so 1px outlines survive), repaint the three robot lights
  6. write PNGs + metadata + a 4x contact sheet that proves the coordinates

Only Pillow is required.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "assets" / "reference" / "duck-robot.webp"
OUT = ROOT / "assets" / "sprites"

DUCK_TARGET_H = 64
ROBOT_TARGET_H = 52
DUCK_COLORS = 16
ROBOT_COLORS = 13          # + screen, cyan, amber, green  -> <= 20 total

# --- hand-placed regions of the reference, in ORIGINAL reference pixels -------
# (the reference is a single fixed artwork, so these are stable)
DUCK_SEED = (360, 560)                 # a point inside the duck body
ROBOT_SEED = (880, 780)                # a point inside the robot's lower body
SCREEN_SEED = (860, 600)               # a point inside the robot's screen
DUCK_ROWS_SHADOW_FROM = 790            # below this y ground shadow may exist
ROBOT_ROWS_SHADOW_FROM = 850
DUCK_EYE_ROI = (415, 320, 460, 385)    # near eye of the duck
DUCK_BEAK_ROI = (430, 350, 600, 470)
ROBOT_FACE_ROI = (770, 575, 960, 735)  # where the cyan eyes / mouth live
FACE_SPLIT_Y = 672                     # cyan above = eyes, below = mouth
LIGHTS_ROI = (856, 792, 976, 832)      # the three small lights
LIGHT_NAMES = ("amber", "green", "black")


# ============================================================================
# helpers
# ============================================================================
def lum(c):
    return (c[0] + c[1] + c[2]) / 3.0


def d2(a, b):
    # slightly perceptual weighting
    return 2 * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + 3 * (a[2] - b[2]) ** 2


def hexs(c):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c[:3])


def mean_color(cols):
    n = len(cols)
    return tuple(sum(c[i] for c in cols) / n for i in range(3))


def mode_color(cols):
    """Mode of 5-bit-quantised colours, returned as the mean of that bin."""
    bins = {}
    for c in cols:
        bins.setdefault((c[0] >> 3, c[1] >> 3, c[2] >> 3), []).append(c)
    best = max(bins.values(), key=len)
    return mean_color(best)


# ============================================================================
# 1. grid detection
# ============================================================================
def detect_grid(im):
    """Return (block, phase_x, phase_y) of the logical pixel grid."""
    W, H = im.size
    data = im.tobytes()
    stride = W * 4
    dx = [0] * (W - 1)
    dy = [0] * (H - 1)
    for y in range(0, H - 1):
        row = y * stride
        nxt = row + stride
        for x in range(0, W - 1):
            i = row + x * 4
            j = i + 4
            k = nxt + x * 4
            dx[x] += (abs(data[i] - data[j]) + abs(data[i + 1] - data[j + 1])
                      + abs(data[i + 2] - data[j + 2]) + abs(data[i + 3] - data[j + 3]))
            dy[y] += (abs(data[i] - data[k]) + abs(data[i + 1] - data[k + 1])
                      + abs(data[i + 2] - data[k + 2]) + abs(data[i + 3] - data[k + 3]))

    def score(d, p):
        s = [sum(d[o::p]) for o in range(p)]
        mean = sum(s) / p
        return (max(s) / mean if mean else 0.0), s.index(max(s))

    scores = {}
    for p in range(2, 17):
        sx, ox = score(dx, p)
        sy, oy = score(dy, p)
        scores[p] = (min(sx, sy), (ox + 1) % p, (oy + 1) % p)
    best = max(v[0] for v in scores.values())
    # smallest period that is (almost) as strong as the strongest one
    for p in sorted(scores):
        if scores[p][0] >= 0.85 * best:
            return p, scores[p][1], scores[p][2]
    raise RuntimeError("grid detection failed")


# ============================================================================
# 2. native-resolution grid
# ============================================================================
def to_native(im, block, px0, py0):
    """grid[y][x] = (r,g,b) or None; block colour = mode of the block."""
    W, H = im.size
    nw, nh = (W - px0) // block, (H - py0) // block
    px = im.load()
    grid = [[None] * nw for _ in range(nh)]
    for by in range(nh):
        for bx in range(nw):
            cols = []
            n = 0
            for y in range(py0 + by * block, py0 + by * block + block):
                for x in range(px0 + bx * block, px0 + bx * block + block):
                    r, g, b, a = px[x, y]
                    n += 1
                    if a >= 128:
                        cols.append((r, g, b))
            if len(cols) * 2 > n:
                c = mode_color(cols)
                grid[by][bx] = tuple(int(round(v)) for v in c)
    return grid


def components(mask_fn, w, h, conn8=True):
    seen = [[False] * w for _ in range(h)]
    comps = []
    nbrs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if conn8:
        nbrs += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    for y in range(h):
        for x in range(w):
            if seen[y][x] or not mask_fn(x, y):
                continue
            stack = [(x, y)]
            seen[y][x] = True
            comp = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for ox, oy in nbrs:
                    nx, ny = cx + ox, cy + oy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and mask_fn(nx, ny):
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            comps.append(comp)
    return comps


def is_light_neutral(c):
    return (max(c) - min(c)) <= 40 and lum(c) >= 150


# ============================================================================
# palette
# ============================================================================
def build_palette(cc, k, seeds):
    """Weighted k-means; cc = {colour: count}. Seeds are fixed starting points."""
    cols = list(cc)
    pal = [tuple(s) for s in seeds]
    while len(pal) < k:
        best, bs = None, -1
        for c in cols:
            dist = min(d2(c, p) for p in pal)
            sc = dist * math.sqrt(cc[c])
            if sc > bs:
                best, bs = c, sc
        if bs <= 0:
            break
        pal.append(best)
    for _ in range(12):
        sums = [[0.0, 0.0, 0.0, 0] for _ in pal]
        for c in cols:
            i = min(range(len(pal)), key=lambda j: d2(c, pal[j]))
            w = cc[c]
            s = sums[i]
            s[0] += c[0] * w
            s[1] += c[1] * w
            s[2] += c[2] * w
            s[3] += w
        new = []
        for p, s in zip(pal, sums):
            new.append(tuple(s[i] / s[3] for i in range(3)) if s[3] else p)
        pal = new
    return [tuple(int(round(v)) for v in p) for p in pal]


def nearest(pal, c):
    return min(range(len(pal)), key=lambda j: d2(c, pal[j]))


# ============================================================================
# reduction with palette vote
# ============================================================================
def reduce_ids(ids, w, h, dw, dh, outline_ids, outline_bias=1.9, t_bias=0.8):
    """ids[y][x] = palette index or -1 (transparent). Fractional-coverage vote."""
    sx, sy = w / dw, h / dh
    out = [[-1] * dw for _ in range(dh)]
    for j in range(dh):
        y0, y1 = j * sy, (j + 1) * sy
        for i in range(dw):
            x0, x1 = i * sx, (i + 1) * sx
            votes = {}
            for yy in range(int(y0), min(h, int(math.ceil(y1)))):
                wy = min(yy + 1, y1) - max(yy, y0)
                if wy <= 0:
                    continue
                for xx in range(int(x0), min(w, int(math.ceil(x1)))):
                    wx = min(xx + 1, x1) - max(xx, x0)
                    if wx <= 0:
                        continue
                    k = ids[yy][xx]
                    wgt = wx * wy
                    if k == -1:
                        wgt *= t_bias
                    elif k in outline_ids:
                        wgt *= outline_bias
                    votes[k] = votes.get(k, 0) + wgt
            if votes:
                out[j][i] = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return out


def crop_bbox(grid):
    h, w = len(grid), len(grid[0])
    xs = [x for y in range(h) for x in range(w) if grid[y][x] is not None]
    ys = [y for y in range(h) for x in range(w) if grid[y][x] is not None]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def drop_small(ids, w, h):
    """Keep only the largest 8-connected opaque component."""
    comps = components(lambda x, y: ids[y][x] != -1, w, h, True)
    if len(comps) <= 1:
        return
    big = max(comps, key=len)
    keep = set(big)
    for comp in comps:
        if comp is not big:
            for x, y in comp:
                ids[y][x] = -1


def close_gaps(ids, w, h, maxgap=2):
    """Fill 1-2 px horizontal holes in the silhouette that have solid pixels
    on both sides and above (left by ground-shadow removal at the base)."""
    for y in range(1, h):
        x = 0
        while x < w:
            if ids[y][x] == -1:
                x1 = x
                while x1 < w and ids[y][x1] == -1:
                    x1 += 1
                if 0 < x and x1 < w and x1 - x <= maxgap and                         all(ids[y - 1][i] != -1 for i in range(x, x1)):
                    for i in range(x, x1):
                        ids[y][i] = ids[y][x - 1]
                x = x1
            else:
                x += 1


def ids_to_image(ids, pal):
    h, w = len(ids), len(ids[0])
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y in range(h):
        for x in range(w):
            if ids[y][x] >= 0:
                px[x, y] = pal[ids[y][x]] + (255,)
    return im


# ============================================================================
# main
# ============================================================================
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ref = Image.open(REF).convert("RGBA")
    rpx = ref.load()

    # ---- 1/2. grid + native -------------------------------------------------
    block, ox, oy = detect_grid(ref)
    print("grid: block=%d phase=(%d,%d)" % (block, ox, oy))
    grid = to_native(ref, block, ox, oy)
    NH, NW = len(grid), len(grid[0])

    def nat(px_, py_):  # original pixel -> native cell
        return (px_ - ox) // block, (py_ - oy) // block

    # ---- 3. segmentation ----------------------------------------------------
    # remove ground-shadow pixels (light neutral grey below the characters)
    duck_row0 = nat(0, DUCK_ROWS_SHADOW_FROM)[1]
    robot_row0 = nat(0, ROBOT_ROWS_SHADOW_FROM)[1]
    split_x = nat(700, 0)[0]
    for y in range(NH):
        for x in range(NW):
            c = grid[y][x]
            if c is None:
                continue
            if x < split_x:
                if y >= duck_row0 and is_light_neutral(c):
                    grid[y][x] = None
            elif y >= robot_row0 and is_light_neutral(c) and lum(c) <= 199:
                # ground shadow (~#bcbcc4); the body's underside shading is lighter
                grid[y][x] = None

    comps = components(lambda x, y: grid[y][x] is not None, NW, NH, True)

    def comp_at(pt):
        cx, cy = nat(*pt)
        for comp in comps:
            if (cx, cy) in set(comp):
                return comp
        raise RuntimeError("no component at %r" % (pt,))

    duck_comp = comp_at(DUCK_SEED)
    robot_comp = comp_at(ROBOT_SEED)

    def cut(comp):
        xs = [p[0] for p in comp]
        ys = [p[1] for p in comp]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        g = [[None] * (x1 - x0) for _ in range(y1 - y0)]
        for x, y in comp:
            g[y - y0][x - x0] = grid[y][x]
        return g, x0, y0

    duck_g, dgx, dgy = cut(duck_comp)
    robot_g, rgx, rgy = cut(robot_comp)

    # ---- sample feature colours straight from the reference -----------------
    def region_pixels(roi, pred):
        x0, y0, x1, y1 = roi
        res = []
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b, a = rpx[x, y]
                if a >= 128 and pred(r, g, b):
                    res.append((x, y, (r, g, b)))
        return res

    is_cyan = lambda r, g, b: b > 190 and g > 170 and r < 200 and (b - r) > 50
    face = region_pixels(ROBOT_FACE_ROI, is_cyan)
    cyan = tuple(int(round(v)) for v in mode_color([c for _, _, c in face]))

    # ---- 4. robot: remove face, flat screen ---------------------------------
    rw, rh = len(robot_g[0]), len(robot_g)

    def is_dark(c):
        return c is not None and lum(c) < 60

    def is_cyanish(c):
        return c is not None and is_cyan(*c)

    scx, scy = nat(*SCREEN_SEED)
    scx -= rgx
    scy -= rgy
    scr = components(lambda x, y: is_dark(robot_g[y][x]) or is_cyanish(robot_g[y][x]),
                     rw, rh, False)
    scr_ring = next(c for c in scr if (scx, scy) in set(c))
    ring = set(scr_ring)
    # fill holes (odd highlight / noise cells enclosed by the ring)
    rx0 = min(p[0] for p in ring); rx1 = max(p[0] for p in ring)
    ry0 = min(p[1] for p in ring); ry1 = max(p[1] for p in ring)
    outside = set()
    stack = [(x, y) for x in range(rx0 - 1, rx1 + 2) for y in (ry0 - 1, ry1 + 1)] + \
            [(x, y) for y in range(ry0 - 1, ry1 + 2) for x in (rx0 - 1, rx1 + 1)]
    while stack:
        p = stack.pop()
        if p in outside or p in ring:
            continue
        if not (rx0 - 1 <= p[0] <= rx1 + 1 and ry0 - 1 <= p[1] <= ry1 + 1):
            continue
        outside.add(p)
        stack += [(p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)]
    ring |= {(x, y) for y in range(ry0, ry1 + 1) for x in range(rx0, rx1 + 1)
             if (x, y) not in outside}
    # interior = ring minus its outermost 1-cell layer (that layer is the outline)
    interior = set(p for p in ring
                   if all((p[0] + dx_, p[1] + dy_) in ring
                          for dx_, dy_ in ((1, 0), (-1, 0), (0, 1), (0, -1))))
    dark_inside = [robot_g[y][x] for x, y in interior
                   if is_dark(robot_g[y][x]) and not is_cyanish(robot_g[y][x])]
    screen_col = tuple(int(round(v)) for v in mean_color(dark_inside))
    print("screen colour", hexs(screen_col), "cyan", hexs(cyan))

    # erase the three lights from the native robot (repainted cleanly later)
    lx0, ly0 = nat(LIGHTS_ROI[0], LIGHTS_ROI[1])
    lx1, ly1 = nat(LIGHTS_ROI[2] - 1, LIGHTS_ROI[3] - 1)
    cells = [(x - rgx, y - rgy) for y in range(ly0, ly1 + 1) for x in range(lx0, lx1 + 1)]
    body_col = mode_color([robot_g[y][x] for x, y in cells if robot_g[y][x] is not None])
    body_col = tuple(int(round(v)) for v in body_col)
    for x, y in cells:
        if robot_g[y][x] is not None:
            robot_g[y][x] = body_col

    # ---- 5. palettes + reduction -------------------------------------------
    def make_ids(g, k, seeds, fixed=None, fixed_cells=()):
        h, w = len(g), len(g[0])
        fixed_cells = set(fixed_cells)
        cc = Counter(g[y][x] for y in range(h) for x in range(w)
                     if g[y][x] is not None and (x, y) not in fixed_cells)
        pal = build_palette(cc, k, seeds)
        extra = {}
        if fixed:
            for name, col in fixed.items():
                # re-use a palette entry if it is already (nearly) that colour
                j = nearest(pal, col)
                if d2(pal[j], col) < 400 and name != "screen":
                    pal[j] = col
                    extra[name] = j
                else:
                    pal.append(col)
                    extra[name] = len(pal) - 1
        ids = [[-1] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                if (x, y) in fixed_cells:
                    ids[y][x] = extra["screen"]
                elif g[y][x] is not None:
                    ids[y][x] = nearest(pal, g[y][x])
        return pal, ids, extra

    BLACK_SEED, WHITE_SEED, ORANGE_SEED = (6, 6, 8), (252, 252, 244), (252, 176, 40)

    # --- duck
    dpal, dids, _ = make_ids(duck_g, DUCK_COLORS, [BLACK_SEED, WHITE_SEED, ORANGE_SEED])
    d_outline = {i for i, c in enumerate(dpal) if lum(c) < 45}
    dh0, dw0 = len(dids), len(dids[0])
    ddh = DUCK_TARGET_H
    ddw = round(dw0 * ddh / dh0)
    dred = reduce_ids(dids, dw0, dh0, ddw, ddh, d_outline)
    drop_small(dred, ddw, ddh)

    # --- robot
    rpal, rids, rextra = make_ids(
        robot_g, ROBOT_COLORS, [BLACK_SEED, WHITE_SEED],
        fixed={"cyan": cyan, "screen": screen_col}, fixed_cells=interior)
    SCREEN_ID = rextra["screen"]
    r_outline = {i for i, c in enumerate(rpal) if lum(c) < 45 and i != SCREEN_ID}
    rh0, rw0 = len(rids), len(rids[0])
    rdh = ROBOT_TARGET_H
    rdw = round(rw0 * rdh / rh0)
    rred = reduce_ids(rids, rw0, rh0, rdw, rdh, r_outline)
    drop_small(rred, rdw, rdh)
    close_gaps(rred, rdw, rdh)

    # screen = largest 4-connected region of SCREEN_ID
    scomps = components(lambda x, y: rred[y][x] == SCREEN_ID, rdw, rdh, False)
    sbig = max(scomps, key=len)
    sset = set(sbig)
    for comp in scomps:
        if comp is not sbig:               # stray screen-coloured pixels -> outline
            for x, y in comp:
                rred[y][x] = nearest(rpal, (6, 6, 8))
    # fill 1-cell pin-holes inside the screen bbox (enclosed by screen cells)
    xs0 = min(p[0] for p in sset); xs1 = max(p[0] for p in sset)
    ys0 = min(p[1] for p in sset); ys1 = max(p[1] for p in sset)
    changed = True
    while changed:
        changed = False
        for y in range(ys0, ys1 + 1):
            for x in range(xs0, xs1 + 1):
                if (x, y) in sset:
                    continue
                n = sum(((x + a, y + b) in sset) for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if n >= 3:
                    rred[y][x] = SCREEN_ID
                    sset.add((x, y))
                    changed = True
    xs0 = min(p[0] for p in sset); xs1 = max(p[0] for p in sset)
    ys0 = min(p[1] for p in sset); ys1 = max(p[1] for p in sset)

    # ---- coordinates (original px -> reduced sprite px) ---------------------
    def to_sprite(ptx, pty, gx, gy, w0, h0, dw, dh):
        u = (ptx - ox) / block - gx
        v = (pty - oy) / block - gy
        return u * dw / w0, v * dh / h0

    def rsp(ptx, pty):
        return to_sprite(ptx, pty, rgx, rgy, rw0, rh0, rdw, rdh)

    def dsp(ptx, pty):
        return to_sprite(ptx, pty, dgx, dgy, dw0, dh0, ddw, ddh)

    def centroid(pts):
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    # eyes / mouth centres
    eyes_px = [p for p in face if p[1] < FACE_SPLIT_Y]
    mouth_px = [p for p in face if p[1] >= FACE_SPLIT_Y]
    xs_sorted = sorted(p[0] for p in eyes_px)
    midx = (xs_sorted[0] + xs_sorted[-1]) / 2
    left = centroid([p for p in eyes_px if p[0] < midx])
    right = centroid([p for p in eyes_px if p[0] >= midx])
    mx0, mx1 = min(p[0] for p in mouth_px), max(p[0] for p in mouth_px)
    my0, my1 = min(p[1] for p in mouth_px), max(p[1] for p in mouth_px)
    mouth = ((mx0 + mx1) / 2, (my0 + my1) / 2)

    def ipt(p):
        return [int(math.floor(p[0])), int(math.floor(p[1]))]

    eyeL, eyeR, mouthS = ipt(rsp(*left)), ipt(rsp(*right)), ipt(rsp(*mouth))

    # lights: repaint as clean 2x2 dots
    amber_px = region_pixels(LIGHTS_ROI, lambda r, g, b: r > 220 and 140 < g < 215 and b < 90)
    green_px = region_pixels(LIGHTS_ROI, lambda r, g, b: g > r + 40 and g > b + 40)
    black_px = region_pixels(LIGHTS_ROI, lambda r, g, b: r < 70 and g < 70 and b < 70)
    amber_col = tuple(int(round(v)) for v in mode_color([c for _, _, c in amber_px]))
    green_col = tuple(int(round(v)) for v in mode_color([c for _, _, c in green_px]))
    black_col = rpal[nearest(rpal, (6, 6, 8))]
    lights = []
    for name, pts, col in (("amber", amber_px, amber_col), ("green", green_px, green_col),
                           ("black", black_px, black_col)):
        cxs, cys = rsp(*centroid([(p[0], p[1]) for p in pts]))
        tlx, tly = int(round(cxs)) - 1, int(round(cys)) - 1
        rpal.append(col)
        cid = len(rpal) - 1 if name != "black" else nearest(rpal[:-1], (6, 6, 8))
        for yy in (tly, tly + 1):
            for xx in (tlx, tlx + 1):
                rred[yy][xx] = cid
        lights.append([tlx, tly, name])

    # antenna tip = top-most opaque pixel (column of the antenna line)
    top_y = next(y for y in range(rdh) if any(v != -1 for v in rred[y]))
    tip_x = [x for x in range(rdw) if rred[top_y][x] != -1]
    antenna_tip = [int(round(sum(tip_x) / len(tip_x))), top_y]

    # duck feature points
    eye_px = region_pixels(DUCK_EYE_ROI, lambda r, g, b: r < 70 and g < 70 and b < 70)
    ex, ey = dsp(*centroid([(p[0], p[1]) for p in eye_px]))
    duck_eye = [int(math.floor(ex)), int(math.floor(ey))]
    orange = region_pixels(DUCK_BEAK_ROI, lambda r, g, b: r > 200 and 90 < g < 215 and b < 90)
    tipx = max(p[0] for p in orange)
    tip_pts = [(p[0], p[1]) for p in orange if p[0] >= tipx - 2 * block]
    bx, by = dsp(*centroid(tip_pts))
    duck_beak = [int(math.floor(bx)), int(math.floor(by))]

    # ---- build images --------------------------------------------------------
    duck_img = ids_to_image(dred, dpal)
    robot_img = ids_to_image(rred, rpal)

    # tight crop (post-reduction) and shift coordinates accordingly
    def tight(img):
        bb = img.getchannel("A").getbbox()
        return bb

    bb = tight(duck_img)
    duck_img = duck_img.crop(bb)
    duck_eye = [duck_eye[0] - bb[0], duck_eye[1] - bb[1]]
    duck_beak = [duck_beak[0] - bb[0], duck_beak[1] - bb[1]]

    bb = tight(robot_img)
    robot_img = robot_img.crop(bb)
    sh = lambda p: [p[0] - bb[0], p[1] - bb[1]]
    eyeL, eyeR, mouthS = sh(eyeL), sh(eyeR), sh(mouthS)
    lights = [[l[0] - bb[0], l[1] - bb[1], l[2]] for l in lights]
    antenna_tip = sh(antenna_tip)
    screen_rect = [xs0 - bb[0], ys0 - bb[1], xs1 - bb[0], ys1 - bb[1]]

    # hard alpha
    for img in (duck_img, robot_img):
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                if px[x, y][3] not in (0, 255):
                    px[x, y] = px[x, y][:3] + ((255 if px[x, y][3] >= 128 else 0),)

    robot_img.save(OUT / "robot_body.png")

    # ---- shadow --------------------------------------------------------------
    sw = robot_img.width
    shh = max(6, round(sw * 0.22))
    ss = 8
    big = Image.new("L", (sw * ss, shh * ss), 0)
    bd = ImageDraw.Draw(big)
    levels = ((1.0, 55), (0.78, 95), (0.52, 135))
    for scale, alpha in levels:
        w_, h_ = sw * ss * scale, shh * ss * scale
        cx, cy = sw * ss / 2, shh * ss / 2
        bd.ellipse([cx - w_ / 2, cy - h_ / 2, cx + w_ / 2, cy + h_ / 2], fill=alpha)
    small = big.resize((sw, shh), Image.BOX)
    shadow = Image.new("RGBA", (sw, shh), (70, 70, 84, 0))
    shadow.putalpha(small)
    shadow.save(OUT / "shadow.png")

    # ---- metadata --------------------------------------------------------------
    meta = {
        "robot": {"file": "robot_body.png", "size": [robot_img.width, robot_img.height],
                  "screen": screen_rect,
                  "eyes": {"left": eyeL, "right": eyeR},
                  "mouth": mouthS,
                  "lights": lights,
                  "antenna_tip": antenna_tip,
                  "base_y": robot_img.height - 1},
        "screen_color": hexs(screen_col),
        "cyan": hexs(cyan),
        "block": block,
    }
    (OUT / "sprites.json").write_text(json.dumps(meta, indent=2) + "\n")

    # ---- verification: screen must be flat ------------------------------------
    rp = robot_img.load()
    sx0, sy0, sx1, sy1 = screen_rect
    cols_in = Counter(rp[x, y][:3] for y in range(sy0, sy1 + 1) for x in range(sx0, sx1 + 1))
    print("colours inside screen rect:", {hexs(k): v for k, v in cols_in.items()})
    n_pal = lambda im_: len({im_.getpixel((x, y)) for y in range(im_.height)
                             for x in range(im_.width)} - {(0, 0, 0, 0)})
    print("palette size: robot %d" % n_pal(robot_img))

    # ---- preview -----------------------------------------------------------------
    test = robot_img.copy()
    tp = test.load()
    cy_ = tuple(cyan) + (255,)
    for ex_, ey_ in (eyeL, eyeR):
        for dx_ in (0, 1):
            for dy_ in (-1, 0, 1):
                xx, yy = ex_ + dx_ - 0, ey_ + dy_
                tp[xx, yy] = cy_
    mx_, my_ = mouthS
    for i, dx_ in enumerate(range(-3, 4)):
        tp[mx_ + dx_, my_ + (i % 2)] = cy_

    Z = 4
    items = [("robot_body", robot_img), ("shadow", shadow),
             ("robot + test face", test)]
    pad, lab = 16, 14
    bg = (217, 217, 217, 255)
    wtot = pad + sum(i.width * Z + pad for _, i in items)
    htot = pad * 2 + lab + max(i.height for _, i in items) * Z
    sheet = Image.new("RGBA", (wtot, htot), bg)
    dr = ImageDraw.Draw(sheet)
    xcur = pad
    for name, img in items:
        big_ = img.resize((img.width * Z, img.height * Z), Image.NEAREST)
        y_ = pad + lab + (htot - pad * 2 - lab - big_.height)
        sheet.alpha_composite(big_, (xcur, y_))
        dr.text((xcur, pad - 2), "%s %dx%d" % (name, img.width, img.height), fill=(40, 40, 40, 255))
        xcur += big_.width + pad
    sheet.convert("RGB").save(OUT / "preview.png")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
