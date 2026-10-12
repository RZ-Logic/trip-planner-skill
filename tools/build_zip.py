"""Build dist/trip-planner.zip for claude.ai upload and the Claude API.

The zip holds the skill folder at its root (trip-planner/SKILL.md), which is
what claude.ai and POST /v1/skills expect. Run: python tools/build_zip.py
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "plugins", "trip-planner", "skills", "trip-planner")
SKIP_DIRS = {"__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {".DS_Store"}


def main():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "trip-planner.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SKILL):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                if f in SKIP_FILES or f.endswith(".pyc"):
                    continue
                full = os.path.join(base, f)
                arc = os.path.join("trip-planner", os.path.relpath(full, SKILL)).replace(os.sep, "/")
                z.write(full, arc)
                count += 1
    print("wrote %s (%d files)" % (out, count))


if __name__ == "__main__":
    main()
