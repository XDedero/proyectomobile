(() => {
    function formatDate(iso) {
        return iso.split('-').reverse().join('-');
    }

    const units = { days: 'días', weeks: 'semanas', months: 'meses', years: 'años' };
    function frequency({ interval, unit }) {
        const singular = { days: 'día', weeks: 'semana', months: 'mes', years: 'año' };
        return `Cada ${interval} ${(interval === 1 ? singular : units)[unit]}`;
    }

    function fields(id) {
        return `<fieldset id="${id}Maintenance" class="border rounded p-3 mb-3">
          <legend class="float-none w-auto px-1 mb-2 fs-6">Mantención <small class="text-muted">(opcional)</small></legend>
          <label class="form-label" for="${id}MaintenanceDate">Última mantención</label>
          <input type="date" id="${id}MaintenanceDate" class="form-control mb-2">
          <label class="form-label" for="${id}MaintenanceInterval">Frecuencia de mantención</label>
          <div class="input-group">
            <input type="number" id="${id}MaintenanceInterval" class="form-control" min="1" max="999" step="1" placeholder="Cantidad">
            <select id="${id}MaintenanceUnit" class="form-select" aria-label="Unidad de frecuencia">
              ${Object.entries(units).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
            </select>
          </div>
          <p class="small mb-0 mt-2">Próxima mantención <output id="${id}MaintenanceNext" class="fw-semibold" aria-live="polite">—</output></p>
        </fieldset>`;
    }

    function nextDate({ last_date, interval, unit }) {
        const date = new Date(`${last_date}T00:00:00Z`);
        if (unit === 'days' || unit === 'weeks') {
            date.setUTCDate(date.getUTCDate() + interval * (unit === 'weeks' ? 7 : 1));
        } else {
            const day = date.getUTCDate();
            date.setUTCDate(1);
            date.setUTCMonth(date.getUTCMonth() + interval * (unit === 'years' ? 12 : 1));
            const end = new Date(date);
            end.setUTCMonth(end.getUTCMonth() + 1, 0);
            date.setUTCDate(Math.min(day, end.getUTCDate()));
        }
        return date.toISOString().slice(0, 10);
    }

    function connect(id) {
        const find = suffix => document.getElementById(id + 'Maintenance' + suffix);
        const date = find('Date'), interval = find('Interval'), unit = find('Unit'), next = find('Next');
        function read() {
            if (!date.value && !interval.value && !date.validity.badInput && !interval.validity.badInput) return null;
            if (!date.value || !date.validity.valid) throw new Error('Ingresa una fecha de última mantención que no sea futura.');
            if (!interval.value || !interval.validity.valid) throw new Error('Ingresa una frecuencia entre 1 y 999.');
            return { last_date: date.value, interval: interval.valueAsNumber, unit: unit.value };
        }
        function preview() {
            next.value = '—';
            try {
                const values = read();
                if (values) next.value = formatDate(nextDate(values));
            } catch {
                // The save action displays validation errors after the user fills in the fields.
            }
        }
        for (const input of [date, interval, unit]) input.addEventListener('input', preview);
        return {
            read,
            set(values) {
                const today = new Intl.DateTimeFormat('sv-SE', {
                    timeZone: 'America/Santiago', year: 'numeric', month: '2-digit', day: '2-digit',
                });
                date.max = today.format();
                date.value = values?.last_date || '';
                interval.value = values?.interval ?? '';
                unit.value = values?.unit || 'months';
                preview();
            },
        };
    }

    window.buttonMaintenanceForm = { fields, connect, formatDate, frequency };
})();
