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
    bends = LAYOUTS[layout].get('bend', {})
    def control_point(start, end):
        x1, y1 = positions[start]
        x2, y2 = positions[end]
        offsets = bends.get(f'{start}-{end}', bends.get(f'{end}-{start}', (0, -min(22, abs(x2-x1) * .10))))
        return (x1 + x2) / 2 + offsets[0], (y1 + y2) / 2 + offsets[1]

    def path(nodes):
        # Reuse exactly the same curved segment when a route follows an edge backwards.
        curves = []
        for start, end in zip(nodes, nodes[1:]):
            cx, cy = control_point(start, end)
            x, y = positions[end]
            curves.append(f"Q{cx:g},{cy:g} {x},{y}")
        x, y = positions[nodes[0]]
        return f"M{x},{y} " + " ".join(curves)
    parts=[f'<svg class="mail-mesh mail-mesh-{layout}" viewBox="{view}" role="img" aria-labelledby="{prefix}-title {prefix}-desc">',f'<title id="{prefix}-title">Sender and recipient connected through six equal peers</title>',f'<desc id="{prefix}-desc">An illustrative decentralised mesh of six peers and eleven connections. Two example routes share no intermediate peer. Every peer has alternatives; no single peer is required by all routes. This is not live connectivity or guaranteed delivery.</desc>']
    for a,b in DATA['edges']:
        d=path([a,b]);parts.extend([f'<path class="mesh-underlay" d="{d}"/>',f'<path class="mesh-edge" data-edge="{a}-{b}" d="{d}"/>'])
    for route in DATA['example_routes']:
        if route['display']=='dashed':parts.append(f'<path class="mesh-route-bed" d="{path(route["nodes"])}"/>')
        parts.append(f'<path class="mesh-route mesh-{route["display"]}" data-route="{route["id"]}" d="{path(route["nodes"])}"/>')
    # The envelope sits halfway along B–E. Its centre is a point on the
    # quadratic curve, independent of any junction in the graph.
    x1, y1 = positions['b']
    x2, y2 = positions['e']
    cx, cy = control_point('b', 'e')
    mx = (x1 + 2 * cx + x2) / 4
    my = (y1 + 2 * cy + y2) / 4
    parts.append(f'<g class="mesh-letter" aria-hidden="true" transform="translate({mx:g} {my:g})"><rect x="-13" y="-8" width="26" height="16" rx="1.5"/><path d="m-12-7 12 9 12-9"/><circle cy="1" r="1.6"/></g>')
    for node in DATA['nodes']:
        x,y=positions[node['id']];ident=node['id'];label=node['label']
        if node['role']=='peer':
            parts.append(f'<g data-peer="{ident}"><title>{label}</title><circle class="mesh-peer" cx="{x}" cy="{y}" r="4"/><path class="mesh-spark" d="M{x-8},{y}h16 M{x},{y-8}v16"/><text class="mesh-peer-label" x="{x}" y="{y+26}">{label}</text></g>')
        else:
            parts.append(f'<g data-endpoint="{ident}"><title>{label}</title><circle class="mesh-endpoint" cx="{x}" cy="{y}" r="6"/><circle class="mesh-endpoint-halo" cx="{x}" cy="{y}" r="10"/><text class="mesh-device-label" x="{x}" y="{y+28}">{label}</text></g>')
    parts.append('</svg>');return ''.join(parts)
