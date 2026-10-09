"""Zip a folder at the strongest standard (Deflate) level, with "/" paths: python tools/make_zip.py <folder> <zip>.
The folder itself becomes the zip's top-level entry (AIPet/...), as Explorer and 7-Zip expect."""
import os
import sys
import zipfile


def make_zip(folder, target):
    folder = os.path.abspath(folder)
    base = os.path.dirname(folder)
    tmp = target + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                z.write(path, os.path.relpath(path, base).replace(os.sep, "/"))
    os.replace(tmp, target)


if __name__ == "__main__":
    make_zip(sys.argv[1], sys.argv[2])
