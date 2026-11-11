"""Agreement and accuracy statistics, with the methods named so they can be reported.

Cohen's kappa is reported with two 95% confidence intervals: the large-sample (Wald) interval from the standard error
of Fleiss, Cohen and Everitt (1969), cut to the range kappa can take (-1 to 1) and saying so when it is cut, and a
percentile bootstrap interval, reported alongside it: BOOT_REPS resamples, with
replacement, of the paired votes, drawn with Python's random module from the fixed seed BOOT_SEED, so the same votes
always give the same interval.

When the two reviewers agree on every record, neither of those methods gives an interval (the standard error is 0,
and every resample agrees perfectly too). The interval is then the one from the goodness-of-fit method of Donner and
Eliasziw (1992), which works from how far the counts could move before they no longer fit a given kappa
(gof_interval)."""
from __future__ import annotations

import functools
import math
import random

Z = 1.959964
CHI2_1_95 = 3.841458820694124            # the 95th percentile of chi-square with 1 degree of freedom (the exact value)
BOOT_REPS = 2000
BOOT_SEED = 12345
CELLS = ("both_yes", "only_a", "only_b", "both_no")
# The agreement sample is "small" below SMALL_SAMPLE records, or when either reviewer sent on fewer than FEW_SENT_ON
# of them: the same limits the screen uses to warn the lead before the rule is applied (static/views_screen.js).
SMALL_SAMPLE, FEW_SENT_ON = 50, 5
GOF_REF = ("Donner A, Eliasziw M. A goodness-of-fit approach to inference procedures for the kappa statistic: confidence "
           "interval construction, significance-testing and sample size estimation. Stat Med 1992;11:1511-1519.")


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score 95% confidence interval for a proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def two_by_two(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (reviewer A says yes, reviewer B says yes)."""
    a = sum(1 for x, y in pairs if x and y)
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if not x and y)
    d = sum(1 for x, y in pairs if not x and not y)
    return {"both_yes": a, "only_a": b, "only_b": c, "both_no": d, "n": a + b + c + d}


def kappa(t: dict) -> dict:
    """Cohen's kappa for two raters and two categories, with the large-sample standard error of Fleiss,
    Cohen and Everitt (1969) and a 95% confidence interval; percentage agreement; prevalence and bias indices."""
    n = t["n"]
    out = {"n": n, "agreement": None, "kappa": None, "ci_low": None, "ci_high": None, "se": None,
           "a_yes": t["both_yes"] + t["only_a"], "b_yes": t["both_yes"] + t["only_b"], **t,
           "method": "Cohen's kappa; 95% CI from the large-sample standard error of Fleiss, Cohen and Everitt (1969), "
                     "truncated to -1..1"}
    if not n:
        return out
    po = (t["both_yes"] + t["both_no"]) / n
    pa, pb = (t["both_yes"] + t["only_a"]) / n, (t["both_yes"] + t["only_b"]) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    out["agreement"] = po
    out["prevalence_index"] = (t["both_yes"] - t["both_no"]) / n
    out["bias_index"] = (t["only_a"] - t["only_b"]) / n
    if pe >= 1:
        out["note"] = "Every record had the same answer from both reviewers, so kappa cannot be calculated."
        return out
    k = (po - pe) / (1 - pe)
    p = [[t["both_yes"] / n, t["only_a"] / n], [t["only_b"] / n, t["both_no"] / n]]   # rows A yes/no, cols B yes/no
    pr = [p[0][0] + p[0][1], p[1][0] + p[1][1]]
    pc = [p[0][0] + p[1][0], p[0][1] + p[1][1]]
    A = sum(p[i][i] * (1 - (pr[i] + pc[i]) * (1 - k)) ** 2 for i in range(2))
    B = (1 - k) ** 2 * (p[0][1] * (pc[0] + pr[1]) ** 2 + p[1][0] * (pc[1] + pr[0]) ** 2)
    C = (k - pe * (1 - k)) ** 2
    se = math.sqrt(max((A + B - C) / (n * (1 - pe) ** 2), 0))
    raw = (k - Z * se, k + Z * se)
    out.update({"kappa": k, "se": se, "ci_low": max(-1.0, raw[0]), "ci_high": min(1.0, raw[1]), "ci_raw": list(raw),
                "ci_truncated": [w for w, cut in (("lower", raw[0] < -1.0), ("upper", raw[1] > 1.0)) if cut]})
    one_sided = min(pa, pb) <= 0 or max(pa, pb) >= 1
    if se < 1e-12 or one_sided:
        out["ci_low"] = out["ci_high"] = out["ci_raw"] = None
        out["ci_truncated"] = []
        out["ci_note"] = "not estimable: no disagreements" if t["only_a"] + t["only_b"] == 0 else "not estimable"
    if one_sided and t["only_a"] + t["only_b"]:
        out["note"] = ("One reviewer gave the same answer every time, so kappa is 0 whatever the agreement; "
                       "percentage agreement is the better summary here.")
    return out


def kappa_of(both_yes: int, only_a: int, only_b: int, both_no: int):
    """Cohen's kappa of a two-by-two table, or None when it cannot be calculated (both reviewers gave one answer)."""
    n = both_yes + only_a + only_b + both_no
    if not n:
        return None
    pa, pb = (both_yes + only_a) / n, (both_yes + only_b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1 - 1e-12:
        return None
    return ((both_yes + both_no) / n - pe) / (1 - pe)


def gof_limits(both_pos: int, discordant: int, both_neg: int, crit: float = CHI2_1_95):
    """Donner and Eliasziw's (1992) goodness-of-fit 95% confidence limits for kappa, (low, high), or None.

    Their common correlation model: both reviewers send a record on with the same probability pi, so a record is sent
    on by both with P1 = pi^2 + pi(1-pi)k, by one only with P2 = 2 pi(1-pi)(1-k) and by neither with
    P3 = (1-pi)^2 + pi(1-pi)k. pi is estimated from both reviewers' votes, (2 both_pos + discordant) / 2N, and held at
    that value; the limits are the values of k at which the chi-square goodness-of-fit statistic of the three counts
    equals crit (1 degree of freedom). At the estimate of k the statistic is 0. Under perfect agreement (no
    discordant pairs, kappa 1) the upper limit is 1. Solved here by scanning out from the estimate and then
    bisecting; Donner and Eliasziw solve the same equation as a cubic."""
    n = both_pos + discordant + both_neg
    if n < 2:
        return None
    pi = (2 * both_pos + discordant) / (2 * n)
    q = pi * (1 - pi)
    if q <= 0:
        return None                                   # everyone gave the same answer: kappa cannot be calculated
    k_hat = 1 - discordant / (n * 2 * q)
    k_min = max(-pi / (1 - pi), -(1 - pi) / pi)       # below this a cell probability would be negative

    def chi(k: float) -> float:
        s = 0.0
        for obs, prob in ((both_pos, pi * pi + q * k), (discordant, 2 * q * (1 - k)), (both_neg, (1 - pi) ** 2 + q * k)):
            e = n * prob
            if e <= 0:
                if obs:
                    return math.inf
                continue
            s += (obs - e) ** 2 / e
        return s

    def limit(to: float) -> float:
        """The first value of k between the estimate and `to` at which the statistic reaches crit (or `to`)."""
        steps = 4000
        prev = k_hat
        for j in range(1, steps + 1):
            k = k_hat + (to - k_hat) * j / steps
            if chi(k) >= crit:
                a, b = prev, k                        # chi(a) < crit <= chi(b)
                for _ in range(100):
                    m = (a + b) / 2
                    if chi(m) >= crit:
                        b = m
                    else:
                        a = m
                return (a + b) / 2
            prev = k
        return to
    low = limit(k_min) if k_hat > k_min else k_min
    high = limit(1.0) if k_hat < 1 else 1.0
    return (low, high)


def gof_interval(t: dict):
    """gof_limits for a two-by-two table of the two reviewers' votes."""
    return gof_limits(t["both_yes"], t["only_a"] + t["only_b"], t["both_no"])


def _percentile(xs: list, q: float) -> float:
    """The q-th quantile of sorted values, interpolating between neighbours (as R's type 7 and NumPy's default)."""
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    return xs[lo] + (h - lo) * (xs[min(lo + 1, len(xs) - 1)] - xs[lo])


@functools.lru_cache(maxsize=512)
def bootstrap(both_yes: int, only_a: int, only_b: int, both_no: int, reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> dict:
    """Percentile bootstrap 95% CI for kappa: reps resamples, with replacement, of the n paired votes (each pair drawn
    with equal chance, so a resample is a new two-by-two table), from random.Random(seed). Resamples in which kappa
    cannot be calculated are left out, and their number is given ("used" of "reps")."""
    n = both_yes + only_a + only_b + both_no
    out = {"low": None, "high": None, "reps": reps, "used": 0, "seed": seed, "n": n}
    if n < 2:
        return out
    rng = random.Random(seed)
    cum = [both_yes, both_yes + only_a, both_yes + only_a + only_b, n]
    cells = (0, 1, 2, 3)
    ks = []
    for _ in range(reps):
        draw = rng.choices(cells, cum_weights=cum, k=n)
        k = kappa_of(draw.count(0), draw.count(1), draw.count(2), draw.count(3))
        if k is not None:
            ks.append(k)
    if ks:
        ks.sort()
        out.update({"low": _percentile(ks, 0.025), "high": _percentile(ks, 0.975), "used": len(ks)})
    return out


def f2(x) -> str:
    """Two decimals without a negative zero."""
    if x is None:
        return "n/a"
    return f"{0.0 if abs(x) < 0.005 else x:.2f}"


# Kappa "reaches" a threshold allowing for the arithmetic's own rounding: 0.7999999999999999 reaches 0.80
# (screening.EPS is this number; the rule is applied there).
THRESHOLD_EPS = 1e-9


def reaches(kappa, threshold) -> bool:
    """Whether kappa is at or above the threshold, as the agreement rule decides it."""
    return kappa is not None and threshold is not None and kappa >= threshold - THRESHOLD_EPS


def fmt_threshold(x) -> str:
    """A kappa threshold as it was set: two decimals, or as many more as it was given (0.8 is "0.80", 0.805 is
    "0.805"), so that it is never printed as a number it is not."""
    if x is None:
        return "n/a"
    whole, _, dec = f"{float(x):.6f}".partition(".")
    return f"{whole}.{dec.rstrip('0').ljust(2, '0')}"


def fmt_kappa(kappa, threshold=None) -> str:
    """Kappa for printing beside its threshold: two decimals, or as many more as it takes to show which side of the
    threshold it is on. 0.7954 against a threshold of 0.80 is "0.795", never "0.80", which would read as reaching
    it; 0.8041 against 0.804 is "0.804", never "0.80". Without a threshold, two decimals (as f2)."""
    if kappa is None:
        return "n/a"
    if threshold is None:
        return f2(kappa)
    want = reaches(kappa, threshold)
    out = f2(kappa)
    for places in range(2, 10):
        out = f"{kappa:.{places}f}"
        if reaches(float(out), threshold) == want:
            break
    return out[1:] if out.startswith("-") and not float(out) else out


def _table_of(k: dict):
    """The two-by-two table behind a kappa result: its own cells, or the 'table' a stored result keeps."""
    t = k.get("table") if isinstance(k.get("table"), dict) else k
    if not all(isinstance(t.get(c), int) for c in CELLS):
        return None
    t = {c: t[c] for c in CELLS}
    t["n"] = sum(t.values())
    if k.get("n") not in (None, t["n"]):
        return None
    return t


def kappa_ci(k: dict, unit: str = "records") -> dict:
    """What can be said about the uncertainty of a kappa: {"wald": (low, high) cut to -1..1, "raw": (low, high)
    before cutting or None, "truncated": ["lower", "upper"], "boot": the bootstrap result or None, "why_none": a
    reason when no interval can be given}. Works on stats.kappa's result and on the results ReviewDesk stores (a
    calibration round or the agreement sample), recomputing from the table when the result keeps one."""
    out = {"wald": None, "raw": None, "truncated": [], "boot": None, "gof": None, "why_none": "", "perfect": ""}
    if k.get("kappa") is None:
        return out
    t = _table_of(k)
    full = kappa(t) if t else None
    if full and (full.get("kappa") is None or abs(full["kappa"] - k["kappa"]) > 1e-9):
        full = None                                   # the stored figures are not this table's: use them as they are
    src = full or k
    if src.get("ci_low") is not None:
        out["wald"] = (src["ci_low"], src["ci_high"])
        if full:
            out["raw"] = tuple(full["ci_raw"])
            out["truncated"] = list(full["ci_truncated"])
        else:                                         # an older stored result: only the cut limits were kept
            out["truncated"] = [w for w, v in (("lower", src["ci_low"]), ("upper", src["ci_high"])) if abs(abs(v) - 1) < 1e-12]
    if full and t:
        b = bootstrap(t["both_yes"], t["only_a"], t["only_b"], t["both_no"])
        if b["low"] is not None and f2(b["low"]) != f2(b["high"]):
            out["boot"] = b
    if not out["wald"] and not out["boot"]:
        if t and t["only_a"] + t["only_b"] == 0:
            lo, hi = wilson(t["n"], t["n"])
            # What is known when the reviewers agree on every record, said without nested brackets so that it reads
            # inside the brackets after the kappa
            out["perfect"] = (f"the reviewers agreed on all {t['n']:,} {unit}, each sending {t['both_yes']:,} on; neither "
                              "the large-sample method nor the percentile bootstrap gives an interval when the reviewers "
                              "agree on every record")
            out["agreement_ci"] = (lo, hi)
            g = gof_interval(t) if full else None
            if g and g[0] < g[1]:
                out["gof"] = g
            else:
                out["why_none"] = (out["perfect"] + "; no other interval was calculated; the Wilson 95% CI for "
                                   f"percentage agreement is {fmt_pct(lo)} to {fmt_pct(hi)}")
        elif t and min(t["both_yes"] + t["only_a"], t["both_yes"] + t["only_b"], t["only_b"] + t["both_no"],
                       t["only_a"] + t["both_no"]) == 0:
            out["why_none"] = "no confidence interval can be given, because one reviewer gave the same answer every time"
        else:
            out["why_none"] = "no confidence interval could be calculated"
    return out


def kappa_text(k: dict, unit: str = "records", threshold=None) -> str:
    """"Cohen's kappa 0.83 (95% CI 0.63 to 1.00 by the large-sample method, with the upper limit of 1.02 truncated
    at 1, the highest possible value; 0.59 to 0.97 by percentile bootstrap)", or with the reason there is no
    interval. When the result has a threshold (the agreement sample's stored result keeps its own; or give one),
    kappa is printed with enough decimals to show which side of it it is on (fmt_kappa)."""
    if k.get("kappa") is None:
        return "Cohen's kappa could not be calculated"
    thr = _threshold_of(k, threshold)
    shown = fmt_kappa(k["kappa"], thr)
    lim = lambda x: fmt_kappa(x, thr)          # noqa: E731  (each limit, too, on its own side of the threshold)
    ci = kappa_ci(k, unit)
    if ci["why_none"]:
        return f"Cohen's kappa {shown} ({ci['why_none']})"
    if ci["gof"]:
        lo, hi = ci["agreement_ci"]
        return (f"Cohen's kappa {shown} ({ci['perfect']}, but the goodness-of-fit method of Donner and Eliasziw "
                f"gives a 95% CI of {lim(ci['gof'][0])} to {lim(ci['gof'][1])}; the Wilson 95% CI for percentage agreement "
                f"is {fmt_pct(lo)} to {fmt_pct(hi)})")
    parts = []
    if ci["wald"]:
        lo, hi = ci["wald"]
        s = f"95% CI {lim(lo)} to {lim(hi)} by the large-sample method"
        cut = ci["truncated"]
        raw = ci["raw"]
        if cut == ["upper"]:
            s += (f", with the upper limit of {f2(raw[1])} truncated at 1" if raw else ", with the upper limit truncated at 1") \
                + ", the highest possible value"
        elif cut == ["lower"]:
            s += (f", with the lower limit of {f2(raw[0])} truncated at −1" if raw else ", with the lower limit truncated at −1") \
                + ", the lowest possible value"
        elif cut:
            s += ", with both limits truncated to the possible range, −1 to 1"
        parts.append(s)
    if ci["boot"]:
        b = ci["boot"]
        s = f"{lim(b['low'])} to {lim(b['high'])} by percentile bootstrap"
        if not ci["wald"]:
            s = "95% CI " + s
        if b["used"] < b["reps"]:
            s += f", from the {b['used']:,} of {b['reps']:,} resamples in which kappa could be calculated"
        parts.append(s)
    return f"Cohen's kappa {shown} ({'; '.join(parts)})"


def _threshold_of(k: dict, threshold=None):
    """The threshold a kappa is printed beside: the one given, or the one a stored agreement-sample result keeps."""
    thr = threshold if threshold is not None else k.get("threshold")
    return thr if isinstance(thr, (int, float)) and not isinstance(thr, bool) else None


def kappa_cell(k: dict, threshold=None) -> str:
    """Kappa for a table cell headed "Cohen's kappa (95% CI)": "0.74 (0.57 to 0.91; bootstrap 0.55 to 0.89)"."""
    if k.get("kappa") is None:
        return "could not be calculated"
    thr = _threshold_of(k, threshold)
    shown = fmt_kappa(k["kappa"], thr)
    lim = lambda x: fmt_kappa(x, thr)          # noqa: E731
    ci = kappa_ci(k)
    if ci["why_none"]:
        return f"{shown} (no interval: see the text)"
    if ci["gof"]:
        return f"{shown} (goodness-of-fit {lim(ci['gof'][0])} to {lim(ci['gof'][1])}: see the text)"
    bits = []
    if ci["wald"]:
        bits.append(f"{lim(ci['wald'][0])} to {lim(ci['wald'][1])}" + (", truncated" if ci["truncated"] else ""))
    if ci["boot"]:
        bits.append(f"bootstrap {lim(ci['boot']['low'])} to {lim(ci['boot']['high'])}")
    return f"{shown} ({'; '.join(bits)})"


def threshold_text(k: dict, threshold) -> str:
    """A sentence when the confidence intervals reach across the pre-specified kappa threshold, else ""."""
    if k.get("kappa") is None or threshold is None:
        return ""
    ci = kappa_ci(k)
    ivs = [(n, lo, hi) for n, lo, hi in (("large-sample", *(ci["wald"] or (None, None))),
                                         ("bootstrap", (ci["boot"] or {}).get("low"), (ci["boot"] or {}).get("high")),
                                         ("goodness-of-fit", *(ci["gof"] or (None, None))))
           if lo is not None]
    if not ivs:
        return ""
    thr = fmt_threshold(threshold)
    if reaches(k["kappa"], threshold):
        cross = [n for n, lo, _ in ivs if not reaches(lo, threshold)]
        words = "values below"
    else:
        cross = [n for n, _, hi in ivs if reaches(hi, threshold)]
        words = "values at or above"
    if not cross:
        return ""
    which = "Both confidence intervals include" if len(cross) == 2 else \
        f"The {cross[0]} confidence interval includes" if len(ivs) == 2 or cross == ["goodness-of-fit"] else \
        "The confidence interval includes"
    return f"{which} {words} the pre-specified threshold of {thr}."


def sent_on(k: dict):
    """(records reviewer A sent on, records reviewer B sent on, records) from a kappa result, or None."""
    t = _table_of(k)
    if not t:
        return None
    return t["both_yes"] + t["only_a"], t["both_yes"] + t["only_b"], t["n"]


def small_sample_text(k: dict, a_name: str, b_name: str, decided: str = "") -> str:
    """A sentence when the agreement sample is small or few of its records were sent on, as the screen warns before
    the rule is applied (SMALL_SAMPLE, FEW_SENT_ON), else "". decided: what rested on the kappa ("the decision to allow
    single screening"), said when given."""
    s = sent_on(k)
    if not s or k.get("kappa") is None:
        return ""
    ya, yb, n = s
    small, few = n < SMALL_SAMPLE, min(ya, yb) < FEW_SENT_ON
    if not (small or few):
        return ""
    why = "so few records, and so few sent on" if small and few else "so few records" if small else "so few records sent on"
    who = (f"each reviewer sent on {ya:,} of them" if ya == yb else f"{a_name} sent on {ya:,} of them and {b_name} {yb:,}")
    if n == 1:
        who = ("both reviewers sent it on" if ya and yb else "neither reviewer sent it on" if not (ya or yb) else
               f"{a_name if ya else b_name} sent it on and {b_name if ya else a_name} did not")
    head = f"The agreement sample was {'small: ' if small else ''}{n:,} record{'' if n == 1 else 's'}, and {who} (Include or Maybe)."
    same = [nm for nm, y in ((a_name, ya), (b_name, yb)) if y in (0, n)]
    if same:                                    # one reviewer gave the same answer every time: kappa is 0 by construction
        whom = "each reviewer" if len(same) == 2 else same[0]
        return (f"{head} Because {whom} gave the same answer for every record, kappa is 0 by construction, whatever the "
                "agreement" + (f", and {decided} rested on it" if decided else "") + ".")
    return (f"{head[:-1]}. With {why}, kappa can come out high or low largely by chance"
            + (f", and {decided} rested on it" if decided else "") + ".")


def bootstrap_method_text() -> str:
    return (f"Percentile bootstrap intervals come from {BOOT_REPS:,} resamples, with replacement, of the paired votes "
            f"(Python's random module, seed {BOOT_SEED}), and are reported alongside the large-sample intervals; "
            "large-sample limits beyond the range kappa can take (−1 to 1) are truncated, and the text says so.")


def gof_method_text() -> str:
    """How the interval is worked out when the reviewers agree on every record."""
    return ("When the reviewers agreed on every record, the interval is from the goodness-of-fit method of Donner and "
            "Eliasziw (1992), which assumes that the two reviewers send records on at the same underlying rate.")


def accuracy(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (reference positive, tool positive): sensitivity and specificity with Wilson intervals."""
    tp = sum(1 for r, t in pairs if r and t)
    fn = sum(1 for r, t in pairs if r and not t)
    fp = sum(1 for r, t in pairs if not r and t)
    tn = sum(1 for r, t in pairs if not r and not t)
    out = {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "n": len(pairs)}
    out["sensitivity"] = tp / (tp + fn) if tp + fn else None
    out["sens_ci"] = list(wilson(tp, tp + fn)) if tp + fn else None
    out["specificity"] = tn / (tn + fp) if tn + fp else None
    out["spec_ci"] = list(wilson(tn, tn + fp)) if tn + fp else None
    out["tool_positive_rate"] = (tp + fp) / len(pairs) if pairs else None
    return out


def fmt_pct(x, digits=1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{digits}f}%"
