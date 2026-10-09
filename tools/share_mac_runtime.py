"""Share the bundled hook's Python library with the app, before distribution signing.

Keep the hook's directory layout intact: deploy_files dereferences these file
or framework-directory links, leaving an independent installed runtime.
Supports both libpython builds and Python.framework builds from macOS CI.
"""
import argparse
import filecmp
import os
from pathlib import Path
import shutil
import subprocess


def share_runtime(bundle):
    frameworks = Path(bundle).resolve() / "Contents" / "Frameworks"
    internal = frameworks / "hook" / "_internal"
    replacements = []
    already_shared = 0
    framework = internal / "Python.framework"
    candidates = [framework] if framework.exists() else list(internal.glob("libpython*.dylib"))
    for source in candidates:
        relative = source.relative_to(internal)
        target = frameworks / relative
        if not target.exists() or not target.resolve().is_relative_to(frameworks):
            raise RuntimeError(f"App Python library missing or outside bundle: {target}")
        if source.is_symlink():
            if source.resolve() == target.resolve():
                already_shared += 1
            else:
                raise RuntimeError(f"Unexpected Python runtime link: {source}")
            continue
        files = [p for p in source.rglob('*') if p.is_file() and not p.is_symlink()] if source.is_dir() else [source]
        if not files:
            raise RuntimeError(f"Empty Python runtime: {source}")
        for file in files:
            if '_CodeSignature' in file.parts:
                continue  # regenerated after sharing
            other = target / file.relative_to(source) if source.is_dir() else target
            if not other.is_file() or not filecmp.cmp(file, other, shallow=False):
                raise RuntimeError(f"App and hook Python libraries differ: {relative}")
        replacements.append((source, target, sum(p.stat().st_size for p in files)))
    if not replacements and not already_shared:
        raise RuntimeError("No matching bundled Python runtime found")
    # Validate every candidate before changing any file.
    for source, target, _size in replacements:
        link = source.with_name(source.name + ".shared")
        link.symlink_to(os.path.relpath(target, source.parent), target_is_directory=target.is_dir())
        original = source.with_name(source.name + ".unshared")
        source.rename(original)
        try:
            os.replace(link, source)
        except BaseException:
            original.rename(source)
            raise
        else:
            shutil.rmtree(original) if original.is_dir() else original.unlink()
        finally:
            link.unlink(missing_ok=True)
    return sum(size for _source, _target, size in replacements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    saved = share_runtime(args.bundle)
    # PyInstaller's ad-hoc signature covered the old layout. Re-sign after the
    # symlink change; a release signing identity can be applied after this step.
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(args.bundle)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(args.bundle)], check=True)
    print(f"Shared Mac Python runtime: saved {saved / 2**20:.2f} MiB; bundle signature verified")


if __name__ == "__main__":
    main()
