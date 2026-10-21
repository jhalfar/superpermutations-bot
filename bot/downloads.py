"""downloads.py - files from a link: allowed hosts (also after every redirect), https only, a size cap and deadlines;
file names and extensions of submissions.
"""
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import ui
from config import ALLOWED_EXT, ALLOWED_HOSTS, BIG_HINT, REDIRECT_HOSTS, VERSION, UserError


def clean_name(name):
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(str(name or "")))[:80] or "file"
    return base


def ext_of(name):
    low = name.lower()
    for e in ALLOWED_EXT:
        if low.endswith(e):
            return e
    return None


def safe_host(host):
    return ui.safe_text(host, 60)


def validate_url(url, hosts=ALLOWED_HOSTS):
    """-> normalized https URL on an allowed host (github.com /blob/ pages become /raw/), or UserError."""
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        raise UserError("that does not look like a URL", hint="Give a link such as https://github.com/user/repo/blob/main/word.txt.xz.")
    if u.scheme != "https" or u.username or u.password or (u.port not in (None, 443)):
        raise UserError("only plain https URLs (no login, no port) are accepted", hint="Use an https link without login or port.")
    host = (u.hostname or "").lower()
    if host not in hosts:
        raise UserError("host %s is not allowed" % safe_host(host), hint="Use a link on github.com, raw.githubusercontent.com or gitlab.com.")
    path = u.path
    if host == "github.com":
        path = re.sub(r"^(/[^/]+/[^/]+)/blob/", r"\1/raw/", path)
    elif host == "gitlab.com":
        path = path.replace("/-/blob/", "/-/raw/")
    return urllib.parse.urlunsplit(("https", host, path, u.query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def default_fetch(url, timeout):
    """one GET without following redirects; returns an object with status, headers, read(n), close()."""
    op = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "superpermutations-bot/" + VERSION})
    try:
        return op.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return e
        raise UserError("the server answered %d" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise UserError("download failed: %s" % getattr(e, "reason", e))


def download(url, dest, cfg, fetch=default_fetch):
    """stream a public file to dest: https, allowed hosts (also after each redirect, at most 4), size cap, deadlines.
    Blocking; call it with asyncio.to_thread. Returns the number of bytes."""
    t0 = time.time()
    url = validate_url(url)
    for _ in range(5):
        r = fetch(url, cfg.download_read_s)
        status = getattr(r, "status", None) or getattr(r, "code", 200)
        if 300 <= status < 400:
            loc = r.headers.get("Location")
            r.close()
            if not loc:
                raise UserError("redirect without a target")
            url = validate_url(urllib.parse.urljoin(url, loc), REDIRECT_HOSTS)
            continue
        if status != 200:
            r.close()
            raise UserError("the server answered %d" % status)
        try:
            cl = r.headers.get("Content-Length")
            if cl and int(cl) > cfg.max_download:
                raise UserError("file is larger than %d MB" % (cfg.max_download >> 20), hint=BIG_HINT)
            total = 0
            with open(dest, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    total += len(b)
                    if total > cfg.max_download:
                        raise UserError("file is larger than %d MB" % (cfg.max_download >> 20), hint=BIG_HINT)
                    if time.time() - t0 > cfg.download_total_s:
                        raise UserError("download took longer than %d s" % cfg.download_total_s)
                    f.write(b)
            return total
        except OSError as e:
            raise UserError("download failed: %s" % e)
        finally:
            r.close()
    raise UserError("too many redirects")
