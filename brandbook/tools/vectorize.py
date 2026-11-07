"""Trace flat-colour logo crops into layered SVG using potrace.
usage: vectorize.py in.png out.svg bg=R,G,B layer=name:R,G,B [layer=...] [crop=x0,y0,x1,y1] [scale=4]
"""
import sys, subprocess, re, os, tempfile
import numpy as np
from PIL import Image

args = sys.argv[1:]
src, out = args[0], args[1]
opts = {'scale': '4'}
layers = []
for a in args[2:]:
    k, v = a.split('=', 1)
    if k == 'layer':
        n, c = v.split(':')
        layers.append((n, np.array([int(x) for x in c.split(',')], float)))
    else:
        opts[k] = v
bg = np.array([int(x) for x in opts['bg'].split(',')], float)
S = int(opts['scale'])
im = Image.open(src).convert('RGB')
if 'crop' in opts:
    im = im.crop(tuple(int(x) for x in opts['crop'].split(',')))
a = np.asarray(im).astype(float)
H, W, _ = a.shape

# per pixel: pick the ink whose bg->ink line best explains the pixel, alpha = projection
best_alpha = np.zeros((len(layers), H, W))
best_err = np.full((len(layers), H, W), 1e9)
for i, (n, c) in enumerate(layers):
    d = c - bg
    t = ((a - bg) @ d) / (d @ d)
    t = np.clip(t, 0, 1)
    recon = bg + t[..., None] * d
    err = np.linalg.norm(a - recon, axis=2)
    best_alpha[i] = t
    best_err[i] = err
owner = np.argmin(best_err, axis=0)

paths = {}
tmp = tempfile.mkdtemp()
for i, (n, c) in enumerate(layers):
    alpha = np.where(owner == i, best_alpha[i], 0.0)
    alpha_img = Image.fromarray((alpha * 255).astype(np.uint8), 'L').resize((W * S, H * S), Image.BICUBIC)
    bw = np.asarray(alpha_img) > 127
    pbm = os.path.join(tmp, n + '.pbm')
    Image.fromarray((~bw).astype(np.uint8) * 255, 'L').convert('1').save(pbm)
    svg = os.path.join(tmp, n + '.svg')
    subprocess.run(['potrace', pbm, '-s', '-o', svg, '--turdsize', opts.get('turd', '40'),
                    '--alphamax', opts.get('alphamax', '0.75'), '--opttolerance', '0.2', '--flat'], check=True)
    txt = open(svg).read()
    g = re.search(r'<g transform="([^"]+)"[^>]*>(.*?)</g>', txt, re.S)
    ds = re.findall(r'd="([^"]+)"', g.group(2))
    paths[n] = (g.group(1), ds)

# bounding box of all ink (in upscaled px)
ys, xs = np.where((owner >= 0) & (np.max(best_alpha * (np.arange(len(layers))[:, None, None] == owner), axis=0) > 0.5))
pad = int(opts.get('pad', '0'))
x0, x1, y0, y1 = xs.min() - pad, xs.max() + 1 + pad, ys.min() - pad, ys.max() + 1 + pad
vb = f'{x0*S} {y0*S} {(x1-x0)*S} {(y1-y0)*S}'
parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">']
for n, (tr, ds) in paths.items():
    parts.append(f'<g class="{n}" transform="{tr}">' + ''.join(f'<path d="{d}"/>' for d in ds) + '</g>')
parts.append('</svg>')
open(out, 'w').write('\n'.join(parts))
print(out, 'bbox px', x0, y0, x1, y1, 'aspect', round((x1-x0)/(y1-y0), 3))
