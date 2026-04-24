#!/usr/bin/env python3
"""
Meta AI WS RE Client v10 — True Reverse Engineering: Capture → Locate → Edit → Replay

Captures the ACTUAL message frame (with user's typed text), finds the exact
byte offset of the prompt in the protobuf, then replays modified frames.
v10: Stealth cleanup (auto-delete conversations), faster response detection.
"""
import argparse, base64, json, os, re, sys, uuid, copy, time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except: pass

try:
    import blackboxprotobuf
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f"[!] {e}\n    pip install blackboxprotobuf playwright && playwright install chromium")
    sys.exit(1)

DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(DIR, "meta_profile")
CAPFILE = os.path.join(DIR, "captured_session.json")
URL = "https://www.meta.ai"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36")

g_ws_latest = None   # Only track the LATEST WebSocket (fixes 3x dupe)
g_ws_ids = {}        # All seen WS IDs (for capture mode)
g_raw_sent = []      # ALL raw CDP payloads (sent)
g_responses = []     # CDP received frames


# ═══════════════════════════════════════════════════════════
#  CDP SETUP
# ═══════════════════════════════════════════════════════════

def setup_cdp(page, ctx, capture_mode=False):
    """Set up CDP listeners. Accepts from ALL WebSocket connections.
    Deduplication in recv_response handles multi-WS duplicates."""
    global g_ws_latest, g_ws_ids, g_raw_sent, g_responses
    g_ws_latest, g_ws_ids = None, {}
    g_raw_sent, g_responses = [], []
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")

    def on_created(ev):
        global g_ws_latest
        u = ev.get("url", "")
        if "gateway.meta.ai" in u or "edge-chat" in u:
            rid = ev["requestId"]
            g_ws_ids[rid] = u
            g_ws_latest = rid

    def on_sent(ev):
        if ev.get("requestId") not in g_ws_ids: return
        data = ev.get("response", {}).get("payloadData", "")
        if data and len(data) > 5:
            g_raw_sent.append(data)

    def on_recv(ev):
        # Accept from ANY known Meta AI WebSocket connection
        if ev.get("requestId") not in g_ws_ids: return
        data = ev.get("response", {}).get("payloadData", "")
        if data:
            g_responses.append(data)

    cdp.on("Network.webSocketCreated", on_created)
    cdp.on("Network.webSocketFrameSent", on_sent)
    cdp.on("Network.webSocketFrameReceived", on_recv)
    return cdp


# ═══════════════════════════════════════════════════════════
#  LOGIN
# ═══════════════════════════════════════════════════════════

def do_login():
    print("=" * 55)
    print("  Log in to Meta AI, then close the browser.")
    print("=" * 55)
    input("  Press ENTER...")
    pw = sync_playwright().start()
    os.makedirs(PROFILE, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(PROFILE, headless=False,
        viewport={"width": 1280, "height": 800}, user_agent=UA)
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(URL, wait_until="networkidle", timeout=30000)
    except Exception as e:
        if "closed" not in str(e).lower():
            print(f"[!] {e}")
    # Wait for user to close browser
    try:
        pg.wait_for_event("close", timeout=0)
    except: pass
    try: ctx.close()
    except: pass
    pw.stop()
    print("[+] Profile saved.\n")


# ═══════════════════════════════════════════════════════════
#  CAPTURE — Find where the prompt lives in the protobuf
# ═══════════════════════════════════════════════════════════

def do_capture(test_text="test_marker_xyz123"):
    """Open browser, type a KNOWN message, capture the WS frame,
    then binary-search for the text to find exact protobuf location."""
    global g_raw_sent
    g_raw_sent = []

    print("\n" + "=" * 55)
    print(f"  CAPTURE MODE")
    print(f"  The browser will open and auto-type: '{test_text}'")
    print(f"  Then close automatically after capturing.")
    print("=" * 55)
    input("\n  Press ENTER...")

    pw = sync_playwright().start()
    os.makedirs(PROFILE, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(PROFILE, headless=False,
        viewport={"width": 1280, "height": 800}, user_agent=UA)
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    cdp = setup_cdp(pg, ctx, capture_mode=True)

    try:
        pg.goto(URL, wait_until="networkidle", timeout=30000)
    except Exception as e:
        if "closed" in str(e).lower():
            try: ctx.close()
            except: pass
            pw.stop()
            print("[!] Browser closed too early"); return None

    # Wait for any input element
    print("[*] Waiting for chat to be ready...")
    SELECTORS = [
        'textarea[data-testid="composer-input"]',
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="ask"]',
        'textarea[placeholder*="Meta"]',
        'textarea[placeholder*="message"]',
        'div[contenteditable="true"]',
        'textarea',
    ]
    ta = None
    for _ in range(25):
        for sel in SELECTORS:
            try:
                loc = pg.locator(sel).first
                if loc.is_visible():
                    ta = loc
                    print(f"[+] Found input: {sel}")
                    break
            except: pass
        if ta: break
        pg.wait_for_timeout(1000)

    if not ta:
        print("[!] Chat not ready — no input found")
        try: ctx.close()
        except: pass
        pw.stop(); return None

    # Wait for init frames to settle
    pg.wait_for_timeout(3000)
    pre_count = len(g_raw_sent)
    print(f"[*] {pre_count} init frames sent. Now typing test message...")

    # Type and send the test message
    ta.click()
    pg.wait_for_timeout(300)
    ta.fill(test_text)
    pg.wait_for_timeout(300)
    ta.press("Enter")
    print(f"[+] Sent: '{test_text}'")

    # Wait for response
    pg.wait_for_timeout(8000)

    post_count = len(g_raw_sent)
    new_frames = g_raw_sent[pre_count:]
    print(f"[+] {post_count - pre_count} new frames after typing")

    # Close browser
    try: ctx.close()
    except: pass
    pw.stop()

    # Analyze: search ALL frames for the test text
    print(f"\n{'=' * 55}")
    print(f"  ANALYSIS: Searching for '{test_text}' in {len(g_raw_sent)} frames")
    print(f"{'=' * 55}\n")

    result = None
    for i, raw_b64 in enumerate(g_raw_sent):
        try:
            raw = base64.b64decode(raw_b64)
        except:
            continue

        # Search for test text in raw bytes
        test_bytes = test_text.encode('utf-8')
        pos = raw.find(test_bytes)

        # Find JSON envelope
        idx = raw.find(b'{')
        if idx < 0: continue
        header = raw[:idx]
        try:
            depth, end = 0, -1
            jtext = raw[idx:].decode('utf-8', errors='ignore')
            for j, c in enumerate(jtext):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0: end = j + 1; break
            if end <= 0: continue
            env = json.loads(jtext[:end])
        except:
            continue

        is_new = i >= pre_count
        marker = " [AFTER TYPING]" if is_new else " [INIT]"

        if pos >= 0:
            print(f"  Frame #{i+1}{marker}: *** TEXT FOUND at byte {pos} ***")
        else:
            print(f"  Frame #{i+1}{marker}: text not in raw bytes")

        # Also search in decoded protobuf
        if "payload" in env:
            pb_raw = base64.b64decode(env["payload"])
            pb_pos = pb_raw.find(test_bytes)

            if pb_pos >= 0:
                print(f"    -> TEXT in protobuf at offset {pb_pos}!")
                # Decode protobuf and find exact field
                try:
                    msg, typedef = blackboxprotobuf.decode_message(pb_raw)
                    field_path = _find_text_field(msg, test_text)
                    if field_path:
                        print(f"    -> Protobuf field: {field_path}")
                except Exception as e:
                    print(f"    -> Protobuf decode error: {e}")

                result = {
                    "frame_idx": i,
                    "header_hex": header.hex(),
                    "envelope": env,
                    "proto_offset": pb_pos,
                    "field_path": field_path if 'field_path' in dir() else None,
                    "test_text": test_text,
                    "all_raw": g_raw_sent,
                    "typedef": None  # will be set below
                }
                # Save typedef
                try:
                    _, td = blackboxprotobuf.decode_message(pb_raw)
                    result["typedef_json"] = json.loads(json.dumps(td, default=str))
                except: pass

            # Dump all string fields
            try:
                msg, _ = blackboxprotobuf.decode_message(pb_raw)
                strings = []
                _collect_strings(msg, "", strings)
                for path, val in strings:
                    mark = " <<<" if test_text in val else ""
                    print(f"    {path}: '{val[:60]}'{mark}")
            except: pass

    if result:
        with open(CAPFILE, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\n[+] Capture saved to {CAPFILE}")
    else:
        print(f"\n[!] Test text '{test_text}' NOT FOUND in any frame!")
        print("    The message might be sent via HTTP, not WebSocket.")
        print("    Saving all raw frames for manual analysis...")
        with open(CAPFILE, "w") as f:
            json.dump({"all_raw": g_raw_sent, "test_text": test_text,
                       "init_count": pre_count, "not_found": True}, f, indent=2)

    return result


def _find_text_field(obj, text, path=""):
    """Recursively find which protobuf field contains the text."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}[{k}]"
            if isinstance(v, bytes):
                try:
                    if text in v.decode('utf-8', errors='ignore'):
                        return p
                except: pass
            elif isinstance(v, dict):
                r = _find_text_field(v, text, p)
                if r: return r
    return None


def _collect_strings(obj, path, out):
    """Collect all string fields from protobuf."""
    if isinstance(obj, dict):
        for k, v in sorted(obj.items()):
            p = f"{path}[{k}]"
            if isinstance(v, bytes):
                try:
                    t = v.decode('utf-8')
                    if t.isprintable() and len(t) > 1:
                        out.append((p, t))
                except: pass
            elif isinstance(v, dict):
                _collect_strings(v, p, out)


# ═══════════════════════════════════════════════════════════
#  MODIFY + REPLAY
# ═══════════════════════════════════════════════════════════

def build_frame(session, new_prompt):
    """Edit the captured frame: replace test text with new prompt."""
    env = session["envelope"]
    field_path = session.get("field_path")
    test_text = session["test_text"]

    pb_raw = base64.b64decode(env["payload"])
    msg, typedef = blackboxprotobuf.decode_message(pb_raw)
    m = copy.deepcopy(msg)

    # Replace at the known field path
    if field_path:
        parts = re.findall(r'\[(\w+)\]', field_path)
        obj = m
        for p in parts[:-1]:
            obj = obj[p]
        old = obj[parts[-1]]
        obj[parts[-1]] = new_prompt.encode('utf-8')
    else:
        print("[!] No field path. Doing binary replacement in protobuf...")
        # Fallback: direct bytes replacement
        pb_raw = pb_raw.replace(test_text.encode(), new_prompt.encode())
        new_payload_b64 = base64.b64encode(pb_raw).decode()
        new_req_id = str(uuid.uuid4())
        new_env = json.dumps({"req-id": new_req_id,
                              "payload": new_payload_b64}, separators=(',', ':'))
        header = bytes.fromhex(session["header_hex"])
        return _build_binary(header, new_env)

    # Fresh req-id
    new_req_id = str(uuid.uuid4())
    try: m["1"]["6"] = new_req_id.encode()
    except: pass

    proto = blackboxprotobuf.encode_message(m, typedef)
    new_env = json.dumps({"req-id": new_req_id,
                          "payload": base64.b64encode(proto).decode()},
                         separators=(',', ':'))

    header = bytes.fromhex(session["header_hex"])
    return _build_binary(header, new_env)


def _build_binary(orig_header, json_str):
    """Build binary frame with corrected length header."""
    json_bytes = json_str.encode('utf-8')
    json_len = len(json_bytes)

    header = bytearray(orig_header)
    # Header format: [type:1][00 00][LE_u16 length at bytes 3-4][trailing...]
    if len(header) == 8:
        # Recalculate LE u16 at bytes 3-4
        # Original offset: orig_le_u16 - orig_json_len (usually 2)
        header[3] = (json_len + 2) & 0xFF
        header[4] = ((json_len + 2) >> 8) & 0xFF
    elif len(header) == 6:
        header[3] = json_len & 0xFF

    frame = bytes(header) + json_bytes
    return base64.b64encode(frame).decode()


def _build_handshake(session):
    """Build the handshake frame from the captured init frames."""
    all_raw = session.get("all_raw", [])
    for raw_b64 in all_raw:
        try:
            raw = base64.b64decode(raw_b64)
            idx = raw.find(b'{')
            if idx < 0: continue
            text = raw[idx:].decode('utf-8', errors='ignore')
            depth, end = 0, -1
            for j, c in enumerate(text):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0: end = j + 1; break
            if end <= 0: continue
            obj = json.loads(text[:end])
            if "x-dgw-app-client-payload-type" in obj:
                return raw_b64  # Return exact captured handshake
        except: continue
    return None


# ═══════════════════════════════════════════════════════════
#  SEND + RECEIVE via browser WS
# ═══════════════════════════════════════════════════════════

SEND_HOOK = """
window.__wsh={ws:null,rdy:false};
const _W=window.WebSocket;
window.WebSocket=function(u,p){
  const w=p?new _W(u,p):new _W(u);
  if(u.includes('gateway.meta.ai')||u.includes('edge-chat')){
    window.__wsh.ws=w;
    w.addEventListener('open',()=>{window.__wsh.rdy=true;});
  }
  return w;
};
window.WebSocket.prototype=_W.prototype;
window.WebSocket.CONNECTING=0;window.WebSocket.OPEN=1;
window.WebSocket.CLOSING=2;window.WebSocket.CLOSED=3;
"""


def send_binary(page, b64_data):
    return page.evaluate("""(b64)=>{
        const w=window.__wsh?window.__wsh.ws:null;
        if(!w||w.readyState!==1) return 'no_ws';
        const bin=atob(b64);
        const arr=new Uint8Array(bin.length);
        for(let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
        w.send(arr.buffer);
        return 'ok';
    }""", b64_data)


# Regex to strip Meta AI internal reference tags like {{IE_0}}f5fb{{/IE_0}}
_IE_TAG_RE = re.compile(r'\{\{IE_\d+\}\}[^{]*\{\{/IE_\d+\}\}')

def _clean_text(text):
    """Strip Meta internal markup from response text."""
    return _IE_TAG_RE.sub('', text).strip()


def recv_response(page, timeout=25, stream=True):
    """Receive and stream response text in real-time."""
    global g_responses
    parts = []
    full_text = ""
    stale = 0
    seen_deltas = set()

    for _ in range(timeout * 5):
        found = False
        if g_responses:
            batch = g_responses[:]
            g_responses = []
            for r in batch:
                results = _parse_frame(r)
                for parsed in results:
                    if parsed["type"] == "full":
                        cleaned = _clean_text(parsed["text"])
                        if len(cleaned) > 5:  # ignore empty/status frames
                            full_text = cleaned
                            found = True
                    elif parsed["type"] == "delta":
                        txt = parsed["text"]
                        if txt in seen_deltas:
                            continue
                        seen_deltas.add(txt)
                        parts.append(txt)
                        if stream:
                            sys.stdout.write(txt)
                            sys.stdout.flush()
                        found = True

        if found:
            stale = 0
        else:
            stale += 1
            if stale > 6 and (parts or full_text): break  # 1.2s silence

        # If full response already received, accelerate exit
        if full_text and stale > 2: break

        try: page.wait_for_timeout(200)
        except: break

    if stream:
        if parts:
            print()  # newline after streamed deltas
        elif full_text:
            # Response arrived as "full" (not streamed) — print it now
            sys.stdout.write(full_text + "\n")
            sys.stdout.flush()

    result = full_text if full_text else "".join(parts)
    return _clean_text(result)


def _parse_frame(data):
    """Parse a WS frame and return ALL results (list), not just the first."""
    results = _try_json_all(data)
    if results: return results
    try:
        raw = base64.b64decode(data)
        text = raw.decode('utf-8', errors='ignore')
        idx = text.find('{')
        if idx >= 0:
            results = _try_json_all(text[idx:])
            if results: return results
    except: pass
    return []


def _extract_json_objects(text):
    """Extract all top-level JSON objects from text using brace-counting.
    Handles unlimited nesting depth."""
    objects = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(text)):
                if text[j] == '{': depth += 1
                elif text[j] == '}': depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:j+1])
                        objects.append(obj)
                    except:
                        pass
                    i = j + 1
                    break
            else:
                i += 1
        else:
            i += 1
    return objects


def _try_json_all(text):
    """Parse ALL delta/full results from text. Returns a list."""
    if not isinstance(text, str) or '{' not in text: return []
    results = []
    for o in _extract_json_objects(text):
        if o.get("type") == "patch":
            for op in o.get("operations", []):
                if op.get("op") == "delta" and "text" in op.get("path", ""):
                    results.append({"type": "delta", "text": op.get("value", "")})
        if o.get("type") == "full" and "response" in o:
            for sec in o["response"].get("sections", []):
                prim = sec.get("view_model", {}).get("primitive", {})
                if "text" in prim:
                    results.append({"type": "full", "text": prim["text"]})
    return results


# ═══════════════════════════════════════════════════════════
#  CONVERSATION CLEANUP (stealth)
# ═══════════════════════════════════════════════════════════

# JavaScript to delete a conversation from Meta AI history
_DELETE_CONV_JS = """(convId) => {
    // Try multiple known delete mutation doc_ids
    const deleteDocIds = [
        '9fe498de8e4fd637fd50052ea0158db5',
        'b12a6da72a8adb3a9041f44e1ea8df08',
        'a0d7564f78ea89e0a33b98ca67292489'
    ];
    const doDelete = async () => {
        for (const docId of deleteDocIds) {
            try {
                const resp = await fetch('/api/graphql', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        doc_id: docId,
                        variables: {input: {conversationId: convId}}
                    })
                });
                if (resp.ok) return 'deleted';
            } catch(e) {}
        }
        // Fallback: try conversationId as top-level variable
        for (const docId of deleteDocIds) {
            try {
                const resp = await fetch('/api/graphql', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        doc_id: docId,
                        variables: {conversationId: convId}
                    })
                });
                if (resp.ok) return 'deleted';
            } catch(e) {}
        }
        return 'failed';
    };
    return doDelete();
}"""

# JavaScript to get all conversation IDs from sidebar
_LIST_CONVS_JS = """() => {
    // Extract conversation IDs from sidebar links
    const links = document.querySelectorAll('a[href*="/prompt/"]');
    const ids = new Set();
    links.forEach(a => {
        const m = a.href.match(/\\/prompt\\/([a-f0-9-]{36})/);
        if (m) ids.add(m[1]);
    });
    return Array.from(ids);
}"""


def _extract_conv_id(page):
    """Extract conversation ID from the current page URL."""
    try:
        url = page.url
        # URL format: https://www.meta.ai/prompt/{conv_id}?...
        m = re.search(r'/prompt/([a-f0-9-]{36})', url)
        if m:
            return m.group(1)
    except: pass
    return None


def _cleanup_conversations(page, conv_ids):
    """Delete conversations from Meta AI history."""
    if not conv_ids:
        return
    for cid in conv_ids:
        try:
            result = page.evaluate(_DELETE_CONV_JS, cid)
        except:
            pass


# ═══════════════════════════════════════════════════════════
#  PROMPT MODE — Edit + Replay + Stealth Cleanup
# ═══════════════════════════════════════════════════════════

def do_prompt(session, args, stealth=True):
    global g_raw_sent, g_responses, g_ws_ids
    g_raw_sent, g_responses, g_ws_ids = [], [], {}

    handshake_b64 = _build_handshake(session)
    if not handshake_b64:
        print("[!] No handshake frame found in capture"); return

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(PROFILE, headless=True,
        viewport={"width": 1280, "height": 800}, user_agent=UA)
    ctx.add_init_script(SEND_HOOK)
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    cdp = setup_cdp(pg, ctx)

    try:
        pg.goto(URL, wait_until="networkidle", timeout=30000)
    except Exception as e:
        if "closed" in str(e).lower():
            print("[!] Browser closed"); pw.stop(); return

    # Wait for WS
    for _ in range(20):
        if g_ws_ids: break
        pg.wait_for_timeout(1000)
    if not g_ws_ids:
        print("[!] No WS. Run --login")
        try: ctx.close()
        except: pass
        pw.stop(); return

    # Wait for JS hook
    for _ in range(10):
        try:
            if pg.evaluate("()=>!!(window.__wsh&&window.__wsh.rdy)"): break
        except: break
        pg.wait_for_timeout(1000)

    stealth_label = "stealth ON" if stealth else "stealth OFF"
    print(f"[+] Ready. ({stealth_label})\n")

    session_conv_ids = set()  # track all conversation IDs for cleanup

    try:
        p = args.prompt
        while True:
            if p: inp = p; p = None
            else:
                try: inp = input("  You> ").strip()
                except: break
                if not inp or inp.lower() in ("exit", "quit", "q"): break

            # Build modified frame (silent)
            payload_b64 = build_frame(session, inp)
            if not payload_b64: continue

            g_responses = []

            # Send handshake + payload
            r1 = send_binary(pg, handshake_b64)
            if r1 != "ok":
                print(f"[!] Handshake failed: {r1}"); continue
            pg.wait_for_timeout(100)
            r2 = send_binary(pg, payload_b64)
            if r2 != "ok":
                print(f"[!] Payload failed: {r2}"); continue

            print()  # blank line before response
            resp = recv_response(pg, stream=True)

            # Extract conversation ID after first response
            cid = _extract_conv_id(pg)
            if cid:
                session_conv_ids.add(cid)

            if resp:
                with open(os.path.join(DIR, "last_response.txt"), "w",
                          encoding="utf-8") as f:
                    f.write(resp)
            else:
                print("[!] No response received")

            if args.prompt: break
            print()
    finally:
        # Stealth cleanup: delete all conversations created in this session
        if stealth and session_conv_ids:
            try:
                # Also grab any conversation IDs from sidebar
                try:
                    sidebar_ids = pg.evaluate(_LIST_CONVS_JS)
                    for sid in (sidebar_ids or []):
                        session_conv_ids.add(sid)
                except: pass

                _cleanup_conversations(pg, session_conv_ids)
            except: pass

        try: ctx.close()
        except: pass
        pw.stop()


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print()
    print("  ======================================================")
    print("   Meta AI RE Client v10 -- Stealth + Live Streaming     ")
    print("  ======================================================")
    print()

    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="Browser login")
    ap.add_argument("--capture", action="store_true", help="Capture message frame")
    ap.add_argument("--prompt", type=str, help="Send modified prompt")
    ap.add_argument("--no-stealth", action="store_true",
                    help="Keep conversations in browser history")
    args = ap.parse_args()

    if args.login:
        do_login(); return

    if not os.path.exists(PROFILE) or not os.listdir(PROFILE):
        print("[!] Run --login first"); return

    if args.capture or not os.path.exists(CAPFILE):
        result = do_capture()
        if not result:
            print("[!] Capture failed"); return
        if args.capture:
            print("\n[+] Now run: --prompt \"your question\""); return

    # Load capture
    with open(CAPFILE) as f:
        session = json.load(f)

    if session.get("not_found"):
        print("[!] Previous capture didn't find text in WS frames.")
        print("    Run --capture again"); return

    stealth = not args.no_stealth
    print(f"[+] Loaded capture (field: {session.get('field_path', '?')})")
    do_prompt(session, args, stealth=stealth)


if __name__ == "__main__":
    main()
