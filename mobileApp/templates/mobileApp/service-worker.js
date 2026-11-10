/* Only public UI files are cached here. Account data lives in the offline vault. */
const VERSION = '{{ asset_version }}';
const CACHE = `reloncavi-shell-${VERSION}`;
const HOME = '/';
self.addEventListener('install', event => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        const response = await fetch(HOME, {cache: 'reload'});
        if (!response.ok) throw new Error('No se pudo preparar la app sin conexión.');
        const html = await response.clone().text();
        // Do not activate with a half-downloaded interface.
        const assets = [...html.matchAll(/(?:src|href)="(\/static\/mobileApp\/[^"<>]+)"/g)]
            .map(match => match[1].replaceAll('&amp;', '&'));
        assets.push('/static/mobileApp/img/pwa/apple-touch-icon.png');
        assets.push('/static/mobileApp/vendor/pdfjs/pdf-entry.mjs?v={{ asset_version }}',
            '/static/mobileApp/vendor/pdfjs/pdf.worker-entry.mjs?v={{ asset_version }}',
            '/static/mobileApp/vendor/pdfjs/compat.mjs?v=1',
            '/static/mobileApp/vendor/pdfjs/pdf.mjs?v=6.4.299-legacy-1',
            '/static/mobileApp/vendor/pdfjs/pdf.worker.mjs?v=6.4.299-legacy-1');
        await cache.addAll([...new Set(assets)]);
        const dependencies=[];
        for(const asset of assets.filter(path=>new URL(path,location.origin).pathname.endsWith('.css'))) {
            const css=await (await cache.match(asset)).text();
            for(const match of css.matchAll(/url\(\s*["']?([^"')\s]+)["']?\s*\)/g)) {
                if(match[1].startsWith('data:')||match[1].startsWith('#')) continue;
                const url=new URL(match[1],new URL(asset,location.origin));
                if(url.origin===location.origin && url.pathname.startsWith('/static/mobileApp/')) dependencies.push(url.href);
            }
        }
        await cache.addAll([...new Set(dependencies)]);
        await cache.put(HOME, response);
    })());
});
self.addEventListener('activate', event => {
    // Keep older public assets for tabs that have not accepted an update yet.
    event.waitUntil((async () => {
        const versions=(await caches.keys()).filter(key=>key.startsWith('reloncavi-shell-')&&key!==CACHE);
        await Promise.all(versions.slice(0,-2).map(key=>caches.delete(key)));
        await self.clients.claim();
    })());
});
self.addEventListener('message', event => {
    if (event.data?.type === 'ACTIVATE_UPDATE') self.skipWaiting();
});
self.addEventListener('fetch', event => {
    const request = event.request, url = new URL(request.url);
    if (request.method !== 'GET' || url.origin !== location.origin) return;
    if (request.mode === 'navigate' && url.pathname === HOME) {
        event.respondWith(fetch(request).catch(async () => (await caches.open(CACHE)).match(HOME)));
    } else if (url.pathname.startsWith('/static/mobileApp/')) {
        event.respondWith((async () => {
            const cache = await caches.open(CACHE);
            const saved = await cache.match(request);
            if (saved) return saved;
            const response = await fetch(request);
            if (response.ok && response.type === 'basic') await cache.put(request, response.clone());
            return response;
        })());
    }
});
