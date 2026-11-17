"""Two static SVG layouts of the supplied illustrative mail graph, never live data."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'src/network.json').read_text())
LAYOUTS=json.loads((ROOT/'src/scene-layouts.json').read_text())

def render_mesh(layout):
    view=' '.join(map(str,LAYOUTS[layout]['viewBox']))
    positions=LAYOUTS[layout]['positions']
    prefix='mail-mesh-'+layout
    def path(nodes):return 'M'+' L'.join(','.join(map(str,positions[n])) for n in nodes)
    parts=[f'<svg class="mail-mesh mail-mesh-{layout}" viewBox="{view}" role="img" aria-labelledby="{prefix}-title {prefix}-desc">',f'<title id="{prefix}-title">Sender and recipient connected through six equal peers</title>',f'<desc id="{prefix}-desc">An illustrative decentralised mesh of six peers and eleven connections. Two example routes share no intermediate peer. Every peer has alternatives; no single peer is required by all routes. This is not live connectivity or guaranteed delivery.</desc>']
    for a,b in DATA['edges']:
        d=path([a,b]);parts.extend([f'<path class="mesh-underlay" d="{d}"/>',f'<path class="mesh-edge" data-edge="{a}-{b}" d="{d}"/>'])
    for route in DATA['example_routes']:
        if route['display']=='dashed':parts.append(f'<path class="mesh-route-bed" d="{path(route["nodes"])}"/>')
        parts.append(f'<path class="mesh-route mesh-{route["display"]}" data-route="{route["id"]}" d="{path(route["nodes"])}"/>')
    for node in DATA['nodes']:
        x,y=positions[node['id']];ident=node['id'];label=node['label']
        if node['role']=='peer':
            parts.append(f'<g data-peer="{ident}"><title>{label}</title><circle class="mesh-peer" cx="{x}" cy="{y}" r="4"/><path class="mesh-spark" d="M{x-8},{y}h16 M{x},{y-8}v16"/><text class="mesh-peer-label" x="{x}" y="{y+26}">{label}</text></g>')
        else:
            parts.append(f'<g data-endpoint="{ident}"><title>{label}</title><rect class="mesh-endpoint" x="{x-16}" y="{y-12}" width="32" height="23" rx="2"/><path class="mesh-envelope" d="M{x-16},{y-11}l16,12 16,-12"/><text class="mesh-device-label" x="{x}" y="{y+36}">{label}</text></g>')
    parts.append('</svg>');return ''.join(parts)
