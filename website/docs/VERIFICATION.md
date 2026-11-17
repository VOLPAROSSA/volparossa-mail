# Verification — 17 November 2026

The completed page was compared with the supplied original concept in real
Chromium and Firefox renders. All nine sections were inspected on mobile,
tablet, desktop and a wide Firefox desktop. Sources and review images are linked
in the website README. Final browser capture time: 2026-11-17T17:10:03.451Z.

## Results

| Check | Result | Evidence / scope |
|---|---|---|
| Offline preview build | PASS | Real `/volparossa-mail/` prefix, noindex/nofollow, no sitemap |
| Explicit production build | PASS | Own canonical, Open Graph image and sitemap; index/follow |
| Independent nested prefix | PASS | `https://preview.example/nested/mail/`, all local assets/fragments |
| Determinism | PASS | Repeated production build byte-identical across 200 files |
| Asset/theme provenance | PASS | All recorded SHA-256 hashes and unmodified central theme checked |
| Network topology | PASS | Eight tests: 2 endpoints, 6 peers, 11 edges, independent routes, deletion of any peer, no false crossings, both accessible layouts |
| Existing Python source contracts | PASS | 25 tests; no product runtime launched |
| Existing JMAP extension contracts | PASS | 12 tests; no mail sent and no server/model installed |
| Chromium | PASS | 320, 390, 768, 1440, 1920; also 844 × 390 landscape |
| Firefox | PASS | 390, 1440, 2560; full-page and section renders |
| Enlarged text | PASS | 200% root text size; upstream branding and route labels no longer overlap |
| 400% reflow | PASS | Equivalent 320 × 225 CSS viewport for a 1280 × 900 desktop at 400%; no horizontal overflow |
| Keyboard/menu | PASS | Skip-link first and visible, main focus, native disclosures, menu Escape/outside click |
| No JavaScript / reduced motion | PASS | Native menu and explanations work; no smooth scrolling under reduced motion |
| Images disabled | PASS | Content and topology remain understandable without decorative scenery |
| Axe WCAG 2.2 AA | PASS | Zero violations in 390/1440 views, normal and expanded; manual scene/focus/contrast review added |
| Requests/console | PASS | No unexpected external requests, console errors, failed assets or missing images |
| Central documentation | PASS | Central PR #18 merged, live `https://volparossa.org/docs/#mail` verified before configuring DOCS_URL |
| WebKit | NOT RUN | Browser installation found six missing platform libraries; not reported as tested |
| Product runtime / actual peer crypto | NOT RUN | Outside this website task; illustrations are not runtime evidence |

## Actual local transfer measurements

Bytes include browser resource transfer overhead. These uncompressed local HTTP
measurements are conservative relative to a compressed production server. The
largest mobile hero AVIF is 81,294 bytes; largest desktop hero AVIF is 141,057 bytes.
HTML/CSS/JavaScript gzip is approximately 25 KB. The full artifact contains
alternative formats and sizes; those are not all downloaded by a visitor.

| Engine | CSS viewport | Initial bytes | Full-page bytes | Image bytes |
|---|---:|---:|---:|---:|
| chromium | 1440 × 900 | 465,594 | 814,694 | 651,630 |
| chromium | 390 × 844 | 304,872 | 462,474 | 299,410 |
| chromium | 320 × 740 | 262,399 | 462,474 | 299,410 |
| chromium | 768 × 1024 | 407,405 | 759,681 | 596,617 |
| chromium | 1920 × 1080 | 711,871 | 941,460 | 778,396 |
| chromium | 844 × 390 | 309,339 | 759,681 | 596,617 |
| firefox | 390 × 844 | 217,100 | 462,474 | 299,410 |
| firefox | 1440 × 900 | 330,719 | 814,694 | 651,630 |
| firefox | 2560 × 1440 | 536,907 | 1,157,135 | 994,071 |

The extra Chromium touch profile at 390 × 844, device scale 2, transferred
309,158 initial bytes and 759,500 full-page bytes. This is below
the brief's 1.2 MB initial and 3 MB full mobile targets. Browser resource lists
and selected variants are recorded in the review JSON.

## Visual corrections made after rendering

- Dedicated portrait hero, complete vertical mesh and separate resting-fox scene
  keep the story visible on phones rather than cropping a desktop image.
- All six delivery points align with posthouse roofs. Solid and dashed routes
  remain distinguishable on both layouts. No artwork contains baked-in network lines.
- The three-pane interface is large on desktop; mobile folds into a readable
  message view with an explicit illustrative caption.
- A real enlarged-text render exposed upstream wordmark and ordinary-mail label
  collisions. Intrinsic wrapping fixed them; the browser check now catches them.
- The own-mailbox cabinet was corrected into recognisable equipment. Its text
  zone was moved beyond the cabinet after an actual render showed low contrast.
- The selected public-text panel stays separate from the private inbox and has
  explicit rights/licence labels. Four workrooms align with their SVG connections.
- One low-contrast search label was darkened; all normal/expanded axe checks pass.

## Publication sequence and authority

The user's explicit request authorizes merge and publication, overriding the
attached package's review-only stopping point. All other technical requirements
were retained. The documentation entrance was merged separately in central
[PR #18](https://github.com/VOLPAROSSA/volparossa.github.io/pull/18). Mail stays
`published=false` in that change. This product PR is merged only after CI passes;
its manual Pages deployment is followed by live metadata/assets/mobile/desktop
verification. Only then does a separate central registry PR activate Mail.
No featured application, DNS, CNAME, root robots policy or product runtime changes.

Live verification is appended to the website pull request after deployment.

The pinned upstream theme contains original CRLF/trailing whitespace in its
Immich SVG and licence. Those files are deliberately unchanged to preserve
provenance; whitespace checks on new page-owned source pass.
