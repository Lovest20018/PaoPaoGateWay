#!/usr/bin/env python3
"""Add Debian save/apply hooks to the shipped PPsub editor, preserving its UI."""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare_dashboard(output):
    source = ROOT / 'FILES/etc/config/clash/clash-dashboard/ui'
    shutil.copytree(source, output, dirs_exist_ok=True)
    bundles = list(source.glob('bundle.*.js'))
    if len(bundles) != 1:
        raise ValueError('Expected one upstream dashboard bundle')
    original = bundles[0]
    bundle = original.read_text(encoding='utf-8')
    for anchor in (',"/cloudflared":[3]', '{id:"/cloudflared",icon:"cloud",label:gi("nav.cloudflared")},'):
        if bundle.count(anchor) != 1:
            raise ValueError('Cloudflared removal hook changed upstream')
        bundle = bundle.replace(anchor, '')
    load_hook = 'async function B(){try{N(await Li(`/data/ppsub.json?t=${Math.floor(Date.now()/1e3)}`))'
    load_patch = ('async function B(){if(window.ppgwPpsub?.isAvailable())'
                  '{await window.ppgwPpsub.loadCurrent(N);return}'
                  'try{N(await Li(`/data/ppsub.json?t=${Math.floor(Date.now()/1e3)}`))')
    mount_hook = ('Cn("click",kt,()=>gn(r).push({priority:gn(r).length+1,remark:"",name:"",type:"manual",'
                  'url:"",rule_set_url:"",content:"",isforced:!1,behavior:"domain",interval:86400,'
                  'format:"auto",use_proxy:!1,proxy_group:""})),zn(e,oe),Vt()}')
    mount_patch = mount_hook.replace('zn(e,oe),Vt()}',
        'Fl(()=>window.ppgwPpsub?.mount({header:ce,exportConfig:z,importConfig:N})),zn(e,oe),Vt()}')
    identities = (
        ('({name:e.name||"",url:e.url||"",isforced:', '({_clash_id:e._clash_id||"",name:e.name||"",url:e.url||"",isforced:'),
        ('return{name:e.name||"",keywords:e.keywords', 'return{_clash_id:e._clash_id||"",name:e.name||"",keywords:e.keywords'),
        ('{priority:t+1,remark:e.remark', '{_clash_id:e._clash_id||"",priority:t+1,remark:e.remark'),
        ('const t={name:e.name,url:e.url};', 'const t={_clash_id:e._clash_id||"",name:e.name,url:e.url};'),
        ('const l={name:e.name,keywords:e.keywords', 'const l={_clash_id:e._clash_id||"",name:e.name,keywords:e.keywords'),
        ('const a={priority:10*(t+1)};', 'const a={_clash_id:e._clash_id||"",priority:10*(t+1)};'),
    )
    rules_hook = 'a.fixrule=(e.content||"").split("\\n").map(e=>function(e)'
    rules_patch = ('a.fixrule=window.ppgwPpsub?.clashEditing()?'
                   '(e.content||"").split("\\n").map(e=>e.trim()).filter(Boolean):'
                   '(e.content||"").split("\\n").map(e=>function(e)')
    choices_hook = 'e.push({name:"DIRECT",type:"group"},{name:"REJECT",type:"group"})),e.filter(e=>e.name&&'
    choices_patch = ('e.push({name:"DIRECT",type:"group"},{name:"REJECT",type:"group"})),e=e.concat('
                     '(window.ppgwPpsub?.nodeChoices?.()||[]).filter(t=>!e.some(a=>a.name===t.name))),e.filter(e=>e.name&&')
    for anchor, replacement in ((load_hook, load_patch), (mount_hook, mount_patch), *identities,
                                (rules_hook, rules_patch), (choices_hook, choices_patch)):
        if bundle.count(anchor) != 1:
            raise ValueError('PPsub integration hook changed upstream; review before packaging')
        bundle = bundle.replace(anchor, replacement)
    patched_name = 'bundle.debian.' + hashlib.sha256(bundle.encode()).hexdigest()[:12] + '.js'
    (output / patched_name).write_text(bundle, encoding='utf-8', newline='\n')
    extension = (ROOT / 'integration/ppsub_panel.js').read_bytes()
    extension_name = 'ppsub-panel.' + hashlib.sha256(extension).hexdigest()[:12] + '.js'
    (output / extension_name).write_bytes(extension)
    index = (source / 'index.html').read_text(encoding='utf-8').replace(original.name, patched_name)
    index = index.replace('</head>', f'<script src="/ui/{extension_name}"></script>\n</head>')
    (output / 'index.html').write_text(index, encoding='utf-8', newline='\n')
    return patched_name


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare_dashboard(args.output))
