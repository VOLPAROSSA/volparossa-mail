# Design, content and provenance

The supplied Harbour of Letters concept guides the walnut writing room, teal
inhabited harbour, ivory paper, brass details and natural fox. The page has nine
sections in the supplied order. Text, upstream marks, interface controls and
network lines are separate HTML/SVG, never a flattened concept screenshot.

The shared header, footer, logo, buttons, Inter and Cormorant Garamond come from
the exact central 1.0.3 export. Applications is the active navigation family;
the breadcrumb identifies Mail. All visitor copy is English. Layout widths are
capped at 1320px. Soft shoreline transitions follow the scene/paper alternation.
Intrinsic wrapping supplements width breakpoints for enlarged text. On phones,
the full reading pane stacks below the folder/message lists; it is not a tiny
scaled laptop. Each hero/closing has a dedicated portrait composition.

## Illustrative networks

The supplied `network.json` is unchanged: two endpoints, six equal peers and
eleven undirected edges. The two example routes share no intermediate peer.
Removing any one peer still leaves a model path. These are illustration tests,
not runtime reliability claims. Both SVGs share the same dataset; roof-aligned
coordinates in `scene-layouts.json` differ to fit the independently generated
desktop and mobile scenes. Understrokes distinguish the dashed route from the
solid one without relying solely on colour. No crossing becomes an extra node.

The separate cooperative-intelligence illustration shows four workrooms and
public document symbols. It does not connect the private inbox directly to peers.
A distinct review panel represents a fictional public event notice and shows
explicit rights and licence review. The illustrations have accessible titles,
descriptions and visible development captions. No controls perform requests.

## Product facts checked on 17 November 2026

Source: `VOLPAROSSA/volparossa-mail` main at
`ba932378dc6878cdcbf062f41d9cb5681ee58b5c`. Read the current README,
`extensions/volparossa-mail-host/NOTICE.txt`,
`third_party/stalwart-extension-smoke.json`, `integration/mail-ai.html` and
`THIRD_PARTY_LICENSES.md`. The dated evidence receipt qualifies the older generic
connector notice; it does not establish the whole product.

- Existing Thunderbird accounts remain ordinary mail accounts. No arbitrary
  address migration or finished release is claimed.
- Peer delivery intends the actual Signal Protocol and whole-message encryption,
  including private headers. Discovery, native integration and end-to-end delivery
  remain development work. No HPKE shortcut is treated as Signal Protocol evidence.
- Stored, Imported and Read remain different states. A missing acknowledgement
  must not silently cause an ordinary-email resend.
- Optional Stalwart Community is a separately running, explicitly trusted service.
  The connector selects an existing writable mailbox, imports a chosen `.eml` and
  checks metadata. It does not send mail, install the service or establish private
  offline SMTP reception.
- The isolated 30 September 2026 Stalwart v0.16.24 receipt uses synthetic `.test`
  data and loopback. Actual adapter account/mailbox discovery, import and metadata
  readback are scoped evidence. Comparison of all 313 MIME bytes belongs to the
  separate fixture, not the extension feature. SMTP, native UI, service lifecycle
  and end-to-end peer delivery were not demonstrated.
- The current shared-brain adapter accepts only explicitly reviewed public text
  with sharing rights and a supported licence. No mail/account/draft/attachment
  collection, private public-endpoint inference, mailbox training or automatic
  cloud fallback is promised. A working Thunderbird-to-peer deployment is unproven.
- Ordinary SMTP hosting and ciphertext-only custody have different operator
  privacy boundaries. Those limits are visible in the page, not hidden in tooling.

## Assets and rights

`docs/brief-source-hashes.json` records every supplied file. The original concept
is preserved unchanged as `masters/design-reference.png` for source review only.
All generated scene originals, full generation prompts, source paths and later
corrections are retained in `masters`. Export dimensions and exact crop rectangles
are in `scripts/export_images.py` and `asset-manifest.json`; the latter includes
SHA-256 and byte dimensions for every master and public asset. No external image,
font, analytics, server or AI request occurs in a visitor's page.

The unmodified Thunderbird mark was retrieved from its official website and
links directly to that upstream product. Its exact URL, SHA-256, access date and
Mozilla trademark policy are in `assets/upstream/source.json`. A nearby notice
states independence and no official association with Thunderbird or Mozilla.
Stalwart is attributed by name; no Stalwart mark was generated or reproduced.
The synthetic Thunderbird-style interface uses fictional contacts and `.test`
addresses and is explicitly captioned as an illustration in development.

Lucide geometry is unmodified, pinned with original blob hashes and its ISC
licence. Simple envelope/server/document diagrams are original SVG. Shared font
licences and the theme licence are retained. Public credits link to the original
sources and notices. Only optimized assets and public credits enter deployment;
masters, prompts and review captures stay in Git.

## Publication authority

The user asked to build, deliver a reviewable PR, merge and publish. That explicit
request overrides only the package's no-merge/no-deploy stopping instruction.
All technical, content, privacy, asset and review requirements still apply. No
DNS, CNAME, root robots policy or product runtime is changed.
