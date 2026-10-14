"""Exercise the packaged Mac hooks, their standalone deployment, and ZIP relocation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.hook_probe import check_hook
from aipetlib.integrations import deployment as hook_deployment
from aipetlib.integrations import locations as hook_locations


def check_bundle(bundle):
    bundle = Path(bundle).resolve()
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    with tempfile.TemporaryDirectory(prefix='aipet-package-check-') as directory:
        root = Path(directory)
        archive = root / 'app.zip'
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(bundle), str(archive)], check=True)
        relocated = root / 'unpacked elsewhere'
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(relocated)], check=True)
        restored = relocated / bundle.name
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(restored)], check=True)
        assets_report = root / 'assets.json'
        subprocess.run([str(restored / 'Contents/MacOS/AIPet'), '--selftest-assets', str(assets_report)],
                       check=True, timeout=30)
        assets = json.loads(assets_report.read_text())
        assert assets['ok'], assets
        print(f"  Robot assets: PNG loading and {assets['faces']} rendered faces passed")
        resources = restored / 'Contents/Resources'
        check_hook(resources / 'hook/aipet-hook', root / 'bundled-check')
        deployed = root / 'standalone-hook'
        with patch.object(hook_locations, 'resource_path', lambda *p: str(resources.joinpath(*p))), \
                patch.object(hook_locations, 'INSTALL_DIR', str(deployed)), patch.object(hook_locations, 'PET_DIR', str(root / 'config')):
            hook_deployment.deploy_files()
        assert not any(p.is_symlink() for p in deployed.rglob('*')), 'Deployment still depends on bundle links'
        # Make every original bundle path unavailable before exercising the installed copy.
        restored.rename(relocated / 'moved.app')
        check_hook(deployed / 'hook/aipet-hook', root / 'deployed-check')
        assert (deployed / 'aipet_hook_runtime/cli.py').is_file(), 'Source runtime package missing'
        check_hook(deployed / 'aipet_hook.py', root / 'source-check', interpreter=[sys.executable, '-S'])
        size = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file() and not p.is_symlink())
        print(f'Verified ZIP relocation and independent deployment: app {size / 2**20:.2f} MiB, ZIP {archive.stat().st_size / 2**20:.2f} MiB')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    check_bundle(parser.parse_args().bundle)
