/* Only public UI files are cached here. Account data lives in the offline vault. */
const VERSION = '{{ asset_version }}';
const CACHE = `reloncavi-shell-${VERSION}`;
const HOME = '/';
const INBOX = "{% url 'notifications' %}";
self.addEventListener('push', event => {
    let alert;
    try { alert = event.data?.json(); } catch { alert = null; }
    event.waitUntil(self.registration.showNotification(alert?.title || 'planimetry cl', {
        body: alert?.body || 'Tienes un aviso nuevo en el buzón.',
        icon: '/static/mobileApp/img/pwa/icon-192.png?v={{ asset_version }}',
        tag: alert?.tag,
    }));
});
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
        const inbox = windows.find(client => new URL(client.url).pathname === INBOX);
        return inbox ? inbox.focus() : self.clients.openWindow(INBOX);
    })());
});
self.addEventListener('install', event => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        const response = await fetch(HOME, {cache: 'reload', credentials: 'omit'});
        if (!response.ok) throw new Error('No se pudo preparar la app sin conexión.');
        const html = await response.clone().text();
        // Do not activate with a half-downloaded interface.
        const assets = [...html.matchAll(/(?:src|href|data-zip-url)="(\/static\/mobileApp\/[^"<>]+)"/g)]
            .map(match => match[1].replaceAll('&amp;', '&'));
        assets.push('/static/mobileApp/vendor/pdfjs/pdf-entry.mjs?v={{ asset_version }}',
            '/static/mobileApp/vendor/pdfjs/pdf.worker-entry.mjs?v={{ asset_version }}',
            '/static/mobileApp/vendor/pdfjs/compat.mjs?v=1');
        await cache.addAll([...new Set(assets)]);
        // A new shell can reuse the byte-for-byte PDF engine from an older shell.
        // Keep both bundles required for the offline viewer before activation.
        const previousShells = (await caches.keys()).filter(name => name.startsWith('reloncavi-shell-') && name !== CACHE).reverse();
        await Promise.all([
            '/static/mobileApp/vendor/pdfjs/pdf.mjs?v=6.4.299-legacy-1',
            '/static/mobileApp/vendor/pdfjs/pdf.worker.mjs?v=6.4.299-legacy-1',
        ].map(async url => {
            if (await cache.match(url)) return;
            for (const cacheName of previousShells) {
                const saved = await caches.match(url, {cacheName});
                if (saved) { await cache.put(url, saved); return; }
            }
            await cache.add(url);
        }));
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
        event.respondWith((async () => {
            const response = await fetch(request).catch(() => null);
            if (response && ![502, 503, 504].includes(response.status)) return response;
            return (await (await caches.open(CACHE)).match(HOME)) || response || Response.error();
        })());
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
