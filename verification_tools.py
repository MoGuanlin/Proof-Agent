import ast
import math
import re
from dataclasses import dataclass

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    import sympy  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    sympy = None


SAFE_FUNCTIONS = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "cos": math.cos,
    "exp": math.exp,
    "log": math.log,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
SAFE_CONSTANTS = {"e": math.e, "pi": math.pi}
SAFE_CALLABLE_NAMES = set(SAFE_FUNCTIONS) | set(SAFE_CONSTANTS) | {"math"}
ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Load,
    ast.Name,
    ast.Constant,
    ast.Call,
    ast.Attribute,
)


class _MathProxy:
    def __getattr__(self, name):
        if name in SAFE_FUNCTIONS:
            return SAFE_FUNCTIONS[name]
        if name in SAFE_CONSTANTS:
            return SAFE_CONSTANTS[name]
        raise AttributeError(name)


MATH_PROXY = _MathProxy()


@dataclass
class VerificationReport:
    status: str
    mode: str
    summary: str
    details: list[dict]
    notes: str = ""
    symbolic: dict | None = None

    def to_dict(self):
        return {
            "status": self.status,
            "mode": self.mode,
            "summary": self.summary,
            "details": self.details,
            "notes": self.notes,
            "symbolic": self.symbolic or {},
        }


def _normalize_expression(expr):
    text = str(expr or "").strip()
    return (
        text.replace("^", "**")
        .replace("π", "pi")
        .replace("−", "-")
        .replace("–", "-")
    )


def _normalize_variable_names(variable_names, fallback="x"):
    if variable_names is None:
        raw_items = []
    elif isinstance(variable_names, str):
        raw_items = [variable_names]
    else:
        raw_items = list(variable_names)

    names = []
    for item in raw_items:
        name = str(item or "").strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid variable name: {name}")
        if name in SAFE_CALLABLE_NAMES:
            raise ValueError(f"variable name conflicts with reserved symbol: {name}")
        if name not in names:
            names.append(name)
    if not names and fallback:
        names.append(fallback)
    return names


def _validate_ast(node, variable_names):
    allowed_names = set(variable_names) | SAFE_CALLABLE_NAMES
    for child in ast.walk(node):
        if not isinstance(child, ALLOWED_AST_NODES):
            raise ValueError(f"unsupported expression node: {type(child).__name__}")
        if isinstance(child, ast.Name) and child.id not in allowed_names:
            raise ValueError(f"unsupported name: {child.id}")
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                if child.func.id not in SAFE_FUNCTIONS:
                    raise ValueError(f"unsupported function: {child.func.id}")
            elif isinstance(child.func, ast.Attribute):
                if not (
                    isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "math"
                    and child.func.attr in SAFE_FUNCTIONS
                ):
                    raise ValueError("unsupported attribute call")
            else:
                raise ValueError("unsupported call target")
        if isinstance(child, ast.Attribute):
            if not (
                isinstance(child.value, ast.Name)
                and child.value.id == "math"
                and child.attr in (set(SAFE_FUNCTIONS) | set(SAFE_CONSTANTS))
            ):
                raise ValueError("unsupported attribute access")


def compile_expression(expr, variable_names):
    normalized = _normalize_expression(expr)
    node = ast.parse(normalized, mode="eval")
    _validate_ast(node, variable_names)
    code = compile(node, "<verification_expr>", "eval")

    def evaluator(**kwargs):
        env = dict(SAFE_FUNCTIONS)
        env.update(SAFE_CONSTANTS)
        env["math"] = MATH_PROXY
        env.update(kwargs)
        return float(eval(code, {"__builtins__": {}}, env))

    return evaluator, normalized


def _float_or_default(value, default):
    try:
        return float(value)
    except Exception:
        return float(default)


def _int_or_default(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def _build_grid(lo, hi, points):
    if points < 2:
        return [lo, hi]
    step = (hi - lo) / (points - 1)
    return [lo + idx * step for idx in range(points)]


def _evaluate_gap(relation, expr_value, threshold):
    if relation in {"<", "<="}:
        return expr_value - threshold
    if relation in {">", ">="}:
        return threshold - expr_value
    raise ValueError(f"unsupported relation: {relation}")


def _render_relation_target(relation, threshold):
    return f"{relation} {threshold:g}"


def _relation_is_strict(relation):
    return relation in {"<", ">"}


def _gap_is_violation(relation, gap):
    return gap >= 0.0 if _relation_is_strict(relation) else gap > 0.0


def _symbolic_analysis(inequalities, variable_name):
    if sympy is None:
        return {
            "status": "unavailable",
            "reason": "sympy is not installed",
            "items": [],
        }

    var = sympy.symbols(variable_name, real=True)
    items = []
    for item in inequalities:
        label = str(item.get("label", "")).strip() or "ineq"
        expression = _normalize_expression(item.get("expression", ""))
        try:
            parsed = _sympy_parse_expression(expression, [variable_name])
            derivative = sympy.simplify(sympy.diff(parsed, var))
            items.append(
                {
                    "label": label,
                    "expression": str(parsed),
                    "derivative": str(derivative),
                }
            )
        except Exception as exc:
            items.append(
                {
                    "label": label,
                    "error": str(exc),
                }
            )
    return {"status": "ok", "items": items}


def _normalize_symbol_assumptions(assumptions, variable_names):
    allowed = {
        "positive",
        "negative",
        "nonpositive",
        "nonnegative",
        "nonzero",
        "integer",
        "finite",
    }
    if not isinstance(assumptions, dict):
        return {}

    normalized = {}
    valid_names = set(_normalize_variable_names(variable_names, fallback=None))
    for name, raw_flags in assumptions.items():
        key = str(name or "").strip()
        if key not in valid_names:
            continue
        if isinstance(raw_flags, str):
            tokens = re.split(r"[\s,|/]+", raw_flags.strip())
        elif isinstance(raw_flags, (list, tuple, set)):
            tokens = [str(item).strip() for item in raw_flags]
        else:
            continue
        flags = {}
        for token in tokens:
            if token in allowed:
                flags[token] = True
        if flags:
            normalized[key] = flags
    return normalized


def _sympy_locals(variable_names, assumptions=None):
    if sympy is None:
        return {}
    names = _normalize_variable_names(variable_names, fallback=None)
    normalized_assumptions = _normalize_symbol_assumptions(assumptions, names)
    locals_map = {}
    for name in names:
        symbol_kwargs = {"real": True}
        symbol_kwargs.update(normalized_assumptions.get(name, {}))
        locals_map[name] = sympy.symbols(name, **symbol_kwargs)
    locals_map.update(
        {
            "abs": sympy.Abs,
            "acos": sympy.acos,
            "asin": sympy.asin,
            "atan": sympy.atan,
            "cos": sympy.cos,
            "exp": sympy.exp,
            "log": sympy.log,
            "sin": sympy.sin,
            "sqrt": sympy.sqrt,
            "tan": sympy.tan,
            "pi": sympy.pi,
            "e": sympy.E,
        }
    )
    return locals_map


def _sympy_var(variable_name, assumptions=None):
    if sympy is None:
        return None
    return _sympy_locals([variable_name], assumptions=assumptions).get(variable_name)


def _sympy_parse_expression(expression, variable_names, assumptions=None):
    if sympy is None:
        return None
    names = _normalize_variable_names(variable_names, fallback=None)
    normalized = _normalize_expression(expression or "0")
    node = ast.parse(normalized, mode="eval")
    _validate_ast(node, names)
    return sympy.sympify(normalized, locals=_sympy_locals(names, assumptions=assumptions))


def _sympy_parse_scalar(value, variable_names, assumptions=None):
    if sympy is None:
        return None
    if isinstance(value, (int, float)):
        return sympy.Float(value)
    text = str(value or "").strip() or "0"
    return _sympy_parse_expression(text, variable_names, assumptions=assumptions)


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _estimate_lipschitz_bound(expression, variable_name, lo, hi, provided_lipschitz=None, derivative_samples=4097):
    if provided_lipschitz is not None:
        return abs(_float_or_default(provided_lipschitz, 0.0)), "provided"
    if sympy is None or np is None:
        return None, "unavailable"

    try:
        var = _sympy_var(variable_name)
        parsed = _sympy_parse_expression(expression, [variable_name])
        derivative = sympy.diff(parsed, var)
        derivative_fn = sympy.lambdify(var, derivative, modules=["numpy", "math"])
        xs = np.linspace(lo, hi, max(257, int(derivative_samples)))
        vals = derivative_fn(xs)
        vals = np.asarray(vals, dtype=float)
        finite_vals = np.abs(vals[np.isfinite(vals)])
        if finite_vals.size == 0:
            return None, "symbolic_nonfinite"
        bound = float(finite_vals.max())
        if not math.isfinite(bound):
            return None, "symbolic_nonfinite"
        bound = max(bound * 1.05, bound + 1e-12)
        return bound, "symbolic_sampled_derivative"
    except Exception:
        return None, "symbolic_error"


def _numpy_vector_eval(expression, variable_name, xs):
    if sympy is None or np is None:
        return None
    try:
        parsed = _sympy_parse_expression(expression, [variable_name])
        var = _sympy_var(variable_name)
        fn = sympy.lambdify(var, parsed, modules=["numpy", "math"])
        vals = fn(xs)
        vals = np.asarray(vals, dtype=float)
        if vals.shape == ():
            vals = np.full_like(xs, float(vals), dtype=float)
        if vals.shape != xs.shape:
            vals = np.broadcast_to(vals, xs.shape).astype(float)
        return vals
    except Exception:
        return None


def _evaluate_grid_case(
    evaluator,
    normalized_expression,
    variable_name,
    lo,
    hi,
    relation,
    threshold,
    grid_points,
    local_lipschitz,
    lipschitz_source="provided",
):
    if np is not None:
        grid = np.linspace(lo, hi, grid_points)
        values = _numpy_vector_eval(normalized_expression, variable_name, grid)
    else:
        grid = _build_grid(lo, hi, grid_points)
        values = None
    step = (hi - lo) / max(grid_points - 1, 1)
    max_sample_gap = None
    max_sample_x = None
    max_sample_value = None
    failure_count = 0

    if values is not None and np is not None:
        if relation in {"<", "<="}:
            gaps = values - threshold
        else:
            gaps = threshold - values
        idx = int(np.argmax(gaps))
        max_sample_gap = float(gaps[idx])
        max_sample_x = float(grid[idx])
        max_sample_value = float(values[idx])
        if _relation_is_strict(relation):
            failure_count = int(np.count_nonzero(gaps >= 0.0))
        else:
            failure_count = int(np.count_nonzero(gaps > 0.0))
    else:
        for x in grid:
            value = evaluator(**{variable_name: x})
            gap = _evaluate_gap(relation, value, threshold)
            if max_sample_gap is None or gap > max_sample_gap:
                max_sample_gap = gap
                max_sample_x = x
                max_sample_value = value
            if _gap_is_violation(relation, gap):
                failure_count += 1

    verified_upper_gap = max_sample_gap
    if local_lipschitz is not None:
        L = abs(_float_or_default(local_lipschitz, 0.0))
        verified_upper_gap = max_sample_gap + 0.5 * L * step

    passed = verified_upper_gap < 0.0 if _relation_is_strict(relation) else verified_upper_gap <= 0.0
    return {
        "label": None,
        "expression": normalized_expression,
        "relation_target": _render_relation_target(relation, threshold),
        "worst_x": max_sample_x,
        "worst_value": max_sample_value,
        "sample_max_gap": max_sample_gap,
        "verified_upper_gap": verified_upper_gap,
        "grid_points": grid_points,
        "step": step,
        "lipschitz": local_lipschitz,
        "lipschitz_source": lipschitz_source,
        "sample_failures": failure_count,
        "pass": passed,
        "status": "pass" if passed else "fail",
        "strategy": "grid",
        "evaluations": grid_points,
    }


def _interval_upper_gap(left_gap, right_gap, lipschitz, width):
    return 0.5 * (left_gap + right_gap + lipschitz * width)


def _piyavskii_probe_point(a, b, ga, gb, lipschitz):
    width = b - a
    if width <= 0.0 or lipschitz <= 0.0:
        return 0.5 * (a + b)
    probe = 0.5 * (a + b) + (gb - ga) / (2.0 * lipschitz)
    eps = min(width * 1e-6, 1e-12)
    lower = a + eps
    upper = b - eps
    if lower >= upper:
        return 0.5 * (a + b)
    return min(max(probe, lower), upper)


def _evaluate_branch_bound_case(
    evaluator,
    normalized_expression,
    variable_name,
    lo,
    hi,
    relation,
    threshold,
    local_lipschitz,
    max_iterations,
    min_width,
    tolerance,
    strategy_name="branch_bound",
    lipschitz_source="provided",
):
    L = abs(_float_or_default(local_lipschitz, 0.0))
    if L <= 0.0:
        raise ValueError("branch_bound requires a positive Lipschitz bound")

    eval_count = 0

    def eval_gap(x):
        nonlocal eval_count
        eval_count += 1
        value = evaluator(**{variable_name: x})
        gap = _evaluate_gap(relation, value, threshold)
        return value, gap

    f_lo, g_lo = eval_gap(lo)
    if _gap_is_violation(relation, g_lo):
        return {
            "label": None,
            "expression": normalized_expression,
            "relation_target": _render_relation_target(relation, threshold),
            "worst_x": lo,
            "worst_value": f_lo,
            "sample_max_gap": g_lo,
            "verified_upper_gap": g_lo,
            "grid_points": 0,
            "step": hi - lo,
            "lipschitz": local_lipschitz,
            "lipschitz_source": lipschitz_source,
            "sample_failures": 1,
            "pass": False,
            "status": "fail",
            "strategy": strategy_name,
            "evaluations": eval_count,
            "counterexample_x": lo,
            "counterexample_value": f_lo,
            "reason": "endpoint violates the target inequality",
        }

    f_hi, g_hi = eval_gap(hi)
    if _gap_is_violation(relation, g_hi):
        return {
            "label": None,
            "expression": normalized_expression,
            "relation_target": _render_relation_target(relation, threshold),
            "worst_x": hi,
            "worst_value": f_hi,
            "sample_max_gap": g_hi,
            "verified_upper_gap": g_hi,
            "grid_points": 0,
            "step": hi - lo,
            "lipschitz": local_lipschitz,
            "lipschitz_source": lipschitz_source,
            "sample_failures": 1,
            "pass": False,
            "status": "fail",
            "strategy": strategy_name,
            "evaluations": eval_count,
            "counterexample_x": hi,
            "counterexample_value": f_hi,
            "reason": "endpoint violates the target inequality",
        }

    intervals = [
        {
            "a": lo,
            "b": hi,
            "fa": f_lo,
            "fb": f_hi,
            "ga": g_lo,
            "gb": g_hi,
            "upper": _interval_upper_gap(g_lo, g_hi, L, hi - lo),
        }
    ]
    best_sample_gap = max(g_lo, g_hi)
    best_sample_x = lo if g_lo >= g_hi else hi
    best_sample_value = f_lo if g_lo >= g_hi else f_hi
    iteration = 0

    while intervals and iteration < max_iterations:
        intervals.sort(key=lambda item: item["upper"], reverse=True)
        current = intervals.pop(0)
        width = current["b"] - current["a"]
        upper_gap = current["upper"]

        if best_sample_gap is None or upper_gap > best_sample_gap:
            pass

        if _relation_is_strict(relation):
            if upper_gap < 0.0:
                max_remaining = max([upper_gap] + [item["upper"] for item in intervals]) if intervals else upper_gap
                return {
                    "label": None,
                    "expression": normalized_expression,
                    "relation_target": _render_relation_target(relation, threshold),
                    "worst_x": best_sample_x,
                    "worst_value": best_sample_value,
                    "sample_max_gap": best_sample_gap,
                    "verified_upper_gap": max_remaining,
                    "grid_points": 0,
                    "step": width,
                    "lipschitz": local_lipschitz,
                    "lipschitz_source": lipschitz_source,
                    "sample_failures": 0,
                    "pass": True,
                    "status": "pass",
                    "strategy": strategy_name,
                    "evaluations": eval_count,
                    "intervals_closed": iteration + 1,
                }
        else:
            if upper_gap <= 0.0:
                max_remaining = max([upper_gap] + [item["upper"] for item in intervals]) if intervals else upper_gap
                return {
                    "label": None,
                    "expression": normalized_expression,
                    "relation_target": _render_relation_target(relation, threshold),
                    "worst_x": best_sample_x,
                    "worst_value": best_sample_value,
                    "sample_max_gap": best_sample_gap,
                    "verified_upper_gap": max_remaining,
                    "grid_points": 0,
                    "step": width,
                    "lipschitz": local_lipschitz,
                    "lipschitz_source": lipschitz_source,
                    "sample_failures": 0,
                    "pass": True,
                    "status": "pass",
                    "strategy": strategy_name,
                    "evaluations": eval_count,
                    "intervals_closed": iteration + 1,
                }

        if width <= min_width:
            return {
                "label": None,
                "expression": normalized_expression,
                "relation_target": _render_relation_target(relation, threshold),
                "worst_x": best_sample_x,
                "worst_value": best_sample_value,
                "sample_max_gap": best_sample_gap,
                "verified_upper_gap": upper_gap,
                "grid_points": 0,
                "step": width,
                "lipschitz": local_lipschitz,
                "lipschitz_source": lipschitz_source,
                "sample_failures": 0,
                "pass": False,
                "status": "inconclusive",
                "strategy": strategy_name,
                "evaluations": eval_count,
                "intervals_closed": iteration + 1,
                "reason": (
                    "interval width reached min_width before the strict inequality was certified"
                    if upper_gap > -tolerance
                    else "interval width reached min_width"
                ),
            }

        if strategy_name == "piyavskii":
            probe = _piyavskii_probe_point(current["a"], current["b"], current["ga"], current["gb"], L)
        else:
            probe = 0.5 * (current["a"] + current["b"])
        f_mid, g_mid = eval_gap(probe)
        if g_mid > best_sample_gap:
            best_sample_gap = g_mid
            best_sample_x = probe
            best_sample_value = f_mid
        if _gap_is_violation(relation, g_mid):
            return {
                "label": None,
                "expression": normalized_expression,
                "relation_target": _render_relation_target(relation, threshold),
                "worst_x": probe,
                "worst_value": f_mid,
                "sample_max_gap": g_mid,
                "verified_upper_gap": g_mid,
                "grid_points": 0,
                "step": width,
                "lipschitz": local_lipschitz,
                "lipschitz_source": lipschitz_source,
                "sample_failures": 1,
                "pass": False,
                "status": "fail",
                "strategy": strategy_name,
                "evaluations": eval_count,
                "counterexample_x": probe,
                "counterexample_value": f_mid,
                "reason": f"{strategy_name} probe point violates the target inequality",
            }

        left = {
            "a": current["a"],
            "b": probe,
            "fa": current["fa"],
            "fb": f_mid,
            "ga": current["ga"],
            "gb": g_mid,
            "upper": _interval_upper_gap(current["ga"], g_mid, L, probe - current["a"]),
        }
        right = {
            "a": probe,
            "b": current["b"],
            "fa": f_mid,
            "fb": current["fb"],
            "ga": g_mid,
            "gb": current["gb"],
            "upper": _interval_upper_gap(g_mid, current["gb"], L, current["b"] - probe),
        }
        intervals.extend([left, right])
        iteration += 1

    intervals.sort(key=lambda item: item["upper"], reverse=True)
    worst_upper = intervals[0]["upper"] if intervals else best_sample_gap
    return {
        "label": None,
        "expression": normalized_expression,
        "relation_target": _render_relation_target(relation, threshold),
        "worst_x": best_sample_x,
        "worst_value": best_sample_value,
        "sample_max_gap": best_sample_gap,
        "verified_upper_gap": worst_upper,
        "grid_points": 0,
        "step": intervals[0]["b"] - intervals[0]["a"] if intervals else hi - lo,
        "lipschitz": local_lipschitz,
        "lipschitz_source": lipschitz_source,
        "sample_failures": 0,
        "pass": False,
        "status": "inconclusive",
        "strategy": strategy_name,
        "evaluations": eval_count,
        "intervals_remaining": len(intervals),
        "reason": "max_iterations reached before the inequality was certified",
    }


def verify_numeric_1d(spec):
    variable_name = str(spec.get("variable", "")).strip() or "x"
    domain = spec.get("domain") or [0.0, 1.0]
    if not isinstance(domain, (list, tuple)) or len(domain) != 2:
        raise ValueError("domain must be a length-2 list")
    lo = _float_or_default(domain[0], 0.0)
    hi = _float_or_default(domain[1], 1.0)
    if not lo < hi:
        raise ValueError("domain lower bound must be < upper bound")

    inequalities = list(spec.get("inequalities") or [])
    if not inequalities:
        raise ValueError("inequalities must be a non-empty list")

    points = max(101, _int_or_default(spec.get("grid_points"), 2001))
    global_lipschitz = spec.get("lipschitz")
    requested_strategy = str(spec.get("strategy", "")).strip().lower()
    max_iterations = max(10, _int_or_default(spec.get("max_iterations"), 5000))
    min_width = abs(_float_or_default(spec.get("min_width"), 1e-6))
    tolerance = abs(_float_or_default(spec.get("tolerance"), 1e-9))
    symbolic = _symbolic_analysis(inequalities, variable_name)

    details = []
    item_statuses = []

    for item in inequalities:
        label = str(item.get("label", "")).strip() or f"ineq_{len(details) + 1}"
        relation = str(item.get("relation", "<")).strip() or "<"
        threshold = _float_or_default(item.get("threshold"), 0.0)
        evaluator, normalized_expression = compile_expression(item.get("expression", ""), [variable_name])
        estimated_lipschitz, lipschitz_source = _estimate_lipschitz_bound(
            normalized_expression,
            variable_name,
            lo,
            hi,
            provided_lipschitz=item.get("lipschitz", global_lipschitz),
        )
        local_lipschitz = estimated_lipschitz
        strategy = requested_strategy or ("piyavskii" if local_lipschitz is not None else "grid")
        if strategy in {"branch_bound", "piyavskii"} and local_lipschitz is None:
            strategy = "grid"

        if strategy in {"branch_bound", "piyavskii"}:
            detail = _evaluate_branch_bound_case(
                evaluator,
                normalized_expression,
                variable_name,
                lo,
                hi,
                relation,
                threshold,
                local_lipschitz,
                max_iterations,
                min_width,
                tolerance,
                strategy_name=strategy,
                lipschitz_source=lipschitz_source,
            )
        else:
            detail = _evaluate_grid_case(
                evaluator,
                normalized_expression,
                variable_name,
                lo,
                hi,
                relation,
                threshold,
                points,
                local_lipschitz,
                lipschitz_source=lipschitz_source,
            )
        detail["label"] = label
        details.append(detail)
        item_statuses.append(detail.get("status", "inconclusive"))

    if any(status == "fail" for status in item_statuses):
        overall_status = "verified_fail"
        summary = "at least one inequality failed under the current verification protocol"
    elif item_statuses and all(status == "pass" for status in item_statuses):
        overall_status = "verified_pass"
        summary = "all inequalities verified under the current verification protocol"
    else:
        overall_status = "inconclusive"
        summary = "verification did not find a counterexample, but some inequalities remain uncertified"

    return VerificationReport(
        status=overall_status,
        mode="numeric_1d",
        summary=summary,
        details=details,
        notes=str(spec.get("notes", "")).strip(),
        symbolic=symbolic,
    )


def _symbolic_gap_expression(parsed_expr, relation, threshold_expr):
    if relation in {">", ">="}:
        return sympy.simplify(parsed_expr - threshold_expr)
    if relation in {"<", "<="}:
        return sympy.simplify(threshold_expr - parsed_expr)
    raise ValueError(f"unsupported relation: {relation}")


def _classify_symbolic_gap(gap_expr, relation):
    strict = _relation_is_strict(relation)
    simplified = sympy.simplify(gap_expr)

    if strict:
        if simplified.is_positive is True:
            return "pass", "sympy proved the strict gap is positive"
        if simplified.is_nonpositive is True or simplified.equals(0) is True:
            return "fail", "sympy proved the strict gap is non-positive"
    else:
        if simplified.is_nonnegative is True or simplified.equals(0) is True:
            return "pass", "sympy proved the gap is non-negative"
        if simplified.is_negative is True:
            return "fail", "sympy proved the gap is negative"

    numeric_value = _safe_float(simplified.evalf())
    if numeric_value is not None:
        if strict:
            return ("pass", "numeric evaluation of symbolic gap is positive") if numeric_value > 0.0 else (
                "fail",
                "numeric evaluation of symbolic gap is non-positive",
            )
        return ("pass", "numeric evaluation of symbolic gap is non-negative") if numeric_value >= 0.0 else (
            "fail",
            "numeric evaluation of symbolic gap is negative",
        )

    return "inconclusive", "sympy could not determine the sign of the gap from the provided assumptions"


def _normalize_substitution_items(raw_items):
    if raw_items is None:
        return []
    if isinstance(raw_items, dict):
        return [{"label": "sample", "values": raw_items}]
    if isinstance(raw_items, list):
        return raw_items
    return []


def _evaluate_symbolic_substitution(parsed_expr, gap_expr, relation, variables, assumptions, substitution):
    label = str(substitution.get("label", "")).strip() or "sample"
    raw_values = substitution.get("values") or substitution.get("assignment") or {}
    if not isinstance(raw_values, dict):
        return {"label": label, "status": "inconclusive", "reason": "substitution values must be an object"}

    locals_map = _sympy_locals(variables, assumptions=assumptions)
    subs_map = {}
    used_values = {}
    for key, value in raw_values.items():
        name = str(key or "").strip()
        if name not in locals_map:
            continue
        try:
            parsed_value = _sympy_parse_scalar(value, variables, assumptions=assumptions)
        except Exception as exc:
            return {"label": label, "status": "inconclusive", "reason": f"invalid substitution for {name}: {exc}"}
        subs_map[locals_map[name]] = parsed_value
        used_values[name] = str(parsed_value)

    try:
        substituted_expr = sympy.simplify(parsed_expr.subs(subs_map))
        substituted_gap = sympy.simplify(gap_expr.subs(subs_map))
        status, reason = _classify_symbolic_gap(substituted_gap, relation)
        return {
            "label": label,
            "status": status,
            "reason": reason,
            "values": used_values,
            "expression_value": str(substituted_expr),
            "gap_value": str(substituted_gap),
            "gap_numeric": _safe_float(substituted_gap.evalf()),
        }
    except Exception as exc:
        return {"label": label, "status": "inconclusive", "reason": f"failed to evaluate substitution: {exc}"}


def verify_symbolic_multivar(spec):
    if sympy is None:
        return VerificationReport(
            status="unavailable",
            mode=str((spec or {}).get("mode", "symbolic_multivar")).strip() or "symbolic_multivar",
            summary="sympy is not installed",
            details=[],
            notes=str((spec or {}).get("notes", "")).strip(),
            symbolic={"status": "unavailable", "reason": "sympy is not installed"},
        )

    payload = dict(spec or {})
    variables = _normalize_variable_names(payload.get("variables") or payload.get("variable"), fallback=None)
    assumptions = payload.get("assumptions") or {}
    simplifications = list(payload.get("simplifications") or [])
    partial_derivatives = list(payload.get("partial_derivatives") or [])
    inequality_checks = list(payload.get("inequality_checks") or [])
    global_substitutions = _normalize_substitution_items(payload.get("substitutions"))

    symbolic = {
        "status": "ok",
        "assumptions": _normalize_symbol_assumptions(assumptions, variables),
        "simplifications": [],
        "partial_derivatives": [],
        "inequality_checks": [],
    }
    success_count = 0
    error_count = 0

    for item in simplifications:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip() or f"simplify_{len(symbolic['simplifications']) + 1}"
        expression = str(item.get("expression", "")).strip()
        try:
            parsed = _sympy_parse_expression(expression, variables, assumptions=assumptions)
            simplified = sympy.simplify(parsed)
            symbolic["simplifications"].append(
                {
                    "label": label,
                    "expression": str(parsed),
                    "simplified": str(simplified),
                }
            )
            success_count += 1
        except Exception as exc:
            symbolic["simplifications"].append({"label": label, "error": str(exc)})
            error_count += 1

    for item in partial_derivatives:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip() or f"partial_{len(symbolic['partial_derivatives']) + 1}"
        expression = str(item.get("expression", "")).strip()
        wrt = str(item.get("wrt", "")).strip()
        if not expression or not wrt:
            symbolic["partial_derivatives"].append({"label": label, "error": "each partial derivative item must include expression and wrt"})
            error_count += 1
            continue
        try:
            parsed = _sympy_parse_expression(expression, variables, assumptions=assumptions)
            wrt_symbol = _sympy_var(wrt, assumptions=assumptions)
            if wrt_symbol is None:
                raise ValueError(f"unknown differentiation variable: {wrt}")
            derivative = sympy.simplify(sympy.diff(parsed, wrt_symbol))
            symbolic["partial_derivatives"].append(
                {
                    "label": label,
                    "expression": str(parsed),
                    "wrt": wrt,
                    "derivative": str(derivative),
                }
            )
            success_count += 1
        except Exception as exc:
            symbolic["partial_derivatives"].append({"label": label, "wrt": wrt, "error": str(exc)})
            error_count += 1

    inequality_statuses = []
    for item in inequality_checks:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip() or f"ineq_{len(symbolic['inequality_checks']) + 1}"
        expression = str(item.get("expression", "")).strip()
        relation = str(item.get("relation", ">=")).strip() or ">="
        substitutions = _normalize_substitution_items(item.get("substitutions")) or global_substitutions
        try:
            parsed = _sympy_parse_expression(expression, variables, assumptions=assumptions)
            threshold_expr = _sympy_parse_scalar(item.get("threshold", 0), variables, assumptions=assumptions)
            simplified_expr = sympy.simplify(parsed)
            gap_expr = _symbolic_gap_expression(simplified_expr, relation, threshold_expr)
            status, reason = _classify_symbolic_gap(gap_expr, relation)
            substitution_results = []
            for substitution in substitutions:
                substitution_results.append(
                    _evaluate_symbolic_substitution(
                        simplified_expr,
                        gap_expr,
                        relation,
                        variables,
                        assumptions,
                        substitution,
                    )
                )
            if any(result.get("status") == "fail" for result in substitution_results):
                status = "fail"
                reason = "at least one substitution violates the target inequality"
            symbolic["inequality_checks"].append(
                {
                    "label": label,
                    "expression": str(parsed),
                    "simplified_expression": str(simplified_expr),
                    "relation_target": f"{relation} {threshold_expr}",
                    "gap_expression": str(gap_expr),
                    "status": status,
                    "reason": reason,
                    "substitutions": substitution_results,
                }
            )
            inequality_statuses.append(status)
            success_count += 1
        except Exception as exc:
            symbolic["inequality_checks"].append({"label": label, "error": str(exc), "status": "inconclusive"})
            inequality_statuses.append("inconclusive")
            error_count += 1

    if success_count == 0 and error_count > 0:
        symbolic["status"] = "tool_error"
        overall_status = "tool_error"
        summary = "symbolic analysis failed for all requested items"
    elif any(status == "fail" for status in inequality_statuses):
        symbolic["status"] = "ok"
        overall_status = "verified_fail"
        summary = "at least one symbolic inequality check failed"
    elif inequality_statuses and all(status == "pass" for status in inequality_statuses):
        symbolic["status"] = "ok"
        overall_status = "verified_pass"
        summary = "all symbolic inequality checks were certified from the provided assumptions"
    else:
        symbolic["status"] = "partial" if error_count else "ok"
        overall_status = "analysis_complete"
        summary = "symbolic multivariable analysis completed, but some inequalities remain uncertified"

    return VerificationReport(
        status=overall_status,
        mode=str(payload.get("mode", "symbolic_multivar")).strip() or "symbolic_multivar",
        summary=summary,
        details=[],
        notes=str(payload.get("notes", "")).strip(),
        symbolic=symbolic,
    )


def run_verification_spec(spec):
    payload = dict(spec or {})
    status = str(payload.get("status", "ready")).strip().lower() or "ready"
    if status in {"skip", "unavailable"}:
        return VerificationReport(
            status="unavailable",
            mode=str(payload.get("mode", "none")).strip() or "none",
            summary=str(payload.get("reason", "")).strip() or "verification spec unavailable",
            details=[],
            notes=str(payload.get("notes", "")).strip(),
        )

    mode = str(payload.get("mode", "numeric_1d")).strip().lower() or "numeric_1d"
    if mode == "numeric_1d":
        return verify_numeric_1d(payload)
    if mode == "symbolic_multivar":
        return verify_symbolic_multivar(payload)
    if mode != "numeric_1d":
        return VerificationReport(
            status="unavailable",
            mode=mode,
            summary=f"unsupported verification mode: {mode}",
            details=[],
            notes=str(payload.get("notes", "")).strip(),
        )
    return verify_numeric_1d(payload)


def render_verification_report(spec, report):
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
    requested_mode = str((spec or {}).get("mode", "unknown")).strip() or "unknown"
    lines = [
        "## Verification Report",
        f"- Requested Mode: {requested_mode}",
        f"- Executed Mode: {payload.get('mode', 'unknown')}",
        f"- Status: {payload.get('status', 'unknown')}",
        f"- Summary: {payload.get('summary', '[missing]')}",
    ]
    notes = str(payload.get("notes", "")).strip()
    if notes:
        lines.append(f"- Notes: {notes}")

    symbolic = payload.get("symbolic") or {}
    if symbolic:
        lines.extend(["", "### Symbolic Analysis"])
        lines.append(f"- Status: {symbolic.get('status', 'unknown')}")
        reason = str(symbolic.get("reason", "")).strip()
        if reason:
            lines.append(f"- Reason: {reason}")
        assumptions = symbolic.get("assumptions") or {}
        if assumptions:
            lines.append(f"- Assumptions: {assumptions}")
        legacy_items = symbolic.get("items") or []
        for item in legacy_items:
            label = str(item.get("label", "")).strip() or "item"
            if item.get("error"):
                lines.append(f"- {label}: error={item.get('error')}")
            else:
                lines.append(f"- {label}: derivative={item.get('derivative', '[missing]')}")
        simplifications = symbolic.get("simplifications") or []
        if simplifications:
            lines.extend(["", "### Symbolic Simplifications"])
            for item in simplifications:
                label = str(item.get("label", "")).strip() or "expr"
                if item.get("error"):
                    lines.append(f"- {label}: error={item.get('error')}")
                else:
                    lines.append(
                        f"- {label}: expression={item.get('expression', '[missing]')}; "
                        f"simplified={item.get('simplified', '[missing]')}"
                    )
        partials = symbolic.get("partial_derivatives") or []
        if partials:
            lines.extend(["", "### Partial Derivatives"])
            for item in partials:
                label = str(item.get("label", "")).strip() or "partial"
                if item.get("error"):
                    lines.append(f"- {label}: error={item.get('error')}")
                else:
                    lines.append(
                        f"- {label}: d/d{item.get('wrt', '?')} {item.get('expression', '[missing]')} = "
                        f"{item.get('derivative', '[missing]')}"
                    )
        inequality_checks = symbolic.get("inequality_checks") or []
        if inequality_checks:
            lines.extend(["", "### Inequality Checks"])
            for item in inequality_checks:
                label = str(item.get("label", "")).strip() or "ineq"
                if item.get("error"):
                    lines.append(f"- {label}: error={item.get('error')}")
                    continue
                lines.append(
                    f"- {label}: status={item.get('status', 'unknown')}; "
                    f"target {item.get('relation_target', '?')}; "
                    f"simplified={item.get('simplified_expression', '[missing]')}; "
                    f"gap={item.get('gap_expression', '[missing]')}"
                )
                item_reason = str(item.get("reason", "")).strip()
                if item_reason:
                    lines.append(f"  reason={item_reason}")
                for sample in item.get("substitutions") or []:
                    lines.append(
                        f"  sample {sample.get('label', 'sample')}: status={sample.get('status', 'unknown')}; "
                        f"values={sample.get('values', {})}; "
                        f"expression_value={sample.get('expression_value', '[missing]')}; "
                        f"gap_value={sample.get('gap_value', '[missing]')}"
                    )
                    sample_reason = str(sample.get("reason", "")).strip()
                    if sample_reason:
                        lines.append(f"    reason={sample_reason}")

    details = payload.get("details") or []
    if details:
        lines.extend(["", "### Numeric Checks"])
        for item in details:
            lines.append(
                f"- {item.get('label', 'ineq')}: status={item.get('status', 'unknown')}; "
                f"strategy={item.get('strategy', 'unknown')}; "
                f"target {item.get('relation_target', '?')}; "
                f"worst_x={item.get('worst_x')}; "
                f"worst_value={item.get('worst_value')}; "
                f"sample_max_gap={item.get('sample_max_gap')}; "
                f"verified_upper_gap={item.get('verified_upper_gap')}; "
                f"lipschitz={item.get('lipschitz')}; "
                f"lipschitz_source={item.get('lipschitz_source')}; "
                f"grid_points={item.get('grid_points')}; "
                f"evaluations={item.get('evaluations')}"
            )
            reason = str(item.get("reason", "")).strip()
            if reason:
                lines.append(f"  reason={reason}")

    return "\n".join(lines).strip()
