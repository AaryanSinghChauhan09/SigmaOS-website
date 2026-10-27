"""Static documentation site generator for SigmaOS-website / SigmaOS-guide-*.

Principles: markdown subset is parsed and validated fail-fast before any file
is written, rendering is a pure function of the parsed model (deterministic
byte-identical output), plan and execute are separated (self_test only builds
into a tempdir -- serve_plan returns argv and never starts a server), and all
generated HTML is escaped so a page cannot inject script handlers.
"""
import argparse, html, json, os, re, shlex, sys

ENGINE = {
    "name": "sigma-docs",
    "summary": "Docs site generator: markdown to static HTML + search index",
    "language": "python",
    "verbs": ["build", "nav", "search-index", "serve-plan"],
    "principles": ["Separation of Concerns", "Defensive Programming",
                   "Fail-Fast", "KISS", "Deterministic Testing"],
}

cli_doc = """\
$ sigma-docs build docs/ public/
wrote 12 pages to public/
$ sigma-docs nav docs/
<nav class="site"><a href="index.html">Home</a> ...
$ sigma-docs search-index docs/ > public/search-index.json
$ sigma-docs serve-plan public --port 8080
python3 -m http.server 8080 --bind 127.0.0.1 --directory public
"""

_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_STOPWORDS = frozenset(
    "the a an and or of to in for is are was were be been with on at by "
    "it this that as from but if then so not no you your we our us they "
    "their i he she his her have has had will would can could should "
    "what when where which who whom how all any some more most other "
    "into over under about than there here".split())
_SAFE_SCHEMES = ("http://", "https://")


class DocsError(ValueError):
    pass


def _check_text(text, what):
    if not isinstance(text, str) or not text.strip():
        raise DocsError(f"empty {what}")
    if "\x00" in text:
        raise DocsError(f"{what} contains NUL")
    return text


# --------------------------------------------------------------------------- #
#  Headings & anchors (fenced code skipped, anchors de-duplicated)
# --------------------------------------------------------------------------- #
def slugify(text):
    _check_text(text, "heading")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        raise DocsError(f"heading {text!r} has no usable slug")
    return slug


def parse_headings(md):
    """Return [{"level", "text", "anchor"}] for ATX headings outside fences."""
    _check_text(md, "markdown")
    heads, seen, in_fence = [], {}, False
    for line in md.splitlines():
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEAD_RE.match(line)
        if not m:
            continue
        level, text = len(m.group(1)), m.group(2)
        if not text:
            raise DocsError(f"empty heading at level {level}")
        base = slugify(text)
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchor = base if n == 0 else f"{base}-{n}"
        heads.append({"level": level, "text": text, "anchor": anchor})
    return heads


# --------------------------------------------------------------------------- #
#  Markdown subset -> HTML (everything escaped)
# --------------------------------------------------------------------------- #
def _inline(text):
    """Escape text, then apply code / bold / link spans."""
    out = html.escape(text, quote=False)

    def do_code(m):
        return f"<code>{m.group(1)}</code>"

    out = _CODE_SPAN_RE.sub(do_code, out)

    def do_bold(m):
        return f"<strong>{m.group(1)}</strong>"

    out = _BOLD_RE.sub(do_bold, out)

    def do_link(m):
        label, href = m.group(1), m.group(2)
        if not href.startswith(_SAFE_SCHEMES) and not href.startswith("#") \
                and not href.endswith(".html"):
            raise DocsError(f"unsupported link target: {href!r}")
        safe = html.escape(href, quote=True)
        return f'<a href="{safe}">{label}</a>'

    return _LINK_RE.sub(do_link, out)


def md_to_html(md):
    """Render the supported markdown subset to an HTML fragment."""
    _check_text(md, "markdown")
    heads = parse_headings(md)
    lines, para, out = md.splitlines(), [], []
    in_fence = False
    buf = []
    items = None
    head_i = 0

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_items():
        nonlocal items
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>"
                                        for i in items) + "</ul>")
            items = None

    for line in lines:
        if _FENCE_RE.match(line.strip()):
            flush_items()
            if in_fence:
                out.append("<pre><code>" + html.escape("\n".join(buf))
                           + "</code></pre>")
                buf = []
            in_fence = not in_fence
            continue
        if in_fence:
            buf.append(line)
            continue
        m = _HEAD_RE.match(line)
        if m:
            flush()
            flush_items()
            level, text = len(m.group(1)), m.group(2)
            if head_i < len(heads):
                anchor = heads[head_i]["anchor"]
                head_i += 1
            else:
                anchor = slugify(text)
            out.append(f'<h{level} id="{html.escape(anchor, quote=True)}">'
                       f"{_inline(text)}</h{level}>")
            continue
        if line.strip().startswith("- "):
            flush()
            if items is None:
                items = []
            items.append(line.strip()[2:])
            continue
        flush_items()
        if not line.strip():
            flush()
            continue
        para.append(line.strip())
    flush()
    flush_items()
    if in_fence:
        raise DocsError("unclosed code fence")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  Navigation tree (index.md first, then alphabetical; deterministic)
# --------------------------------------------------------------------------- #
def build_nav(root):
    """Build a nav tree from the .md files under `root` (pure-ish: reads only).

    Returns {"name", "path", "children": [...]}; directories sort after files,
    `index.md` comes first inside each directory.
    """
    _check_text(root, "root")
    if not os.path.isdir(root):
        raise DocsError(f"not a directory: {root}")
    tree = {"name": os.path.basename(os.path.abspath(root)) or "docs",
            "path": "", "children": []}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            node = {"name": fn[:-3], "path": rel, "children": []}
            parent = _nav_parent(tree, os.path.dirname(rel))
            parent["children"].append(node)
    _sort_nav(tree)
    return tree


def _nav_parent(tree, rel_dir):
    if not rel_dir or rel_dir == ".":
        return tree
    for child in tree["children"]:
        if child["children"] and child["path"] == rel_dir:
            return child
    # directory node synthesized lazily for intermediate dirs
    node = {"name": os.path.basename(rel_dir), "path": rel_dir, "children": []}
    tree["children"].append(node)
    return node


def _sort_nav(node):
    def key(c):
        # directories after files, index.md first, then alphabetical
        is_index = 0 if c["path"].endswith("index.md") else 1
        is_dir = 1 if c["children"] or not c["path"].endswith(".md") else 0
        return (is_dir, is_index, c["name"].lower())
    node["children"].sort(key=key)
    for c in node["children"]:
        if c["children"]:
            _sort_nav(c)


def render_nav_html(tree, current=None):
    """Render the nav tree to HTML. `current` marks the active page."""
    parts = ['<nav class="site">']

    def walk(node, depth):
        if not node["children"]:
            return
        parts.append("<ul>")
        for c in node["children"]:
            if c["children"]:
                parts.append(f'<li class="dir">{html.escape(c["name"])}')
                walk(c, depth + 1)
                parts.append("</li>")
            else:
                href = html.escape(c["path"][:-3] + ".html", quote=True)
                mark = ' aria-current="page"' if c["path"] == current else ""
                label = "Home" if c["path"].endswith("index.md") \
                    and os.path.dirname(c["path"]) == "" else c["name"]
                parts.append(f'<li><a href="{href}"{mark}>'
                             f"{html.escape(label)}</a></li>")
        parts.append("</ul>")

    walk(tree, 0)
    parts.append("</nav>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
#  Search index (lowercased tokens >= 2 chars, stopwords removed)
# --------------------------------------------------------------------------- #
def tokenize(text):
    _check_text(text, "text")
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


def search_index(pages):
    """pages: [{"path", "title", "text"}] -> {"terms": {term: {path: count}},
    "docs": [{path, title}]}. Counts are deterministic; sorted term list."""
    if not isinstance(pages, list) or not pages:
        raise DocsError("search index needs a non-empty page list")
    terms, docs, seen = {}, [], set()
    for i, p in enumerate(pages):
        if not isinstance(p, dict):
            raise DocsError(f"page #{i} is not an object")
        path = p.get("path")
        if not isinstance(path, str) or not path or path.startswith("/"):
            raise DocsError(f"page #{i}: bad path {path!r}")
        if path in seen:
            raise DocsError(f"duplicate page path: {path!r}")
        seen.add(path)
        title = _check_text(p.get("title"), f"page {path} title")
        text = _check_text(p.get("text"), f"page {path} text")
        docs.append({"path": path, "title": title})
        for w in tokenize(title + " " + text):
            per = terms.setdefault(w, {})
            per[path] = per.get(path, 0) + 1
    return {"terms": terms, "docs": docs}


def format_index(index):
    """Stable JSON for the client-side search widget."""
    return json.dumps(
        {"docs": index["docs"],
         "terms": {t: index["terms"][t] for t in sorted(index["terms"])}},
        indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
#  Build (execute layer: writes files; self_test only uses a tempdir)
# --------------------------------------------------------------------------- #
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
{nav}
<main>
<h1>{title}</h1>
{body}
</main>
<script src="search-index.json" type="application/json" id="search-data"></script>
</body>
</html>
"""


def _page_title(md, fallback):
    heads = parse_headings(md)
    if heads and heads[0]["level"] == 1:
        return heads[0]["text"]
    return fallback


def build_site(src, out):
    """Render every .md under `src` into `out` plus search-index.json.

    Returns a summary dict. Raises DocsError on bad input, OSError on write
    failures. Pure rendering: the same tree yields byte-identical files.
    """
    tree = build_nav(src)
    if not tree["children"]:
        raise DocsError(f"no markdown pages under {src}")
    os.makedirs(out, exist_ok=True)
    pages, written = [], []
    for dirpath, _, filenames in os.walk(src):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            with open(full, "r", encoding="utf-8") as fh:
                md = fh.read()
            fallback = os.path.splitext(fn)[0]
            title = _page_title(md, fallback)
            pages.append({"path": rel, "title": title, "text": md})
    pages.sort(key=lambda p: p["path"])
    nav = render_nav_html(tree)
    for p in pages:
        with open(os.path.join(src, p["path"]), "r", encoding="utf-8") as fh:
            md = fh.read()
        body = md_to_html(md)
        page = _PAGE_TEMPLATE.format(title=html.escape(p["title"]),
                                     nav=nav, body=body)
        dest = os.path.join(out, p["path"][:-3] + ".html")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(page)
        written.append(p["path"][:-3] + ".html")
    index = search_index(pages)
    idx_path = os.path.join(out, "search-index.json")
    with open(idx_path, "w", encoding="utf-8") as fh:
        fh.write(format_index(index))
    written.append("search-index.json")
    return {"pages": len(pages), "written": sorted(written)}


def serve_plan(root, port=8080):
    """argv for a local preview server -- never executed by self_test."""
    _check_text(root, "root")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise DocsError(f"bad port: {port!r}")
    return ["python3", "-m", "http.server", str(port),
            "--bind", "127.0.0.1", "--directory", root]


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"], description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    bd = sub.add_parser("build", help="render markdown tree to an output dir")
    bd.add_argument("src")
    bd.add_argument("out")
    nv = sub.add_parser("nav", help="print the site navigation HTML")
    nv.add_argument("src")
    si = sub.add_parser("search-index", help="print the search index JSON")
    si.add_argument("src")
    sp = sub.add_parser("serve-plan", help="print a preview server argv")
    sp.add_argument("root")
    sp.add_argument("--port", type=int, default=8080)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "build":
            summary = build_site(args.src, args.out)
            print(f"wrote {summary['pages']} page(s) to {args.out}")
            return 0
        if args.cmd == "nav":
            print(render_nav_html(build_nav(args.src)))
            return 0
        if args.cmd == "search-index":
            pages = []
            for dirpath, _, filenames in os.walk(args.src):
                for fn in sorted(filenames):
                    if not fn.endswith(".md"):
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, args.src)
                    with open(full, "r", encoding="utf-8") as fh:
                        md = fh.read()
                    pages.append({"path": rel,
                                  "title": _page_title(md, fn[:-3]),
                                  "text": md})
            print(format_index(search_index(pages)), end="")
            return 0
        if args.cmd == "serve-plan":
            print(shlex.join(serve_plan(args.root, args.port)))
            return 0
        return 2
    except (DocsError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    import tempfile

    # --- slugify -------------------------------------------------------------
    assert slugify("Getting Started!") == "getting-started"
    assert slugify("  SigmaOS   Guide  ") == "sigmaos-guide"
    assert slugify("C++ & Rust") == "c-rust"
    for bad in ("", "   ", "!!!", None, 7):
        try:
            slugify(bad)
        except DocsError:
            pass
        else:
            raise AssertionError(f"slugify should have rejected: {bad!r}")

    # --- headings (fences skipped, anchors de-duplicated) --------------------
    MD = ("# SigmaOS Guide\n"
          "\n"
          "Intro text for the guide.\n"
          "\n"
          "## Install\n"
          "\n"
          "```bash\n"
          "# not a heading\n"
          "echo hi\n"
          "```\n"
          "\n"
          "## Install\n"
          "\n"
          "Second install section.\n"
          "\n"
          "### Deep Dive **bold**\n")
    heads = parse_headings(MD)
    assert [(h["level"], h["text"]) for h in heads] == [
        (1, "SigmaOS Guide"), (2, "Install"), (2, "Install"),
        (3, "Deep Dive **bold**")]
    assert [h["anchor"] for h in heads] == [
        "sigmaos-guide", "install", "install-1", "deep-dive-bold"]
    assert heads == parse_headings(MD)                  # deterministic
    for bad in (None, "", "# \n"):
        try:
            parse_headings(bad)
        except DocsError:
            pass
        else:
            raise AssertionError(f"headings should have been rejected: {bad!r}")

    # --- markdown to html ----------------------------------------------------
    body = md_to_html(MD)
    assert '<h1 id="sigmaos-guide">SigmaOS Guide</h1>' in body
    assert '<h2 id="install-1">Install</h2>' in body
    assert '<h3 id="deep-dive-bold">Deep Dive <strong>bold</strong></h3>' in body
    assert "<p>Intro text for the guide.</p>" in body
    assert "<pre><code># not a heading\necho hi</code></pre>" in body
    assert body == md_to_html(MD)                       # deterministic
    listy = md_to_html("## Tools\n\n- nvim\n- kitty\n")
    assert "<ul><li>nvim</li><li>kitty</li></ul>" in listy
    links = md_to_html("See [the docs](https://example.test/guide).")
    assert '<a href="https://example.test/guide">the docs</a>' in links
    assert "<p>See <code>escaped</code> &amp; safe.</p>" == \
        md_to_html("See `escaped` & safe.")
    for bad in (None, "", "# \n"):
        try:
            md_to_html(bad)
        except DocsError:
            pass
        else:
            raise AssertionError(f"md_to_html should have rejected: {bad!r}")
    try:
        md_to_html("```unclosed\nfence")
    except DocsError:
        pass
    else:
        raise AssertionError("unclosed fence not rejected")
    for bad_link in ("[x](javascript:alert(1))", "[x](file:///etc/passwd)",
                     "[x](data:text/html,x)"):
        try:
            md_to_html(bad_link)
        except DocsError:
            pass
        else:
            raise AssertionError(f"link should have been rejected: {bad_link}")

    # --- tokenize / search index --------------------------------------------
    toks = tokenize("The SigmaOS guide and the guide")
    assert toks == ["sigmaos", "guide", "guide"]
    assert tokenize("a an the of") == []                # stopwords only
    try:
        tokenize("")
    except DocsError:
        pass
    else:
        raise AssertionError("empty text not rejected")
    pages = [
        {"path": "index.md", "title": "SigmaOS Home",
         "text": "Welcome to SigmaOS. Fast by default."},
        {"path": "guide/install.md", "title": "Install",
         "text": "Install SigmaOS with the guided setup."},
    ]
    index = search_index(pages)
    assert index["terms"]["sigmaos"] == {"index.md": 2, "guide/install.md": 1}
    assert index["terms"]["install"] == {"guide/install.md": 2}
    assert "the" not in index["terms"]
    assert [d["path"] for d in index["docs"]] == ["index.md", "guide/install.md"]
    blob = format_index(index)
    assert json.loads(blob) == {"docs": index["docs"],
                                "terms": index["terms"]}
    assert blob == format_index(index)                  # stable JSON
    # sorted terms: "default" (d) sorts before "sigmaos" (s)
    assert blob.index('"default"') < blob.index('"sigmaos"')
    for bad in ([], "x", None,
                [{"path": "/abs.md", "title": "T", "text": "x"}],
                [{"path": "a.md", "title": "", "text": "x"}],
                [{"path": "a.md", "title": "T", "text": None}],
                [{"path": "a.md", "title": "T", "text": "x"},
                 {"path": "a.md", "title": "T", "text": "x"}],
                "not-a-page"):
        try:
            search_index(bad)
        except DocsError:
            pass
        else:
            raise AssertionError(f"search_index should have rejected: {bad!r}")

    # --- nav tree (tempdir) --------------------------------------------------
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "guide"))
        os.makedirs(os.path.join(root, "api"))
        files = {
            "index.md": "# Home\n\nWelcome to SigmaOS. Fast by default.\n",
            "about.md": "# About\n\nAbout page.\n",
            "guide/install.md": "# Install\n\nInstall SigmaOS here.\n",
            "guide/index.md": "# Guide\n\nGuide index.\n",
            "api/reference.md": "# Reference\n\nAPI page.\n",
            "notes.txt": "ignored\n",
        }
        for rel, content in files.items():
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(content)
        tree = build_nav(root)
        top = [c["name"] for c in tree["children"]]
        # index.md first, files before directories, dirs alphabetical
        assert top == ["index", "about", "api", "guide"]
        nav = render_nav_html(tree)
        assert '<nav class="site">' in nav and nav.endswith("</nav>")
        assert '<a href="index.html" aria-current="page">Home</a>' in \
            render_nav_html(tree, current="index.md")
        assert 'href="guide/install.html"' in nav
        assert 'href="api/reference.html"' in nav
        assert nav == render_nav_html(tree)             # deterministic
        for bad in ("", None, os.path.join(root, "nope")):
            try:
                build_nav(bad)
            except DocsError:
                pass
            else:
                raise AssertionError(f"build_nav should have rejected: {bad!r}")

        # --- build (writes only inside the tempdir) --------------------------
        out = os.path.join(root, "public")
        summary = build_site(root, out)
        assert summary["pages"] == 5
        assert "index.html" in summary["written"]
        assert "guide/install.html" in summary["written"]
        assert "search-index.json" in summary["written"]
        assert os.path.isfile(os.path.join(out, "index.html"))
        assert os.path.isfile(os.path.join(out, "guide", "install.html"))
        idx = json.loads(open(os.path.join(out, "search-index.json"),
                              encoding="utf-8").read())
        assert idx["terms"]["sigmaos"] == {"index.md": 1, "guide/install.md": 1}
        page = open(os.path.join(out, "guide", "install.html"),
                    encoding="utf-8").read()
        assert "<title>Install</title>" in page
        assert '<h1 id="install">Install</h1>' in page
        assert '<nav class="site">' in page
        # byte-identical rebuild
        out2 = os.path.join(root, "public2")
        build_site(root, out2)
        assert open(os.path.join(out2, "guide", "install.html"),
                    encoding="utf-8").read() == page
        for bad_src in ("", os.path.join(root, "nope")):
            try:
                build_site(bad_src, os.path.join(root, "x"))
            except DocsError:
                pass
            else:
                raise AssertionError(f"build_site should have rejected: {bad_src!r}")

    # --- serve plan (argv only, never executed) ------------------------------
    argv = serve_plan("public", 8080)
    assert argv == ["python3", "-m", "http.server", "8080",
                    "--bind", "127.0.0.1", "--directory", "public"]
    assert serve_plan("public", 8080) == argv           # deterministic
    for bad in (0, -1, 65536, "8080", None, True):
        try:
            serve_plan("public", bad)
        except DocsError:
            pass
        else:
            raise AssertionError(f"serve_plan should have rejected port {bad!r}")
    try:
        serve_plan("", 8080)
    except DocsError:
        pass
    else:
        raise AssertionError("empty root not rejected")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
