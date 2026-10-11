#!/usr/bin/env python3
"""Offline SEO and AEO audit for the CCSwitcherX website (standard library only).

Run from the repo root:   python -I scripts/check-site.py [--base docs]

Prints one line per check: ok, WARN or FAIL, with the reason. Exit code 1 if any FAIL.
Missing files are reported as FAIL with "missing file", so they are easy to tell apart
from real problems in files that exist.
"""
import argparse
import html.parser
import json
import os
import re
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime

SITE = "https://mhjoy99.github.io/CCSwitcherX/"
KNOWN_HOSTS = {"github.com", "mhjoy99.github.io"}
REPO_PATH = "/mhjoy99/ccswitcherx"  # compared lower-case
ALLOWED_EXTERNAL_HOSTS = {"static.cloudflareinsights.com"}
CRAWLERS = ["GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
            "anthropic-ai", "PerplexityBot", "Google-Extended", "CCBot", "Applebot-Extended"]
HYPE_RE = re.compile(r"\b(revolutionary|seamless\w*|supercharg\w*|unlock\w*|game-changing)\b", re.I)
CLAIM_RE = re.compile(
    r"\b(unlimited|bypass\w*|free claude|works on (?:mac|macos|linux)|github stars?|testimonials?)\b"
    r"|\bavoids? (?:the )?(?:rate |usage )?limits?\b"
    r"|\b\d[\d,.]*\+?\s+(?:users|downloads|stars)\b", re.I)
OFFICIAL_RE = re.compile(r"\bofficial\b|\bendorsed by anthropic\b", re.I)
# A sentence that disclaims ("not", "No.", "never", "independent") or asks a question is allowed.
DISCLAIM_RE = re.compile(r"\bnot\b|(?-i:\bNo\b)|\bnever\b|\bindependent\b|n't\b", re.I)
SKIP_SCHEMES = ("mailto:", "tel:", "data:")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


class Report:
    def __init__(self):
        self.fails = []
        self.warns = 0
        self.oks = 0

    def _line(self, status, section, name, detail):
        text = "%-4s %s: %s" % (status, section, name)
        if detail:
            text += " - " + detail
        print(text)

    def ok(self, section, name, detail=""):
        self.oks += 1
        self._line("ok", section, name, detail)

    def warn(self, section, name, detail=""):
        self.warns += 1
        self._line("WARN", section, name, detail)

    def fail(self, section, name, detail=""):
        self.fails.append((section, name, detail))
        self._line("FAIL", section, name, detail)

    def check(self, cond, section, name, good="", bad=""):
        if cond:
            self.ok(section, name, good)
        else:
            self.fail(section, name, bad)

    def check_range(self, section, name, value, lo, hi, shown=""):
        self.check(lo <= value <= hi, section, name,
                   "%s (%d-%d)" % (shown or value, lo, hi),
                   "%s is outside %d-%d" % (shown or value, lo, hi))


class PageParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang = None
        self.title = ""
        self._in_title = False
        self._raw = None              # "script" or "style" while inside one
        self.metas = []
        self.links = []
        self.h1 = 0
        self.ids = []                 # (id, line)
        self.refs = []                # (tag, attr, value, line)
        self.imgs = []                # (attrs, line)
        self.scripts = []             # dicts: attrs, text, line
        self.styles = []              # dicts: text, line
        self.controls = []            # dicts: tag, attrs, text, line
        self._open_controls = []
        self._cur = None
        self.visible = []             # visible text fragments, joined without separators

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        line = self.getpos()[0]
        if tag == "html":
            self.html_lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs.append((a, line))
            for c in self._open_controls:
                c["text"].append(a.get("alt", ""))
        elif tag in ("a", "button"):
            self._open_controls.append({"tag": tag, "attrs": a, "text": [], "line": line})
        elif tag == "script":
            self._cur = {"attrs": a, "text": [], "line": line}
            self._raw = "script"
            self.scripts.append(self._cur)
        elif tag == "style":
            self._cur = {"text": [], "line": line}
            self._raw = "style"
            self.styles.append(self._cur)
        if "id" in a:
            self.ids.append((a["id"], line))
        for attr in ("href", "src"):
            if attr in a:
                self.refs.append((tag, attr, a[attr], line))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._raw = None
            self._cur = None
        elif tag in ("a", "button"):
            for i in range(len(self._open_controls) - 1, -1, -1):
                if self._open_controls[i]["tag"] == tag:
                    self.controls.append(self._open_controls.pop(i))
                    break

    def handle_data(self, data):
        if self._raw is not None:
            if self._cur is not None:
                self._cur["text"].append(data)
            return
        if self._in_title:
            self.title += data
            return
        self.visible.append(data)
        for c in self._open_controls:
            c["text"].append(data)


def parse_page(text):
    p = PageParser()
    p.feed(text)
    p.close()
    p.visible_text = "".join(p.visible)
    return p


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def resolve_local(base, page_rel, value):
    """Resolve a relative href/src. Returns (target_path, error, fragment)."""
    path_part, _, frag = value.strip().partition("#")
    path_part = urllib.parse.unquote(path_part.split("?", 1)[0])
    page_path = os.path.join(base, page_rel)
    if path_part == "":
        return page_path, None, frag
    if path_part.startswith("/"):
        return None, "root-relative path breaks on a GitHub project page", frag
    is_dir = path_part.endswith("/")
    target = os.path.normpath(os.path.join(os.path.dirname(page_path), path_part))
    if is_dir or os.path.isdir(target):
        target = os.path.join(target, "index.html")
    base_norm = os.path.normpath(base)
    if not (target == base_norm or target.startswith(base_norm + os.sep)):
        return None, "points outside the site folder", frag
    if not os.path.isfile(target):
        return None, "does not resolve to a file", frag
    return target, None, frag


def url_to_path(base, url):
    """Map a URL under SITE to a file under base. Returns (path or None, reason)."""
    if not url.startswith(SITE):
        return None, "not under " + SITE
    rel = urllib.parse.unquote(url[len(SITE):].split("#", 1)[0].split("?", 1)[0])
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    return os.path.join(base, *rel.split("/")), None


def faq_from_md(text):
    """Return list of (question, answer) from FAQ.md: each '## ' heading is a question."""
    items, cur, buf = [], None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                items.append((cur, norm(" ".join(buf))))
            cur, buf = norm(m.group(1)), []
            continue
        if re.match(r"^#{1,6}\s", line):
            if cur is not None:
                items.append((cur, norm(" ".join(buf))))
            cur, buf = None, []
            continue
        if cur is not None and line.strip():
            buf.append(line.strip())
    if cur is not None:
        items.append((cur, norm(" ".join(buf))))
    return items


def walk_jsonld(obj, types, faq, strings):
    if isinstance(obj, list):
        for x in obj:
            walk_jsonld(x, types, faq, strings)
        return
    if isinstance(obj, str):
        strings.append(obj)
        return
    if not isinstance(obj, dict):
        return
    t = obj.get("@type")
    for name in ([t] if isinstance(t, str) else (t or [])):
        types.add(name)
    if t == "FAQPage" or (isinstance(t, list) and "FAQPage" in t):
        for q in obj.get("mainEntity") or []:
            if isinstance(q, dict):
                ans = q.get("acceptedAnswer") or {}
                faq.append((norm(q.get("name", "")), norm(ans.get("text", "") if isinstance(ans, dict) else "")))
    for v in obj.values():
        walk_jsonld(v, types, faq, strings)


def meta_value(p, key, attr="name"):
    for m in p.metas:
        if m.get(attr, "").lower() == key:
            return m.get("content", "")
    return None


def check_faq(R, sec, base, p, faq_items):
    md_path = os.path.join(base, "FAQ.md")
    if not os.path.isfile(md_path):
        R.fail(sec, "FAQ.md matches FAQPage", "missing file FAQ.md (expected until its owner writes it)")
        return
    md_items = faq_from_md(read_text(md_path))
    md = {}
    dup = False
    for q, a in md_items:
        if q in md:
            dup = True
        md[q] = a
    R.check(not dup, sec, "FAQ.md headings unique", "%d questions" % len(md), "a question heading appears twice in FAQ.md")
    jl = {}
    for q, a in faq_items:
        jl.setdefault(q, a)
    problems = []
    for q, a in faq_items:
        if q not in md:
            problems.append("FAQPage question not a FAQ.md heading: %s" % ascii(q[:60]))
        elif md[q] != a:
            problems.append("answer differs from FAQ.md: %s" % ascii(q[:60]))
    R.check(not problems, sec, "FAQPage questions and answers appear verbatim in FAQ.md",
            "%d questions" % len(faq_items), "; ".join(problems[:6]))
    vis = norm(p.visible_text)
    miss = []
    for q, a in faq_items:
        if q not in vis:
            miss.append("question not visible: %s" % ascii(q[:60]))
        if a not in vis:
            miss.append("answer not visible: %s" % ascii(q[:60]))
    R.check(not miss, sec, "FAQPage text visible on page", "all %d Q&A visible" % len(faq_items), "; ".join(miss[:6]))


def audit_page(R, base, rel, kind):
    sec = rel
    path = os.path.join(base, *rel.split("/"))
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file %s (expected until its owner writes it)" % rel)
        return None
    size = os.path.getsize(path)
    R.check(size < 120 * 1024, sec, "html size budget", "%d KB (< 120 KB)" % (size // 1024),
            "%d KB is over the 120 KB budget" % (size // 1024))
    p = parse_page(read_text(path))
    expected = {"home": SITE, "demo": SITE + "demo/", "404": None}[kind]

    R.check_range(sec, "title length", len(norm(p.title)), 30, 65)
    desc = meta_value(p, "description")
    R.check_range(sec, "meta description length", len(norm(desc)) if desc is not None else 0, 70, 160)

    canon = [l.get("href", "") for l in p.links if "canonical" in l.get("rel", "").lower().split()]
    if kind == "404":
        if canon:
            R.warn(sec, "canonical", "404 page declares a canonical; it should not")
        else:
            R.ok(sec, "canonical", "none (correct for a 404)")
    else:
        R.check(canon == [expected], sec, "canonical",
                expected, "canonical is %s, expected %s" % (canon or "absent", expected))

    R.check(p.h1 == 1, sec, "exactly one h1", "", "found %d h1 elements" % p.h1)
    R.check(bool(p.html_lang), sec, "html lang", p.html_lang or "", "html lang attribute missing or empty")

    vp = meta_value(p, "viewport")
    R.check(vp is not None and "width=device-width" in vp, sec, "viewport meta",
            "", "viewport meta with width=device-width missing")
    R.check(meta_value(p, "theme-color") is not None, sec, "theme-color meta", "", "theme-color meta missing")

    robots_all = [m.get("content", "").lower() for m in p.metas if m.get("name", "").lower() == "robots"]
    robots = " ".join(robots_all)
    if len(robots_all) > 1:
        R.warn(sec, "robots meta", "%d robots meta tags; keep one" % len(robots_all))
    if kind == "404":
        R.check("noindex" in robots, sec, "robots meta", "noindex", "404 page must carry robots noindex")
    else:
        R.check("noindex" not in robots, sec, "robots meta", "indexable", "page must not be noindex")

    if kind != "404":
        for prop in ("og:title", "og:description", "og:image", "og:url", "og:type"):
            v = meta_value(p, prop, attr="property")
            R.check(bool(v), sec, "meta " + prop, "", "%s missing or empty" % prop)
        R.check(bool(meta_value(p, "twitter:card")), sec, "meta twitter:card", "", "twitter:card missing")
        og_url = meta_value(p, "og:url", attr="property") or ""
        R.check(og_url == expected, sec, "og:url", og_url if og_url == expected else "",
                "og:url is %s, expected %s" % (og_url or "absent", expected))
        og_img = meta_value(p, "og:image", attr="property") or ""
        if og_img.startswith(SITE):
            img_path, _ = url_to_path(base, og_img)
            if img_path and os.path.isfile(img_path):
                dims = png_size(img_path)
                if dims is None and img_path.lower().endswith(".png"):
                    R.fail(sec, "og:image size", "%s is not a readable PNG" % os.path.basename(img_path))
                elif dims is None:
                    R.warn(sec, "og:image size", "%s is not a PNG; size not read" % os.path.basename(img_path))
                else:
                    R.check(dims == (1200, 630), sec, "og:image size", "1200x630",
                            "og:image is %dx%d, expected 1200x630" % dims)
            else:
                R.fail(sec, "og:image file", "missing file %s (expected until its owner writes it)"
                       % os.path.basename(img_path or og_img))
        elif og_img:
            R.fail(sec, "og:image", "og:image must be under " + SITE)

    # JSON-LD
    ld = [s for s in p.scripts if s["attrs"].get("type", "").lower() == "application/ld+json"]
    types, faq_items, strings, bad = set(), [], [], []
    for b in ld:
        try:
            walk_jsonld(json.loads("".join(b["text"])), types, faq_items, strings)
        except ValueError as e:
            bad.append("line %d: %s" % (b["line"], e.__class__.__name__))
    R.check(not bad, sec, "JSON-LD parses", "%d block(s)" % len(ld), "; ".join(bad))
    required = {"home": ["SoftwareApplication", "FAQPage", "BreadcrumbList", "WebSite"],
                "demo": ["WebApplication", "BreadcrumbList"],
                "404": []}[kind]
    for t in required:
        R.check(t in types, sec, "JSON-LD type " + t, "", "JSON-LD type %s missing" % t)
    if "FAQPage" in types:
        check_faq(R, sec, base, p, faq_items)

    # images
    img_problems = []
    for a, line in p.imgs:
        miss = [k for k in ("alt", "width", "height") if k not in a]
        if miss:
            img_problems.append("line %d lacks %s" % (line, "/".join(miss)))
    R.check(not img_problems, sec, "img alt, width, height",
            "%d image(s)" % len(p.imgs), "; ".join(img_problems[:8]))

    # ids
    seen, dups = {}, []
    for i, line in p.ids:
        seen[i] = seen.get(i, 0) + 1
    dups = sorted(i for i, n in seen.items() if n > 1)
    R.check(not dups, sec, "ids unique", "%d ids" % len(seen), "duplicate ids: " + ", ".join(dups[:10]))

    # links, sources, anchors
    ref_problems = []
    for tag, attr, value, line in p.refs:
        v = value.strip()
        if v == "":
            if tag == "a":
                ref_problems.append("line %d empty href" % line)
            continue
        low = v.lower()
        if low.startswith("javascript:"):
            ref_problems.append("line %d javascript: URL" % line)
            continue
        if low.startswith(SKIP_SCHEMES) or low.startswith("//") or re.match(r"^[a-z][a-z0-9+.\-]*:", low):
            continue  # external or special scheme: host policy is checked below
        if v.startswith("#"):
            if urllib.parse.unquote(v[1:]) not in seen:
                ref_problems.append("line %d anchor %s not found on page" % (line, v))
            continue
        target, err, frag = resolve_local(base, rel, v)
        if err:
            ref_problems.append("line %d %s: %s" % (line, v, err))
            continue
        if frag and target.lower().endswith(".html"):
            tp = parse_page(read_text(target))
            if urllib.parse.unquote(frag) not in [i for i, _ in tp.ids]:
                ref_problems.append("line %d %s: anchor #%s not in target" % (line, v, frag))
    R.check(not ref_problems, sec, "relative links and sources resolve",
            "%d reference(s)" % len(p.refs), "; ".join(ref_problems[:8]))

    # accessible names
    unnamed = []
    for c in p.controls:
        a = c["attrs"]
        name = norm("".join(c["text"]))
        if not (name or a.get("aria-label") or a.get("aria-labelledby") or a.get("title")):
            unnamed.append("line %d <%s>" % (c["line"], c["tag"]))
    R.check(not unnamed, sec, "links and buttons have accessible names",
            "%d control(s)" % len(p.controls), "no name at " + ", ".join(unnamed[:8]))

    # external hosts (scripts and stylesheets)
    ext_bad = []
    for s in p.scripts:
        src = s["attrs"].get("src", "")
        host = urllib.parse.urlsplit(src).hostname if re.match(r"^(https?:)?//", src) else None
        if host and host not in ALLOWED_EXTERNAL_HOSTS:
            ext_bad.append("script from %s" % host)
    for l in p.links:
        if "stylesheet" in l.get("rel", "").lower().split():
            href = l.get("href", "")
            host = urllib.parse.urlsplit(href).hostname if re.match(r"^(https?:)?//", href) else None
            if host and host not in ALLOWED_EXTERNAL_HOSTS:
                ext_bad.append("stylesheet from %s" % host)
    for st in p.styles:
        if re.search(r"@import|url\(\s*['\"]?https?:", "".join(st["text"]), re.I):
            ext_bad.append("inline style loads an external resource")
    R.check(not ext_bad, sec, "no external scripts or styles",
            "only static.cloudflareinsights.com allowed", "; ".join(ext_bad))
    ext_img = [a.get("src", "") for a, _ in p.imgs if re.match(r"^(https?:)?//", a.get("src", ""))]
    if ext_img:
        R.warn(sec, "external images", "%d external image(s): %s" % (len(ext_img), ext_img[0][:80]))

    # CSS budget: linked files and inline blocks
    css_problems = []
    for l in p.links:
        if "stylesheet" in l.get("rel", "").lower().split():
            href = l.get("href", "")
            if re.match(r"^(https?:)?//", href):
                continue
            t, _, _ = resolve_local(base, rel, href)
            if t and os.path.getsize(t) >= 30 * 1024:
                css_problems.append("%s is %d KB" % (href, os.path.getsize(t) // 1024))
    for st in p.styles:
        n = len("".join(st["text"]).encode("utf-8"))
        if n >= 30 * 1024:
            css_problems.append("inline style at line %d is %d KB" % (st["line"], n // 1024))
    R.check(not css_problems, sec, "css size budget", "< 30 KB each", "; ".join(css_problems))

    # text policy: official claims, hype words, claim words
    text_blob = "\n".join([norm(p.title), norm(desc or "")] +
                          [m.get("content", "") for m in p.metas] +
                          [p.visible_text] + strings)
    off = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text_blob):
        if OFFICIAL_RE.search(sent) and not (DISCLAIM_RE.search(sent) or sent.rstrip().endswith("?")):
            off.append(sent.strip()[:70])
    R.check(not off, sec, "no official or endorsement claims",
            "only the disclaimer uses the word", "; ".join(off[:4]))
    hype = sorted(set(m.group(0).lower() for m in HYPE_RE.finditer(text_blob)))
    R.check(not hype, sec, "no hype words", "", "banned words: " + ", ".join(hype))
    # "-ExecutionPolicy Bypass" is the PowerShell flag in the install command, not a claim about limits
    claims = sorted(set(m.group(0).lower() for m in CLAIM_RE.finditer(re.sub(r"(?i)ExecutionPolicy\s+Bypass", "", text_blob))))
    if claims:
        R.warn(sec, "claim words", "check these against the copy rules: " + ", ".join(claims))
    return p


def check_sitemap(R, base):
    sec = "sitemap.xml"
    path = os.path.join(base, "sitemap.xml")
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file sitemap.xml")
        return
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        R.fail(sec, "XML parses", str(e))
        return
    R.check(root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset", sec, "sitemap 0.9 namespace",
            "", "root element is not urlset in the sitemaps.org 0.9 namespace")
    locs = []
    for url in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
        loc = url.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        lastmod = url.find("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
        if loc is None or not (loc.text or "").strip():
            R.fail(sec, "url entry", "entry without loc")
            continue
        locs.append(loc.text.strip())
        if lastmod is not None:
            try:
                datetime.strptime(lastmod.text.strip(), "%Y-%m-%d")
            except (ValueError, AttributeError):
                R.fail(sec, "lastmod", "%s is not YYYY-MM-DD" % lastmod.text)
    missing_urls = []
    for u in locs:
        fp, why = url_to_path(base, u)
        if fp is None:
            missing_urls.append("%s (%s)" % (u, why))
        elif not os.path.isfile(fp):
            missing_urls.append("%s (no file %s)" % (u, os.path.relpath(fp, base)))
    R.check(not missing_urls, sec, "sitemap URLs map to files", "%d URL(s)" % len(locs),
            "; ".join(missing_urls))
    for want in (SITE, SITE + "demo/"):
        R.check(want in locs, sec, "sitemap lists " + want.replace(SITE, "/"), "", "%s not listed" % want)


def check_robots(R, base):
    sec = "robots.txt"
    path = os.path.join(base, "robots.txt")
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file robots.txt")
        return
    blocks, cur, last_agent, sitemaps = {}, [], False, []
    for raw in read_text(path).splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if not last_agent:
                cur = []
            cur.append(v.lower())
            blocks.setdefault(v.lower(), [])
            last_agent = True
        elif k in ("allow", "disallow"):
            last_agent = False
            for a in cur:
                blocks[a].append((k, v))
        elif k == "sitemap":
            last_agent = False
            sitemaps.append(v)
    R.check(("allow", "/") in blocks.get("*", []), sec, "User-agent * allows /", "", "missing 'User-agent: *' with 'Allow: /'")
    for c in CRAWLERS:
        R.check(("allow", "/") in blocks.get(c.lower(), []), sec, "allow " + c, "", "no explicit 'Allow: /' block for " + c)
    disallowed = [a for a, rules in blocks.items() if ("disallow", "/") in rules]
    R.check(not disallowed, sec, "no blanket Disallow: /", "", "Disallow: / in blocks for " + ", ".join(disallowed))
    R.check((SITE + "sitemap.xml") in sitemaps, sec, "Sitemap line", SITE + "sitemap.xml",
            "no 'Sitemap: %ssitemap.xml' line (found %s)" % (SITE, sitemaps or "none"))


def check_llms(R, base):
    sec = "llms.txt"
    path = os.path.join(base, "llms.txt")
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file llms.txt")
        return
    text = read_text(path)
    lines = text.splitlines()
    R.check(bool(lines) and lines[0].startswith("# "), sec, "H1 name", "", "first line must be '# <name>'")
    R.check(any(l.startswith("> ") for l in lines), sec, "blockquote summary", "", "no '> ' summary line")
    heads = [l[3:].strip() for l in lines if l.startswith("## ")]
    for want in ("Docs", "Try it", "Optional"):
        R.check(want in heads, sec, "section " + want, "", "missing '## %s' section" % want)
    bad = []
    links = re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", text)
    for _, url in links:
        if url.startswith(SITE):
            fp, why = url_to_path(base, url)
            if not os.path.isfile(fp):
                bad.append("%s (no file)" % url)
            continue
        host = urllib.parse.urlsplit(url).hostname or ""
        if host == "github.com" and urllib.parse.urlsplit(url).path.lower().startswith(REPO_PATH):
            continue
        bad.append("%s (unexpected host or path)" % url)
    R.check(not bad and links, sec, "links return an expected path or known host",
            "%d link(s)" % len(links), "; ".join(bad) or "no links found")


def check_manifest(R, base):
    sec = "manifest.webmanifest"
    path = os.path.join(base, "manifest.webmanifest")
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file manifest.webmanifest")
        return
    try:
        m = json.loads(read_text(path))
    except ValueError as e:
        R.fail(sec, "JSON parses", str(e))
        return
    want = {"name": "CCSwitcherX", "short_name": "CCSwitcherX", "start_url": "./", "scope": "./",
            "display": "browser", "background_color": "#1f1e1d", "theme_color": "#1f1e1d", "lang": "en"}
    for k, v in want.items():
        R.check(m.get(k) == v, sec, k, str(v), "%s is %r, expected %r" % (k, m.get(k), v))
    R.check(bool(m.get("description")), sec, "description", "", "description missing")
    icons = m.get("icons") or []
    srcs = {i.get("src"): i for i in icons}
    R.check("favicon-192.png" in srcs and srcs["favicon-192.png"].get("sizes") == "192x192",
            sec, "icon favicon-192.png 192x192", "", "icon favicon-192.png with sizes 192x192 missing")
    R.check("favicon.svg" in srcs and srcs["favicon.svg"].get("sizes") == "any",
            sec, "icon favicon.svg any", "", "icon favicon.svg with sizes any missing")
    for ic in icons:
        src = ic.get("src", "")
        fp = os.path.join(base, *src.split("/"))
        if os.path.isfile(fp):
            R.ok(sec, "icon file " + src)
        else:
            R.fail(sec, "icon file " + src, "missing file %s (expected until its owner writes it)" % src)


def check_security(R, base):
    sec = ".well-known/security.txt"
    path = os.path.join(base, ".well-known", "security.txt")
    if not os.path.isfile(path):
        R.fail(sec, "file", "missing file .well-known/security.txt")
        return
    fields = {}
    for line in read_text(path).splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            fields.setdefault(k.strip().lower(), v.strip())
    R.check("contact" in fields, sec, "Contact", fields.get("contact", ""), "Contact missing")
    exp = fields.get("expires", "")
    try:
        when = datetime.strptime(exp, "%Y-%m-%dT%H:%M:%S.%fZ").date()
        R.check(when > date.today(), sec, "Expires in the future", exp, "Expires %s is not in the future" % exp)
    except ValueError:
        R.fail(sec, "Expires", "not an ISO 8601 UTC timestamp: %r" % exp)
    R.check(fields.get("canonical") == SITE + ".well-known/security.txt", sec, "Canonical",
            "", "Canonical is %r" % fields.get("canonical"))


def main():
    ap = argparse.ArgumentParser(description="Offline SEO and AEO audit for the CCSwitcherX site.")
    ap.add_argument("--base", default=None, help="site folder (default: docs next to this script)")
    args = ap.parse_args()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = args.base or os.path.join(repo, "docs")
    if not os.path.isdir(base) and not os.path.isabs(base):
        base = os.path.join(repo, base)
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        print("FAIL base folder not found: %s" % base)
        return 1

    R = Report()
    print("check-site: base=%s expected site=%s" % (base, SITE))
    audit_page(R, base, "index.html", "home")
    audit_page(R, base, "demo/index.html", "demo")
    audit_page(R, base, "404.html", "404")
    check_sitemap(R, base)
    check_robots(R, base)
    check_llms(R, base)
    check_manifest(R, base)
    check_security(R, base)

    missing = [f for f in R.fails if f[2].startswith("missing file")]
    other = [f for f in R.fails if not f[2].startswith("missing file")]
    print()
    print("summary: %d ok, %d WARN, %d FAIL (%d missing files, %d other)"
          % (R.oks, R.warns, len(R.fails), len(missing), len(other)))
    return 1 if R.fails else 0


if __name__ == "__main__":
    sys.exit(main())
