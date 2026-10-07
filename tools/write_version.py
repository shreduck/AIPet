"""Write _build_version.py with this build's version, taken from git: the nearest v* tag (`git describe`), e.g. "v0.2.4"
on a tagged commit or "v0.2.4-2-g573f03e" two commits later. "dev" when git or the tags aren't available.
build.bat, build_mac.sh and the GitHub Actions build run this before PyInstaller; the file is git-ignored."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def describe():
    try:
        out = subprocess.run(["git", "describe", "--tags", "--match", "v[0-9]*"], cwd=ROOT, capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "dev"
    except (OSError, subprocess.SubprocessError):
        return "dev"


if __name__ == "__main__":
    version = describe()
    with open(os.path.join(ROOT, "_build_version.py"), "w", encoding="utf-8") as f:
        f.write(f'VERSION = "{version}"  # written by tools/write_version.py at build time\n')
    print(f"Version: {version}")
