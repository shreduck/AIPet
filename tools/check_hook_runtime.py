"""Check independently deployed source hooks, or a Windows portable build and its deployed hooks."""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aipetlib.integrations import deployment, locations
from tools.hook_probe import check_hook


def check(app=None):
    with tempfile.TemporaryDirectory(prefix='aipet-hook-runtime-') as directory:
        root = Path(directory)
        destination = root / 'installed hook'
        with patch.object(locations,'INSTALL_DIR',str(destination)), patch.object(locations,'PET_DIR',str(root / 'config')):
            if app is None:
                deployment.deploy_files()
            else:
                original = root / 'portable app'
                shutil.copytree(Path(app).resolve(), original)
                check_hook(original / 'aipet-hook.exe',root / 'packaged')
                with patch.object(sys,'frozen',True,create=True), patch.object(sys,'_MEIPASS',str(original / '_internal'),create=True), \
                        patch.object(sys,'executable',str(original / 'AIPet.exe')):
                    deployment.deploy_files()
                original.rename(root / 'original app unavailable')
                check_hook(destination / 'hook/aipet-hook.exe',root / 'deployed-binary')
        expected = {p.relative_to(Path(locations.resource_path('aipet_hook_runtime')))
                    for p in Path(locations.resource_path('aipet_hook_runtime')).rglob('*.py')}
        installed = destination / 'aipet_hook_runtime'
        assert {p.relative_to(installed) for p in installed.rglob('*.py')} == expected, 'Incomplete source runtime'
        assert not any(p.is_symlink() for p in destination.rglob('*')), 'Deployment still depends on original paths'
        check_hook(destination / 'aipet_hook.py',root / 'deployed-source',interpreter=[sys.executable,'-S'])
        print('Independent hook runtime: PASS')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,help='Windows portable app folder containing AIPet.exe and aipet-hook.exe')
    check(parser.parse_args().app)
