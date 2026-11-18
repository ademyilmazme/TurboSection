"""The Nastran reader's worker process: nastran_adapter behind a pipe.

    <runtime python> -I serve.py

Run by turbonastran (python/turbonastran/_client.py) in the isolated
runtime (runtime/venv: pyNastran and the NumPy it requires), so the
application's interpreter never imports pyNastran.  One worker per opened
model; it holds the parsed BDF / OP2 until ``close``.

Protocol: one JSON request per line on stdin
(``{"op": ..., ...}``); one response per request on the original stdout:
an 8-byte little-endian length, then an uncompressed .npz whose
``__meta__`` entry is the UTF-8 JSON metadata (``{"error": kind,
"message": ...}`` on failure) and whose other entries are the arrays.
Everything else the process prints (pyNastran's log) goes to stderr.
"""

import io
import json
import os
import struct
import sys
import traceback
from pathlib import Path

# the response channel is the original stdout; whatever else writes to
# stdout — Python or the C runtime — lands on stderr
_RESPONSES = os.fdopen(os.dup(1), "wb")
os.dup2(2, 1)
sys.stdout = sys.stderr

sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I does not add the script's folder

import numpy as np  # noqa: E402

import nastran_adapter as A  # noqa: E402


def _send(meta, arrays=None) -> None:
    buf = io.BytesIO()
    np.savez(buf, __meta__=np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8),
             **(arrays or {}))
    data = buf.getvalue()
    _RESPONSES.write(struct.pack("<Q", len(data)))
    _RESPONSES.write(data)
    _RESPONSES.flush()


def main() -> int:
    session = A.Session()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if req.get("op") == "exit":
                _send({"ok": True})
                return 0
            meta, arrays = session.handle(req)
            _send(meta, arrays)
        except A.AdapterError as exc:
            _send({"error": exc.kind, "message": str(exc)})
        except Exception as exc:                            # a pyNastran failure: reported, not fatal
            _send({"error": "native", "message": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc(limit=6)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
