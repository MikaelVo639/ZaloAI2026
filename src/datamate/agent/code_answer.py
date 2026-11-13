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
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from datamate.semantics.text import fold

CODE_MAX_NEW_TOKENS = 700
CODE_TIMEOUT_SECONDS = 120.0  # one generation of up to CODE_MAX_NEW_TOKENS tokens
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

CODE_SYSTEM = """You answer one Vietnamese question about a shop by writing Python.
Write exactly one function and nothing else:

def answer(sales, today):
    ...
    return {"type": ..., "answer": ...}

`sales` is a list of dicts, one per order line, already cleaned. Lines dated after today
are already removed. Fields (any may be None when the shop does not record it):
{fields}
`today` (datetime.date) is the day the question is asked: "hôm nay", "tháng này",
"tuần trước" are relative to it. The data starts on {first_day}.
Values seen in the data: {values}
Already available (never import anything): date, timedelta, Counter, defaultdict, math,
statistics, fold (fold(text) lowercases and removes Vietnamese accents, for matching names).

Shop rules (follow them exactly):
{rules}

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
    system = CODE_SYSTEM
    for key, value in (("{fields}", fields), ("{first_day}", profile["first_day"]),
                       ("{values}", values), ("{rules}", rule_text(rulebook, question))):
        system = system.replace(key, value)
    shots = "\n\n".join(f"Câu hỏi: {q}\n```python\n{code}\n```" for q, code in examples)
    user = (f"{shots}\n\nHôm nay của các ví dụ là một ngày bất kỳ. Câu hỏi thật (hôm nay là "
            f"{today.isoformat()}):\nCâu hỏi: {question}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
_SAFE_BUILTINS = {name: __builtins__[name] if isinstance(__builtins__, dict)
                  else getattr(__builtins__, name) for name in """
    abs all any bool dict divmod enumerate filter float format frozenset int isinstance len
    list map max min next pow range reversed round set slice sorted str sum tuple zip
    ValueError KeyError ZeroDivisionError TypeError IndexError StopIteration Exception
""".split()}


def check_code(code: str) -> str | None:
    """Reason the code is refused, or None. No imports, no dunder access, no I/O names."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Code does not parse: {exc.msg}"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if not any(f.name == "answer" for f in functions):
        return "Code has no answer function"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
                             ast.AsyncFunctionDef, ast.Await, ast.ClassDef, ast.Delete)):
            return f"Code uses a forbidden statement: {type(node).__name__}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return "Code reads a private attribute"
        if isinstance(node, ast.Name) and (node.id in _FORBIDDEN_NAMES or node.id.startswith("__")):
            return f"Code uses a forbidden name: {node.id}"
    for node in tree.body:  # only definitions at top level: nothing runs on load
        if not isinstance(node, ast.FunctionDef) and not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
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


def profile(cache: dict[str, Any], today: date) -> dict[str, Any]:
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
    return {"fields": fields, "values": values,
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


def run_code(cache: dict[str, Any], code: str, today: date) -> Any:
    """Worker task: run ``answer(sales, today)`` on a fresh copy of the lines."""
    refused = check_code(code)
    if refused:
        return {"refused": refused}
    namespace: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS, "date": date, "timedelta": timedelta,
        "Counter": Counter, "defaultdict": defaultdict, "math": math,
        "statistics": statistics, "fold": fold}
    exec(compile(code, "<model-code>", "exec"), namespace)  # noqa: S102 - checked above
    lines = [dict(line) for line in sales_lines(cache, today)]
    return {"value": _plain(namespace["answer"](lines, today))}


# ---------------------------------------------------------------- answers

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
