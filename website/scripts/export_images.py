#!/usr/bin/env python3
"""Maintainer-only responsive exports; Pillow 12.3.0. Production builds are offline."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/mail'
COMPOSITIONS = {'hero-harbour': {'desktop': {'master': 'hero-harbour.png', 'crop': [0, 0, 1774, 887]},
                  'mobile': {'master': 'hero-harbour-mobile.png', 'crop': [0, 0, 1086, 1448]}},
 'delivery-harbour': {'desktop': {'master': 'delivery-harbour.png', 'crop': [0, 0, 2172, 724]},
                      'mobile': {'master': 'delivery-harbour-mobile.png',
                                 'crop': [0, 0, 1086, 1448]}},
 'custody-encrypt': {'desktop': {'master': 'custody-encrypt.png', 'crop': [0, 0, 1448, 1086]},
                     'mobile': {'master': 'custody-encrypt.png', 'crop': [0, 0, 1448, 1086]}},
 'custody-hold': {'desktop': {'master': 'custody-hold.png', 'crop': [0, 0, 1448, 1086]},
                  'mobile': {'master': 'custody-hold.png', 'crop': [0, 0, 1448, 1086]}},
 'custody-retrieve': {'desktop': {'master': 'custody-retrieve.png', 'crop': [0, 0, 1448, 1086]},
                      'mobile': {'master': 'custody-retrieve.png', 'crop': [0, 0, 1448, 1086]}},
 'own-mailbox': {'desktop': {'master': 'own-mailbox.png', 'crop': [0, 0, 1774, 887]},
                 'mobile': {'master': 'own-mailbox.png', 'crop': [0, 0, 900, 887]}},
 'shared-brain-harbour': {'desktop': {'master': 'shared-brain-harbour.png',
                                      'crop': [0, 0, 1536, 1024]},
                          'mobile': {'master': 'shared-brain-harbour.png',
                                     'crop': [0, 0, 1536, 1024]}},
 'closing-harbour': {'desktop': {'master': 'closing-harbour.png', 'crop': [0, 0, 2059, 764]},
                     'mobile': {'master': 'closing-harbour-mobile.png',
                                'crop': [0, 0, 1086, 1448]}}}


def inventory():
    manifest = {'compositions': COMPOSITIONS, 'files': {}}
    for directory in [ROOT/'masters', ROOT/'assets']:
        for p in sorted(directory.rglob('*')):
            if not p.is_file():
                continue
            record = {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            if p.suffix in ['.png','.jpg','.webp','.avif']:
                with Image.open(p) as im:
                    record['width'], record['height'] = im.size
            manifest['files'][p.relative_to(ROOT).as_posix()] = record
    (ROOT/'asset-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


def export():
    OUT.mkdir(parents=True,exist_ok=True)
    previous = json.loads((OUT/'variants.json').read_text()) if (OUT/'variants.json').exists() else {}
    old_stems = {item['stem'] for variants in previous.values() for items in variants.values() for item in items}
    variants = {}
    for name, plan in COMPOSITIONS.items():
        variants[name] = {}
        for kind, composition in plan.items():
            original = Image.open(ROOT/'masters'/composition['master']).convert('RGB')
            box = composition['crop']
            assert box[2] <= original.width and box[3] <= original.height
            source = original.crop(box)
            widths = sorted(set([min(w,source.width) for w in ([480,768] if kind=='mobile' else [640,960,1440,source.width])]))
            variants[name][kind] = []
            for width in widths:
                size = (width,round(width*source.height/source.width))
                im = source.resize(size,Image.Resampling.LANCZOS)
                stem = f'{name}-{kind}-{width}'
                im.save(OUT/(stem+'.avif'),quality=57,speed=6)
                im.save(OUT/(stem+'.webp'),quality=83,method=6)
                im.save(OUT/(stem+'.jpg'),quality=84,optimize=True,progressive=True)
                variants[name][kind].append({'stem':stem,'width':size[0],'height':size[1]})
    (OUT/'variants.json').write_text(json.dumps(variants,indent=2)+'\n')
    new_stems = {item['stem'] for sizes in variants.values() for items in sizes.values() for item in items}
    for stem in old_stems - new_stems:
        for extension in ['avif','webp','jpg']:
            (OUT/(stem+'.'+extension)).unlink(missing_ok=True)
    inventory()
    print('Exported actual crops and dimensions; original masters preserved.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only',action='store_true')
    args=parser.parse_args()
    inventory() if args.inventory_only else export()
