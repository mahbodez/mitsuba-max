"""Rewrite the checked-in golden fixtures.

    uv run python -m tests.golden.regenerate

Run this **deliberately**, never to make a failing test pass. `test_golden.py` compares the
generated IR against these files, and the asset paths inside them are content hashes, so a
diff here means some conversion changed its output. That is either the change you intended
or a regression, and the only way to tell them apart is to read the diff.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.golden.scenes import build_all  # noqa: E402


def main() -> int:
    assets = HERE / "assets"
    scenes = build_all(assets)
    for name, scene in sorted(scenes.items()):
        path = HERE / f"{name}.json"
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        text = scene.to_json()
        path.write_text(text, encoding="utf-8")
        state = "unchanged" if before == text else ("created" if before is None else "CHANGED")
        print(f"{state:>9}  {path.relative_to(ROOT)}  "
              f"({len(scene.meshes)} shapes, {len(scene.warnings)} warnings)")
    print(f"\nassets under {assets.relative_to(ROOT)} — not checked in, rebuilt on demand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
