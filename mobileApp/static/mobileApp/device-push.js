(() => {
    const control = document.getElementById('deviceNotifications');
    if (!control) return;
    const button = control.querySelector('button');
    const status = control.querySelector('[role="status"]');
    const csrfToken = control.querySelector('[name="csrfmiddlewaretoken"]').value;
    let registration, publicKey, installedSubscription, active = false;

    function show(text, disabled = false) {
        button.disabled = disabled;
        button.textContent = active ? 'Desactivar en este dispositivo' : 'Activar notificaciones en este dispositivo';
        status.textContent = text;
    }

    async function request(method, body) {
        const response = await fetch(control.dataset.subscriptionUrl, {
            method, cache: 'no-store', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
            body: body ? JSON.stringify(body) : undefined,
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || 'No se pudo guardar el cambio. Intenta nuevamente.');
        }
        return method === 'GET' ? response.json() : null;
    }

    function decodeKey(value) {
        const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
        return Uint8Array.from(atob(padded), character => character.charCodeAt(0));
    }

    function usesCurrentKey(subscription) {
        if (!subscription) return false;
        const key = new Uint8Array(subscription.options.applicationServerKey);
        return key.length === publicKey.length && key.every((byte, index) => byte === publicKey[index]);
    }

    async function prepare() {
        const ios = /iPhone|iPad|iPod/.test(navigator.userAgent)
            || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
        if (ios && !window.matchMedia('(display-mode: standalone)').matches && !navigator.standalone) {
            show('En iPhone o iPad, agrega Reloncaví a la pantalla de inicio y abre la app desde su icono.', true);
            return;
        }
        if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window)
                || !('Notification' in window)) {
            show('Este navegador no admite notificaciones de la app.', true);
            return;
        }
        if (Notification.permission === 'denied') {
            show('Las notificaciones están bloqueadas. Permítelas en los ajustes del navegador o del dispositivo.', true);
            return;
        }
        try {
            await navigator.serviceWorker.register(control.dataset.workerUrl, {scope: '/', updateViaCache: 'none'});
            const [worker, settings] = await Promise.all([navigator.serviceWorker.ready, request('GET')]);
            registration = worker;
            publicKey = decodeKey(settings.public_key);
            installedSubscription = await registration.pushManager.getSubscription();
            active = settings.registered && usesCurrentKey(installedSubscription) && Notification.permission === 'granted';
            show(active ? 'Notificaciones activadas.' : 'Recibe los avisos aunque la app esté cerrada.');
        } catch {
            show('No se pudieron preparar las notificaciones. Vuelve a abrir el buzón para reintentar.', true);
        }
    }

    button.addEventListener('click', async () => {
        show('Guardando…', true);
        try {
            if (active) {
                await request('DELETE');
                active = false;
                await installedSubscription.unsubscribe();
                installedSubscription = null;
                show('Notificaciones desactivadas en este dispositivo.');
                return;
            }
            // Ask directly from the user's click, as required by iOS and iPadOS.
            const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
            if (permission !== 'granted') {
                show(permission === 'denied' ? 'Permite las notificaciones en los ajustes del dispositivo.'
                    : 'Para recibir avisos, acepta el permiso de notificaciones.', permission === 'denied');
                return;
            }
            if (installedSubscription) await installedSubscription.unsubscribe();
            installedSubscription = await registration.pushManager.subscribe({
                userVisibleOnly: true, applicationServerKey: publicKey,
            });
            await request('POST', installedSubscription.toJSON());
            active = true;
            show('Notificaciones activadas.' + (registration.waiting
                ? ' Si hay una actualización pendiente, aplícala desde la pantalla principal.' : ''));
        } catch (error) {
            show(error.message || 'No se pudo cambiar el permiso. Intenta nuevamente.');
        }
    });
    prepare();
})();
