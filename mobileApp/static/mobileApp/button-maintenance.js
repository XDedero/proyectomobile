(() => {
    function formatDate(iso) {
        return iso.split('-').reverse().join('-');
    }

    const units = { days: 'días', weeks: 'semanas', months: 'meses', years: 'años' };
    const todayInChile = new Intl.DateTimeFormat('sv-SE', {
        timeZone: 'America/Santiago', year: 'numeric', month: '2-digit', day: '2-digit',
    });
    function frequency({ interval, unit }) {
        const singular = { days: 'día', weeks: 'semana', months: 'mes', years: 'año' };
        return `Cada ${interval} ${(interval === 1 ? singular : units)[unit]}`;
    }

    function fields(id) {
        return `<fieldset id="${id}Maintenance" class="border rounded p-3 mb-3">
          <legend class="float-none w-auto px-1 mb-2 fs-6">Mantención <small id="${id}MaintenanceRequirement" class="text-muted">(opcional)</small></legend>
          <label class="form-label" for="${id}MaintenanceDate">Última mantención</label>
          <input type="date" id="${id}MaintenanceDate" class="form-control mb-2">
          <label class="form-label" for="${id}MaintenanceInterval">Frecuencia de mantención</label>
          <div class="input-group">
            <input type="number" id="${id}MaintenanceInterval" class="form-control" min="1" max="999" step="1" placeholder="Cantidad">
            <select id="${id}MaintenanceUnit" class="form-select" aria-label="Unidad de frecuencia">
              ${Object.entries(units).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
            </select>
          </div>
          <p id="${id}MaintenanceNextRow" class="small mb-0 mt-2">Próxima mantención <output id="${id}MaintenanceNext" class="fw-semibold" aria-live="polite">—</output></p>
          <p id="${id}MaintenanceWarning" class="alert alert-warning mt-2 mb-0 py-2" role="status" hidden>Por favor realizar mantención</p>
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
        const nextRow = find('NextRow'), warning = find('Warning'), requirement = find('Requirement');
        function read() {
            if (!date.value && !interval.value && !date.validity.badInput && !interval.validity.badInput) {
                if (date.required) throw new Error('Esta categoría requiere la fecha de última mantención y la frecuencia.');
                return null;
            }
            if (!date.value || !date.validity.valid) throw new Error('Ingresa una fecha de última mantención que no sea futura.');
            if (!interval.value || !interval.validity.valid) throw new Error('Ingresa una frecuencia entre 1 y 999.');
            return { last_date: date.value, interval: interval.valueAsNumber, unit: unit.value };
        }
        function preview() {
            next.value = '—';
            nextRow.hidden = false;
            warning.hidden = true;
            try {
                const values = read();
                if (!values) return;
                const due = nextDate(values);
                const overdue = due < todayInChile.format();
                nextRow.hidden = overdue;
                warning.hidden = !overdue;
                if (!overdue) next.value = formatDate(due);
            } catch {
                // The save action displays validation errors after the user fills in the fields.
            }
        }
        for (const input of [date, interval, unit]) input.addEventListener('input', preview);
        return {
            read,
            setRequired(required) {
                date.required = interval.required = required;
                requirement.textContent = required ? '(obligatoria)' : '(opcional)';
                preview();
            },
            set(values) {
                date.max = todayInChile.format();
                date.value = values?.last_date || '';
                interval.value = values?.interval ?? '';
                unit.value = values?.unit || 'months';
                preview();
            },
        };
    }

    function categoryOption(category) {
        const option = new Option(category.name, category.id);
        option.dataset.maintenanceRequired = String(category.maintenance_required);
        return option;
    }

    function assignmentFields(id) {
        return `<div id="${id}Assignment" class="mb-3" hidden>
          <fieldset class="border rounded p-3 mb-2">
            <legend class="float-none w-auto px-1 fs-6">Responsables de mantención</legend>
            <div class="overflow-auto" style="max-height: 10rem" data-role="responsibles"></div>
          </fieldset>
          <fieldset class="border rounded p-3">
            <legend class="float-none w-auto px-1 fs-6">Supervisores (máximo 2)</legend>
            <div class="overflow-auto" style="max-height: 10rem" data-role="supervisors"></div>
          </fieldset>
        </div>`;
    }

    function connectAssignment(id) {
        const container = document.getElementById(id + 'Assignment');
        const responsible = container.querySelector('[data-role="responsibles"]');
        const supervisors = container.querySelector('[data-role="supervisors"]');
        const selection = group => Array.from(group.querySelectorAll('input:checked'), input => input.value);
        const enforceLimit = () => {
            const limit = selection(supervisors).length >= 2;
            for (const input of supervisors.querySelectorAll('input')) input.disabled = limit && !input.checked;
        };
        supervisors.addEventListener('change', enforceLimit);
        function options(group, choices, selected) {
            group.replaceChildren();
            for (const choice of choices) {
                const label = document.createElement('label');
                label.className = 'd-flex align-items-center gap-2 py-1';
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.className = 'form-check-input mt-0';
                input.value = choice.value;
                input.checked = selected.includes(choice.value);
                label.append(input, document.createTextNode(choice.label));
                group.append(label);
            }
            if (!choices.length) group.textContent = 'No hay cuentas disponibles.';
        }
        return {
            set(values) {
                container.hidden = !values;
                if (!values) return;
                options(responsible, values.responsible_options, values.responsibles);
                options(supervisors, values.supervisor_options, values.supervisors);
                enforceLimit();
            },
            read() {
                if (container.hidden) return undefined;
                return { responsibles: selection(responsible), supervisors: selection(supervisors) };
            },
        };
    }

    window.buttonMaintenanceForm = { fields, connect, formatDate, frequency, categoryOption, assignmentFields, connectAssignment };
})();
