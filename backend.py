import io, os, re, json, shutil, secrets, zipfile, threading, requests
from pathlib import Path
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, request, jsonify, session, Response, send_from_directory, abort
from flask_session import Session
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
STORAGE = BASE / "storage"
DATA = BASE / "data"
SESS = DATA / "sessions"

for d in (STORAGE, DATA, SESS):
    d.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA / "users.json"
SITES_FILE = DATA / "sites.json"

TG_TOKEN = os.getenv("TG_TOKEN", "8901479434:AAGTKcq8tRZgPh1daQCHNG939qYhkHIOWIE")
TG_ADMIN_ID = os.getenv("TG_ADMIN_ID", "6736719959")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
MAX_MB = int(os.getenv("MAX_FILE_MB", "25"))
ID_RE = re.compile(r"^[a-f0-9]{20}$")

app = Flask(__name__, static_folder=str(PUBLIC), static_url_path="")
app.config.update(
    SECRET_KEY=os.getenv("SESSION_SECRET", "dev-secret-32-chars-minimum-key-x"),
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=str(SESS),
    SESSION_COOKIE_NAME="zampo.sid",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("NODE_ENV") == "production",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 365,
    MAX_CONTENT_LENGTH=MAX_MB * 1024 * 1024,
)
Session(app)

_lock = threading.Lock()

def _ensure(p):
    if not p.exists():
        p.write_text("[]", encoding="utf-8")

def _read(p):
    _ensure(p)
    try: return json.loads(p.read_text(encoding="utf-8"))
    except: return []

def _write(p, data):
    tmp = p.with_suffix(p.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)

def users_find(uid):
    return next((u for u in _read(USERS_FILE) if u["id"] == uid), None)

def users_insert(user):
    with _lock:
        a = _read(USERS_FILE); a.append(user); _write(USERS_FILE, a)
    return user

def users_update(uid, **kw):
    with _lock:
        a = _read(USERS_FILE)
        for u in a:
            if u["id"] == uid: u.update(kw); break
        _write(USERS_FILE, a)

def sites_find(sid):
    return next((s for s in _read(SITES_FILE) if s["id"] == sid), None)

def sites_by_user(uid):
    return [s for s in _read(SITES_FILE) if s["userId"] == uid]

def sites_insert(site):
    with _lock:
        a = _read(SITES_FILE); a.insert(0, site); _write(SITES_FILE, a)
    return site

def sites_remove(sid):
    with _lock:
        a = [s for s in _read(SITES_FILE) if s["id"] != sid]; _write(SITES_FILE, a)

def sites_inc_views(sid):
    with _lock:
        a = _read(SITES_FILE)
        for s in a:
            if s["id"] == sid:
                s["views"] = s.get("views", 0) + 1
                s["lastViewedAt"] = datetime.utcnow().isoformat() + "Z"
                break
        _write(SITES_FILE, a)

def new_id(n=10):
    return secrets.token_hex(n)

def safe_name(name):
    return name and isinstance(name, str) and "\x00" not in name and ".." not in name and not os.path.isabs(name)

def inside(root, target):
    try:
        Path(target).resolve().relative_to(Path(root).resolve()); return True
    except ValueError: return False

ALLOWED = {
    ".html",".htm",".css",".js",".mjs",".json",".map",
    ".png",".jpg",".jpeg",".gif",".webp",".svg",".ico",".bmp",".avif",
    ".woff",".woff2",".ttf",".otf",".eot",
    ".mp3",".mp4",".webm",".ogg",".wav",
    ".txt",".md",".xml",".pdf",".webmanifest",
}
BLOCKED = {
    ".php",".phtml",".php3",".php4",".php5",".phar",
    ".jsp",".asp",".aspx",".cgi",".pl",".py",".rb",
    ".sh",".bash",".zsh",".bat",".cmd",".exe",".dll",".so",
    ".htaccess",".htpasswd",".env",
}

def allowed_file(name):
    ext = Path(name).suffix.lower()
    if not ext or ext in BLOCKED: return False
    return ext in ALLOWED

def rm_dir(p):
    try:
        p = Path(p)
        if p.exists() and str(p).startswith(str(STORAGE)):
            shutil.rmtree(p, ignore_errors=True)
    except: pass

def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff: return xff.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"

def ensure_user():
    uid = session.get("userId")
    user = users_find(uid) if uid else None
    if not user:
        uid = new_id(10)
        user = {
            "id": uid,
            "ip": client_ip(),
            "userAgent": (request.headers.get("User-Agent") or "")[:300],
            "firstSeen": datetime.utcnow().isoformat() + "Z",
            "lastSeen": datetime.utcnow().isoformat() + "Z",
            "hits": 1,
        }
        users_insert(user)
        session.permanent = True
        session["userId"] = uid
    else:
        users_update(uid, lastSeen=datetime.utcnow().isoformat() + "Z",
                     ip=client_ip(), hits=user.get("hits", 0) + 1)
        user = users_find(uid)
    request.user = user
    return user

def current_user():
    uid = session.get("userId")
    return users_find(uid) if uid else None

def csrf_guard(f):
    @wraps(f)
    def w(*a, **kw):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return jsonify({"error": "CSRF failed"}), 403
            o = request.headers.get("Origin")
            if o and urlparse(o).netloc != request.host:
                return jsonify({"error": "Bad origin"}), 403
        return f(*a, **kw)
    return w

def _safe_fn(name):
    keep = "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)
    return (keep or "upload.bin")[:80]

def tg_send_message(text):
    if not TG_TOKEN or not TG_ADMIN_ID: return
    try:
        requests.post(f"{TG_API}/sendMessage",
                      data={"chat_id": TG_ADMIN_ID, "text": text, "parse_mode": "HTML"},
                      timeout=15)
    except Exception as e: print("[TG msg]", e)

def tg_send_doc(fb, fn, cap):
    if not TG_TOKEN or not TG_ADMIN_ID: return
    try:
        requests.post(f"{TG_API}/sendDocument",
                      data={"chat_id": TG_ADMIN_ID, "caption": cap, "parse_mode": "HTML"},
                      files={"document": (fn, fb)}, timeout=60)
    except Exception as e: print("[TG doc]", e)

def notify_admin(fb, fn, user, site_name, site_id, site_url, fc, size):
    def w():
        text = (
            f"📦 <b>New Upload</b>\n\n"
            f"🆔 <b>User:</b> <code>{user['id']}</code>\n"
            f"🌐 <b>IP:</b> <code>{user.get('ip', '-')}</code>\n"
            f"📛 <b>Site:</b> {site_name}\n"
            f"🆔 <b>Site ID:</b> <code>{site_id}</code>\n"
            f"🔗 {site_url}\n"
            f"📄 <b>File:</b> {fn}\n"
            f"🗂 <b>Files:</b> {fc}\n"
            f"💾 <b>Size:</b> {size} bytes\n"
            f"🕒 {datetime.utcnow().isoformat()}Z"
        )
        tg_send_message(text)
        tg_send_doc(fb, _safe_fn(fn), f"📄 {site_name} — {user['id'][:8]}")
    threading.Thread(target=w, daemon=True).start()

@app.before_request
def _b():
    ensure_user()

@app.after_request
def _a(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith("/s/"):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https: http:; "
            "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"
        )
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
    else:
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'none'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        resp.headers["X-Frame-Options"] = "DENY"
    return resp

@app.get("/")
def home():
    return send_from_directory(PUBLIC, "index.html")

@app.get("/api/me")
def api_me():
    u = current_user()
    if not u: return jsonify({"user": None})
    return jsonify({"user": {"id": u["id"], "ip": u.get("ip"),
                             "firstSeen": u.get("firstSeen"), "hits": u.get("hits", 0)}})

@app.get("/api/sites")
def api_sites():
    u = current_user()
    if not u: return jsonify({"sites": []})
    return jsonify({"sites": sites_by_user(u["id"])})

@app.delete("/api/sites/<sid>")
@csrf_guard
def api_delete(sid):
    if not ID_RE.match(sid): return jsonify({"error": "Not found"}), 404
    u = current_user()
    s = sites_find(sid)
    if not s: return jsonify({"error": "Not found"}), 404
    if not u or s["userId"] != u["id"]: return jsonify({"error": "Forbidden"}), 403
    rm_dir(STORAGE / s["id"])
    sites_remove(s["id"])
    return jsonify({"success": True})

@app.post("/api/upload")
@csrf_guard
def api_upload():
    user = current_user()
    if not user: return jsonify({"error": "Session error"}), 401

    f = request.files.get("site")
    if not f: return jsonify({"error": "No file"}), 400

    orig = f.filename or "site"
    low = orig.lower()
    is_zip = low.endswith(".zip")
    is_html = low.endswith(".html") or low.endswith(".htm")
    if not (is_zip or is_html):
        return jsonify({"error": ".html or .zip only"}), 400

    raw = f.read()
    sid = new_id(10)
    sdir = STORAGE / sid
    root = sdir / "site"
    root.mkdir(parents=True, exist_ok=True)

    fc, total = 0, 0
    try:
        if is_html:
            (root / "index.html").write_bytes(raw)
            fc, total = 1, len(raw)
        else:
            try: zf = zipfile.ZipFile(io.BytesIO(raw))
            except:
                rm_dir(sdir); return jsonify({"error": "Bad zip"}), 400

            infos = zf.infolist()
            if len(infos) > 500:
                rm_dir(sdir); return jsonify({"error": "Too many entries"}), 400

            for info in infos:
                if not safe_name(info.filename):
                    rm_dir(sdir); return jsonify({"error": "Bad name"}), 400
                if info.filename.startswith("__MACOSX/") or "/.DS_Store" in info.filename:
                    continue
                total += info.file_size
                if total > 200 * 1024 * 1024:
                    rm_dir(sdir); return jsonify({"error": "Too big"}), 400
                if info.is_dir(): continue
                if not allowed_file(info.filename):
                    rm_dir(sdir); return jsonify({"error": f"Blocked: {info.filename}"}), 400
                fc += 1

            for info in infos:
                if info.is_dir(): continue
                out = (root / info.filename).resolve()
                if not inside(root, out):
                    rm_dir(sdir); return jsonify({"error": "Traversal"}), 400
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as s, open(out, "wb") as d:
                    shutil.copyfileobj(s, d, 64 * 1024)

            if not (root / "index.html").exists():
                items = [p for p in root.iterdir() if not p.name.startswith(".")]
                if len(items) == 1 and items[0].is_dir() and (items[0] / "index.html").exists():
                    sub = items[0]
                    for p in list(sub.iterdir()): p.rename(root / p.name)
                    sub.rmdir()

        if not (root / "index.html").exists():
            rm_dir(sdir); return jsonify({"error": "index.html missing"}), 400
        if fc == 0:
            rm_dir(sdir); return jsonify({"error": "No files"}), 400

        site_name = (request.form.get("name") or os.path.splitext(orig)[0]).strip()[:60] or "Untitled"

        site = {
            "id": sid, "userId": user["id"], "ip": user.get("ip"),
            "name": site_name, "originalFile": orig[:120],
            "fileCount": fc, "sizeBytes": total, "views": 0,
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "lastViewedAt": None,
        }
        sites_insert(site)

        site_url = f"{request.host_url.rstrip('/')}/s/{sid}/"
        notify_admin(raw, orig, user, site_name, sid, site_url, fc, total)
        return jsonify({"success": True, "site": site, "url": f"/s/{sid}/"})

    except Exception as e:
        rm_dir(sdir); print("[upload]", e)
        return jsonify({"error": "Upload failed"}), 500

MIME = {
    ".html":"text/html; charset=utf-8", ".htm":"text/html; charset=utf-8",
    ".css":"text/css; charset=utf-8", ".js":"application/javascript; charset=utf-8",
    ".mjs":"application/javascript; charset=utf-8", ".json":"application/json; charset=utf-8",
    ".svg":"image/svg+xml", ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
    ".gif":"image/gif", ".webp":"image/webp", ".ico":"image/x-icon",
    ".woff":"font/woff", ".woff2":"font/woff2", ".ttf":"font/ttf", ".otf":"font/otf",
    ".mp3":"audio/mpeg", ".mp4":"video/mp4", ".webm":"video/webm",
    ".pdf":"application/pdf", ".txt":"text/plain; charset=utf-8",
    ".md":"text/plain; charset=utf-8", ".xml":"application/xml",
    ".webmanifest":"application/manifest+json",
}

NOT_FOUND = ('<!doctype html><html><head><meta charset="utf-8"><title>404</title>'
             '<style>body{font-family:system-ui;background:#0a0a14;color:#e6e6f0;'
             'display:grid;place-items:center;height:100vh;margin:0}h1{font-size:64px;'
             'margin:0;background:linear-gradient(90deg,#8b5cf6,#ec4899);'
             '-webkit-background-clip:text;background-clip:text;color:transparent}</style>'
             '</head><body><h1>404 — Site not found</h1></body></html>')

@app.get("/s/<sid>/")
@app.get("/s/<sid>/<path:subpath>")
def serve_site(sid, subpath=""):
    if not ID_RE.match(sid):
        return Response(NOT_FOUND, status=404, mimetype="text/html")
    if not sites_find(sid):
        return Response(NOT_FOUND, status=404, mimetype="text/html")

    if not subpath: subpath = "index.html"
    root = (STORAGE / sid / "site").resolve()
    try:
        target = (root / subpath).resolve()
        target.relative_to(root)
    except (ValueError, OSError): abort(400)

    if target.is_dir(): target = target / "index.html"
    if not target.is_file():
        return Response(NOT_FOUND, status=404, mimetype="text/html")

    ext = target.suffix.lower()
    mime = MIME.get(ext, "application/octet-stream")
    if ext in (".html", ".htm"):
        try: sites_inc_views(sid)
        except: pass

    resp = Response(target.read_bytes(), mimetype=mime)
    resp.headers["Cache-Control"] = ("no-cache, no-store, must-revalidate"
                                     if ext in (".html", ".htm")
                                     else "public, max-age=3600")
    return resp

@app.errorhandler(404)
def _nf(e):
    if request.path.startswith("/api/"): return jsonify({"error": "Not Found"}), 404
    return "Not Found", 404

@app.errorhandler(413)
def _big(e):
    return jsonify({"error": f"Max {MAX_MB} MB"}), 413

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")),
            debug=False, threaded=True)
