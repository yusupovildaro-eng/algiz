"""Export colour variants of the traced logos and inject <symbol>s into index.html.

Masters live in logo/traced/*.svg; each has two groups: class="ink" (wordmark / letter A)
and class="accent" (.uz, waves, underline swoosh).

    python3 tools/export_logos.py        # run from the brandbook/ folder
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORANGE, INK, WHITE, LASER = '#F2700E', '#0F1319', '#FFFFFF', '#B4B9C0'

VARIANTS = {
    # file name                      master       ink    accent
    'aloqa-compact-on-dark.svg':    ('compact',   WHITE, ORANGE),
    'aloqa-compact-white.svg':      ('compact',   WHITE, WHITE),
    'aloqa-compact-on-light.svg':   ('compact',   INK,   ORANGE),
    'aloqa-full-on-dark.svg':       ('full',      WHITE, ORANGE),
    'aloqa-full-on-light.svg':      ('full',      INK,   ORANGE),
    'aloqa-slogan-ru-on-dark.svg':  ('slogan-ru', WHITE, ORANGE),
    'aloqa-slogan-ru-on-light.svg': ('slogan-ru', INK,   ORANGE),
    'aloqa-slogan-uz-on-dark.svg':  ('slogan-uz', WHITE, ORANGE),
    'aloqa-icon-on-dark.svg':       ('icon',      WHITE, ORANGE),
    'aloqa-icon-on-light.svg':      ('icon',      INK,   ORANGE),
    'aloqa-icon-white.svg':         ('icon',      WHITE, WHITE),
}


def load(name):
    return (ROOT / 'logo' / 'traced' / f'{name}.svg').read_text()


def main():
    for fname, (master, ink, accent) in VARIANTS.items():
        svg = load(master)
        svg = svg.replace('<g class="ink"', f'<g fill="{ink}"').replace('<g class="accent"', f'<g fill="{accent}"')
        (ROOT / 'logo' / fname).write_text(svg)

    # <symbol> block for the brandbook page; colours come from --li (ink) and --la (accent)
    symbols = ['<svg width="0" height="0" style="position:absolute" aria-hidden="true">']
    for master in ['compact', 'full', 'slogan-ru', 'slogan-uz', 'icon']:
        svg = load(master)
        vb = re.search(r'viewBox="([^"]+)"', svg).group(1)
        body = svg.split('>', 1)[1].rsplit('</svg>', 1)[0]
        body = body.replace('<g class="ink"', '<g style="fill:var(--li,#fff)"')
        body = body.replace('<g class="accent"', '<g style="fill:var(--la,#F2700E)"')
        symbols.append(f'<symbol id="lg-{master}" viewBox="{vb}">{body}</symbol>')
    symbols.append('</svg>')
    block = '\n'.join(symbols)

    page = ROOT / 'index.html'
    html = page.read_text()
    html = re.sub(r'<!--SYMBOLS:start-->.*?<!--SYMBOLS:end-->',
                  lambda m: f'<!--SYMBOLS:start-->\n{block}\n<!--SYMBOLS:end-->', html, flags=re.S)
    page.write_text(html)
    print('ok:', len(VARIANTS), 'files, symbols injected')


if __name__ == '__main__':
    main()
