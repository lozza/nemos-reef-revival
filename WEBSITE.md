# Project website

[All guides](README.md) · [Open the website](https://nemos-reef-revival.pages.dev/)

The website is deployed from the `main` branch through Cloudflare Pages.
Before launch, visitors see a holding page; the owner can preview the main site
using a server-checked password at `/owner`.

## Edit the website

- `public/index.html`: homepage content, guide links and community invite.
- `public/styles.css`: layout, typography, responsive rules and wave buttons.
- `public/reef-hero-hd.jpg`: the 2048 × 768 shared desktop backdrop, exported at high JPEG quality.
- `public/reef-world.jpg`: the tall shared mobile backdrop, also used inside the community panel.
- `public/discord-mascot.png`: the existing Discord artwork and browser-tab icon.
- `public/logo.png`: the approved white and orange project wordmark used in the header and footer.
- `public/server-illustration.svg`, `public/pc-illustration.svg` and
  `public/phone-illustration.svg`: setup illustrations.
- `public/fonts/fredoka.woff2`: locally hosted display font. Its SIL Open
  Font License is included in `public/fonts/OFL.txt`.

The design follows the approved mockup based on the original game screenshots:
blue popup frames, parchment panels, cyan setup cards and glossy green buttons.
The reef artwork was generated separately; text, controls and responsive layouts
are real HTML and CSS. The community panel uses the existing Discord mascot.
One fixed background sits behind the entire page at each screen size, avoiding
joins or repeated artwork at section boundaries. The hero adds no separate image.

Only the `public` folder is published as the website. The homepage links to
the setup guides on GitHub; edit those Markdown files to update the guides.

Wave buttons also respond to keyboard focus. Waves and hover movement are
disabled when reduced motion is requested.

## Cloudflare Pages build settings

For the Pages project named `nemos-reef-revival`, use:

| Setting | Value |
|---|---|
| Production branch | `main` |
| Framework preset | None |
| Root directory | Repository root |
| Build command | Leave empty; no build step is needed |
| Build output directory | `public` |

Commits to `main` trigger the connected deployment. Check the Cloudflare
Pages build result on the commit before sharing a new version.

The existing `wrangler.jsonc` also protects the earlier Workers address.
It invokes the same Worker before serving assets. Pages uses advanced mode
via `public/_worker.js`, so the dashboard settings above stay the same.

## Launch countdown and owner preview

The launch is set for **5 December 2026 at 10:00 UK time (10:00 UTC)**.

- `public/launch.html`, `launch.css`, `launch.js`: public holding page and countdown.
- `public/_worker.js`: server-side gate, password form, login/logout and launch time.
- `public/index.html`: the existing main website, served after login or launch.
- `public/_routes.json`: routes every request through the gate on Pages.
- `public/.assetsignore`: keeps Worker source out of Workers static assets.

Set `LAUNCH_AT` at the top of `public/_worker.js` to an ISO 8601 date including
its offset, or set the Cloudflare runtime variable `LAUNCH_AT`. For example,
`2026-11-07T18:00:00+00:00` is 18:00 UK time on that date. That is an example,
not an agreed launch date. `null` means no launch date has been announced.
The countdown uses the server clock and displays the launch in UK time.
At the launch instant, the main site becomes public automatically. Visitors
who already have the countdown open are redirected to the main website.

Open `/owner` and use the generated owner password. Successful login stores
a derived bearer token in a Secure, HttpOnly, SameSite=Strict cookie for eight
hours. `/coming-soon` lets the owner inspect the countdown while signed in;
the preview banner also includes logout. Responses containing the main page
are not cached, so a preview cannot leak through a shared cache. Direct
`/index.html` access is gated too.

Only SHA-256 verifiers are committed. The password is randomly generated with
192 bits of entropy; do not replace it with a short or memorable password.
To rotate it, generate a new password with `secrets.token_urlsafe(24)` in
Python. `PASSWORD_HASH` is SHA-256(password). `SESSION_HASH` is
SHA-256(SHA-256("nrr-preview-session:" + password).hexdigest()). Update both
constants together; this invalidates old logins. Keep the password out of
GitHub commits and issues. No paid service or separate account is required.

This gates the website, not the already-public GitHub source and guides.

## Local verification

Run `node tests/website-launch.mjs` from the repository root. It covers
anonymous requests, direct main-page access, valid/invalid logins, cookie
handling, logout, launch boundaries, cross-origin form rejection and HEAD.
Use `npx wrangler pages dev public` for a full local Cloudflare preview.
A static Python server skips authentication and must not be used to test it.

## Discord invite

The website uses [this community invite](https://discord.gg/mseaKJb5G).
It never expires, has no use limit and sends new members to the welcome channel.

To replace it, update the four Discord links in `public/index.html`.
Existing invites can be managed in Discord under **Server Settings → Invites**.


## Google Search appearance

The public countdown and main page both declare the site name as **Nemo's
Reef Revival** using WebSite microdata and `og:site_name`. Microdata works
with the existing strict Content Security Policy without inline scripts.
Both use the canonical homepage URL on pages.dev, including when served by
the legacy Workers address or an alternate HTML route.

The countdown title announces the 5 December launch and the description
explains the Windows/Linux fan revival. The visible note supports the same
facts. The main page has its own evergreen title and description.

`public/favicon-96.png` is a 96x96 PNG export of the existing Discord mascot;
`public/apple-touch-icon.png` is the 180x180 export. Keep the favicon URL
stable so crawlers can associate it with the site. These and `robots.txt`
and `sitemap.xml` are public assets allowed through the pre-launch gate.
The sitemap lists only the canonical public homepage; owner preview remains
password-protected and noindexed.

Run `node --test tests/website-*.mjs` after changing metadata or crawl routes.
After publishing, inspect the homepage in Google Search Console and request
indexing from a verified property. Google chooses the final site name,
title, description and favicon; updates can take days or weeks to appear.
