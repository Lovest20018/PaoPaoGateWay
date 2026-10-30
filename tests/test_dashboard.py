from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_dashboard as dashboard


class DashboardTests(unittest.TestCase):
    def test_shipped_dashboard_prepares_editor_and_removes_cloudflared(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            name = dashboard.prepare_dashboard(output)
            index = (output / 'index.html').read_text(encoding='utf-8')
            bundle = (output / name).read_text(encoding='utf-8')
            self.assertIn(name, index)
            self.assertIn('/ui/ppsub-panel.', index)
            self.assertIn('window.ppgwPpsub.loadCurrent(', bundle)
            self.assertIn('window.ppgwPpsub?.mount(', bundle)
            self.assertIn('_clash_id:e._clash_id', bundle)
            self.assertNotIn('{id:"/cloudflared",', bundle)
            self.assertNotIn(',"/cloudflared":[3]', bundle)

    def test_changed_editor_hook_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'FILES/etc/config/clash/clash-dashboard/ui'
            source.mkdir(parents=True)
            (source / 'bundle.unknown.js').write_text(
                ',"/cloudflared":[3],'
                '{id:"/cloudflared",icon:"cloud",label:gi("nav.cloudflared")},'
                'function changedEditor(){}', encoding='utf-8')
            (source / 'index.html').write_text('</head>', encoding='utf-8')
            with patch.object(dashboard, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'integration hook changed upstream'):
                    dashboard.prepare_dashboard(root / 'output')


if __name__ == '__main__':
    unittest.main()
