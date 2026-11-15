"""Translate through ComfyUI and optionally save an API node fragment.

Only Python's standard library is used. Connect the fragment's text output to
your own downstream workflow before queueing; this node is not an output node.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Source text, or @path/to/source.txt")
    parser.add_argument("--model-key", required=True, help="Exact LM Studio model key")
    parser.add_argument("--target", choices=("en", "ja"), default="en")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--lm-url", default="http://127.0.0.1:1234")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, choices=(256,512,1024,2048), default=512)
    parser.add_argument("--output-json", type=Path, help="Write node 42 as an API fragment (not queued)")
    args = parser.parse_args()
    source = Path(args.source[1:]).read_text(encoding="utf-8") if args.source.startswith("@") else args.source
    payload = {
        "request_id": uuid.uuid4().hex,
        "source_text": source, "target_lang": args.target,
        "config": {"url": args.lm_url, "model_key": args.model_key,
                   "timeout_seconds": args.timeout, "max_tokens": args.max_tokens},
        "token": os.environ.get("COMFYUI_PLAMO_API_TOKEN", ""),
    }
    req = urllib.request.Request(
        args.comfy_url.rstrip("/") + "/plamo_translator/v1/translate",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout+10) as reply:
            result = json.load(reply)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read(65536))
            message = f"{error.get('code', exc.code)}: {error.get('message', 'Request failed')}"
        except (ValueError, UnicodeError):
            message = f"HTTP {exc.code}: Request failed"
        raise SystemExit(message) from None
    except urllib.error.URLError:
        raise SystemExit("Cannot connect to the ComfyUI server.") from None
    print(result.get("text", ""))
    if result.get("status") != "translated" or not isinstance(result.get("translation_state"), dict):
        raise SystemExit("Translation is incomplete; no accepted workflow fragment was saved.")
    if args.output_json:
        fragment = {"42": {"class_type": "PLaMoPromptTranslator", "inputs": {
            "source_text": source, "target_lang": args.target,
            "output_text": result["text"],
            "translation_state": json.dumps(result["translation_state"], ensure_ascii=False),
        }}}
        args.output_json.write_text(json.dumps(fragment, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(f"Saved API node fragment: {args.output_json}", file=sys.stderr)


if __name__ == "__main__":
    main()
