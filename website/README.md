# VOLPAROSSA Mail — The Harbour of Letters

The nine-section English product page uses the central VOLPAROSSA theme and
separate harbour scenes. This is a development-integration website, not a mail
client, a crypto demonstration or a running peer network.

## Build and review

Python 3 is the only production build dependency. No network request, image
generation, npm dependency or product runtime is needed to build.

```sh
python3 website/scripts/build_site.py
python3 website/scripts/check_site.py
python3 website/scripts/test_mesh.py
python3 -m http.server 8781 --bind 127.0.0.1 --directory website/preview
```

Open <http://127.0.0.1:8781/volparossa-mail/>. This default preview is
`noindex,nofollow` and has no sitemap. Native navigation and explanations work
without JavaScript. Only the small shared theme menu enhancement is shipped.

```sh
python3 website/scripts/build_site.py --production --output website/dist/volparossa-mail
python3 website/scripts/check_site.py --production --output website/dist/volparossa-mail
```

Both scripts accept `--origin` and `--base-path`. Relative assets work under
independent project prefixes. The builder refuses to erase an output directory
without its own marker. It excludes masters, source, tooling and review images
from the published artifact and never writes a CNAME or root robots policy.

## Maintain

- `src/index.html`, `src/mail.css`: semantic copy and page composition.
- `src/content.json`: metadata and the one product documentation URL.
- `src/network.json`: byte-identical topology supplied in the brief.
- `src/scene-layouts.json`: coordinates aligned to each independently composed scene.
- `scripts/mesh.py`: accessible static SVG generated from that one topology.
- `assets/mail`, `assets/upstream`: committed local images and attribution.
- `masters`, `asset-manifest.json`: originals, prompts, crop plans and SHA-256 provenance.
- `vendor/volparossa-theme`: immutable theme 1.0.3, pinned to central commit
  `f498f26a06bec792e809e77704b4802bf5f54fbb` and checked during every build.
- `docs`, `review`: content decisions, verification and real browser captures.

The image exporter requires Pillow 12.3.0 only when changing imagery:
`python3 website/scripts/export_images.py`. Original images are preserved.
`--inventory-only` refreshes the asset inventory after a deliberate asset edit.
The optional social renderer uses real HTML typography over the hero scene;
run it against the local preview and then refresh the inventory and rebuild.

## Optional browser QA

```sh
npm ci --ignore-scripts --prefix website
CHROME_PATH=/usr/bin/chromium node website/scripts/browser_check.mjs
```

Install Playwright Firefox separately if not already present. The checker runs
Chromium and Firefox and records output under ignored `website/qa/browser`.
Use `QA_ENGINES`, `QA_OUTPUT` and `SITE_QA_URL` to change engines, destination or
preview URL. Screenshots cover every section, desktop/mobile, enlarged text,
reflow, reduced motion and no JavaScript; axe checks the normal and expanded page.
This tooling is not deployed. The existing Python and JMAP contracts remain intact.

## Publication

The `Mail website` workflow validates previews on PRs and main. Production
deployment requires an explicit manual run on main and the pre-existing GitHub
Pages Actions configuration. Only that deploy job receives Pages/OIDC rights.

The user explicitly authorized merge and publication, overriding the attached
brief's review-only stopping point. Central documentation is handled separately
in [central PR #18](https://github.com/VOLPAROSSA/volparossa.github.io/pull/18).
Its live `#mail` fragment was verified before using it here. The central Mail
application entry must remain unpublished until this project's production URL,
assets, metadata and mobile/desktop views are verified; activation is a separate
central change. Mail is not added to the homepage's featured applications.

See [design and sources](docs/DESIGN-AND-SOURCES.md),
[verification](docs/VERIFICATION.md) and [review images](review/README.md).
