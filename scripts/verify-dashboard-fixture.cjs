/* Exercise the shipped patched editor with synthetic data; no live Gateway. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PPGW_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, 'dist/merged-upstream-ui'));
const origin = 'http://127.0.0.1:18884';
const raw = {
  proxies: [{name: 'N1', type: 'socks5', server: '127.0.0.1', port: 9, password: 'fixture-only'}],
  secret: 'fixture-core', dns: {nameserver: ['1.1.1.1']},
  'proxy-groups': [
    {name: 'Main', type: 'select', proxies: ['Backup', 'DIRECT'], hidden: true},
    {name: 'Backup', type: 'fallback', proxies: ['N1', 'N1'], url: 'https://example.com/test', interval: 135, lazy: true},
  ],
  rules: ['PROCESS-NAME,fixture.exe,Main', 'DOMAIN-KEYWORD,foo#bar,DIRECT', 'MATCH,Main'],
};
function python(code, value) {
  return JSON.parse(execFileSync(process.env.PPGW_PYTHON || 'python', ['-c',
    "import sys,json;sys.path.insert(0,'integration');" + code],
    {cwd: root, input: JSON.stringify(value), encoding: 'utf8'}));
}
const projected = python("from clash_ppsub import editor_config;d=json.load(sys.stdin);print(json.dumps(editor_config(d,'https://example.com/sub')[0]))", raw);
const native = {
  version: 'PaoPaoGateWay 3.1', exported_at: 'fixture',
  subs: [{name: 'Fixture provider', url: 'https://example.com/sub'}],
  'node-groups': [{name: 'Native Main', subs: ['all']}],
  rules: [{fixrule: ['MATCH,Native Main']}],
};

async function exercise(browser, kind) {
  const context = await browser.newContext({viewport: {width: 1440, height: 960}});
  try {
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('PaoPaoGateWay', JSON.stringify({
      clashAPIConfigs: [{secret: 'fixture-token'}],
    })));
    let operation = null, saved = null;
    await page.route(origin + '/**', async route => {
      const target = new URL(route.request().url()).pathname;
      if (target.startsWith('/sniffbox/')) {
        let body = {mode: 'suburl', clash_version: 'fixture', ppsub: kind === 'ppsub'};
        if (target.endsWith('/status')) body = {
          ready: true, edit_kind: kind, ppsub_write_allowed: true, base_revision: 'fixture-revision', operation,
        };
        if (target.endsWith('/config')) body = {
          config: kind === 'ppsub' ? native : projected, source: kind === 'ppsub' ? 'ppsub' : 'clash_runtime',
          edit_kind: kind, snapshot_id: 'fixture-snapshot', write_allowed: true,
          base_revision: 'fixture-revision', node_choices: [{name: 'N1', type: 'node'}], notice: 'Synthetic fixture',
        };
        if (target.endsWith('/maintenance')) body = {
          core: {installed_version: 'fixture', rollback_available: false}, timezone: 'Asia/Shanghai',
          policy: {status: 'success'}, has_local_policy: false,
        };
        if (target.endsWith('/apply')) {
          assert.equal(route.request().headers().authorization, 'Bearer fixture-token');
          const payload = route.request().postDataJSON();
          assert.equal(payload.kind, kind);
          assert.equal(payload.base_revision, 'fixture-revision');
          saved = JSON.parse(payload.value);
          if (kind === 'clash_patch') {
            assert.equal(payload.snapshot_id, 'fixture-snapshot');
            const result = python("from clash_ppsub import merge_editor;d=json.load(sys.stdin);r,u,n=merge_editor(d['raw'],d['baseline'],d['submitted']);print(json.dumps({'unchanged':r==d['raw']}))",
              {raw, baseline: projected, submitted: saved});
            assert.equal(result.unchanged, true);
          } else {
            assert.equal(saved.subs[0].name, 'Edited native provider');
            assert.equal(saved.subs[0].url, native.subs[0].url);
            assert.deepEqual(saved.rules[0].fixrule, native.rules[0].fixrule);
          }
          operation = {id: payload.operation_id, status: kind === 'clash_patch' ? 'unchanged' : 'applied', message: 'Fixture saved ' + kind};
          body = operation;
        }
        return route.fulfill({status: target.endsWith('/apply') ? 202 : 200, contentType: 'application/json', body: JSON.stringify(body)});
      }
      const filename = path.join(output, target === '/ui/' || target === '/ui' ? 'index.html' : path.basename(target));
      try {
        const body = await fs.readFile(filename);
        await route.fulfill({contentType: target.endsWith('.js') ? 'application/javascript' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/html', body});
      } catch { await route.fulfill({status: 404, body: ''}); }
    });
    await page.goto(origin + '/ui/#/ppsub');
    const apply = page.getByRole('button', {name: '保存并应用', exact: true});
    await apply.waitFor();
    if (kind === 'clash_patch') assert.equal(await apply.isDisabled(), true);
    await page.getByRole('button', {name: '当前配置', exact: true}).click();
    await page.getByRole('button', {name: '删除此节点组', exact: true}).last().waitFor();
    if (kind === 'ppsub') await page.getByPlaceholder('例如: 某机场').fill('Edited native provider');
    assert.equal(await apply.isDisabled(), false);
    await apply.click();
    await page.getByText('Fixture saved ' + kind, {exact: true}).waitFor();
    assert.ok(saved);
    assert.equal(await page.locator('input[autocomplete="current-password"]').count(), 0);
    assert.equal(await page.locator('a[href="#/cloudflared"]').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await apply.isVisible(), true);
    await page.setViewportSize({width: 1440, height: 960});
    await page.getByRole('link', {name: '概览', exact: true}).click();
    await apply.waitFor({state: 'detached'});
    assert.deepEqual(errors, []);
    return {kind, actualEditorExport: true, sharedLogin: true, routeCleanup: true, pageErrors: errors};
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    console.log(JSON.stringify({syntheticOnly: true, checks: [
      await exercise(browser, 'clash_patch'), await exercise(browser, 'ppsub'),
    ]}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
