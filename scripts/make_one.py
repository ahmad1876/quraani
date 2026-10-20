"""Render one video locally:  python scripts/make_one.py 39:53-54 yasser-aldosari [out.mp4] [verse|you]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import make  # noqa: E402
from common import CATALOG, config  # noqa: E402


def main():
    pid, reciter = sys.argv[1], sys.argv[2]
    out = Path(sys.argv[3] if len(sys.argv) > 3 else f"out/{pid.replace(':', '_')}_{reciter}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    passages = {p["id"]: p for p in json.loads((CATALOG / "passages.json").read_text(encoding="utf-8"))}
    p = passages.get(pid)
    if not p:
        s, r = pid.split(":")
        a, b = (r.split("-") + [r])[:2]
        p = {"id": pid, "surah": int(s), "start": int(a), "end": int(b), "hook": "", "theme": ""}
    cfg = config()
    meta = make.make_video(p, reciter, out, handle=cfg.get("handle", ""), seed=7,
                           preset=cfg.get("x264_preset", "medium"), hook_style=sys.argv[4] if len(sys.argv) > 4 else "verse",
                           comment_prompt=cfg.get("comment_prompt", True), follow_along=cfg.get("follow_along", True))
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
