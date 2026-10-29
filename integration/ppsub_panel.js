/* Debian persistence for the existing PPsub editor; no separate form or login. */
(() => {
  let current = null;

  function secret() {
    try {
      const saved = JSON.parse(localStorage.getItem('PaoPaoGateWay') || '{}');
      return saved.clashAPIConfigs?.[0]?.secret || saved.secret || '';
    } catch { return ''; }
  }

  async function api(path, body) {
    const token = secret();
    if (!token) throw Error('登录已失效，请刷新面板后重试。');
    const response = await fetch('/sniffbox/subscription/' + path, {
      method: body ? 'POST' : 'GET',
      headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = Error(response.status === 401 ? '登录已失效，请刷新面板后重试。' :
        data.error || '暂时无法连接网关，请稍后重试。');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function mount(editor) {
    const state = { ...editor, available: false, writeAllowed: false, revision: '', editKind: 'ppsub',
      snapshotId: null, nodeChoices: [], active: false, stopped: false, timer: null, locked: [] };
    current = state;
    const apply = document.createElement('button');
    apply.type = 'button';
    apply.className = 'btn btn-brand';
    apply.textContent = '保存并应用';
    apply.dataset.ppsubApply = '';
    apply.disabled = true;
    const notice = document.createElement('div');
    notice.className = 'glass card-pad';
    notice.setAttribute('role', 'status');
    notice.style.whiteSpace = 'pre-wrap';
    notice.dataset.ppsubStatus = '';
    const text = document.createElement('span');
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'btn';
    view.textContent = '查看代理';
    view.style.marginLeft = '14px';
    view.hidden = true;
    view.style.display = 'none';
    view.onclick = () => { location.hash = '#/proxies'; location.reload(); };
    notice.append(text, view);
    const maintenance = document.createElement('details');
    maintenance.className = 'glass card-pad';
    const summary = document.createElement('summary');
    summary.textContent = '网关维护';
    summary.style.cursor = 'pointer';
    const coreInfo = document.createElement('p');
    const actionStatus = document.createElement('p');
    const operations = document.createElement('div');
    operations.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap';
    const actionButtons = [];
    let maintenanceTimer;
    async function updateMaintenance() {
      clearTimeout(maintenanceTimer);
      try {
        const data = await api('maintenance');
        if (state.stopped) return;
        coreInfo.textContent = `Mihomo ${data.core.installed_version || '未安装'} · 官方稳定版 ${data.core.latest_version || '待检查'} · ${data.timezone}`;
        actionStatus.textContent = data.operation?.status === 'running' ? '正在执行维护，请稍候…' :
          data.operation?.status === 'failed' ? '维护失败，原核心已保留或恢复。' :
          data.policy?.status === 'conflict' ? '订阅与本地策略冲突，正在继续使用最后成功配置。' :
          data.has_local_policy ? '本地分流策略已启用，订阅节点继续更新。' : '当前使用上游订阅策略。';
        actionButtons.forEach(({button, action}) => {
          button.disabled = state.active || data.operation?.status === 'running' ||
            (action === 'core/rollback' && !data.core.rollback_available) ||
            (action === 'policy/reset' && !data.has_local_policy);
        });
        if (maintenance.open || data.operation?.status === 'running') maintenanceTimer = setTimeout(updateMaintenance, 3000);
      } catch { actionStatus.textContent = '维护状态暂时无法读取。'; }
    }
    for (const [label, action] of [['检查官方核心', 'core/check'], ['在线更新 Mihomo', 'core/update'],
                                  ['回退上一核心', 'core/rollback'], ['恢复订阅原策略', 'policy/reset']]) {
      const button = document.createElement('button');
      button.className = 'btn'; button.type = 'button'; button.textContent = label;
      button.onclick = async () => {
        button.disabled = true;
        try {
          const payload = action === 'policy/reset' ? {base_revision: state.revision} : {};
          await api(action, payload);
          actionStatus.textContent = '操作已提交，正在处理…';
          await updateMaintenance();
          if (action === 'policy/reset') { state.active = true; setTimeout(refresh, 1000); }
        } catch (error) { actionStatus.textContent = error.message; button.disabled = false; }
      };
      actionButtons.push({button, action}); operations.append(button);
    }
    maintenance.append(summary, coreInfo, operations, actionStatus);
    maintenance.ontoggle = () => { if (maintenance.open) updateMaintenance(); };

    function show(message, failed = false) {
      if (state.stopped) return;
      text.textContent = message;
      text.style.color = failed ? 'var(--err-text)' : 'var(--text-2)';
    }

    function lockEditing(busy) {
      if (busy) {
        state.locked = Array.from(editor.header.parentElement.querySelectorAll('input,select,textarea,button'))
          .filter(element => element !== apply && !element.disabled);
        state.locked.forEach(element => { element.disabled = true; });
      } else {
        state.locked.forEach(element => { element.disabled = false; });
        state.locked = [];
      }
    }

    async function refresh() {
      clearTimeout(state.timer);
      try {
        const data = await api('status');
        if (state.stopped) return;
        state.revision ||= data.base_revision;
        const operation = data.operation;
        state.active = operation?.status === 'applying';
        state.writeAllowed = data.ppsub_write_allowed !== false;
        state.editKind = data.edit_kind || 'ppsub';
        apply.disabled = state.active || !state.writeAllowed || (state.editKind === 'clash_patch' && !state.snapshotId);
        if (operation) {
          show(operation.message, operation.status === 'failed');
          view.hidden = !['applied', 'unchanged'].includes(operation.status);
          view.style.display = view.hidden ? 'none' : 'inline-flex';
          if (operation.status !== 'applying') {
            state.revision = data.base_revision;
            lockEditing(false);
            if (state.editKind === 'clash_patch' && ['applied', 'unchanged'].includes(operation.status)) {
              const message = operation.message;
              await state.loadCurrent(editor.importConfig);
              show(message);
            }
          }
        }
        if (state.active) state.timer = setTimeout(refresh, 1500);
      } catch (error) {
        if (state.active && error.status !== 401) {
          show('正在重启网关并加载 PPsub 配置…');
          if (!state.stopped) state.timer = setTimeout(refresh, 1500);
        } else { show(error.message, true); }
      }
    }

    state.loadCurrent = async (importConfig) => {
      try {
        const data = await api('config');
        if (state.stopped) return;
        importConfig(data.config);
        state.revision = data.base_revision;
        state.writeAllowed = data.write_allowed !== false;
        state.editKind = data.edit_kind || 'ppsub';
        state.snapshotId = data.snapshot_id || null;
        state.nodeChoices = data.node_choices || [];
        apply.disabled = state.active || !state.writeAllowed;
        view.hidden = true;
        view.style.display = 'none';
        show(data.notice);
      } catch (error) { show(error.message, true); }
    };

    apply.onclick = async () => {
      if (state.active || !state.writeAllowed || (state.editKind === 'clash_patch' && !state.snapshotId)) return;
      try {
        const config = editor.exportConfig();
        if (state.editKind !== 'clash_patch' && (!config.subs?.length || config.subs.some(item => !item.name?.trim() || !item.url?.trim()))) {
          throw Error('请先填写订阅提供商的名称和订阅链接。');
        }
        state.active = true;
        apply.disabled = true;
        lockEditing(true);
        view.hidden = true;
        view.style.display = 'none';
        show('正在保存并加载 PPsub 配置，网关将短暂重启…');
        await api('apply', { kind: state.editKind, value: JSON.stringify(config),
          ...(state.editKind === 'clash_patch' ? { snapshot_id: state.snapshotId } : {}),
          base_revision: state.revision, operation_id: 'ppsub_' + Date.now() + '_' + Math.random().toString(36).slice(2) });
        await refresh();
      } catch (error) {
        state.active = false;
        lockEditing(false);
        apply.disabled = !state.writeAllowed;
        show(error.message, true);
      }
    };

    api('status').then(data => {
      if (state.stopped) return;
      state.available = true;
      state.revision = data.base_revision;
      editor.header.append(apply);
      editor.header.after(notice);
      notice.after(maintenance);
      state.writeAllowed = data.ppsub_write_allowed !== false;
      state.editKind = data.edit_kind || 'ppsub';
      apply.disabled = !state.writeAllowed || state.editKind === 'clash_patch';
      show(state.editKind === 'clash_patch' ? '请先点击“当前配置”，载入完整节点组和规则后编辑。保存时会保留原始配置的其他字段。' : state.writeAllowed ? '在此编辑订阅、节点组和规则，然后直接保存并应用。应用会短暂重启网关，加载失败时恢复原配置。' :
        '当前运行完整 Clash/Mihomo 配置。点击“当前配置”可读取实际节点组和分流规则；此格式不能直接用 PPsub 写回。');
      if (data.operation?.status === 'applying') refresh();
    }).catch(() => { /* Non-Debian upstream UI keeps its existing export workflow. */ });

    return () => {
      state.stopped = true;
      clearTimeout(state.timer);
      clearTimeout(maintenanceTimer);
      lockEditing(false);
      apply.remove();
      notice.remove();
      maintenance.remove();
      if (current === state) current = null;
    };
  }

  window.ppgwPpsub = {
    mount,
    isAvailable: () => Boolean(current?.available),
    clashEditing: () => current?.editKind === 'clash_patch',
    nodeChoices: () => (current?.nodeChoices || []).map(name => ({ name, type: 'group' })),
    loadCurrent: importConfig => current?.loadCurrent(importConfig),
  };
})();
