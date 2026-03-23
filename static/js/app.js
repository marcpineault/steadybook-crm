// ── Globals ──
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || '';

function _headers(json = true) {
    const h = { 'X-CSRF-Token': CSRF };
    if (json) h['Content-Type'] = 'application/json';
    return h;
}

function _e(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Toast ──
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.className = 'toast show ' + type;
    toast.textContent = message;
    setTimeout(() => { toast.className = 'toast'; }, 3000);
}

function setLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
        btn.classList.add('loading');
        btn.disabled = true;
    } else {
        btn.classList.remove('loading');
        btn.disabled = false;
    }
}

// ── Prospect Modal ──
let _currentProspect = null;

function openProspectModal(mode, data) {
    // mode: 'add', 'edit', or 'view'
    const modal = document.getElementById('prospectModal');
    const viewDiv = document.getElementById('prospectView');
    const editDiv = document.getElementById('prospectEdit');

    if (mode === 'view' || mode === 'detail') {
        viewDiv.classList.remove('hidden');
        editDiv.classList.add('hidden');
        _currentProspect = data;
        populateProspectView(data);
    } else if (mode === 'add') {
        viewDiv.classList.add('hidden');
        editDiv.classList.remove('hidden');
        document.getElementById('prospectEditTitle').textContent = 'Add Prospect';
        document.getElementById('prospectDeleteBtn').classList.add('hidden');
        document.getElementById('prospectOriginalName').value = '';
        clearProspectForm();
    } else if (mode === 'edit') {
        viewDiv.classList.add('hidden');
        editDiv.classList.remove('hidden');
        document.getElementById('prospectEditTitle').textContent = 'Edit Prospect';
        document.getElementById('prospectDeleteBtn').classList.remove('hidden');
        populateProspectForm(data);
    }

    modal.classList.add('active');
}

function closeProspectModal() {
    document.getElementById('prospectModal').classList.remove('active');
    _currentProspect = null;
}

function openProspectDetail(name) {
    // Fetch detail from API
    fetch('/api/prospect/' + encodeURIComponent(name) + '/detail', { headers: _headers(false) })
        .then(r => r.json())
        .then(data => {
            if (data.error) { showToast(data.error, 'error'); return; }
            openProspectModal('view', data);
        })
        .catch(e => showToast('Error loading prospect', 'error'));
}

function populateProspectView(data) {
    const p = data.prospect || data;
    document.getElementById('prospectViewName').textContent = p.name || '';
    document.getElementById('prospectViewMeta').textContent =
        [p.product, p.source].filter(Boolean).join(' · ');

    // Priority badge
    const priBadge = document.getElementById('prospectViewPriority');
    priBadge.textContent = p.priority || '';
    priBadge.className = 'badge badge-' + (p.priority || '').toLowerCase();

    // Stage badge
    const stageBadge = document.getElementById('prospectViewStage');
    stageBadge.textContent = p.stage || '';
    stageBadge.className = 'badge badge-info';

    // Health score
    const healthDiv = document.getElementById('prospectViewHealth');
    const score = data.health_score || 0;
    const healthColor = score >= 70 ? 'var(--success)' : score >= 40 ? 'var(--warning)' : 'var(--danger)';
    // NOTE: All dynamic values inserted via _e() escaping; static markup only
    healthDiv.textContent = '';
    const scoreSpan = document.createElement('span');
    scoreSpan.style.cssText = 'display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:#fff;background:' + healthColor;
    scoreSpan.textContent = score;
    const labelSpan = document.createElement('span');
    labelSpan.className = 'text-muted';
    labelSpan.style.fontSize = '11px';
    labelSpan.textContent = ' health score';
    healthDiv.appendChild(scoreSpan);
    healthDiv.appendChild(labelSpan);

    // Next action
    document.getElementById('prospectViewNextAction').textContent = data.next_action || '';

    // Fields
    document.getElementById('prospectViewPhone').textContent = p.phone || '—';
    document.getElementById('prospectViewEmail').textContent = p.email || '—';
    document.getElementById('prospectViewProduct').textContent = p.product || '—';
    document.getElementById('prospectViewSource').textContent = p.source || '—';
    document.getElementById('prospectViewAum').textContent = p.aum || '—';
    document.getElementById('prospectViewRevenue').textContent = p.revenue || '—';
    document.getElementById('prospectViewFollowup').textContent = (p.next_followup || '').split(' ')[0] || '—';
    document.getElementById('prospectViewFirstContact').textContent = (p.first_contact || '').split(' ')[0] || '—';
    document.getElementById('prospectViewNotes').textContent = p.notes || 'No notes';

    // Activities
    const actDiv = document.getElementById('prospectViewActivities');
    const acts = data.activities || [];
    actDiv.textContent = '';
    if (acts.length === 0) {
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'text-muted';
        emptyDiv.textContent = 'No activity recorded';
        actDiv.appendChild(emptyDiv);
    } else {
        acts.slice(0, 10).forEach(a => {
            const item = document.createElement('div');
            item.className = 'activity-item';
            const dot = document.createElement('div');
            dot.className = 'activity-dot';
            dot.style.background = 'var(--primary)';
            const wrap = document.createElement('div');
            const textDiv = document.createElement('div');
            textDiv.className = 'activity-text';
            textDiv.textContent = (a.action || '') + (a.outcome ? ' — ' + a.outcome : '');
            const timeDiv = document.createElement('div');
            timeDiv.className = 'activity-time';
            timeDiv.textContent = (a.date || '').split(' ')[0];
            wrap.appendChild(textDiv);
            wrap.appendChild(timeDiv);
            item.appendChild(dot);
            item.appendChild(wrap);
            actDiv.appendChild(item);
        });
    }

    // Tasks
    const taskDiv = document.getElementById('prospectViewTasks');
    const tasks = data.tasks || [];
    taskDiv.textContent = '';
    if (tasks.length === 0) {
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'text-muted';
        emptyDiv.textContent = 'No tasks';
        taskDiv.appendChild(emptyDiv);
    } else {
        tasks.forEach(t => {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:4px';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.style.cssText = 'width:14px;height:14px';
            if (t.status === 'completed') {
                cb.checked = true;
                cb.disabled = true;
            } else {
                cb.onchange = function() { completeTask(t.id, cb); };
            }
            const label = document.createElement('span');
            if (t.status === 'completed') {
                label.style.cssText = 'text-decoration:line-through;color:var(--text-muted)';
            }
            label.textContent = t.title;
            row.appendChild(cb);
            row.appendChild(label);
            if (t.due_date) {
                const dueSpan = document.createElement('span');
                dueSpan.className = 'text-muted';
                dueSpan.style.cssText = 'margin-left:auto;font-size:10px';
                dueSpan.textContent = t.due_date;
                row.appendChild(dueSpan);
            }
            taskDiv.appendChild(row);
        });
    }
}

function switchToEditMode() {
    if (!_currentProspect) return;
    const p = _currentProspect.prospect || _currentProspect;
    openProspectModal('edit', p);
}

function populateProspectForm(p) {
    document.getElementById('prospectOriginalName').value = p.name || '';
    document.getElementById('pName').value = p.name || '';
    document.getElementById('pPhone').value = p.phone || '';
    document.getElementById('pEmail').value = p.email || '';
    document.getElementById('pProduct').value = p.product || '';
    document.getElementById('pStage').value = p.stage || 'New Lead';
    document.getElementById('pPriority').value = p.priority || 'Warm';
    document.getElementById('pAum').value = p.aum || '';
    document.getElementById('pRevenue').value = p.revenue || '';
    document.getElementById('pSource').value = p.source || '';
    document.getElementById('pFollowup').value = (p.next_followup || '').split(' ')[0];
    document.getElementById('pNotes').value = p.notes || '';
}

function clearProspectForm() {
    ['pName','pPhone','pEmail','pProduct','pAum','pRevenue','pSource','pFollowup','pNotes'].forEach(id => {
        document.getElementById(id).value = '';
    });
    document.getElementById('pStage').value = 'New Lead';
    document.getElementById('pPriority').value = 'Warm';
}

function cancelProspectEdit() {
    if (_currentProspect) {
        openProspectModal('view', _currentProspect);
    } else {
        closeProspectModal();
    }
}

function saveProspect(event) {
    event.preventDefault();
    const original = document.getElementById('prospectOriginalName').value;
    const data = {
        name: document.getElementById('pName').value,
        phone: document.getElementById('pPhone').value,
        email: document.getElementById('pEmail').value,
        product: document.getElementById('pProduct').value,
        stage: document.getElementById('pStage').value,
        priority: document.getElementById('pPriority').value,
        aum: document.getElementById('pAum').value,
        revenue: document.getElementById('pRevenue').value,
        source: document.getElementById('pSource').value,
        next_followup: document.getElementById('pFollowup').value,
        notes: document.getElementById('pNotes').value,
    };

    const submitBtn = document.querySelector('#prospectForm button[type="submit"]');
    setLoading(submitBtn, true);
    if (original) {
        // Update
        fetch('/api/prospect/' + encodeURIComponent(original), {
            method: 'PUT', headers: _headers(), body: JSON.stringify(data)
        }).then(r => r.json()).then(res => {
            if (res.error) { showToast(res.error, 'error'); return; }
            showToast('Prospect updated', 'success');
            closeProspectModal();
            location.reload();
        }).catch(e => { setLoading(submitBtn, false); showToast('Error saving', 'error'); });
    } else {
        // Create
        fetch('/api/prospect', {
            method: 'POST', headers: _headers(), body: JSON.stringify(data)
        }).then(r => r.json()).then(res => {
            if (res.error) { showToast(res.error, 'error'); return; }
            showToast('Prospect added', 'success');
            closeProspectModal();
            location.reload();
        }).catch(e => { setLoading(submitBtn, false); showToast('Error saving', 'error'); });
    }
    return false;
}

function deleteProspect() {
    const name = document.getElementById('prospectOriginalName').value;
    if (!name || !confirm('Delete ' + name + '?')) return;
    fetch('/api/prospect/' + encodeURIComponent(name), {
        method: 'DELETE', headers: _headers()
    }).then(r => r.json()).then(res => {
        showToast('Deleted', 'success');
        closeProspectModal();
        location.reload();
    }).catch(e => showToast('Error deleting', 'error'));
}

function prospectAction(type) {
    if (!_currentProspect) return;
    const p = _currentProspect.prospect || _currentProspect;
    if (type === 'call' || type === 'email' || type === 'sms') {
        closeProspectModal();
        openLogModal(type === 'call' ? 'Call' : type === 'email' ? 'Email' : 'SMS', p.name);
    } else if (type === 'reschedule') {
        // Quick reschedule to tomorrow
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const ds = tomorrow.toISOString().split('T')[0];
        fetch('/api/prospect/update', {
            method: 'PUT', headers: _headers(),
            body: JSON.stringify({ name: p.name, updates: { next_followup: ds } })
        }).then(r => r.json()).then(res => {
            showToast('Rescheduled to ' + ds, 'success');
            closeProspectModal();
            location.reload();
        });
    }
}

function toggleMergeSection() {
    document.getElementById('mergeSection').classList.toggle('hidden');
}

function doMerge() {
    const keep = (_currentProspect?.prospect || _currentProspect)?.name;
    const merge = document.getElementById('mergeName').value.trim();
    if (!keep || !merge) { showToast('Enter name to merge', 'error'); return; }
    fetch('/api/prospect/merge', {
        method: 'POST', headers: _headers(),
        body: JSON.stringify({ keep: keep, merge: merge })
    }).then(r => r.json()).then(res => {
        if (res.error) { showToast(res.error, 'error'); return; }
        showToast('Merged', 'success');
        closeProspectModal();
        location.reload();
    });
}

// ── Dropdown menus ──
function closeAllDropdowns() {
    document.querySelectorAll('.dropdown-menu.active').forEach(m => {
        if (m.parentElement === document.body) m.remove();
        else m.classList.remove('active');
    });
}
document.addEventListener('click', function(e) {
    if (!e.target.closest('.dropdown-menu') && !e.target.closest('[data-dropdown]')) {
        closeAllDropdowns();
    }
});

// ── Quick stage/priority change ──
function changeStage(event, prospectName) {
    event.stopPropagation();
    closeAllDropdowns();
    const stages = ['New Lead','Contacted','Discovery Call','Needs Analysis','Plan Presentation','Proposal Sent','Negotiation','Nurture','Closed-Won','Closed-Lost'];
    const stageColors = {'New Lead':'#3498DB','Contacted':'#9B59B6','Discovery Call':'#E67E22','Needs Analysis':'#F39C12','Plan Presentation':'#1ABC9C','Proposal Sent':'#2ECC71','Negotiation':'#E74C3C','Nurture':'#95A5A6','Closed-Won':'#27AE60','Closed-Lost':'#7F8C8D'};
    const menu = document.createElement('div');
    menu.className = 'dropdown-menu active';
    menu.style.position = 'fixed';
    menu.style.left = event.clientX + 'px';
    menu.style.top = event.clientY + 'px';
    menu.style.zIndex = '9999';
    stages.forEach(s => {
        const item = document.createElement('button');
        item.className = 'dropdown-item';
        const dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.background = stageColors[s] || '#94a3b8';
        item.appendChild(dot);
        item.appendChild(document.createTextNode(s));
        item.addEventListener('click', function() {
            menu.remove();
            const btn = event.target.closest('.btn, .badge, button');
            if (btn) { btn.classList.add('loading'); btn.disabled = true; }
            fetch('/api/prospect/update', {
                method: 'PUT', headers: _headers(),
                body: JSON.stringify({ name: prospectName, updates: { stage: s } })
            }).then(r => r.json()).then(res => {
                showToast(prospectName + ' → ' + s, 'success');
                location.reload();
            }).catch(() => { if (btn) { btn.classList.remove('loading'); btn.disabled = false; } showToast('Error updating stage', 'error'); });
        });
        menu.appendChild(item);
    });
    document.body.appendChild(menu);
    // Position adjustment
    requestAnimationFrame(() => {
        const rect = menu.getBoundingClientRect();
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 8) + 'px';
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 8) + 'px';
    });
}

function changePriority(event, prospectName) {
    event.stopPropagation();
    closeAllDropdowns();
    const pris = [
        { name: 'Hot', color: '#dc2626' },
        { name: 'Warm', color: '#f59e0b' },
        { name: 'Cold', color: '#3498db' }
    ];
    const menu = document.createElement('div');
    menu.className = 'dropdown-menu active';
    menu.style.position = 'fixed';
    menu.style.left = event.clientX + 'px';
    menu.style.top = event.clientY + 'px';
    menu.style.zIndex = '9999';
    pris.forEach(p => {
        const item = document.createElement('button');
        item.className = 'dropdown-item';
        const dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.background = p.color;
        item.appendChild(dot);
        item.appendChild(document.createTextNode(p.name));
        item.addEventListener('click', function() {
            menu.remove();
            fetch('/api/prospect/update', {
                method: 'PUT', headers: _headers(),
                body: JSON.stringify({ name: prospectName, updates: { priority: p.name } })
            }).then(r => r.json()).then(res => {
                showToast('Priority updated', 'success');
                location.reload();
            }).catch(() => showToast('Error updating priority', 'error'));
        });
        menu.appendChild(item);
    });
    document.body.appendChild(menu);
}

function quickReschedule(name, days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    const ds = d.toISOString().split('T')[0];
    fetch('/api/prospect/update', {
        method: 'PUT', headers: _headers(),
        body: JSON.stringify({ name: name, updates: { next_followup: ds } })
    }).then(r => r.json()).then(res => {
        showToast('Rescheduled', 'success');
        location.reload();
    });
}

// ── Task Modal ──
function openTaskModal(id, title, prospect, due, remind, notes) {
    document.getElementById('taskModalTitle').textContent = id ? 'Edit Task' : 'New Task';
    document.getElementById('taskId').value = id || '';
    document.getElementById('taskTitle').value = title || '';
    document.getElementById('taskProspect').value = prospect || '';
    document.getElementById('taskDue').value = due || '';
    document.getElementById('taskRemind').value = remind || '';
    document.getElementById('taskNotes').value = notes || '';
    document.getElementById('taskModal').classList.add('active');
}

function closeTaskModal() {
    document.getElementById('taskModal').classList.remove('active');
}

function saveTask(event) {
    event.preventDefault();
    const id = document.getElementById('taskId').value;
    const data = {
        title: document.getElementById('taskTitle').value,
        prospect: document.getElementById('taskProspect').value,
        due_date: document.getElementById('taskDue').value,
        remind_at: document.getElementById('taskRemind').value,
        notes: document.getElementById('taskNotes').value,
    };

    if (id) {
        fetch('/api/task/' + id, {
            method: 'PUT', headers: _headers(), body: JSON.stringify(data)
        }).then(r => r.json()).then(res => {
            showToast('Task updated', 'success');
            closeTaskModal();
            location.reload();
        });
    } else {
        fetch('/api/task', {
            method: 'POST', headers: _headers(), body: JSON.stringify(data)
        }).then(r => r.json()).then(res => {
            showToast('Task created', 'success');
            closeTaskModal();
            location.reload();
        });
    }
    return false;
}

function completeTask(id, checkbox) {
    fetch('/api/task/' + id + '/complete', {
        method: 'PUT', headers: _headers()
    }).then(r => r.json()).then(res => {
        showToast('Task completed', 'success');
        if (checkbox) checkbox.disabled = true;
        setTimeout(() => location.reload(), 500);
    });
}

function deleteTask(id) {
    if (!confirm('Delete this task?')) return;
    fetch('/api/task/' + id, {
        method: 'DELETE', headers: _headers()
    }).then(r => r.json()).then(res => {
        showToast('Task deleted', 'success');
        location.reload();
    });
}

// ── Log Activity Modal ──
function openLogModal(action, prospect) {
    document.getElementById('logAction').value = action || 'Call';
    document.getElementById('logProspect').value = prospect || '';
    document.getElementById('logOutcome').value = '';
    document.getElementById('logNextStep').value = '';
    document.getElementById('logModal').classList.add('active');
}

function closeLogModal() {
    document.getElementById('logModal').classList.remove('active');
}

function submitLog(event) {
    event.preventDefault();
    const data = {
        action: document.getElementById('logAction').value,
        prospect: document.getElementById('logProspect').value,
        outcome: document.getElementById('logOutcome').value,
        next_step: document.getElementById('logNextStep').value,
        date: new Date().toISOString().split('T')[0],
    };
    fetch('/api/activity', {
        method: 'POST', headers: _headers(), body: JSON.stringify(data)
    }).then(r => r.json()).then(res => {
        showToast('Activity logged', 'success');
        closeLogModal();
        location.reload();
    });
    return false;
}

// ── Kanban Drag & Drop ──
function onDragStart(e, prospectName) {
    e.dataTransfer.setData('text/plain', prospectName);
    e.target.style.opacity = '0.5';
}

function onDragEnd(e) {
    e.target.style.opacity = '1';
    document.querySelectorAll('.kanban-col').forEach(c => c.classList.remove('drag-over'));
}

function onDragOver(e) {
    e.preventDefault();
    e.currentTarget.classList.add('drag-over');
}

function onDragLeave(e) {
    e.currentTarget.classList.remove('drag-over');
}

function onDrop(e, newStage) {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    const name = e.dataTransfer.getData('text/plain');
    if (!name) return;
    fetch('/api/prospect/update', {
        method: 'PUT', headers: _headers(),
        body: JSON.stringify({ name: name, updates: { stage: newStage } })
    }).then(r => r.json()).then(res => {
        if (res.error) { showToast(res.error, 'error'); return; }
        showToast(name + ' \u2192 ' + newStage, 'success');
        location.reload();
    });
}

function onCardClick(e, prospectName) {
    if (e.defaultPrevented) return;
    openProspectDetail(prospectName);
}

// ── Pipeline search/filter ──
let _filterTimeout = null;
function filterProspects(query) {
    clearTimeout(_filterTimeout);
    _filterTimeout = setTimeout(() => {
        const q = query.toLowerCase();
        document.querySelectorAll('.kanban-card').forEach(card => {
            const text = card.textContent.toLowerCase();
            card.style.display = text.includes(q) || !q ? '' : 'none';
        });
        document.querySelectorAll('.pipeline-table tbody tr').forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(q) || !q ? '' : 'none';
        });
        // Update column counts
        document.querySelectorAll('.kanban-col').forEach(col => {
            const visible = col.querySelectorAll('.kanban-card:not([style*="display: none"])').length;
            const countEl = col.querySelector('.kanban-col-count');
            if (countEl) countEl.textContent = visible;
        });
    }, 150);
}

// ── Sortable Tables ──
function sortTable(table, colIndex, type) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const th = table.querySelectorAll('th')[colIndex];
    const isAsc = th.classList.contains('sort-asc');

    // Clear all sort indicators
    table.querySelectorAll('th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    th.classList.add(isAsc ? 'sort-desc' : 'sort-asc');

    rows.sort((a, b) => {
        let aVal = a.cells[colIndex]?.textContent?.trim() || '';
        let bVal = b.cells[colIndex]?.textContent?.trim() || '';
        if (type === 'money') {
            aVal = parseFloat(aVal.replace(/[$,K]/g, '').replace('M', '000000')) || 0;
            bVal = parseFloat(bVal.replace(/[$,K]/g, '').replace('M', '000000')) || 0;
        } else if (type === 'number') {
            aVal = parseFloat(aVal) || 0;
            bVal = parseFloat(bVal) || 0;
        } else {
            aVal = aVal.toLowerCase();
            bVal = bVal.toLowerCase();
        }
        if (aVal < bVal) return isAsc ? 1 : -1;
        if (aVal > bVal) return isAsc ? -1 : 1;
        return 0;
    });
    rows.forEach(r => tbody.appendChild(r));
}

// Initialize sortable tables on page load
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', function() {
            const table = th.closest('table');
            const idx = Array.from(th.parentElement.children).indexOf(th);
            const type = th.dataset.sortType || 'text';
            sortTable(table, idx, type);
        });
    });
});

// ── Pipeline view toggle ──
function togglePipelineView(view) {
    const kanban = document.getElementById('kanbanView');
    const table = document.getElementById('tableView');
    if (!kanban || !table) return;
    document.querySelectorAll('.view-toggle-btn').forEach(b => b.classList.remove('active'));
    if (view === 'kanban') {
        kanban.classList.remove('hidden');
        table.classList.add('hidden');
        event.target.classList.add('active');
    } else {
        kanban.classList.add('hidden');
        table.classList.remove('hidden');
        event.target.classList.add('active');
    }
    localStorage.setItem('pipelineView', view);
}

// ── Conversations ──
let _currentPhone = null;

function loadConversations() {
    fetch('/api/conversations', { headers: _headers(false) })
        .then(r => r.json())
        .then(contacts => renderConvList(contacts));
}

function renderConvList(contacts) {
    const list = document.getElementById('convList');
    if (!list) return;
    if (!contacts.length) {
        list.textContent = '';
        const emptyDiv = document.createElement('div');
        emptyDiv.style.cssText = 'padding:20px;text-align:center';
        emptyDiv.className = 'text-muted';
        emptyDiv.textContent = 'No conversations';
        list.appendChild(emptyDiv);
        return;
    }
    list.textContent = '';
    contacts.forEach(c => {
        const name = c.matched_name || c.prospect_name || c.phone;
        const preview = (c.body || '').substring(0, 40);
        const dir = c.direction === 'outbound' ? 'You: ' : '';
        const item = document.createElement('div');
        item.className = 'conv-item';
        item.dataset.phone = c.phone;
        item.onclick = function() { openThread(c.phone, name); };

        const topRow = document.createElement('div');
        topRow.style.cssText = 'display:flex;justify-content:space-between';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'conv-name';
        nameSpan.textContent = name;
        const timeSpan = document.createElement('span');
        timeSpan.className = 'conv-time';
        timeSpan.textContent = (c.created_at || '').substring(11, 16);
        topRow.appendChild(nameSpan);
        topRow.appendChild(timeSpan);

        const previewDiv = document.createElement('div');
        previewDiv.className = 'conv-preview';
        previewDiv.textContent = dir + preview;

        item.appendChild(topRow);
        item.appendChild(previewDiv);
        list.appendChild(item);
    });
}

function openThread(phone, name) {
    _currentPhone = phone;
    const header = document.getElementById('convThreadHeader');
    if (header) {
        header.textContent = '';
        const nameDiv = document.createElement('div');
        nameDiv.className = 'font-bold';
        nameDiv.textContent = name;
        const phoneDiv = document.createElement('div');
        phoneDiv.className = 'text-muted';
        phoneDiv.style.fontSize = '10px';
        phoneDiv.textContent = phone;
        header.appendChild(nameDiv);
        header.appendChild(phoneDiv);
    }
    document.querySelectorAll('.conv-item').forEach(i => i.classList.remove('active'));
    document.querySelector('.conv-item[data-phone="' + phone + '"]')?.classList.add('active');
    loadThread(phone);
}

function loadThread(phone) {
    fetch('/api/conversations/' + encodeURIComponent(phone), { headers: _headers(false) })
        .then(r => r.json())
        .then(msgs => renderThread(msgs));
}

function renderThread(msgs) {
    const container = document.getElementById('convMessages');
    if (!container) return;
    container.textContent = '';
    msgs.forEach(m => {
        const cls = m.direction === 'outbound' ? 'msg-outbound' : 'msg-inbound';
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble ' + cls;
        bubble.textContent = m.body;
        const timeDiv = document.createElement('div');
        timeDiv.className = 'msg-time';
        timeDiv.textContent = (m.created_at || '').substring(0, 16);
        bubble.appendChild(timeDiv);
        container.appendChild(bubble);
    });
    container.scrollTop = container.scrollHeight;
}

function sendConvMessage() {
    if (!_currentPhone) return;
    const input = document.getElementById('convInput');
    const body = input.value.trim();
    if (!body) return;
    input.value = '';
    fetch('/api/conversations/' + encodeURIComponent(_currentPhone) + '/send', {
        method: 'POST', headers: _headers(), body: JSON.stringify({ body: body })
    }).then(r => r.json()).then(res => {
        if (res.error) { showToast(res.error, 'error'); return; }
        showToast('SMS sent', 'success');
        loadThread(_currentPhone);
    });
}

// ── Chat Widget (slide-over panel) ──
function toggleChat() {
    const panel = document.getElementById('chatPanel');
    const backdrop = document.getElementById('chatBackdrop');
    const isOpen = panel.classList.contains('open');
    if (isOpen) {
        panel.classList.remove('open');
        if (backdrop) backdrop.classList.remove('active');
        document.body.style.overflow = '';
    } else {
        panel.classList.add('open');
        if (backdrop) backdrop.classList.add('active');
        // Focus the input when panel opens
        setTimeout(() => document.getElementById('chatInput')?.focus(), 300);
    }
}

function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    const container = document.getElementById('chatMessages');

    // Clear empty state if present
    const emptyState = container.querySelector('.chat-empty');
    if (emptyState) emptyState.remove();

    // Add outbound message
    const outMsg = document.createElement('div');
    outMsg.className = 'chat-msg outbound';
    const outBubble = document.createElement('div');
    outBubble.className = 'chat-bubble';
    outBubble.textContent = msg;
    outMsg.appendChild(outBubble);
    container.appendChild(outMsg);
    container.scrollTop = container.scrollHeight;

    // Add typing indicator
    const typingMsg = document.createElement('div');
    typingMsg.className = 'chat-msg inbound';
    typingMsg.id = 'chatTyping';
    const typingBubble = document.createElement('div');
    typingBubble.className = 'chat-bubble';
    const typingSpan = document.createElement('span');
    typingSpan.className = 'typing';
    typingSpan.textContent = 'Thinking...';
    typingBubble.appendChild(typingSpan);
    typingMsg.appendChild(typingBubble);
    container.appendChild(typingMsg);
    container.scrollTop = container.scrollHeight;

    fetch('/api/chat', {
        method: 'POST', headers: _headers(), body: JSON.stringify({ message: msg })
    }).then(r => r.json()).then(res => {
        // Remove typing indicator
        const typing = document.getElementById('chatTyping');
        if (typing) typing.remove();

        const inMsg = document.createElement('div');
        inMsg.className = 'chat-msg inbound';
        const inBubble = document.createElement('div');
        inBubble.className = 'chat-bubble';
        if (res.reply) {
            inBubble.textContent = res.reply;
        } else if (res.error) {
            inBubble.style.color = 'var(--danger)';
            inBubble.textContent = res.error;
        }
        inMsg.appendChild(inBubble);
        container.appendChild(inMsg);
        container.scrollTop = container.scrollHeight;
    }).catch(e => {
        const typing = document.getElementById('chatTyping');
        if (typing) typing.remove();

        const errMsg = document.createElement('div');
        errMsg.className = 'chat-msg inbound';
        const errBubble = document.createElement('div');
        errBubble.className = 'chat-bubble';
        errBubble.style.color = 'var(--danger)';
        errBubble.textContent = 'Error connecting to AI';
        errMsg.appendChild(errBubble);
        container.appendChild(errMsg);
    });
}

// ── Close modals on overlay click ──
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});

// ── Close modals on Escape ──
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
        if (document.getElementById('chatPanel')?.classList.contains('open')) {
            toggleChat();
        }
    }
});

// ── Restore pipeline view on page load ──
document.addEventListener('DOMContentLoaded', function() {
    const saved = localStorage.getItem('pipelineView');
    if (saved === 'table') {
        const kanban = document.getElementById('kanbanView');
        const table = document.getElementById('tableView');
        if (kanban && table) {
            kanban.classList.add('hidden');
            table.classList.remove('hidden');
            document.querySelectorAll('.view-toggle-btn').forEach(b => {
                b.classList.toggle('active', b.textContent.trim().toLowerCase() === 'table');
            });
        }
    }
    // Load conversations if on conversations page
    if (document.getElementById('convList')) {
        loadConversations();
    }
});
