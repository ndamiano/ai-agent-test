#!/usr/bin/env python3
"""Per-model sampling settings, applied between a harness and llama-server.

Every harness hardcodes its own sampling (build_iface sends temperature=0.3 for every call),
and every model wants something different: Ornith-1.0-9B needs its reasoning block ON and
dies at temperature 0.3 (measured: 20000 tokens of thinking, zero content, three times),
while Qwen3.6 wants thinking OFF. A grid that runs every model at one setting measures the
setting, not the model. This rewrites the request on the way past so each model runs at the
settings its authors published, without touching a harness.

  sampler_proxy.py --port 8081 --upstream http://localhost:8080

Point LLM_BASE (naked/iface) or the worker's --target (maestro) at it.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG = Path(__file__).with_name("model_settings.json")


def load_rules():
    if not CONFIG.exists():
        return {"default": {}, "models": {}}
    return json.loads(CONFIG.read_text())


def settings_for(model, rules, section="request"):
    """Longest matching pattern wins, so 'Qwen3.6-27B' can differ from 'Qwen3.6-35B-A3B'."""
    out = dict((rules.get("default") or {}).get(section) or {})
    best = ""
    for pat, cfg in (rules.get("models") or {}).items():
        if re.search(pat, model or "") and len(pat) > len(best):
            best, hit = pat, cfg
    if best:
        out.update(hit.get(section) or {})
    return {k: v for k, v in out.items() if not k.startswith("_")}


def server_args_for(model, rules):
    out = list((rules.get("default") or {}).get("server_args") or [])
    best = ""
    for pat, cfg in (rules.get("models") or {}).items():
        if re.search(pat, model or "") and len(pat) > len(best):
            best, hit = pat, cfg
    if best and hit.get("server_args") is not None:
        out = list(hit["server_args"])
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self, body, upstream=None):
        req = urllib.request.Request((upstream or self.server.upstream) + self.path, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=self.server.timeout_s) as r:
                data, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path.endswith("/chat/completions") or self.path.endswith("/responses"):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._proxy(body)
            rules = load_rules()          # re-read per request, so edits apply without a restart
            cfg = settings_for(payload.get("model", ""), rules)
            # A model may be served by a different ENGINE (ninfer runs the same Qwen3.6-27B weights
            # 2.8x faster than llama.cpp), so the upstream and the model id it answers to are part
            # of the per-model settings. The harness keeps talking to this one port either way.
            upstream = cfg.pop("upstream", None)
            for k, v in cfg.items():
                if k == "max_tokens_floor":
                    # A reasoning model spends its budget thinking before it answers; too low a
                    # ceiling truncates the answer that the thinking was for.
                    if payload.get("max_tokens", 0) < v:
                        payload["max_tokens"] = v
                else:
                    payload[k] = v
            body = json.dumps(payload).encode()
            if upstream:
                return self._proxy(body, upstream)
        self._proxy(body)

    def do_GET(self):
        req = urllib.request.Request(self.server.upstream + self.path)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--upstream", default="http://localhost:8080")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.upstream, srv.timeout_s = a.upstream.rstrip("/"), a.timeout
    print(f"sampler proxy :{a.port} -> {a.upstream}  rules={CONFIG}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
