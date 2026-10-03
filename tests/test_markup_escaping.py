"""Filesystem names must survive console rendering unchanged.

WHY: the sorter prints real filenames and folder paths through Rich, which parses
`[...]` as MARKUP. A folder literally named `[bold]Album` rendered as `Album` —
and that rendered line is the last human check before folders move on the real
NAS, so the user was consenting to a name they were never shown. The same bug
silently deleted the `⚠️ [derived]` warning (lowercase `[derived]` is a valid tag;
`[REVIEW]` survived only because Rich's tag regex needs a lowercase first char).

WHY extract the statements via AST rather than call the functions: these print
sites sit inside a large progress-reporting loop that is impractical to drive.
Copying the f-strings into this test body would let production regress silently —
the exact failure mode this file exists to prevent (see the
`2026-10-03-vacuous-self-implementing-test` lesson).
"""
import ast
import inspect
import io
import re
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table
from rich.markup import escape

import sort as S

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "sort.py").read_text()

BAD = "[bold]Weird[cyan]Name"
BAD_CAT = "Media/[green]X"
BAD_REASON = "matched by [red]rule"


# Interpolations whose VALUE is a real string that could contain "[...]".
# Deliberately an ALLOW-LIST of string-valued expressions, not a denylist of
# variable names: counters (result['reverted'], len(files)), config values and
# thresholds are safe to print raw, and flagging them buried the real findings.
USER_DATA_NAMES = {
    "fname", "cat", "src_name", "tgt", "tgt_rel", "prefix", "suggested",
    "reason_short",
}
# NOT in the list on purpose: `size_str` (DedupScanner._format_size returns
# "<float> <B|KB|MB|GB|TB|PB>" — structurally incapable of containing brackets),
# counts, and config values. Escaping them would be noise, not safety.
# Subscript/attribute sources that carry a filesystem name.
USER_DATA_SOURCES = {"fe": "path", "fm": "source", "dir_path": "name"}


def _placeholder_key(expr):
    """Normalise an f-string expression to a comparable key, or None if it is
    not a user-data string."""
    if isinstance(expr, ast.Name) and expr.id in USER_DATA_NAMES:
        return expr.id
    if isinstance(expr, ast.Subscript):
        src = ast.unparse(expr.value)
        attr = expr.slice.value if isinstance(expr.slice, ast.Constant) else None
        want = USER_DATA_SOURCES.get(src)
        if want is not None and (attr is None or attr == want):
            return f"{src}[{attr}]" if attr else src
        return None
    if isinstance(expr, ast.Attribute) and expr.attr in ("name",):
        return ast.unparse(expr)
    if isinstance(expr, ast.Call):
        txt = ast.unparse(expr)
        if txt.startswith("escape("):
            return _placeholder_key(expr.args[0]) if expr.args else None
        # str(Path(x).relative_to(root)) — a rendered filesystem path.
        if "Path(" in txt or ".relative_to(" in txt:
            return txt
        return None
    return None


def _placeholders(node):
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.FormattedValue):
            k = _placeholder_key(sub.value)
            if k:
                out.add(k)
    return out


def _escaped(node):
    """Placeholder keys that are wrapped in escape(...) at the interpolation."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == "escape" and sub.args:
            k = _placeholder_key(sub.args[0])
            if k:
                out.add(k)
    return out


def _unescaped_user_data():
    """Every rich-rendering site that interpolates user data WITHOUT escaping it.

    LINT DIRECTION MATTERS: this scans for UNESCAPED sites, not for escaped ones.
    An earlier version collected only statements that already contained
    escape(), so DELETING an escape() made the statement drop out of the test
    suite entirely and the mutation passed. This inverts it: remove an escape
    and the statement is still here, still parsed, and now reported.
    """
    tree = ast.parse(SRC)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("print", "add_row")):
            continue
        seg = ast.get_source_segment(SRC, node) or ""
        if "{" not in seg:
            continue
        used = _placeholders(node)
        if not used:
            continue
        missing = used - _escaped(node)
        if missing:
            findings.append((fn.attr, sorted(missing), seg[:110].replace("\n", " ")))
    return findings


UNESCAPED = _unescaped_user_data()


def _statements_with_escape():
    """Every rich-rendering call in sort.py that escapes an interpolated value.

    Covers `console.print(...)` AND `Table.add_row(...)`: Rich parses markup in
    table cells too, so a category name in the summary table is just as
    misrenderable as one in a print. Matching only `print` left the table cells
    untested — proven by mutation: reverting escape() there changed nothing.

    Matched on the CALL node, so implicit string concatenation across several
    lines is captured whole (a regex over source lines would truncate it)."""
    tree = ast.parse(SRC)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("print", "add_row")):
            continue
        seg = ast.get_source_segment(SRC, node) or ""
        if "escape(" in seg:
            out.append((fn.attr, seg))
    return out


ESCAPED_PRINTS = _statements_with_escape()


def _render(kind, stmt, **ns):
    console = Console(file=io.StringIO(), width=200)
    if kind == "add_row":
        # The receiver is named per-table in production (`tbl`, `f_tbl`); bind
        # every plausible name to one real Table so the extracted call resolves.
        table = Table()
        for _ in range(4):
            table.add_column("c")
        env = {"escape": escape, "cnt": 1, "str": str, "len": len,
               "total_size": 0, "fs": {"total_size": 0}, **ns}
        for name in ("tbl", "f_tbl", "table"):
            env[name] = table
        exec(compile(stmt, "<extracted>", "exec"), env)
        console.print(table)
        return console.file.getvalue()
    # Each statement is exec'd standalone, so the module-level names the REAL
    # production code closes over must be supplied here (e.g. the gate does
    # Path(src).relative_to(nas_root), and sort.py imports Path at module level).
    base = {"escape": escape, "console": console, "Path": Path,
            "str": str, "len": len}
    exec(compile(stmt, "<extracted>", "exec"), {**base, **ns})
    return console.file.getvalue()


def test_sort_py_escapes_where_it_interpolates_names():
    """Guard the guard: if this file finds no escape-bearing prints, someone
    renamed/refactored them and these tests are silently covering nothing."""
    kinds = {k for k, _ in ESCAPED_PRINTS}
    assert len(ESCAPED_PRINTS) >= 11, (
        f"only {len(ESCAPED_PRINTS)} escaping sites found — "
        "the extraction below has stopped matching production"
    )
    # Table cells render markup too; if add_row sites ever vanish from the
    # extraction, their escaping silently stops being tested.
    assert "add_row" in kinds, f"table cells no longer covered: {kinds}"


@pytest.mark.parametrize("kind,stmt", ESCAPED_PRINTS,
                         ids=[f"{k}:{s[:48]}" for k, s in ESCAPED_PRINTS])
def test_markup_like_names_render_verbatim(kind, stmt):
    """A name containing bracket-tags must display as itself, with no literal
    backslash leaking into the output."""
    class _Fe(dict):
        __getattr__ = dict.__getitem__

    fe = _Fe(path=BAD)
    # The gate listing is an `enumerate` body: it reads `fm` and splits `src`
    # into a path relative to `nas_root`.
    nas_root = Path("/nas")
    fm = {"source": str(nas_root / BAD), "target": str(nas_root / BAD_CAT),
          "file_count": 3, "total_size": 1536, "confidence": 85}
    out = _render(kind, stmt, fname=BAD, cat=BAD_CAT, confidence=85,
                  reason_short=BAD_REASON, suggested=BAD, detected_lang="de",
                  src_name=BAD, tgt=BAD_CAT, tgt_rel=BAD_CAT, index=1,
                  prefix="⚠️ [derived] ",
                  fe=fe, dir_path=_Fe(name=BAD), idx=1, entries=[1, 2, 3],
                  fm=fm, src=fm["source"], nas_root=nas_root,
                  fcount=3, size_str="1.5 KB", is_review=False,
                  cat_counts={BAD_CAT: 1},
                  fs={"total_size": 1536, "file_count": 3},
                  fc={"category_path": BAD_CAT, "confidence": 85})
    assert "\\[" not in out, f"literal backslash leaked: {out!r}"

    # WHY name->value, not value-in-statement: the extracted statement refers to
    # VARIABLES (tgt_rel, fname, ...), not the bracketed literals. Matching the
    # literal against the source text never fires, so the check below silently
    # covered nothing — removing one escape() from a site left this test green.
    # Resolve each referenced name to the value we injected and require it back.
    injected = {
        "fname": BAD, "cat": BAD_CAT, "reason_short": BAD_REASON,
        "suggested": BAD, "src_name": BAD, "tgt": BAD_CAT, "tgt_rel": BAD_CAT,
        "dir_path.name": BAD, "prefix": "⚠️ [derived] ",
    }
    referenced = 0
    for name, value in injected.items():
        if name not in stmt:
            continue
        referenced += 1
        assert value in out, (
            f"{name!r} ({value!r}) was altered or dropped by this site: {out!r}"
        )
    # A site that interpolates none of our values cannot be asserted on; keep it
    # honest by requiring the statement to be at least name-aware.
    assert referenced or "escape(" in stmt, f"nothing assertable in: {stmt!r}"


def test_the_derived_warning_is_text_not_markup():
    """The BATCH_3 warning must actually be visible. Before the fix Rich parsed
    `[derived]` as a style tag and dropped it, leaving '⚠️  <name>'."""
    from sort import _confirm_folder_moves
    import builtins, tempfile

    tmp = Path(tempfile.mkdtemp())
    console = Console(file=__import__("io").StringIO(), width=200)
    moves = [{"source": str(tmp / "[bold]Album"), "target": str(tmp / BAD_CAT),
              "file_count": 3, "total_size": 0, "confidence": 85, "_derived": True}]
    orig = builtins.input
    builtins.input = lambda *a, **k: "all"
    try:
        _confirm_folder_moves(moves, tmp, console)
    finally:
        builtins.input = orig
    out = console.file.getvalue()
    assert "⚠️ [derived]" in out, out
    assert "[bold]Album" in out, out


def test_no_rich_site_renders_user_data_unescaped():
    """The lint that catches a REMOVED escape().

    Scans every console.print / Table.add_row in sort.py for interpolating
    filesystem names without escape(). Deleting an escape() leaves the statement
    in place, so it is still scanned here and reported.
    """
    assert not UNESCAPED, (
        "user-controlled values rendered as Rich markup without escape():\n"
        + "\n".join(f"  {kind}: {names} in {seg!r}" for kind, names, seg in UNESCAPED)
    )


def test_lint_detects_a_deliberately_unescaped_site(tmp_path):
    """Non-vacuity for the lint itself: feed it a known-bad snippet and require
    it to be flagged. If this passes silently, the lint is dead code."""
    bad_src = (
        "from rich.markup import escape\n"
        "def go(fname, cat):\n"
        "    console.print(f\"  [bold]{fname}[/] -> {escape(cat)}\")\n"
    )
    tree = ast.parse(bad_src)
    flagged = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("print", "add_row")):
            continue
        if "{" not in (ast.get_source_segment(bad_src, node) or ""):
            continue
        used = _placeholders(node)
        if used and not used <= _escaped(node):
            flagged.append(used)
    assert flagged, "lint failed to flag a known-unescaped interpolation"


def test_extract_still_finds_production_statements():
    """Non-vacuity: the regex/inspect paths used elsewhere must locate real code."""
    fn_src = inspect.getsource(S._classify_with_progress)
    assert "escape(" in fn_src, "classification listing lost its escaping"
    assert len(re.findall(r"escape\(", fn_src)) >= 4