# Browser review — 17 November 2026

These are actual Playwright browser captures of the built page at its real
`/volparossa-mail/` prefix, not design mockups. WebP encoding retains the capture
width; no page elements were edited. Full-page source PNGs remain in ignored QA
output. Every below-fold image was decoded before capture.

- [chromium-1440-full](chromium-1440-full.webp)
- [chromium-390-full](chromium-390-full.webp)
- [firefox-2560-full](firefox-2560-full.webp)
- [chromium-320-full](chromium-320-full.webp)
- [chromium-768-full](chromium-768-full.webp)
- [chromium-1920-full](chromium-1920-full.webp)
- [text-200-percent](text-200-percent.webp)
- [zoom-400-percent](zoom-400-percent.webp)
- [no-js](no-js.webp)
- [chromium-1440-hero](chromium-1440-hero.webp)
- [chromium-1440-foundation](chromium-1440-foundation.webp)
- [chromium-1440-delivery](chromium-1440-delivery.webp)
- [chromium-1440-offline-custody](chromium-1440-offline-custody.webp)
- [chromium-1440-own-mailbox](chromium-1440-own-mailbox.webp)
- [chromium-1440-shared-brain](chromium-1440-shared-brain.webp)
- [chromium-1440-contribute](chromium-1440-contribute.webp)
- [chromium-390-foundation](chromium-390-foundation.webp)
- [chromium-390-delivery](chromium-390-delivery.webp)
- [chromium-390-shared-brain](chromium-390-shared-brain.webp)

`results.json` records all 13 browser/condition profiles and measured resource
transfers. `accessibility.json` records zero axe violations in the mobile and
desktop views, including expanded explanations. `mobile-dpr2.json` adds a touch
viewport with a 2× device scale. The 400% reflow capture uses the equivalent
320 CSS-pixel width of a 1280px desktop viewport at 400% zoom; it is not a claim
of an automated browser-toolbar zoom action.

WebKit: NOT RUN. The browser downloaded, but this environment lacks libgtk-4,
libgraphene, libharfbuzz-icu, libmanette, libhyphen and libGLESv2. Chromium and
Firefox actually ran. These captures are excluded from the deployed artifact.
