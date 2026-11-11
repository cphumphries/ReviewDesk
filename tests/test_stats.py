"""Agreement and accuracy statistics, checked against values worked out by hand (the working is in the comments) and
against published Wilson intervals (Newcombe RG. Two-sided confidence intervals for the single proportion: comparison of
seven methods. Stat Med 1998;17:857-72, Table I, method 3)."""
from __future__ import annotations

import math
import random

import pytest

from reviewdesk import screening, stats

Z = 1.959964


def table(both_yes, only_a, only_b, both_no):
    return stats.two_by_two([(True, True)] * both_yes + [(True, False)] * only_a + [(False, True)] * only_b + [(False, False)] * both_no)


def test_two_by_two_counts_the_pairs():
    t = table(20, 5, 10, 15)
    assert t == {"both_yes": 20, "only_a": 5, "only_b": 10, "both_no": 15, "n": 50}


def test_kappa_and_its_interval_for_a_balanced_table():
    # Reviewer A sent 25 of 50 on, reviewer B 30.         B yes  B no
    #                                              A yes    20     5
    #                                              A no     10    15
    # po = (20 + 15) / 50 = 0.70;  pa = 25/50 = 0.5;  pb = 30/50 = 0.6
    # pe = 0.5 x 0.6 + 0.5 x 0.4 = 0.50;  kappa = (0.70 - 0.50) / (1 - 0.50) = 0.40
    # Fleiss, Cohen and Everitt (1969): p11 .4, p12 .1, p21 .2, p22 .3; row totals .5 .5; column totals .6 .4; 1 - k = .6
    #   A = .4 (1 - (.5 + .6) .6)^2 + .3 (1 - (.5 + .4) .6)^2 = .4 x .1156 + .3 x .2116 = .10972
    #   B = .6^2 (.1 (.6 + .5)^2 + .2 (.4 + .5)^2) = .36 (.121 + .162) = .10188
    #   C = (k - pe (1 - k))^2 = (.4 - .3)^2 = .01
    #   SE = sqrt((A + B - C) / (n (1 - pe)^2)) = sqrt(.2016 / 12.5) = sqrt(.016128) = .126996
    # 95% CI = .40 +/- 1.959964 x .126996 = .40 +/- .248908 = .151092 to .648908
    k = stats.kappa(table(20, 5, 10, 15))
    assert k["agreement"] == pytest.approx(0.70)
    assert k["kappa"] == pytest.approx(0.40)
    assert k["se"] == pytest.approx(0.126996, abs=1e-6)
    assert k["ci_low"] == pytest.approx(0.151092, abs=1e-6) and k["ci_high"] == pytest.approx(0.648908, abs=1e-6)
    assert (k["a_yes"], k["b_yes"]) == (25, 30)
    assert k["prevalence_index"] == pytest.approx((20 - 15) / 50) and k["bias_index"] == pytest.approx((5 - 10) / 50)


def test_kappa_for_a_typical_screening_sample_where_most_records_are_excluded():
    # 100 records; A sent 10 on, B 9; both sent 8 on, both excluded 89; A alone 2, B alone 1.
    # po = 97/100 = .97;  pa = .10;  pb = .09;  pe = .10 x .09 + .90 x .91 = .009 + .819 = .828
    # kappa = (.97 - .828) / .172 = .142 / .172 = .825581
    # 1 - k = .174419;  row totals .10 .90; column totals .09 .91
    #   A = .08 (1 - .19 x .174419)^2 + .89 (1 - 1.81 x .174419)^2 = .08 x .934819 + .89 x .468269 = .074786 + .416759 = .491545
    #   B = .174419^2 (.02 (.09 + .90)^2 + .01 (.91 + .10)^2) = .030422 x .029803 = .000907
    #   C = (.825581 - .828 x .174419)^2 = (.825581 - .144419)^2 = .681162^2 = .463982
    #   SE = sqrt((.491545 + .000907 - .463982) / (100 x .172^2)) = sqrt(.028470 / 2.9584) = .098099
    # 95% CI = .825581 - 1.959964 x .098099 = .633312 to 1.0178, shown as at most 1
    k = stats.kappa(table(8, 2, 1, 89))
    assert k["agreement"] == pytest.approx(0.97)
    assert k["kappa"] == pytest.approx(0.825581, abs=1e-6)
    assert k["se"] == pytest.approx(0.098099, abs=1e-5)
    assert k["ci_low"] == pytest.approx(0.633312, abs=1e-5) and k["ci_high"] == 1.0
    assert k["ci_raw"][1] == pytest.approx(1.017851, abs=1e-5) and k["ci_truncated"] == ["upper"]
    b = stats.bootstrap(8, 2, 1, 89)
    assert stats.kappa_text(k) == ("Cohen's kappa 0.83 (95% CI 0.63 to 1.00 by the large-sample method, with the upper limit "
                                   "of 1.02 truncated at 1, the highest possible value; "
                                   f"{stats.f2(b['low'])} to {stats.f2(b['high'])} by percentile bootstrap)")
    assert stats.threshold_text(k, 0.80) == "Both confidence intervals include values below the pre-specified threshold of 0.80."
    assert stats.threshold_text(k, 0.60) == ("The bootstrap confidence interval includes values below the pre-specified "
                                             "threshold of 0.60.")              # the bootstrap's lower limit is 0.58
    assert stats.threshold_text(k, 0.50) == ""


def test_the_standard_error_is_the_fleiss_cohen_everitt_formula_written_out_again():
    for t in (table(20, 5, 10, 15), table(8, 2, 1, 89), table(3, 4, 6, 37), table(41, 2, 9, 3)):
        n = t["n"]
        p = [[t["both_yes"] / n, t["only_a"] / n], [t["only_b"] / n, t["both_no"] / n]]
        r, c = [sum(p[0]), sum(p[1])], [p[0][0] + p[1][0], p[0][1] + p[1][1]]
        pe = r[0] * c[0] + r[1] * c[1]
        k = (p[0][0] + p[1][1] - pe) / (1 - pe)
        var = (sum(p[i][i] * (1 - (r[i] + c[i]) * (1 - k)) ** 2 for i in range(2))
               + (1 - k) ** 2 * sum(p[i][j] * (c[i] + r[j]) ** 2 for i in range(2) for j in range(2) if i != j)
               - (k - pe * (1 - k)) ** 2) / (n * (1 - pe) ** 2)
        got = stats.kappa(t)
        assert got["kappa"] == pytest.approx(k) and got["se"] == pytest.approx(math.sqrt(var))


def test_perfect_agreement_says_which_methods_give_no_interval_and_gives_the_goodness_of_fit_one():
    # po = 1, pe = .2 x .2 + .8 x .8 = .68, kappa = (1 - .68) / (1 - .68) = 1; with no disagreements the variance is 0,
    # and every resample of the votes also agrees perfectly, so the bootstrap interval is 1 to 1 and says nothing.
    # Wilson for 50 of 50: d = 1 + 3.841459/50 = 1.076829; centre = (1 + .038415) / 1.076829 = .964322;
    # half-width = 1.959964 sqrt(0 + 3.841459/10000) / 1.076829 = .035674: .928648 to 1
    # Donner and Eliasziw's goodness-of-fit lower limit for 10 both on, 0 discordant, 40 both excluded: .786625 (see
    # test_the_goodness_of_fit_limit_under_perfect_agreement_matches_an_independent_calculation)
    k = stats.kappa(table(10, 0, 0, 40))
    assert k["kappa"] == 1.0 and k["ci_low"] is None and k["ci_note"] == "not estimable: no disagreements"
    b = stats.bootstrap(10, 0, 0, 40)
    assert b["low"] == b["high"] == pytest.approx(1.0)
    text = stats.kappa_text(k)
    assert text == ("Cohen's kappa 1.00 (the reviewers agreed on all 50 records, each sending 10 on; neither the large-sample "
                    "method nor the percentile bootstrap gives an interval when the reviewers agree on every record, but the "
                    "goodness-of-fit method of Donner and Eliasziw gives a 95% CI of 0.79 to 1.00; the Wilson 95% CI for "
                    "percentage agreement is 92.9% to 100.0%)")
    assert "not estimable" not in text and "100%" not in text and "((" not in text and "))" not in text
    assert stats.kappa_cell(k) == "1.00 (goodness-of-fit 0.79 to 1.00: see the text)"
    # the interval reaches below a threshold of 0.80, and the text says so, naming the interval
    assert stats.threshold_text(k, 0.8) == ("The goodness-of-fit confidence interval includes values below the "
                                            "pre-specified threshold of 0.80.")
    assert stats.threshold_text(k, 0.7) == ""
    assert "Donner and Eliasziw (1992)" in stats.gof_method_text()


def test_the_goodness_of_fit_limits_reproduce_a_published_example():
    # Donner A, Zou G. Interval estimation for a difference between intraclass kappa statistics. Biometrics
    # 2002;58:209-215, section 5: the multiple sclerosis data of Westlund and Kurland (1953), two neurologists,
    # Winnipeg {both, one, neither} = {119, 20, 10} (kappa .422) and New Orleans {44, 11, 14} (kappa .607). Their hybrid
    # goodness-of-fit (HGOF) 95% interval for kappa1 - kappa2 is (-0.450, 0.122). It combines the single-sample limits
    # (l, u) of Donner and Eliasziw (1992) with the large-sample variance of kappa under the common correlation model
    # (Bloch DA, Kraemer HC. Biometrics 1989;45:269-287) evaluated at those limits:
    #   var(k) = (1 - k)/N [(1 - k)(1 - 2k) + k(2 - k) / (2 pi (1 - pi))]
    #   L = k1 - k2 - z sqrt(var1(l1) + var2(u2));  U = k1 - k2 + z sqrt(var1(u1) + var2(l2))
    def var(k, pi, n):
        return (1 - k) / n * ((1 - k) * (1 - 2 * k) + k * (2 - k) / (2 * pi * (1 - pi)))
    l1, u1 = stats.gof_limits(119, 20, 10)
    l2, u2 = stats.gof_limits(44, 11, 14)
    k1 = 1 - 20 / 149 / (2 * (258 / 298) * (40 / 298))
    k2 = 1 - 11 / 69 / (2 * (99 / 138) * (39 / 138))
    assert round(k1, 3) == 0.422 and round(k2, 3) == 0.607
    assert l1 < k1 < u1 and l2 < k2 < u2
    low = k1 - k2 - Z * math.sqrt(var(l1, 258 / 298, 149) + var(u2, 99 / 138, 69))
    high = k1 - k2 + Z * math.sqrt(var(u1, 258 / 298, 149) + var(l2, 99 / 138, 69))
    assert (round(low, 3), round(high, 3)) == (-0.450, 0.122)


def test_the_goodness_of_fit_limit_under_perfect_agreement_matches_an_independent_calculation():
    # Worked independently with SciPy 1.18: scipy.stats.chisquare for the statistic, scipy.stats.chi2.ppf(.95, 1) for
    # the critical value (3.841458820694124) and scipy.optimize.brentq for the root. With no discordant pairs the upper
    # limit is 1, where the counts fit exactly.
    for counts, low in (((6, 0, 40), 0.6937908350426598), ((3, 0, 11), 0.4414875587899709),
                        ((10, 0, 40), 0.7866248609858376), ((119, 20, 10), 0.21946037596884913)):
        got = stats.gof_limits(*counts)
        assert got[0] == pytest.approx(low, abs=1e-9)
        if counts[1] == 0:
            assert got[1] == 1.0
    # the same by hand for 6/0/40: pi = 6/46, q = pi(1 - pi), d = q(1 - k); with no discordant pairs the statistic is
    # N d^2/(pi - d) + 2 N d + N d^2/(1 - pi - d), which equals 3.841459 at the lower limit
    pi = 6 / 46
    d = pi * (1 - pi) * (1 - 0.6937908350426598)
    assert 46 * (d * d / (pi - d) + 2 * d + d * d / (1 - pi - d)) == pytest.approx(stats.CHI2_1_95, abs=1e-9)
    assert stats.CHI2_1_95 == pytest.approx(Z * Z, abs=1e-6)


def test_the_goodness_of_fit_interval_is_only_given_under_perfect_agreement():
    # with any disagreement the large-sample and bootstrap intervals are given, as before
    k = stats.kappa(table(6, 1, 0, 39))
    ci = stats.kappa_ci(k)
    assert ci["gof"] is None and ci["wald"] and ci["boot"]
    assert "goodness-of-fit" not in stats.kappa_text(k)
    # an older stored result without its table: nothing to work from, so no interval and no invented one
    assert "goodness-of-fit" not in stats.kappa_text({"kappa": 1.0, "n": 46, "agreement": 1.0})


def test_a_small_agreement_sample_is_flagged_as_the_screen_flags_it():
    # the screen warns below 50 records, or when either reviewer sent on fewer than 5 (static/views_screen.js)
    assert (stats.SMALL_SAMPLE, stats.FEW_SENT_ON) == (50, 5)
    k = stats.kappa(table(6, 0, 0, 40))
    assert stats.small_sample_text(k, "NH", "CH", "the decision to allow single screening") == (
        "The agreement sample was small: 46 records, and each reviewer sent on 6 of them (Include or Maybe). With so few "
        "records, kappa can come out high or low largely by chance, and the decision to allow single screening rested on it.")
    k = stats.kappa(table(3, 1, 0, 96))
    assert stats.small_sample_text(k, "NH", "CH") == (
        "The agreement sample was 100 records, and NH sent on 4 of them and CH 3 (Include or Maybe). With so few records "
        "sent on, kappa can come out high or low largely by chance.")
    assert stats.small_sample_text(stats.kappa(table(20, 5, 10, 15)), "NH", "CH") == ""


def test_when_both_reviewers_excluded_everything_kappa_cannot_be_calculated():
    # pa = pb = 0, so pe = 0 x 0 + 1 x 1 = 1 and kappa = 0/0
    k = stats.kappa(table(0, 0, 0, 30))
    assert k["agreement"] == 1.0 and k["kappa"] is None and "cannot be calculated" in k["note"]
    assert stats.kappa_text(k) == "Cohen's kappa could not be calculated"


def test_one_reviewer_giving_the_same_answer_every_time_gives_kappa_zero_and_says_why():
    # pa = 0, pb = 3/50; pe = 0 + 1 x .94 = .94 = po, so kappa = 0 although 94% agreed
    k = stats.kappa(table(0, 0, 3, 47))
    assert k["kappa"] == 0.0 and k["agreement"] == pytest.approx(0.94)
    assert k["ci_low"] is None and "percentage agreement is the better summary" in k["note"]
    assert stats.kappa_text(k) == ("Cohen's kappa 0.00 (no confidence interval can be given, because one reviewer gave the same "
                                   "answer every time)")


def test_the_bootstrap_interval_is_the_percentile_interval_of_resampled_paired_votes():
    # The reference review's agreement sample: 46 records, both sent 6 on, only NH 1, only CH 0, both excluded 39;
    # kappa .9105, Fleiss-Cohen-Everitt interval .7377 to 1.0833 (shown truncated at 1). Written out again here: draw the
    # 46 pairs with replacement 2,000 times from random.Random(seed), keep each resample's kappa (when it has one), and take
    # the 2.5th and 97.5th percentiles (interpolating, as R's type 7).
    pairs = [(True, True)] * 6 + [(True, False)] * 1 + [(False, True)] * 0 + [(False, False)] * 39
    rng = random.Random(stats.BOOT_SEED)
    ks = []
    for _ in range(stats.BOOT_REPS):
        t = stats.two_by_two(rng.choices(pairs, k=len(pairs)))
        k = stats.kappa(t)["kappa"]
        if k is not None:
            ks.append(k)
    ks.sort()

    def q(p):
        h = (len(ks) - 1) * p
        return ks[int(h)] + (h - int(h)) * (ks[min(int(h) + 1, len(ks) - 1)] - ks[int(h)])
    b = stats.bootstrap(6, 1, 0, 39)
    assert (b["reps"], b["seed"], b["used"]) == (stats.BOOT_REPS, stats.BOOT_SEED, len(ks))
    assert b["low"] == pytest.approx(q(0.025), abs=1e-12) and b["high"] == pytest.approx(q(0.975), abs=1e-12)
    assert 0.6 < b["low"] < 0.7 and b["high"] == pytest.approx(1.0)     # the methodologist's 10,000 resamples: .646 to 1
    stats.bootstrap.cache_clear()
    assert stats.bootstrap(6, 1, 0, 39) == b                             # the same votes always give the same interval
    k = stats.kappa(table(6, 1, 0, 39))
    assert k["ci_raw"][1] == pytest.approx(1.0833, abs=1e-4) and k["ci_truncated"] == ["upper"]
    text = stats.kappa_text(k)
    assert text.startswith("Cohen's kappa 0.91 (95% CI 0.74 to 1.00 by the large-sample method, with the upper limit of 1.08 "
                           "truncated at 1, the highest possible value; ")
    assert f"{stats.f2(b['low'])} to 1.00 by percentile bootstrap" in text
    assert stats.threshold_text(k, 0.8) == "Both confidence intervals include values below the pre-specified threshold of 0.80."


def test_a_stored_result_gets_its_intervals_from_its_table():
    # what screening.apply_sample_decision keeps: the figures, the truncated limits and the table
    k = stats.kappa(table(15, 2, 6, 77))
    stored = {x: k.get(x) for x in ("n", "agreement", "kappa", "ci_low", "ci_high", "ci_note")}
    assert stats.kappa_text(dict(stored, table={c: k[c] for c in stats.CELLS})) == stats.kappa_text(k)
    # an older stored result without its table: the limits it kept, and no bootstrap
    assert stats.kappa_text(stored) == "Cohen's kappa 0.74 (95% CI 0.57 to 0.91 by the large-sample method)"
    # a table that does not give the stored kappa is not used
    assert "bootstrap" not in stats.kappa_text(dict(stored, table={"both_yes": 1, "only_a": 2, "only_b": 3, "both_no": 94}))
    assert stats.kappa_cell(k).startswith("0.74 (0.57 to 0.91; bootstrap ")


def test_an_empty_table_gives_no_figures():
    k = stats.kappa(table(0, 0, 0, 0))
    assert k["n"] == 0 and k["kappa"] is None and k["agreement"] is None


def test_a_kappa_of_exactly_the_threshold_meets_it_despite_rounding():
    # both yes 3, A alone 0, B alone 1, both no 8 (n = 12): po = 11/12, pa = 3/12, pb = 4/12,
    # pe = 3/12 x 4/12 + 9/12 x 8/12 = (12 + 72) / 144 = 7/12, kappa = (11/12 - 7/12) / (5/12) = 4/5 exactly;
    # in floating point it comes out as 0.7999999999999999
    k = stats.kappa(table(3, 0, 1, 8))
    assert k["kappa"] < 0.8 and k["kappa"] == pytest.approx(0.8)
    assert k["kappa"] >= 0.8 - screening.EPS
    assert stats.f2(k["kappa"]) == "0.80"


@pytest.mark.parametrize("k, n, low, high", [
    (81, 263, 0.2553, 0.3662),      # Newcombe 1998, Table I
    (15, 148, 0.0624, 0.1605),
    (0, 20, 0.0000, 0.1611),
    (1, 29, 0.0061, 0.1718),
])
def test_wilson_intervals_match_the_published_values(k, n, low, high):
    lo, hi = stats.wilson(k, n)
    assert round(lo, 4) == low and round(hi, 4) == high


def test_a_wilson_interval_worked_by_hand():
    # 8 of 10: p = .8, z^2 = 3.841459; d = 1 + z^2/n = 1.384146
    # centre = (p + z^2 / 2n) / d = (.8 + .192073) / 1.384146 = .716741
    # half-width = z sqrt(p(1 - p)/n + z^2 / 4n^2) / d = 1.959964 sqrt(.016 + .009604) / 1.384146 = .226579
    # interval .490162 to .943320
    lo, hi = stats.wilson(8, 10)
    assert lo == pytest.approx(0.490162, abs=1e-6) and hi == pytest.approx(0.943318, abs=1e-5)
    lo, hi = stats.wilson(29, 29)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.883030, abs=1e-6)
    assert all(math.isnan(x) for x in stats.wilson(0, 0))


def test_sensitivity_and_specificity_with_their_intervals():
    # 20 records the reviewers sent on: the AI kept 18 (sensitivity 18/20 = .90); 80 they excluded: the AI excluded 50
    # (specificity 50/80 = .625). Wilson for 18/20: d = 1.192073, centre = (.9 + .096036) / 1.192073 = .835551,
    # half-width = 1.959964 sqrt(.0045 + .002401) / 1.192073 = .136585: .698966 to .972136
    pairs = [(True, True)] * 18 + [(True, False)] * 2 + [(False, True)] * 30 + [(False, False)] * 50
    a = stats.accuracy(pairs)
    assert (a["tp"], a["fn"], a["fp"], a["tn"], a["n"]) == (18, 2, 30, 50, 100)
    assert a["sensitivity"] == pytest.approx(0.9) and a["specificity"] == pytest.approx(0.625)
    assert a["sens_ci"][0] == pytest.approx(0.698966, abs=1e-5) and a["sens_ci"][1] == pytest.approx(0.972134, abs=1e-5)
    assert a["tool_positive_rate"] == pytest.approx(0.48)
    none = stats.accuracy([(False, False)] * 5)
    assert none["sensitivity"] is None and none["sens_ci"] is None


def test_percentages_and_two_decimals_read_naturally():
    assert stats.fmt_pct(0.8256) == "82.6%" and stats.fmt_pct(0.5, 0) == "50%" and stats.fmt_pct(None) == "n/a"
    assert stats.fmt_pct(float("nan")) == "n/a"
    assert stats.f2(-0.001) == "0.00" and stats.f2(None) == "n/a"
