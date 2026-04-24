#!/usr/bin/env python3
"""Capture the FULL GraphQL message body from Meta AI."""
import os, sys, json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except: pass

from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(DIR, "meta_profile")
URL = "https://www.meta.ai"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

print("\n  Capture GraphQL message body")
print("  Type a message like 'What is 2+2?' then close browser.")
input("  Press ENTER...")

graphql_calls = []

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(PROFILE, headless=False,
    viewport={"width": 1280, "height": 800}, user_agent=UA)
pg = ctx.pages[0] if ctx.pages else ctx.new_page()
cdp = ctx.new_cdp_session(pg)
cdp.send("Network.enable")

def on_req(ev):
    url = ev.get("request", {}).get("url", "")
    if "/api/graphql" not in url: return
    body = ev.get("request", {}).get("postData", "")
    headers = ev.get("request", {}).get("headers", {})
    if not body: return
    try:
        obj = json.loads(body)
    except:
        return
    graphql_calls.append({"url": url, "body": obj, "headers": headers})
    doc_id = obj.get("doc_id", "?")
    print(f"  [GQL] doc_id={doc_id}, body={len(body)} chars")

cdp.on("Network.requestWillBeSent", on_req)
pg.goto(URL, wait_until="networkidle")
print("\n[*] Type your message and send it, then close browser.\n")

try: pg.wait_for_event("close", timeout=0)
except: pass
try: ctx.close()
except: pass
pw.stop()

# Find the message-send call
print(f"\n{'='*60}")
print(f"Captured {len(graphql_calls)} GraphQL calls")
for i, c in enumerate(graphql_calls):
    doc = c["body"].get("doc_id", "?")
    variables = c["body"].get("variables", {})
    has_input = "input" in variables
    has_msg = "messageId" in variables.get("input", {}) if has_input else False
    marker = " *** MESSAGE SEND ***" if has_msg else ""
    print(f"  #{i+1}: doc_id={doc[:16]}...  input={has_input}{marker}")
    if has_msg:
        print(f"\n  FULL MESSAGE BODY:")
        print(json.dumps(c["body"], indent=2))
        print(f"\n  HEADERS:")
        for k, v in c["headers"].items():
            if k.lower() in ("x-fb-lsd", "x-csrftoken", "x-fb-friendly-name",
                              "content-type", "x-asbd-id", "cookie"):
                print(f"    {k}: {v[:60]}...")

out = os.path.join(DIR, "graphql_capture.json")
with open(out, "w") as f:
    json.dump(graphql_calls, f, indent=2)
print(f"\n[+] Saved to {out}")
