"""Check illustration semantics only, never runtime or availability."""
import unittest
from collections import deque
from itertools import combinations
import xml.etree.ElementTree as ET
from mesh import DATA,LAYOUTS,render_mesh

class MailTopology(unittest.TestCase):
 def graph(self,removed=None):
  g={n['id']:set() for n in DATA['nodes'] if n['id']!=removed}
  for a,b in DATA['edges']:
   if removed not in (a,b):g[a].add(b);g[b].add(a)
  return g
 def reachable(self,g):
  seen={'sender'};q=deque(seen)
  while q:
   for n in g[q.popleft()]-seen:seen.add(n);q.append(n)
  return seen
 def test_exact_supplied_topology(self):
  expected={frozenset(e.split('-')) for e in ['sender-a','a-b','b-c','c-recipient','recipient-f','f-e','e-d','d-sender','a-d','b-e','c-f']}
  self.assertEqual({frozenset(e) for e in DATA['edges']},expected)
  self.assertEqual(len(DATA['edges']),11);self.assertEqual(len(DATA['nodes']),8)
  self.assertEqual({n['id'] for n in DATA['nodes'] if n['role']=='peer'},set('abcdef'))
 def test_connected(self):self.assertEqual(self.reachable(self.graph()),set(self.graph()))
 def test_independent_routes(self):
  routes=[r['nodes'] for r in DATA['example_routes']];self.assertEqual(len(routes),2)
  for r in routes:
   self.assertEqual((r[0],r[-1]),('sender','recipient'));self.assertEqual(len(r),len(set(r)))
   for a,b in zip(r,r[1:]):self.assertIn(b,self.graph()[a])
  self.assertFalse(set(routes[0][1:-1])&set(routes[1][1:-1]))
 def test_no_mandatory_peer(self):
  for n in 'abcdef':self.assertIn('recipient',self.reachable(self.graph(n)))
 def test_every_peer_has_alternatives(self):
  for n in 'abcdef':self.assertGreaterEqual(len(self.graph()[n]),3)
 def test_complete_accessible_renderings(self):
  for layout in ['desktop','mobile']:
   svg=ET.fromstring(render_mesh(layout));nodes=list(svg.iter())
   self.assertEqual({n.get('data-peer') for n in nodes if n.get('data-peer')},set('abcdef'))
   self.assertEqual({n.get('data-endpoint') for n in nodes if n.get('data-endpoint')},{'sender','recipient'})
   self.assertEqual({n.get('data-edge') for n in nodes if n.get('data-edge')},{'-'.join(e) for e in DATA['edges']})
   self.assertEqual(len([n for n in nodes if n.get('data-route')]),2)
   self.assertTrue(svg.find('title').text);self.assertTrue(svg.find('desc').text)
 def test_mobile_not_cropped_desktop(self):
  p=LAYOUTS['mobile']['positions']
  self.assertGreaterEqual(len({p[n][0] for n in 'abcdef'}),2)
  self.assertTrue(all(p['sender'][1]<p[n][1]<p['recipient'][1] for n in 'abcdef'))
 def test_no_false_junctions(self):
  def side(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
  for layout in ['desktop','mobile']:
   p=LAYOUTS[layout]['positions']
   for (a,b),(c,d) in combinations(DATA['edges'],2):
    if len({a,b,c,d})<4:continue
    u,v,w,z=[p[n] for n in (a,b,c,d)]
    with self.subTest(layout=layout,edges=(a,b,c,d)):self.assertFalse(side(u,v,w)*side(u,v,z)<0 and side(w,z,u)*side(w,z,v)<0)
if __name__=='__main__':unittest.main()
