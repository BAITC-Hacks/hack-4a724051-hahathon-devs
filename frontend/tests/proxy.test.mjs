import assert from 'node:assert/strict';
import { test } from 'node:test';
import { GET, POST } from '../src/app/api/v1/[...path]/route.ts';

function request(path, method = 'GET', body, headers = {}) {
  const req = new Request(`http://localhost:3000/api/v1/${path}`, {
    method, body, headers: { origin: 'http://localhost:3000', ...headers },
  });
  req.nextUrl = new URL(req.url);
  return req;
}
const context = (...path) => ({ params: Promise.resolve({ path }) });

test('proxy blocks foreign origins and encoded path separators before upstream', async () => {
  const response = await POST(request('session', 'POST', undefined, { origin: 'https://other.example' }), context('session'));
  assert.equal(response.status, 403);
  const bad = await GET(request('products'), context('products/../session'));
  assert.equal(bad.status, 400);
});

test('ordinary JSON requests cannot use the upload byte allowance', async () => {
  const response = await POST(request('conversations', 'POST', 'x'.repeat(32769)), context('conversations'));
  assert.equal(response.status, 413);
  const tooLarge = await POST(request('assets/upload', 'POST', undefined, { 'content-length': '10485761' }), context('assets', 'upload'));
  assert.equal(tooLarge.status, 413);
});

test('127.0.0.1 origin remains valid when Next normalizes its internal URL to localhost', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  globalThis.fetch = async () => new Response('{"data":{}}');
  const req = request('session', 'POST', undefined, { origin: 'http://127.0.0.1:3000', host: '127.0.0.1:3000' });
  assert.equal((await POST(req, context('session'))).status, 200);
});

test('upload forwards binary bytes and auth, keeps separate cookies and safety headers', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  let forwarded;
  globalThis.fetch = async (url, options) => {
    forwarded = { url, options };
    const headers = new Headers({ 'content-type': 'application/json', 'x-content-type-options': 'nosniff' });
    headers.append('set-cookie', 'one=1; HttpOnly');
    headers.append('set-cookie', 'two=2; HttpOnly');
    return new Response('{"data":{"id":"asset"}}', { status: 201, headers });
  };
  const bytes = new Uint8Array(65536).fill(173);
  const response = await POST(request('assets/upload?filename=a.pdf', 'POST', bytes, {
    'content-type': 'application/pdf', 'x-csrf-token': 'csrf', cookie: 'ekt_session=opaque',
  }), context('assets', 'upload'));
  assert.equal(response.status, 201);
  assert.deepEqual(new Uint8Array(forwarded.options.body), bytes);
  assert.equal(forwarded.url.pathname, '/api/v1/assets/upload');
  assert.equal(forwarded.url.searchParams.get('filename'), 'a.pdf');
  assert.equal(forwarded.options.headers.get('origin'), 'http://localhost:3000');
  assert.equal(forwarded.options.headers.get('x-csrf-token'), 'csrf');
  assert.equal(forwarded.options.headers.get('cookie'), 'ekt_session=opaque');
  assert.equal(forwarded.options.redirect, 'manual');
  assert.equal(response.headers.getSetCookie().length, 2);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('truncated body is rejected, private certificate headers survive', async t => {
  const short = await POST(request('assets/upload', 'POST', 'abc', { 'content-length': '9' }), context('assets', 'upload'));
  assert.equal(short.status, 400);
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  globalThis.fetch = async () => new Response('%PDF', { headers: {
    'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="SYN-1.pdf"',
    'content-security-policy': "default-src 'none'",
  } });
  const response = await GET(request('certificates/SYN-1.pdf'), context('certificates', 'SYN-1.pdf'));
  assert.equal(response.headers.get('content-disposition'), 'attachment; filename="SYN-1.pdf"');
  assert.equal(response.headers.get('content-security-policy'), "default-src 'none'");
  assert.equal(await response.text(), '%PDF');
});
