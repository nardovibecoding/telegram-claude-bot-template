// Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
/**
 * Cloudflare Worker: YouTube Proxy
 * Proxies requests to YouTube so VPS (blocked IP) can fetch video pages.
 * The actual transcript parsing happens on VPS in Python.
 *
 * Usage: GET /?url=https://www.youtube.com/watch?v=VIDEO_ID
 * Returns: Raw HTML/JSON from YouTube
 */

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const targetUrl = url.searchParams.get('url');

    if (!targetUrl || !targetUrl.includes('youtube.com')) {
      return new Response(JSON.stringify({ error: 'Missing or invalid ?url= parameter' }), {
        status: 400, headers: { 'Content-Type': 'application/json' }
      });
    }

    try {
      const resp = await fetch(targetUrl, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Accept-Language': 'en-US,en;q=0.9',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        redirect: 'follow',
      });

      const body = await resp.text();
      return new Response(body, {
        status: resp.status,
        headers: {
          'Content-Type': resp.headers.get('Content-Type') || 'text/html',
          'Cache-Control': 'public, max-age=3600',
          'Access-Control-Allow-Origin': '*',
        }
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500, headers: { 'Content-Type': 'application/json' }
      });
    }
  }
};
