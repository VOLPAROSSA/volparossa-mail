#!/usr/bin/env python3
"""Build the Mail product page offline. Review previews are noindex by default."""
import argparse
import hashlib
from html import escape
import importlib.util
import json
from pathlib import Path
import re
import shutil
from mesh import render_mesh
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / 'vendor/volparossa-theme'
DATA = json.loads((ROOT / 'src/content.json').read_text())


def render(source, values):
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise ValueError('Unknown template variable: ' + key)
        return str(values[key])
    return re.sub(r'{{(\w+)}}', replace, source)


def icon(name):
    svg = (ROOT / 'assets/icons/lucide' / (name + '.svg')).read_text()
    return re.sub(r'<svg\b[^>]*>', '<svg class="mail-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">', svg, count=1)


def picture(name, hero=False, sizes="100vw"):
    group = json.loads((ROOT / 'assets/mail/variants.json').read_text())[name]
    sources = []
    for kind, media in [('mobile', '(max-width: 900px)'), ('desktop', None)]:
        if kind not in group:
            continue
        items = group[kind]
        condition = f' media="{media}"' if media else ''
        for ext, mime in [('avif', 'image/avif'), ('webp', 'image/webp'), ('jpg', 'image/jpeg')]:
            srcset = ', '.join(f'assets/mail/{i["stem"]}.{ext} {i["width"]}w' for i in items)
            sources.append(f'<source{condition} type="{mime}" srcset="{srcset}" sizes="{sizes}" width="{items[-1]["width"]}" height="{items[-1]["height"]}">')
    fallback = group.get('desktop', group.get('mobile'))[-1]
    loading = 'fetchpriority="high" loading="eager"' if hero else 'loading="lazy" decoding="async"'
    return '<picture class="mail-picture">' + ''.join(sources) + f'<img src="assets/mail/{fallback["stem"]}.jpg" width="{fallback["width"]}" height="{fallback["height"]}" alt="" {loading}></picture>'


def canonical_url(origin, base_path):
    parts = urlsplit(origin)
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Origin must be an HTTP(S) origin without a path')
    if not base_path.startswith('/') or not base_path.endswith('/') or '..' in base_path or '?' in base_path or '#' in base_path:
        raise ValueError('Base path must be an absolute directory path with a trailing slash')
    return origin.rstrip('/') + base_path


def build(output, production, origin, base_path):
    canonical = canonical_url(origin, base_path)
    manifest = json.loads((THEME / 'theme-version.json').read_text())
    if manifest['source_commit'] != DATA['theme_commit']:
        raise SystemExit('Unexpected shared theme source')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((THEME / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Changed pinned theme file: ' + name)
    output = output.resolve()
    if output == ROOT or output in ROOT.parents or any(output.is_relative_to(ROOT / p) for p in ['src', 'vendor', 'assets', 'masters', 'scripts', 'docs', 'review']):
        raise SystemExit('Output must be a dedicated build directory')
    if output.exists() and any(output.iterdir()) and not (output / '.mail-website-build').exists():
        raise SystemExit('Refusing to replace a directory not owned by this builder')
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / '.mail-website-build').write_text('Static Mail website output\n')
    shutil.copytree(THEME / 'assets', output / 'assets')
    shutil.copytree(ROOT / 'assets', output / 'assets', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'src/mail.css', output / 'assets/mail.css')
    shutil.copy2(THEME / 'favicon.ico', output / 'favicon.ico')
    shutil.copy2(THEME / 'LICENSE', output / 'LICENSE')
    (output / '.nojekyll').touch()
    spec = importlib.util.spec_from_file_location('shared_navigation', THEME / 'shared/navigation.py')
    navigation_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(navigation_module)
    nav = json.loads((THEME / 'data/navigation.json').read_text())
    projects = json.loads((THEME / 'data/projects.json').read_text())
    links = navigation_module.site_links(nav, projects, 'https://volparossa.org/', {'github': DATA['repository']})
    github = (THEME / 'assets/icons/github.svg').read_text().replace('<svg ', '<svg class="github-icon" aria-hidden="true" focusable="false" ', 1)
    values = {**links, 'root': '', 'github_icon': github}
    # Applications is the active family in the shared navigation contract.
    values['nav_links'] = navigation_module.navigation_links(nav['main'], links, active='applications', github_icon=github)
    values['footer_links'] = navigation_module.navigation_links(nav['footer'], links, active='applications', github_icon=github, footer=True)
    values['header'] = render((THEME / 'templates/header.html').read_text(), values)
    values['footer'] = render((THEME / 'templates/footer.html').read_text(), values)
    values.update({key: escape(str(value), quote=True) for key, value in DATA.items()})
    values['canonical'] = escape(canonical, quote=True)
    scenes = {'hero':'hero-harbour','delivery':'delivery-harbour','encrypt':'custody-encrypt','hold':'custody-hold','retrieve':'custody-retrieve','mailbox':'own-mailbox','brain':'shared-brain-harbour','closing':'closing-harbour'}
    for key,name in scenes.items():
        sizes = '(max-width:900px) 100vw, 32vw' if key in ['encrypt','hold','retrieve'] else '(max-width:68rem) 100vw, 40vw' if key=='brain' else '100vw'
        values[key+'_picture'] = picture(name, hero=key=='hero', sizes=sizes)
    values.update(desktop_mesh=render_mesh('desktop'), mobile_mesh=render_mesh('mobile'))
    names = {'lock':'lock-keyhole','network':'network','user':'user-round','key':'key-round','archive':'archive','check':'circle-check','shield':'shield-check','globe':'globe','device':'laptop','database':'database'}
    values.update({key+'_icon': icon(name) for key,name in names.items()})
    custom = {'star':'<path d="m12 3 2.7 5.6 6.2.9-4.5 4.4 1.1 6.1-5.5-2.9L6.5 20l1.1-6.1-4.5-4.4 6.2-.9Z"/>','send':'<path d="m3 3 18 9-18 9 3-9zM6 12h15"/>','cloud':'<path d="M6 19a5 5 0 0 1-.6-10 7 7 0 0 1 13.2-1A5.5 5.5 0 0 1 18.5 19Z"/>','mail':'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>','server':'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M8 6h.01M8 12h.01M8 18h.01"/>','document':'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 12h8M8 16h8"/>','arrow':'<path d="M4 12h16m-6-6 6 6-6 6"/>'}
    values.update({key+'_icon':'<svg class="mail-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+paths+'</svg>' for key,paths in custom.items()})
    values['docs_indicator'] = '↗' if urlsplit(DATA['docs_url']).netloc=='github.com' else '→'
    values['shoreline'] = '<svg class="mail-shoreline" viewBox="0 0 1440 36" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path d="M0 22Q190-8 390 14T810 16T1200 12T1440 18V36H0Z"/></svg>'
    values['content'] = render((ROOT / 'src/index.html').read_text(), values)
    values.update(social_image=escape(canonical + 'assets/mail/social.jpg', quote=True), body_class='mail-page')
    robots = 'index,follow' if production else 'noindex,nofollow'
    values['head_extra'] = f'<meta name="robots" content="{robots}"><link rel="stylesheet" href="assets/mail.css">'
    html = render((THEME / 'templates/layout.html').read_text(), values)
    external_arrow = '<svg class="external-arrow" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 13 13 3M3 3h10v10"/></svg>'
    html = re.sub(r'(>[^<>]*)↗', lambda m: m.group(1) + external_arrow, html)
    (output / 'index.html').write_text(html)
    if production:
        (output / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>' + escape(canonical) + '</loc></url></urlset>\n')
    print(f'Built {"production" if production else "noindex preview"}: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'preview/volparossa-mail')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--origin', default='https://volparossa.org')
    parser.add_argument('--base-path', default='/volparossa-mail/')
    args = parser.parse_args()
    build(args.output, args.production, args.origin, args.base_path)
