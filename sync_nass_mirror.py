"""
Generates the standalone NASS Production app (a MIRROR of the RMA dashboard's first tab) from app.py, the way the admin portal's apps/ are generated mirrors.
app.py stays the one place the NASS tab is edited; run this again after changing it and push the output folder.

    python sync_nass_mirror.py [output_dir]        # default: ../nass-production
    python sync_nass_mirror.py --locked            # keep the shared-password gate (default: the mirror is open)

What it does, from the syntax tree of app.py (not text matching, so it follows the code as it changes):
  * keeps only the module-level code the NASS tab reaches (functions, constants, imports), in the original order, as the original text;
  * writes a main() made of the dashboard's own set-up (styling, header, boundaries, adjustments), the NASS tab block as it is, and the footer; every other tab is dropped;
  * a one-line shim makes `tab_nass` an always-open tab, so the tab's `if tab_nass.open:` guard still works;
  * a few visible texts are swapped for the mirror (page title, header, the line that compares the NASS and RMA tabs);
  * copies the three logos, the theme and a slim requirements.txt, and refuses to write (exit 1) if the code no longer has the shape it expects or the result does not compile.
It never edits app.py. Secrets the mirror needs: NASS_API_KEY only.
"""
import ast
import collections
import copy
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
LOGOS = ["50 Year logo JSA.png", "Transparent Smal logo.png", "logo-full.png"]

# visible texts changed for the mirror: (old, new). Each must be found exactly once or the sync stops, so a change in app.py is noticed, not silently skipped.
SWAPS = [
    ("page_title=\"JSA Agricultural Intelligence Dashboard\"", "page_title=\"JSA NASS Production\""),
    ("<b>JSA Agricultural Intelligence Dashboard</b>", "<b>JSA NASS Production</b>"),
    ("Production, supply &amp; demand &middot; John Stewart &amp; Associates", "USDA NASS production by state, ASD district and county &middot; John Stewart &amp; Associates"),
]
ASOF_OLD_START = '<div class="asof"><b>NASS tab</b>'
ASOF_NEW = ('<div class="asof"><b>NASS production</b>: USDA survey-based final production figures (post-harvest, all acres), by state, ASD district and county.</div>"""')


def fail(msg):
    print("REFUSING TO WRITE:", msg)
    sys.exit(1)


def bound(n):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {n.name}
    if isinstance(n, ast.Import):
        return {(a.asname or a.name.split(".")[0]) for a in n.names}
    if isinstance(n, ast.ImportFrom):
        return {(a.asname or a.name) for a in n.names}
    out = set()
    for x in ast.walk(n):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            out.add(x.id)
        elif isinstance(x, (ast.FunctionDef, ast.ClassDef)) and x is not n:
            out.add(x.name)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            out |= bound(x)
    return out


def used(n):
    return ({x.id for x in ast.walk(n) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
            | {x.value.id for x in ast.walk(n) if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)})


def first_line(n):
    return min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])


def text(lines, n):
    return "".join(lines[first_line(n) - 1:n.end_lineno])


def main(out_dir, locked):
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.replace("\r\n", "\n").splitlines(keepends=True)
    top = tree.body
    fn = [n for n in top if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if len(fn) != 1:
        fail("app.py no longer has exactly one top-level main()")
    mn = fn[0]
    body = mn.body

    # ---- find the parts of main() by what they are
    def is_tab_with(s, name=None):
        return isinstance(s, ast.With) and isinstance(s.items[0].context_expr, ast.Name) and s.items[0].context_expr.id.startswith("tab_") and (name is None or s.items[0].context_expr.id == name)
    tabs_assign = [i for i, s in enumerate(body) if isinstance(s, ast.Assign) and isinstance(s.value, ast.Call) and ast.unparse(s.value.func) == "st.tabs"]
    nass_with = [i for i, s in enumerate(body) if is_tab_with(s, "tab_nass")]
    if len(tabs_assign) != 1 or len(nass_with) != 1:
        fail("could not find the st.tabs(...) assignment and the `with tab_nass:` block exactly once")
    ta, nw = tabs_assign[0], nass_with[0]
    first_tab = min(i for i, s in enumerate(body) if is_tab_with(s))
    last_tab = max(i for i, s in enumerate(body) if is_tab_with(s))
    pre = list(range(0, ta))                                         # everything before st.tabs: styling, gate, loaders, header, adjustments
    after_tabs = [i for i in range(ta + 1, first_tab)]                # e.g. the line that scopes the high-oleic carve-out to the NASS tab
    foot = list(range(last_tab + 1, len(body)))
    gate = [i for i in pre if "draw_auth" in ast.unparse(body[i])]
    keep_pre = [i for i in pre if (locked or i not in gate)]

    # ---- reachability from what the mirror's main() uses (the RMA workbook is loaded for the RMA tab only, so that call does not count)
    def without_rma_load(s_):
        if locked or not isinstance(s_, ast.With):
            return s_
        c = copy.deepcopy(s_)
        c.body = [b_ for b_ in c.body if not (isinstance(b_, ast.Assign) and isinstance(b_.value, ast.Call) and ast.unparse(b_.value.func) == "load_data")] or [ast.Pass()]
        return c
    roots = [without_rma_load(body[i]) for i in keep_pre] + [body[nw]] + [body[i] for i in foot]
    need = set()
    for r in roots:
        need |= used(r)
    modbind = collections.defaultdict(list)
    for i, n in enumerate(top):
        if n is not mn:
            for b in bound(n):
                modbind[b].append(i)
    keep = set()
    for i, n in enumerate(top):
        if n is mn:
            continue
        if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test):
            keep.add(i)
        elif isinstance(n, ast.Expr):                                 # page config, styling, badge: always
            keep.add(i)
            need |= used(n)
        elif isinstance(n, ast.Try) and not bound(n):                 # a try block that only does something (the badge)
            keep.add(i)
            need |= used(n)
    seen, queue = set(), list(need)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for i in modbind.get(name, []):
            if i not in keep:
                keep.add(i)
                queue.extend(u for u in used(top[i]) if u not in seen)
    missing = {n for n in need if n not in modbind and n not in dir(__builtins__) and n not in bound_in_main(body, keep_pre + after_tabs + [nw] + foot) and n != "tab_nass"}
    # ---- write the source
    out = ['"""\nGENERATED by sync_nass_mirror.py from app.py of the RMA dashboard (JSA-Dashboards/rma-production-map): its NASS Production tab as its own app.\n'
           'Do not edit here; change app.py there and run the sync again.\n"""\n']
    tail = ""
    for i in sorted(keep):
        n = top[i]
        t = text(lines, n)
        if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test):
            tail = t                                                  # `if __name__ == "__main__": main()` goes after main() is defined
            continue
        if isinstance(n, (ast.Import, ast.ImportFrom)) and "draw_auth" in t and not locked:
            continue
        out.append(t)
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            out.append("\n")
    main_src = [text(lines, mn).split("\n", 1)[0] + "\n"]              # `def main():`
    for i in keep_pre:
        main_src.append(text(lines, body[i]))
    main_src.append(
        "    # The mirror has one page, not tabs: the NASS block runs as if its tab were open.\n"
        "    class _Always:\n"
        "        open = True\n\n"
        "        def __enter__(self):\n"
        "            return self\n\n"
        "        def __exit__(self, *a):\n"
        "            return False\n\n"
        "    tab_nass = _Always()\n"
        "    _tls.ho_scope = True\n\n")
    main_src.append(text(lines, body[nw]))
    for i in foot:
        main_src.append(text(lines, body[i]))
    body_txt = "".join(main_src)
    out.append("\n" + body_txt + "\n\n" + tail)
    code = "".join(out)

    for old, new in SWAPS:
        if code.count(old) != 1:
            fail(f"expected exactly one {old!r} in the generated code, found {code.count(old)}")
        code = code.replace(old, new)
    a = code.find(ASOF_OLD_START)
    b = code.find('"""', a) if a >= 0 else -1
    if a < 0 or b < 0:
        fail("could not find the NASS-versus-RMA line in the header to replace")
    code = code[:a] + ASOF_NEW + code[b + 3:]
    if not locked:
        code = "\n".join(l for l in code.split("\n") if l.strip() != "rma_data   = load_data()")
        if "= load_data()" in code or "DATA_PATH" in code:
            fail("the RMA workbook loader is still used in the mirror")
    try:
        compile(code, "app.py", "exec")
    except SyntaxError as e:
        fail(f"the generated app.py does not compile: {e}")

    os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, ".streamlit"), exist_ok=True)
    with open(os.path.join(out_dir, "app.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(code)
    for lg in LOGOS:
        shutil.copy2(os.path.join(HERE, "assets", lg), os.path.join(out_dir, "assets", lg))
    shutil.copy2(os.path.join(HERE, ".streamlit", "config.toml"), os.path.join(out_dir, ".streamlit", "config.toml"))
    imports = {l.split()[1].split(".")[0] if l.startswith("import") else l.split()[1].split(".")[0] for l in code.split("\n") if l.startswith(("import ", "from "))}
    reqs = ["streamlit>=1.35.0", "pandas>=2.0.0", "numpy>=1.24.0", "plotly>=5.24.0"]
    if "geopandas" in imports:
        reqs.append("geopandas>=1.0.0")
    if "shapely" in imports:
        reqs.append("shapely>=2.0.0")
    for extra in ("openpyxl", "pydeck"):
        if extra in imports:
            reqs.append(extra + ">=0")
    with open(os.path.join(out_dir, "requirements.txt"), "w", newline="\n") as fh:
        fh.write("\n".join(reqs) + "\n")
    with open(os.path.join(out_dir, ".gitignore"), "w", newline="\n") as fh:
        fh.write(".streamlit/secrets.toml\n.env\n__pycache__/\n*.pyc\n")
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# JSA NASS Production\n\nUSDA NASS production by state, ASD district and county: the first tab of the RMA dashboard as its own app.\n\n"
                 "**This folder is generated.** `app.py` is produced by `sync_nass_mirror.py` in `JSA-Dashboards/rma-production-map` from that dashboard's `app.py`; "
                 "change the NASS tab there and run the sync, do not edit here.\n\n"
                 "    streamlit run app.py\n\nSecret needed: `NASS_API_KEY` (Streamlit Cloud: Settings > Secrets). County boundaries come from a public URL; there is no database.\n")
    kept_n = len(keep)
    print(f"wrote {out_dir}\n  app.py: {code.count(chr(10))} lines (dashboard app.py: {len(lines)}), {kept_n} module-level statements kept of {len(top) - 1}, gate {'kept' if locked else 'dropped'}")
    print("  requirements:", ", ".join(r.split('>')[0] for r in reqs))
    if missing:
        print("  names used but not defined (check):", sorted(missing))


def bound_in_main(body, idxs):
    out = set()
    for i in idxs:
        out |= bound(body[i])
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(os.path.abspath(args[0]) if args else os.path.abspath(os.path.join(HERE, "..", "nass-production")), "--locked" in sys.argv)
