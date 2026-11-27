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
        return `<fieldset id="${id}Assignment" data-maintenance-assignment class="mb-3" aria-label="Asignación de mantención" hidden>
          ${[
              ['responsibles', 'Responsables de mantención', 'Añadir responsable'],
              ['supervisors', 'Supervisores (máximo 2)', 'Añadir supervisor'],
          ].map(([role, label, action]) => `
            <div class="border rounded p-3 mb-2" data-role="${role}">
              <label class="form-label" for="${id}-${role}">${label}</label>
              <div class="input-group">
                <select id="${id}-${role}" class="form-select"></select>
                <button type="button" class="btn btn-outline-primary" data-add aria-label="${action}">Añadir</button>
              </div>
              <ul class="list-unstyled mb-0" aria-live="polite"></ul>
            </div>`).join('')}
        </fieldset>`;
    }

    function connectAssignment(id) {
        const container = document.getElementById(id + 'Assignment');
        function connectGroup(role, maximum = Infinity) {
            const group = container.querySelector(`[data-role="${role}"]`);
            const select = group.querySelector('select');
            const add = group.querySelector('[data-add]');
            const list = group.querySelector('ul');
            let choices = new Map(), selected = new Set();

            function render(pending = '') {
                const available = [...choices].filter(([value]) => !selected.has(value));
                const atLimit = selected.size >= maximum;
                const prompt = atLimit ? 'Ya hay dos supervisores'
                    : available.length ? 'Elige una persona' : 'No hay más cuentas disponibles';
                select.replaceChildren(new Option(prompt, ''));
                select.disabled = atLimit || !available.length;
                if (!select.disabled) {
                    for (const [value, label] of available) select.add(new Option(label, value));
                    if (available.some(([value]) => value === pending)) select.value = pending;
                }
                add.disabled = !select.value;
                list.replaceChildren();
                for (const value of selected) {
                    const row = document.createElement('li');
                    row.className = 'd-flex align-items-center justify-content-between gap-2 mt-2';
                    const name = document.createElement('span');
                    name.className = 'small text-break';
                    name.textContent = choices.get(value);
                    const remove = document.createElement('button');
                    remove.type = 'button';
                    remove.className = 'btn btn-outline-secondary btn-sm';
                    remove.textContent = 'Quitar';
                    remove.setAttribute('aria-label', `Quitar a ${name.textContent}`);
                    remove.onclick = () => {
                        selected.delete(value);
                        render(select.value);
                        select.focus();
                    };
                    row.append(name, remove);
                    list.append(row);
                }
            }

            select.onchange = () => { add.disabled = !select.value; };
            add.onclick = () => {
                if (!select.value || selected.size >= maximum) return;
                selected.add(select.value);
                render();
                if (select.disabled) list.lastElementChild.querySelector('button').focus();
                else select.focus();
            };

            return {
                set(options, values) {
                    choices = new Map(options.map(({ value, label }) => [value, label]));
                    selected = new Set(values.filter(value => choices.has(value)));
                    render();
                },
                read() {
                    if (select.value) throw new Error('Pulsa «Añadir» para confirmar la persona elegida.');
                    return [...selected];
                },
            };
        }
        const responsible = connectGroup('responsibles');
        const supervisors = connectGroup('supervisors', 2);
        return {
            set(values) {
                container.hidden = !values;
                if (!values) return;
                responsible.set(values.responsible_options, values.responsibles);
                supervisors.set(values.supervisor_options, values.supervisors);
            },
            read() {
                if (container.hidden) return undefined;
                return { responsibles: responsible.read(), supervisors: supervisors.read() };
            },
        };
    }

    window.buttonMaintenanceForm = { fields, connect, formatDate, frequency, categoryOption, assignmentFields, connectAssignment };
})();
