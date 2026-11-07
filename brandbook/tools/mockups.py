"""Cut radios out of the supplied photos, blank the original brand marks, export RGBA WebP.

usage: python3 -I tools/mockups.py <folder with the two source photos> img/
"""
import sys, json
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

up, outdir = sys.argv[1], sys.argv[2]
meta = {}

def cutout(rgb, light_thr, force_bg=None, out=None):
    a = rgb.astype(float)
    L = a @ [0.299, 0.587, 0.114]
    light = L > light_thr
    if force_bg is not None:
        light |= force_bg
    lab, n = ndimage.label(light)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge))
    fg = ~bg
    fg = ndimage.binary_opening(fg, iterations=1)
    fg = ndimage.binary_fill_holes(fg)
    # keep the largest component only (the radio)
    lab2, n2 = ndimage.label(fg)
    sizes = ndimage.sum(fg, lab2, range(1, n2 + 1))
    fg = lab2 == (np.argmax(sizes) + 1)
    alpha = Image.fromarray((fg * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    img = Image.fromarray(rgb.astype(np.uint8)).convert('RGBA')
    img.putalpha(alpha)
    bbox = img.getbbox()
    return img, bbox

# ---------- Type 1: Kanglong-style (no display) ----------
k = np.asarray(Image.open(f'{up}/de8855c1-image.jpg').convert('RGB')).copy()
# blank the nameplate: fill each inner row with that row's median dark colour
x0, x1, y0, y1 = 354, 728, 1005, 1091
for y in range(y0, y1):
    row = k[y, x0:x1].astype(int)
    dark = row[row.sum(1) < 150]
    col = np.median(dark, 0) if len(dark) else np.array([12, 12, 14])
    k[y, x0:x1] = col
# blank the lower shield sticker region? keep as is (product badge)
cx0, cy0, cx1, cy1 = 180, 420, 900, 2220
crop = k[cy0:cy1, cx0:cx1]
force = np.zeros(crop.shape[:2], bool)
force[:330, 420:] = True     # red headline text of the ad, above the knobs
img, bb = cutout(crop, 120, force)
img = img.crop(bb)
# fade the antenna tip: the source photo dissolves it into the background
px = np.asarray(img).copy(); n = int(px.shape[0] * 0.10)
px[:n, :, 3] = (px[:n, :, 3] * (np.linspace(0, 1, n) ** 1.5)[:, None]).astype(np.uint8)
img = Image.fromarray(px)
img.save(f'{outdir}/radio-type1.webp', quality=88, method=6)
W, H = img.size
ox, oy = cx0 + bb[0], cy0 + bb[1]
meta['type1'] = {'w': W, 'h': H, 'plate': [(x0 - ox) / W, (y0 - oy) / H, (x1 - x0) / W, (y1 - y0) / H]}

# ---------- Type 2: display + keypad ----------
a = np.asarray(Image.open(f'{up}/ba308fa1-image.jpg').convert('RGB')).astype(float).copy()
tx0, tx1, ty0, ty1 = 470, 642, 1284, 1338
top = a[ty0 - 2:ty0, tx0:tx1].mean(0)
bot = a[ty1:ty1 + 2, tx0:tx1].mean(0)
for i, y in enumerate(range(ty0, ty1)):
    t = (i + 0.5) / (ty1 - ty0)
    a[y, tx0:tx1] = top * (1 - t) + bot * t
a = np.clip(a, 0, 255)
cx0, cy0, cx1, cy1 = 240, 330, 880, 2340
crop = a[cy0:cy1, cx0:cx1]
img, bb = cutout(crop, 236)
img = img.crop(bb)
img.save(f'{outdir}/radio-type2.webp', quality=88, method=6)
W, H = img.size
ox, oy = cx0 + bb[0], cy0 + bb[1]
gx0, gx1, gy0, gy1 = 368, 742, 1270, 1350   # glass band above the display
meta['type2'] = {'w': W, 'h': H, 'glass': [(gx0 - ox) / W, (gy0 - oy) / H, (gx1 - gx0) / W, (gy1 - gy0) / H]}
print(json.dumps(meta, indent=1))
