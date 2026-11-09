(() => {
    const dialog = document.createElement('dialog');
    dialog.className = 'app-confirm';
    dialog.setAttribute('aria-labelledby', 'appConfirmTitle');
    dialog.setAttribute('aria-describedby', 'appConfirmMessage');
    dialog.innerHTML = `<h2 id="appConfirmTitle"></h2>
        <p id="appConfirmMessage"></p>
        <form method="dialog" class="app-confirm-actions">
            <button type="submit" class="btn btn-outline-secondary" value="cancel" autofocus>Cancelar</button>
            <button type="submit" class="btn" value="accept"></button>
        </form>`;
    const title = dialog.querySelector('h2');
    const message = dialog.querySelector('p');
    const accept = dialog.querySelector('[value="accept"]');
    let pending = false;

    dialog.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopPropagation();
        dialog.close('cancel');
    });

    window.confirmAppAction = (heading, description, action, { danger = false } = {}) => {
        if (pending) return Promise.resolve(false);
        title.textContent = heading;
        message.textContent = description;
        accept.textContent = action;
        accept.className = danger ? 'btn btn-danger' : 'btn btn-primary';
        dialog.returnValue = '';
        // Keep the confirmation inside the current Bootstrap focus trap, if any.
        const parent = document.querySelector('.modal.show') || document.body;
        parent.append(dialog);
        pending = true;
        dialog.showModal();
        return new Promise((resolve) => {
            dialog.addEventListener('close', () => {
                pending = false;
                const confirmed = dialog.returnValue === 'accept';
                dialog.remove();
                resolve(confirmed);
            }, { once: true });
        });
    };
})();
