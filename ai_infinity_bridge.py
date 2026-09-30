#!/usr/bin/env python3
"""AI Infinity local browser/device bridge.

One-time pairing is performed with a short-lived code. The returned bridge token
is kept only in the local runtime. The bridge polls AI Infinity for explicitly
queued, allowlisted commands and never executes arbitrary shell/code commands.

Browser mode: optional Playwright.
Device mode: standard-library notifications/open-url only unless the platform
provides a safe integration of its own.
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.error, urllib.request, webbrowser
from typing import Any, Dict

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


def http_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=raw, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(512 * 1024).decode("utf-8", "replace")
        return json.loads(data or "{}")


def claim(server: str, kind: str, name: str, code: str) -> Dict[str, Any]:
    return http_json(server + "/activation/pair/claim", {
        "code": code, "kind": kind, "name": name,
        "capabilities": capabilities(kind),
        "metadata": {"client":"ai_infinity_bridge", "python": sys.version.split()[0]},
    })


def capabilities(kind: str):
    if kind == "browser":
        return (["browser.web","browser.navigate","browser.click","browser.type","browser.submit","browser.extract"] if sync_playwright else ["browser.web.unavailable"])
    return ["device.ping", "device.info", "device.open_url", "device.notify"]


def heartbeat(server, runtime_id, token, kind):
    return http_json(server + "/bridge/heartbeat", {
        "runtime_id": runtime_id, "bridge_token": token,
        "capabilities": capabilities(kind), "metadata":{"client":"ai_infinity_bridge"}, "healthy": True,
    })


def result(server, runtime_id, token, command_id, status, value=None, error=""):
    return http_json(server + "/bridge/result", {
        "runtime_id": runtime_id, "bridge_token": token,
        "command_id": command_id, "status": status,
        "result": value if isinstance(value, dict) else {"value": value}, "error": error,
    })


class Browser:
    def __init__(self):
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed. Install playwright and Chromium for browser mode.")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=False)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def close(self):
        try: self.browser.close()
        finally: self.pw.stop()

    def run(self, action: str, p: Dict[str, Any]) -> Dict[str, Any]:
        if action == "browser.navigate":
            url = str(p.get("url") or "")
            if not url.startswith(("http://", "https://")): raise ValueError("only http/https navigation is allowed")
            self.page.goto(url, wait_until="domcontentloaded", timeout=int(p.get("timeout_ms", 30000)))
            return {"url": self.page.url, "title": self.page.title()}
        if action == "browser.click":
            selector = str(p.get("selector") or "")
            if not selector: raise ValueError("selector required")
            self.page.locator(selector).first.click(timeout=int(p.get("timeout_ms", 30000)))
            return {"url": self.page.url, "clicked": selector}
        if action == "browser.type":
            selector = str(p.get("selector") or "")
            text = str(p.get("text") or "")
            if not selector: raise ValueError("selector required")
            if len(text) > 10000: raise ValueError("text too large")
            self.page.locator(selector).first.fill(text, timeout=int(p.get("timeout_ms", 30000)))
            return {"url": self.page.url, "typed": True, "selector": selector}
        if action == "browser.submit":
            selector = str(p.get("selector") or "")
            if not selector: raise ValueError("selector required")
            self.page.locator(selector).first.press("Enter", timeout=int(p.get("timeout_ms", 30000)))
            return {"url": self.page.url, "submitted": selector}
        if action == "browser.extract":
            selector = str(p.get("selector") or "body")
            text = self.page.locator(selector).inner_text(timeout=int(p.get("timeout_ms", 30000)))
            return {"url": self.page.url, "text": text[:50000]}
        raise ValueError("unsupported browser action")


def device_run(action: str, p: Dict[str, Any]) -> Dict[str, Any]:
    if action == "device.ping": return {"ok": True}
    if action == "device.info": return {"platform": sys.platform, "python": sys.version.split()[0]}
    if action == "device.open_url":
        url = str(p.get("url") or "")
        if not url.startswith(("http://", "https://")): raise ValueError("only http/https URLs are allowed")
        webbrowser.open(url)
        return {"opened": True, "url": url}
    if action == "device.notify":
        # Cross-platform safe fallback: visible local message, no shell execution.
        message = str(p.get("text") or "AI Infinity notification")[:2000]
        print("[AI Infinity]", message, flush=True)
        return {"notified": True, "text": message}
    raise ValueError("unsupported device action")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--kind", choices=["browser", "device"], required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()
    server = args.server.rstrip("/")
    pair = claim(server, args.kind, args.name, args.code)
    runtime_id, token = pair["runtime_id"], pair["bridge_token"]
    print(json.dumps({"paired": True, "runtime_id": runtime_id, "kind": args.kind}, indent=2), flush=True)
    browser = Browser() if args.kind == "browser" else None
    try:
        while True:
            heartbeat(server, runtime_id, token, args.kind)
            poll = http_json(server + "/bridge/poll", {"runtime_id":runtime_id, "bridge_token":token})
            if poll.get("status") == "command":
                cmd = poll["command"]
                cid, action, payload = cmd["id"], cmd["action"], cmd.get("command") or {}
                try:
                    value = browser.run(action, payload) if browser else device_run(action, payload)
                    result(server, runtime_id, token, cid, "completed", value)
                except Exception as exc:
                    try: result(server, runtime_id, token, cid, "failed", {"error":str(exc)[:500]}, str(exc)[:500])
                    except Exception: pass
            time.sleep(max(0.5, min(args.poll, 30)))
    except KeyboardInterrupt:
        pass
    finally:
        if browser: browser.close()

if __name__ == "__main__": main()
