"""Experimental model calculation: when no plan exists, the model writes a small Python
function over the cleaned sales lines, and the worker runs it in a restricted namespace.

The sales lines come from the same adapters as the deterministic executor (valid / cancelled
/ returned status and line revenue already follow the split's rulebook). Lines dated after
``as_of_date`` are removed before the code sees them. Two independent generations must
return the same answer; anything else is an abstain. The path never answers
``unanswerable``: failing to compute proves nothing about the data.

``CODE_EXAMPLES`` are written by the team: invented questions, names and amounts.
"""

from __future__ import annotations

import ast
import csv
import importlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from datamate.contracts import Catalog
from datamate.ingestion.purchases import (
    PurchaseSourceError,
    load_purchase_lines,
    load_purchases,
)
from datamate.ingestion.stock import StockSourceError, load_stock
from datamate.semantics.text import fold

CODE_MAX_NEW_TOKENS = 700
CODE_TIMEOUT_SECONDS = 120.0  # one generation of up to CODE_MAX_NEW_TOKENS tokens
CODE_REPAIRS = 1  # code that raised or returned an unusable result is sent back once
MAX_LIST_ITEMS = 50
MAX_RULE_ROWS = 8
ANSWER_TYPES = ("number", "percent", "string", "date", "boolean", "list")

# Fields of a cleaned sales line, as the model sees them (Decimal -> float, date kept).
FIELDS = (
    ("order_id", "str", "order code; one order has one or more lines"),
    ("day", "date", "order date (datetime.date)"),
    ("minute", "int|None", "minutes after midnight the order was created"),
    ("status", "str", '"valid" counts for revenue and order counts; "cancelled"; "returned"'),
    ("amount", "float", "revenue of the line in VND (line total after line discount)"),
    ("product_id", "str|None", "product code"),
    ("product_name", "str|None", "product name as written in the data"),
    ("product_group", "str|None", "product group"),
    ("base_unit", "str|None", "base unit of the product"),
    ("quantity_base", "float|None", "quantity sold in the base unit"),
    ("quantity_sold", "float|None", "quantity in the selling unit"),
    ("unit_cost_at_sale", "float|None", "cost of one base unit when sold"),
    ("customer_id", "str|None", "customer code; None for walk-in customers"),
    ("customer_name", "str|None", "customer name"),
    ("customer_group", "str|None", "customer group"),
    ("channel", "str|None", "sales channel as written"),
    ("payment_method", "str|None", "payment method as written"),
    ("staff_id", "str|None", "seller staff code"),
)
_VALUE_FIELDS = ("status", "product_group", "customer_group", "channel", "payment_method",
                 "base_unit")
# Other cleaned records, offered as global lists of dicts when the shop's files have them.
EXTRA_TABLES: dict[str, tuple[str, tuple[tuple[str, str, str], ...]]] = {
    "purchases": ("purchase receipts (phiếu nhập hàng) dated up to today", (
        ("receipt_id", "str", "receipt code"), ("day", "date", "receipt date"),
        ("total", "float", "receipt total in VND"),
        ("supplier", "str|None", "supplier name (or code when no name is recorded)"),
        ("cancelled", "bool", "True for a cancelled receipt; leave those out"))),
    "purchase_lines": ("lines of the purchase receipts dated up to today", (
        ("receipt_id", "str", "receipt code"), ("day", "date", "receipt date"),
        ("product_id", "str|None", "product code"), ("product_name", "str|None", "product name"),
        ("unit", "str", "purchase unit of the line"), ("quantity", "float", "quantity in that unit"),
        ("unit_price", "float", "price of one purchase unit in VND"),
        ("cancelled", "bool", "True when the receipt is cancelled"))),
    "stock": ("stock at the end of today, one row per product", (
        ("product_id", "str|None", "product code"), ("product_name", "str|None", "product name"),
        ("base_unit", "str|None", "base unit"), ("quantity", "float", "quantity in the base unit"))),
}

# Globals that exist only when the shop has the records; code reading a missing one gets
# "no answer" instead of a repair round.
OPTIONAL_GLOBALS = ("purchases", "purchase_lines", "stock", "calendar")

CODE_SYSTEM = """You answer one Vietnamese question about a shop by writing Python.
Write exactly one function and nothing else:

def answer(sales, today):
    ...
    return {"type": ..., "answer": ...}

`sales` is a list of dicts, one per order line, already cleaned. Lines dated after today
are already removed. Fields (any may be None when the shop does not record it):
{fields}
{extra}`today` (datetime.date) is the day the question is asked: "hôm nay", "tháng này",
"tuần trước" are relative to it. The data starts on {first_day}.
Values seen in the data: {values}
Already available: date, timedelta, datetime, Counter, defaultdict, math, statistics, re,
itertools, fold (fold(text) lowercases and removes Vietnamese accents, for matching names).
Only datetime, math, statistics, collections, itertools, functools, re, decimal, calendar,
heapq, bisect and string can be imported; no files, no pandas.

Shop rules (follow them exactly):
{rules}

How to read the usual questions:
- "đơn", "bill", "hoá đơn" are orders: count distinct order_id, never lines. An average per
  order divides by the number of distinct order_id.
- Thresholds and rankings on money ("ngày có doanh thu từ X", "khách mua nhiều tiền nhất",
  "đơn trên X") apply to totals: sum amount per day / customer / order first, then compare.
- "N ngày gần nhất", "N ngày qua" are the N days ending today:
  today - timedelta(days=N - 1) <= day <= today.
- Purchases ("nhập hàng", "giá nhập", "nhà cung cấp") come only from the purchase lists,
  stock ("tồn", "trong kho", "còn lại bao nhiêu") only from the stock list; never from
  `sales`: return None when that list is not given above. Debts ("nợ", "mua chịu", "trả
  nợ") are not in these records: return None.
- Filter only on values listed above or names written in the question; when the question
  names a kind of product or customer that no listed value matches, return None.
- Money is a whole number of VND: return round(x) as int. Names are returned bare, without
  quantities or codes added.

Return {"type": T, "answer": A} with T one of:
- "number": money rounded to whole VND, counts as int, other numbers rounded to 2 decimals.
- "percent": the percentage rounded to 2 decimals (12.5% -> 12.5).
- "date": "YYYY-MM-DD".  "string": one name exactly as written in the data.
- "list": names or numbers in the order the question asks.  "boolean": True or False.
Return None when the data lacks a field the question needs, when the asked period is not
covered by the data, or when you are not sure what is asked.
Output only the code in one ```python block."""

# (question, code). Synthetic: invented names, dates and amounts.
CODE_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("Thứ bảy nào trong tháng 3/2026 bán được nhiều tiền nhất?",
     '''def answer(sales, today):
    totals = defaultdict(float)
    for s in sales:
        if s["status"] == "valid" and s["day"].year == 2026 and s["day"].month == 3 \\
                and s["day"].weekday() == 5:
            totals[s["day"]] += s["amount"]
    if not totals:
        return None
    best = max(totals, key=lambda d: (totals[d], -d.toordinal()))
    return {"type": "date", "answer": best.isoformat()}'''),
    ("tuần trước trung bình mỗi đơn có mấy dòng hàng",
     '''def answer(sales, today):
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=6)
    lines = Counter(s["order_id"] for s in sales
                    if s["status"] == "valid" and start <= s["day"] <= end)
    if not lines:
        return None
    return {"type": "number", "answer": round(sum(lines.values()) / len(lines), 2)}'''),
    ("Từ 18h đến trước 21h tháng này bán được bao nhiêu % doanh thu của tháng?",
     '''def answer(sales, today):
    month = [s for s in sales if s["status"] == "valid"
             and (s["day"].year, s["day"].month) == (today.year, today.month)]
    if any(s["minute"] is None for s in month):
        return None
    total = sum(s["amount"] for s in month)
    evening = sum(s["amount"] for s in month if 18 * 60 <= s["minute"] < 21 * 60)
    if not total:
        return None
    return {"type": "percent", "answer": round(evening * 100 / total, 2)}'''),
    ("Tháng 2/2026 khách Lê Văn Tùng mua mấy đơn?",
     '''def answer(sales, today):
    orders = {s["order_id"] for s in sales if s["status"] == "valid"
              and (s["day"].year, s["day"].month) == (2026, 2)
              and s["customer_name"] and fold(s["customer_name"]) == fold("Lê Văn Tùng")}
    return {"type": "number", "answer": len(orders)}'''),
)


# ---------------------------------------------------------------- prompt

def rule_text(rulebook: str, question: str, limit: int = MAX_RULE_ROWS) -> str:
    """Sections 1-2 style general rules (every row before the first ranking/other section
    is kept when short), plus the rows that share the most word pairs with the question."""
    lines = [line.rstrip() for line in rulebook.splitlines()]
    rows = [line for line in lines if line.startswith("|") and not set(line) <= set("|-: ")]
    general: list[str] = []
    for line in lines:
        if line.startswith("## ") and general and not re.match(r"## [12]\b", line):
            break
        if line.startswith("|") and not set(line) <= set("|-: "):
            general.append(line)
    words = fold(question).split()
    pairs = {" ".join(words[i:i + 2]) for i in range(len(words) - 1)}
    scored = []
    for index, row in enumerate(rows):
        if row in general:
            continue
        text = f" {fold(row)} "
        score = sum(f" {pair} " in text for pair in pairs)
        if score:
            scored.append((-score, index, row))
    chosen = [row for _, _, row in sorted(scored)[:limit]]
    return "\n".join(general + chosen)


def build_code_messages(question: str, today: date, profile: dict[str, Any], rulebook: str,
                        examples: list[tuple[str, str]]) -> list[dict[str, str]]:
    fields = "\n".join(f"  {name} ({kind}): {meaning}" for name, kind, meaning in FIELDS
                       if name in profile["fields"])
    values = "; ".join(f"{name}: {', '.join(map(str, items))}"
                       for name, items in profile["values"].items() if items) or "none"
    extra = ""
    for name in profile.get("tables", ()):
        meaning, columns = EXTRA_TABLES[name]
        extra += (f"Global list `{name}` ({meaning}):\n"
                  + "\n".join(f"  {c} ({kind}): {m}" for c, kind, m in columns) + "\n")
    if not extra:
        extra = "No purchase or stock records are available, only `sales`.\n"
    if profile.get("calendar"):
        first, last = profile["calendar"]
        extra += ("Global dict `calendar` (the shop's lunar calendar, " f"{first} to {last}): "
                  "datetime.date -> {lunar_day, lunar_month, lunar_year, lunar_leap (bool), "
                  "holiday (str, may be empty)}. Use it for Tết and lunar dates (to use the "
                  "calendar module as well, write `import calendar as cal`).\n")
    system = CODE_SYSTEM
    for key, value in (("{fields}", fields), ("{extra}", extra),
                       ("{first_day}", profile["first_day"]),
                       ("{values}", values), ("{rules}", rule_text(rulebook, question))):
        system = system.replace(key, value)
    shots = "\n\n".join(f"Câu hỏi: {q}\n```python\n{code}\n```" for q, code in examples)
    user = (f"{shots}\n\nHôm nay của các ví dụ là một ngày bất kỳ. Câu hỏi thật (hôm nay là "
            f"{today.isoformat()}):\nCâu hỏi: {question}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_messages(messages: list[dict[str, str]], code: str, error: str
                    ) -> list[dict[str, str]]:
    """Ask once more after the code failed, with the error it raised."""
    return [*messages, {"role": "assistant", "content": f"```python\n{code}\n```"},
            {"role": "user", "content": (
                f"Chạy code bị lỗi: {error[:300]}. Sửa lại hàm answer cho đúng câu hỏi (lưu ý "
                "trường có thể là None). Chỉ trả code trong một khối ```python.")}]


def extract_code(text: str) -> str | None:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    code = (match.group(1) if match else text).strip()
    return code if "def answer(" in code else None


# ---------------------------------------------------------------- sandbox

_FORBIDDEN_NAMES = frozenset("""
    open exec eval compile __import__ getattr setattr delattr globals locals vars input
    breakpoint exit quit help memoryview type object super classmethod staticmethod property
    dir id hash iter aiter anext __builtins__ __loader__ __spec__
""".split())
_BUILTIN_NAMES = """
    abs all any bool callable chr dict divmod enumerate filter float format frozenset int
    isinstance issubclass len list map max min next ord pow range repr reversed round set slice
    sorted str sum tuple zip
    ArithmeticError AttributeError Exception IndexError KeyError LookupError NameError
    NotImplementedError OverflowError RuntimeError StopIteration TypeError ValueError
    ZeroDivisionError
""".split()
# Standard modules the code may import or use by name. The code sees a view holding only
# their public, non-module attributes (statistics.sys, calendar.sys stay out of reach).
_MODULES = ("datetime", "math", "statistics", "collections", "itertools", "functools", "re",
            "decimal", "calendar", "heapq", "bisect", "string")


def _module_view(name: str) -> SimpleNamespace:
    module = importlib.import_module(name)
    return SimpleNamespace(**{key: value for key, value in vars(module).items()
                              if not key.startswith("_")
                              and not isinstance(value, ModuleType)})


_VIEWS = {name: _module_view(name) for name in _MODULES}


def _safe_import(name: str, globals: Any = None, locals: Any = None,  # noqa: A002
                 fromlist: Any = (), level: int = 0) -> SimpleNamespace:
    if level or name not in _VIEWS:
        raise ImportError(f"only these modules can be imported: {', '.join(_MODULES)}")
    return _VIEWS[name]


def _print(*args: Any, **kwargs: Any) -> None:
    """print() is accepted and ignored: the answer is the returned dict."""


_SAFE_BUILTINS: dict[str, Any] = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in _BUILTIN_NAMES}
_SAFE_BUILTINS.update(__import__=_safe_import, print=_print)


def _globals() -> dict[str, Any]:
    """Names every calculation starts with (the prompt lists them)."""
    return {"__builtins__": _SAFE_BUILTINS, "date": date, "timedelta": timedelta,
            "datetime": _VIEWS["datetime"], "Counter": Counter, "defaultdict": defaultdict,
            "math": _VIEWS["math"], "statistics": _VIEWS["statistics"], "re": _VIEWS["re"],
            "itertools": _VIEWS["itertools"], "fold": fold}


def check_code(code: str) -> str | None:
    """Reason the code is refused, or None. Imports only of the allowed standard modules,
    no dunder access, no I/O names; at top level only definitions, imports and constants."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Code does not parse: {exc.msg}"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if not any(f.name == "answer" for f in functions):
        return "Code has no answer function"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refused = [a.name for a in node.names if a.name not in _VIEWS]
            if refused:
                return f"Code imports a module that is not allowed: {refused[0]}"
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in _VIEWS or any(
                    a.name == "*" or a.name.startswith("_") for a in node.names):
                return f"Code imports a module that is not allowed: {node.module}"
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await,
                               ast.ClassDef, ast.Delete)):
            return f"Code uses a forbidden statement: {type(node).__name__}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return "Code reads a private attribute"
        if isinstance(node, ast.Name) and (node.id in _FORBIDDEN_NAMES or node.id.startswith("__")):
            return f"Code uses a forbidden name: {node.id}"
    for node in tree.body:  # nothing but definitions, imports and constants runs on load
        if isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and not any(
                isinstance(child, (ast.Call, ast.Lambda, ast.ListComp, ast.SetComp,
                                   ast.DictComp, ast.GeneratorExp))
                for child in ast.walk(node)):
            continue
        return "Code runs statements outside functions"
    return None


def _line(sale: Any) -> dict[str, Any]:
    row = asdict(sale)
    for key, value in row.items():
        if isinstance(value, Decimal):
            row[key] = float(value)
    return {name: row.get(name) for name, _, _ in FIELDS}


def sales_lines(cache: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Cleaned lines up to ``today`` (cached per business and day in the worker)."""
    sales, _ = cache["sales"]
    key = (cache.get("sales_key"), today)
    if cache.get("code_lines_key") != key:
        cache["code_lines_key"] = key
        cache["code_lines"] = tuple(_line(s) for s in sales if s.day <= today)
    return cache["code_lines"]


def _rows(records: Any, columns: tuple[tuple[str, str, str], ...]) -> tuple[dict, ...]:
    rows = []
    for record in records:
        row = asdict(record)
        rows.append({name: float(row[name]) if isinstance(row.get(name), Decimal) else row.get(name)
                     for name, _, _ in columns})
    return tuple(rows)


def extra_tables(cache: dict[str, Any], catalog: Catalog, today: date) -> dict[str, tuple]:
    """Purchases, purchase lines and today's stock, when their sources load (cached)."""
    key = (cache.get("sales_key"), catalog.readme, today)
    if cache.get("code_tables_key") == key:
        return cache["code_tables"]
    tables: dict[str, tuple] = {}
    try:
        book = load_purchases(catalog)
        tables["purchases"] = _rows((r for r in book.receipts if r.day <= today),
                                    EXTRA_TABLES["purchases"][1])
    except (PurchaseSourceError, ValueError, OSError):
        pass
    try:
        lines = load_purchase_lines(catalog)
        tables["purchase_lines"] = _rows((line for line in lines.lines if line.day <= today),
                                         EXTRA_TABLES["purchase_lines"][1])
    except (PurchaseSourceError, ValueError, OSError):
        pass
    try:
        snapshot = load_stock(catalog, as_of=today)
        if snapshot.day == today:  # never an end-of-data stock for another day
            tables["stock"] = _rows(snapshot.items, EXTRA_TABLES["stock"][1])
    except (StockSourceError, ValueError, OSError):
        pass
    cache["code_tables_key"], cache["code_tables"] = key, {k: v for k, v in tables.items() if v}
    return cache["code_tables"]


def lunar_calendar(cache: dict[str, Any], path: Path) -> dict[date, dict[str, Any]]:
    """The split's calendar.csv: solar date -> lunar day/month/year, leap flag, holiday."""
    if cache.get("code_calendar_path") != str(path):
        days: dict[date, dict[str, Any]] = {}
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                solar = row.get("date") or row.get("solar_date")
                if not solar:
                    raise KeyError("calendar has no date column")
                days[date.fromisoformat(solar.strip())] = {
                    "lunar_day": int(row["lunar_day"]), "lunar_month": int(row["lunar_month"]),
                    "lunar_year": int(row["lunar_year"]),
                    "lunar_leap": (row.get("lunar_leap") or "").strip().lower() == "true",
                    "holiday": (row.get("holiday") or "").strip()}
        cache["code_calendar_path"], cache["code_calendar"] = str(path), days
    return cache["code_calendar"]


def profile(cache: dict[str, Any], today: date, catalog: Catalog | None = None,
            calendar: Path | None = None) -> dict[str, Any]:
    """Recorded fields, first day and short lists of label values, for the prompt."""
    lines = sales_lines(cache, today)
    fields = [name for name, _, _ in FIELDS
              if name in ("order_id", "day", "status", "amount")
              or any(line[name] is not None for line in lines)]
    values = {}
    for name in _VALUE_FIELDS:
        if name in fields:
            seen = Counter(line[name] for line in lines if line[name] is not None)
            values[name] = [value for value, _ in seen.most_common(25)]
    days = [line["day"] for line in lines]
    tables = extra_tables(cache, catalog, today) if catalog is not None else {}
    span = None
    if calendar is not None:
        try:
            known = lunar_calendar(cache, calendar)
            span = [min(known).isoformat(), max(known).isoformat()] if known else None
        except (OSError, KeyError, ValueError):
            span = None
    return {"fields": fields, "values": values, "tables": sorted(tables), "calendar": span,
            "first_day": min(days).isoformat() if days else "unknown", "lines": len(lines)}


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def _call(namespace: dict[str, Any], code: str, *args: Any) -> dict[str, Any]:
    """Run the checked code. Reading a table or the calendar this shop does not have is a
    deliberate "no answer", not an error to repair."""
    exec(compile(code, "<model-code>", "exec"), namespace)  # noqa: S102 - checked first
    try:
        return {"value": _plain(namespace["answer"](*args))}
    except NameError as exc:
        if exc.name in OPTIONAL_GLOBALS:
            return {"value": None, "missing": exc.name}
        raise


def run_code(cache: dict[str, Any], code: str, today: date,
             catalog: Catalog | None = None, calendar: Path | None = None) -> Any:
    """Worker task: run ``answer(sales, today)`` on fresh copies of the records."""
    refused = check_code(code)
    if refused:
        return {"refused": refused}
    namespace = _globals()
    if calendar is not None:
        try:
            namespace["calendar"] = {d: dict(v) for d, v in lunar_calendar(cache, calendar).items()}
        except (OSError, KeyError, ValueError):
            pass
    if catalog is not None:
        for name, rows in extra_tables(cache, catalog, today).items():
            namespace[name] = [dict(row) for row in rows]
    lines = [dict(line) for line in sales_lines(cache, today)]
    filtered = unknown_filter(code, lines)
    if filtered is not None:
        return {"refused": filtered}
    result = _call(namespace, code, lines, today)
    result = _bare_names(result, cache, today, namespace)
    unknown = _unknown_name(result.get("value"), cache, today, namespace)
    if unknown is not None:
        return {"refused": f"Code answer names a value that is not in the data: {unknown[:60]}"}
    return result


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_DECORATION = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*$")


def _bare_names(result: dict[str, Any], cache: dict[str, Any], today: date,
                namespace: dict[str, Any]) -> dict[str, Any]:
    """Names the code decorated ("Gạo ST25 (98.0)") without the decoration, when the bare
    name is in the data and the decorated one is not."""
    value = result.get("value")
    if not isinstance(value, dict) or value.get("type") not in ("string", "list"):
        return result
    answer = value.get("answer")
    items = [answer] if isinstance(answer, str) else answer if isinstance(answer, list) else []
    if not any(isinstance(item, str) and _DECORATION.search(item) for item in items):
        return result
    probe = {"type": "list", "answer": []}
    cleaned = []
    for item in items:
        bare = _DECORATION.sub("", item) if isinstance(item, str) else item
        if bare != item and bare and _unknown_name({**probe, "answer": [item]}, cache, today,
                                                    namespace) is not None \
                and _unknown_name({**probe, "answer": [bare]}, cache, today, namespace) is None:
            item = bare
        cleaned.append(item)
    answer = cleaned[0] if isinstance(answer, str) else cleaned
    return {**result, "value": {**value, "answer": answer}}


def _unknown_name(value: Any, cache: dict[str, Any], today: date,
                  namespace: dict[str, Any]) -> str | None:
    """First name in a string or list answer that no record the code read holds. A name
    the code made up, or decorated ("Lê Văn Tùng (KH01)"), is not one the data can have."""
    if not isinstance(value, dict) or value.get("type") not in ("string", "list"):
        return None
    answer = value.get("answer")
    names = [answer] if isinstance(answer, str) else (
        [item for item in answer if isinstance(item, str)] if isinstance(answer, list) else [])
    names = [name.strip() for name in names if not _ISO_DAY.fullmatch(name.strip())]
    if not names:
        return None
    known = {text.strip() for line in sales_lines(cache, today) for text in line.values()
             if isinstance(text, str)}
    for rows in (namespace.get(name) for name in EXTRA_TABLES):
        for row in rows or ():
            known.update(text.strip() for text in row.values() if isinstance(text, str))
    for day in (namespace.get("calendar") or {}).values():
        if isinstance(day, dict) and isinstance(day.get("holiday"), str):
            known.add(day["holiday"].strip())
    return next((name for name in names if name not in known), None)


# ---------------------------------------------------------------- answers

# How the question fixes the answer type, measured on the TRAIN answers (3,000 questions):
# "%" (not the "5%" of a product name), "phần trăm", "tỷ lệ" -> percent, and every percent
# answer carries it; "ngày nào", "hôm nào" -> date 127 of 129; a closing "... không?" -> boolean 18 of 19,
# and every boolean question has a yes/no word. Model code that returns another type
# answered a different question.
_PERCENT_CUE = re.compile(r"phan tra|ty le|ti le")
_PERCENT_SIGN = re.compile(r"(?<![\d.,])(?<![\d.,]\s)%")  # "bao nhiêu %", not "kem 5%"
_DATE_CUE = re.compile(r"(?:ngay|hom) nao|ngay bao nhieu")
_YES_NO_END = re.compile(r"(?:\bkhong|dung khong|phai khong|hay khong|\bko)\s*$")
_YES_NO_WORD = re.compile(r"\b(?:khong|ko|k|hok|khoong|hong)\b|co phai|phai khong")
# A negative amount is only asked for as a difference.
# "lãi ròng" may be a loss; a negative gross profit ("lãi gộp") is a cost read wrongly.
_DIFFERENCE = re.compile(r"chenh lech|tang|giam|lech|hon|kem|lo\b|lai rong|am\b|so voi|tru")


def implausible(question: str, kind: str, answer: Any) -> str | None:
    """Why a model-computed answer cannot answer this question, or None.

    Zero is a train answer 2 times in 1,884 numbers and 2 in 578 percents; model code
    that filters on a misspelt name or a wrong field returns exactly that zero."""
    folded = fold(question).strip(" ?.!")
    percent = bool(_PERCENT_SIGN.search(question) or _PERCENT_CUE.search(folded))
    if percent != (kind == "percent"):
        return "Code answer type does not fit the question (percent)"
    if _DATE_CUE.search(folded) and kind != "date":
        return "Code answer type does not fit the question (date)"
    if bool(_YES_NO_END.search(folded)) and kind != "boolean":
        return "Code answer type does not fit the question (yes/no)"
    if kind == "boolean" and not _YES_NO_WORD.search(folded):
        return "Code answer type does not fit the question (yes/no)"
    if kind in ("number", "percent") and answer == 0:
        return "Code answer is zero"
    if kind == "number" and answer < 0 and not _DIFFERENCE.search(folded):
        return "Code answer is negative"
    return None


# Money questions ("doanh thu", "tiền", "giá", "lãi", "vốn"): the rulebook rounds money, and
# an average of money, to whole VND, half up. Counts and quantities are left as they are.
_MONEY_CUE = re.compile(r"\b(?:doanh thu|dthu|tien|gia|lai|loi nhuan|von|chi phi|gia tri)\b")
_HOW_MANY = re.compile(r"\b(?:bao nhieu|bnhiu|bao nhiu|bn|may|so luong) (?!tien\b)[a-z]+")


def round_money(question: str, kind: str, answer: Any) -> Any:
    """``answer`` rounded to whole VND when the question asks an amount of money."""
    folded = f" {fold(question)} "
    if (kind != "number" or isinstance(answer, int) or abs(answer) < 100
            or not _MONEY_CUE.search(folded) or _HOW_MANY.search(folded)
            and not re.search(r"\bbao nhieu tien\b", folded)):
        return answer
    return int(Decimal(str(answer)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# What the question is about, and what code that answers it must read. Written from the
# wrong model calculations on the TRAIN model benchmark (LOCAL PROXY).
_ORDERS = re.compile(r"\b(?:bao nhieu|bnhiu|bao nhiu|bn|may|so|moi|mot|tung|tren|\d+)"
                     r" (?:don|bill|hoa don)\b(?! (?:vi|gia|bac si|thuoc))")
_PURCHASE = re.compile(r"\bnhap\b|\bnha cung cap\b|\bncc\b")
# Read with accents when the question has them: "Tôn lạnh" is a product, "tồn" is stock.
_STOCK = re.compile(r"\btồn\b|\btrong kho\b|\bcòn (?:lại )?(?:bao nhiêu|bnhiu|bao nhiu|bn|mấy)\b")
_STOCK_FOLDED = re.compile(r"\bton\b|\btrong kho\b|\bcon (?:lai )?(?:bao nhieu|bnhiu|bao nhiu|bn|may)\b")
_LAST_DAYS = re.compile(r"\b(\d+) ngay (?:gan nhat|gan day|qua|vua qua|truoc)\b")
_DEBT = re.compile(r"\bno\b|\bmua chiu\b")
_LINE_THRESHOLD = re.compile(r"\[\s*[\"']amount[\"']\s*\]\s*[<>]=?\s*[\d_.]+"
                             r"|[\d_.]+\s*[<>]=?\s*\w+\s*\[\s*[\"']amount[\"']\s*\]")
_LINE_MAX = re.compile(r"\b(?:max|min)\(\s*\w+\s*,\s*key\s*=\s*lambda\s+(\w+)\s*:\s*\1"
                       r"\s*\[\s*[\"']amount[\"']\s*\]")


def out_of_reach(question: str) -> str | None:
    """Why no code over these records can answer the question, or None."""
    if _DEBT.search(f" {fold(question)} "):
        return "Debts are not in the records the code can read"
    return None


def code_doubt(question: str, code: str) -> str | None:
    """Why this code cannot answer the question, read from the code alone, or None."""
    folded = f" {fold(question)} "
    if _ORDERS.search(folded) and "order_id" not in code:
        return "The question counts orders: use distinct order_id, not lines"
    if _PURCHASE.search(folded) and not re.search(r"\bpurchase(?:s|_lines)\b", code):
        return "The question is about purchases: read `purchases` / `purchase_lines`"
    lowered = unicodedata.normalize("NFC", question).lower()
    stock = _STOCK.search(lowered) if fold(lowered) != lowered else _STOCK_FOLDED.search(folded)
    if stock and not re.search(r"\bstock\b", code):
        return "The question is about stock: read `stock`"
    for days in _LAST_DAYS.findall(folded):
        if re.search(rf"timedelta\(\s*(?:days\s*=\s*)?{days}\s*\)", code) and not re.search(
                rf"\b{days}\s*-\s*1\b|\b{int(days) - 1}\b", code):
            return (f"\"{days} ngày gần nhất\" are the {days} days ending today: "
                    f"today - timedelta(days={int(days) - 1}) <= day <= today")
    if _LINE_THRESHOLD.search(code):
        return "Code compares one line's amount: sum per day, order or customer first"
    if _LINE_MAX.search(code):
        return "Code ranks single lines by amount: sum per customer or product first"
    return None


# Status has a fixed vocabulary ("valid", "cancelled", "returned") that a shop may not use
# every word of; the other labels are the shop's own.
_FILTERED = frozenset((*_VALUE_FIELDS, "product_name", "customer_name")) - {"status"}


def _field(node: ast.AST) -> str | None:
    """``s["field"]``, ``s.get("field")`` or ``fold(...)`` of one, for a filtered field."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "fold" \
            and len(node.args) == 1:
        return _field(node.args[0])
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        key = node.slice.value
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant)):
        key = node.args[0].value
    else:
        return None
    return key if key in _FILTERED else None


def _texts(node: ast.AST) -> list[str] | None:
    """String constants of a literal, a ``fold("...")`` or a collection of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "fold" \
            and len(node.args) == 1:
        return _texts(node.args[0])
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [_texts(item) for item in node.elts]
        return None if any(item is None for item in items) else [t for item in items for t in item]
    return None


def unknown_filter(code: str, lines: Any) -> str | None:
    """First ``field == "value"`` (or ``!=``, ``in [...]``) the code filters on whose value no
    line holds: a group, channel or name that is not in the data filters on nothing."""
    tree = ast.parse(code)
    seen: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left, op, right = node.left, node.ops[0], node.comparators[0]
        if isinstance(op, (ast.Eq, ast.NotEq)):
            pairs = [(_field(left), _texts(right)), (_field(right), _texts(left))]
        elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.List, ast.Tuple, ast.Set)):
            pairs = [(_field(left), _texts(right))]
        else:
            continue
        for field, texts in pairs:
            if field is None or not texts:
                continue
            if field not in seen:
                seen[field] = {fold(str(line[field])).strip() for line in lines
                               if line.get(field) is not None}
            for text in texts:
                if fold(text).strip() not in seen[field]:
                    return f"Code filters {field} on a value that is not in the data: {text[:60]}"
    return None


def normalize_result(value: Any) -> tuple[str, Any] | str:
    """(type, answer) in submission form, or the reason it is unusable."""
    if value is None:
        return "Code returned no answer"
    if not isinstance(value, dict) or set(value) - {"type", "answer"}:
        return "Code result is not {type, answer}"
    kind, answer = value.get("type"), value.get("answer")
    if kind not in ANSWER_TYPES or answer is None:
        return f"Code result type is not supported: {kind!r}"

    def number(item: Any) -> int | float | None:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            return None
        rounded = round(float(item), 2)
        return int(rounded) if rounded == int(rounded) else rounded

    if kind in ("number", "percent"):
        converted = number(answer)
        return (kind, converted) if converted is not None else "Code number is not finite"
    if kind == "boolean":
        return (kind, answer) if isinstance(answer, bool) else "Code boolean is not True/False"
    if kind == "date":
        try:
            return kind, date.fromisoformat(str(answer)).isoformat()
        except ValueError:
            return "Code date is not YYYY-MM-DD"
    if kind == "string":
        text = str(answer).strip() if isinstance(answer, str) else ""
        return (kind, text) if text else "Code string is empty"
    if not isinstance(answer, list) or not answer or len(answer) > MAX_LIST_ITEMS:
        return "Code list is empty or too long"
    items = []
    for item in answer:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        elif number(item) is not None:
            items.append(number(item))
        else:
            return "Code list holds an unsupported item"
    return kind, items
