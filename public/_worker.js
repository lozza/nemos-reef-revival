// Set an explicit ISO 8601 date with an offset, e.g. 2026-11-07T18:00:00+00:00.
// null keeps the site in pre-launch mode without inventing a date.
export const LAUNCH_AT = '2026-12-05T10:00:00+00:00';
// Verifiers for a generated 192-bit password and its derived session token.
// Neither the password nor the bearer session token is published in this source.
const PASSWORD_HASH = 'd8299510d36d173f3f56399d236ae439153bc5aa8fdea6668c1e93bdfa9e7395';
const SESSION_HASH = '45623998429da125cf0992b3940e160e120106e306908fbbbb5309e3a2db2472';
const COOKIE = '__Host-reef_preview';
const allowedAssets = new Set(['/styles.css', '/launch.css', '/launch.js', '/logo.png', '/discord-mascot.png', '/reef-hero-hd.jpg', '/reef-world.jpg', '/fonts/fredoka.woff2', '/fonts/lilita-one.ttf', '/fonts/bubblegum-sans.ttf', '/fonts/google-sans.woff2']);
const securityHeaders = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'same-origin',
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; font-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
};
async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))), b => b.toString(16).padStart(2, '0')).join('');
}
function equal(a, b) {
  let difference = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) difference |= (a.charCodeAt(i) || 0) ^ b.charCodeAt(i);
  return difference === 0;
}
function response(body, status = 200, extra = {}) {
  return new Response(body, { status, headers: { ...securityHeaders, 'Content-Type': 'text/html; charset=utf-8', ...extra } });
}
function redirect(location, cookie) {
  return response(null, 303, { Location: location, ...(cookie ? { 'Set-Cookie': cookie } : {}) });
}
function loginPage(error = false) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Owner preview — Nemo's Reef Revival</title><link rel="icon" href="/discord-mascot.png"><link rel="stylesheet" href="/styles.css?v=mixed-sans20261025"><link rel="stylesheet" href="/launch.css"></head><body class="launch-page"><main class="launch-main wrap"><section class="launch-card owner-card"><h1>Owner preview</h1><p>Sign in to view the main website before launch.</p>${error ? '<p class="owner-error" role="alert">That password was not recognised. Please try again.</p>' : ''}<form class="owner-form" method="post" action="/owner"><label for="password">Preview password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="128"><button class="button button-green" type="submit">View website</button></form><a class="owner-back" href="/">Back to the countdown</a></section></main></body></html>`;
}
async function asset(request, env, path, preview = false) {
  const url = new URL(request.url);
  url.pathname = path;
  const result = await env.ASSETS.fetch(new Request(url, { method: 'GET' }));
  const headers = new Headers(result.headers);
  for (const [name, value] of Object.entries(securityHeaders)) headers.set(name, value);
  if (preview) headers.set('X-Robots-Tag', 'noindex, nofollow');
  let body = result.body;
  if (preview && result.ok) {
    const html = await result.text();
    body = html.replace('</head>', '<link rel="stylesheet" href="/launch.css"></head>').replace('<body>', '<body><aside class="preview-banner"><span>Owner preview · Visitors see the launch page</span><a href="/coming-soon">View countdown</a><form action="/owner/logout" method="post"><button type="submit">Log out</button></form></aside>');
    headers.delete('Content-Length');
    headers.delete('ETag');
  }
  return new Response(request.method === 'HEAD' ? null : body, { status: result.status, headers });
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const configuredDate = env.LAUNCH_AT || LAUNCH_AT;
    const target = configuredDate ? Date.parse(configuredDate) : NaN;
    const launched = Number.isFinite(target) && Date.now() >= target;
    const launchAt = Number.isFinite(target) ? new Date(target).toISOString() : null;
    const token = (request.headers.get('Cookie') || '').split(';').map(p => p.trim()).find(p => p.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1) || '';
    const authenticated = token.length === 64 && equal(await hash(token), SESSION_HASH);
    if (request.method === 'POST') {
      if (request.headers.get('Origin') !== url.origin) return response('Forbidden', 403);
      if (path === '/owner/logout') return redirect('/', `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`);
      if (path !== '/owner') return response('Method not allowed', 405, { Allow: 'GET, HEAD' });
      if (!request.headers.get('Content-Type')?.startsWith('application/x-www-form-urlencoded')) return response('Unsupported form type', 415);
      const raw = await request.text();
      if (raw.length > 1024) return response('Form too large', 413);
      const password = new URLSearchParams(raw).get('password') || '';
      if (password.length > 128 || !equal(await hash(password), PASSWORD_HASH)) return response(loginPage(true), 401);
      const session = await hash('nrr-preview-session:' + password);
      return redirect('/', `${COOKIE}=${session}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=28800`);
    }
    if (!['GET', 'HEAD'].includes(request.method)) return response('Method not allowed', 405, { Allow: 'GET, HEAD' });
    if (path === '/launch-status') return response(request.method === 'HEAD' ? null : JSON.stringify({ launchAt, serverNow: new Date().toISOString(), launched }), 200, { 'Content-Type': 'application/json; charset=utf-8' });
    if (path === '/owner' || path === '/owner/') return authenticated ? redirect('/') : response(request.method === 'HEAD' ? null : loginPage(), 200, { 'X-Robots-Tag': 'noindex, nofollow' });
    if (path.startsWith('/_') || path.includes('..')) return response('Not found', 404);
    if (allowedAssets.has(path)) return env.ASSETS.fetch(request);
    if (path === '/coming-soon' || path === '/launch.html' || path === '/launch') return asset(request, env, '/launch.html');
    if (launched) return asset(request, env, path === '/' ? '/index.html' : path);
    if (authenticated) return asset(request, env, path === '/' ? '/index.html' : path, ['/', '/index', '/index.html'].includes(path));
    // Serve the holding page for HTML requests, including direct /index.html access.
    if (path === '/' || path === '/index' || path === '/index.html') return asset(request, env, '/launch.html');
    return response('Not found', 404);
  }
};
