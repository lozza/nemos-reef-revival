import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const root = new URL('../public/', import.meta.url);
const source = await readFile(new URL('_worker.js', root), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const types = { png: 'image/png', txt: 'text/plain', xml: 'application/xml', html: 'text/html' };
const env = { LAUNCH_AT: '2035-12-05T10:00:00Z', ASSETS: { async fetch(request) {
  const path = new URL(request.url).pathname.slice(1);
  try {
    const bytes = await readFile(new URL(path, root));
    return new Response(request.method === 'HEAD' ? null : bytes, { headers: { 'Content-Type': types[path.split('.').pop()] || 'application/octet-stream' } });
  } catch { return new Response('Not found', { status: 404 }); }
} } };
const url = 'https://nemos-reef-revival.pages.dev';
const fetchSite = (path, options = {}, bindings = env) => worker.fetch(new Request(url + path, options), bindings);

function verifyIdentity(html) {
  assert.equal((html.match(/rel="canonical"/g) || []).length, 1);
  assert.match(html, /rel="canonical" href="https:\/\/nemos-reef-revival\.pages\.dev\/"/);
  assert.match(html, /itemtype="https:\/\/schema\.org\/WebSite"/);
  assert.match(html, /itemprop="name" content="Nemo's Reef Revival"/);
  assert.match(html, /property="og:site_name" content="Nemo's Reef Revival"/);
  assert.match(html, /sizes="96x96" href="\/favicon-96\.png"/);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)/);
}

test('Googlebot can crawl the public countdown with site identity and launch metadata', async () => {
  const result = await fetchSite('/', { headers: { 'User-Agent': 'Googlebot' } });
  assert.equal(result.status, 200);
  assert.equal(result.headers.get('X-Robots-Tag'), null);
  const html = await result.text();
  verifyIdentity(html);
  assert.match(html, /<title>Nemo's Reef Revival — Launching 5 December<\/title>/);
  assert.match(html, /name="description" content="[^\"]*Windows and Linux[^\"]*5 December 2026/);
  assert.match(html, /class="launch-note">[^<]*Windows and Linux[^<]*5 December 2026/);
});
test('site identity is retained after launch and on duplicate homepage routes', async () => {
  const launched = { ...env, LAUNCH_AT: '2020-01-01T00:00:00Z' };
  for (const path of ['/', '/index.html']) {
    const result = await fetchSite(path, {}, launched);
    assert.equal(result.status, 200);
    verifyIdentity(await result.text());
  }
});
test('favicon, touch icon, robots and sitemap are public before launch', async () => {
  for (const [path, mime] of [['/favicon-96.png', 'image/png'], ['/apple-touch-icon.png', 'image/png'], ['/robots.txt', 'text/plain'], ['/sitemap.xml', 'application/xml']]) {
    const result = await fetchSite(path, { headers: { 'User-Agent': 'Googlebot-Image' } });
    assert.equal(result.status, 200, path);
    assert.equal(result.headers.get('Content-Type'), mime, path);
  }
  const bytes = Buffer.from(await (await fetchSite('/favicon-96.png')).arrayBuffer());
  assert.equal(bytes.subarray(1, 4).toString(), 'PNG');
  assert.equal(bytes.readUInt32BE(16), 96);
  assert.equal(bytes.readUInt32BE(20), 96);
});
test('crawl files list only the canonical public page and keep owner access private', async () => {
  const robots = await (await fetchSite('/robots.txt')).text();
  assert.match(robots, /Disallow: \/owner/);
  assert.match(robots, /Sitemap: https:\/\/nemos-reef-revival\.pages\.dev\/sitemap\.xml/);
  const sitemap = await (await fetchSite('/sitemap.xml')).text();
  assert.deepEqual([...sitemap.matchAll(/<loc>(.*?)<\/loc>/g)].map(m => m[1]), [url + '/']);
  assert.match((await fetchSite('/owner')).headers.get('X-Robots-Tag'), /noindex/);
  assert.doesNotMatch(await (await fetchSite('/index.html')).text(), /id="setup"/);
});
