"""Builds the results figure and the LaTeX table rows used in the paper
directly from results/*_summary.json.

    python make_figures.py                 # writes to ./output/figs and ./output/tables
    python make_figures.py --out ../paper  # writes to ../paper/figs and ../paper/tables
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
_ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--out", default=os.path.join(HERE, "output"),
                 help="folder that receives figs/ and tables/ (default: ./output)")
_args = _ap.parse_args()
FIG = os.path.join(_args.out, "figs")
TAB = os.path.join(_args.out, "tables")
POLS = ("TTA-lite", "TTA-lite-noCP", "RTC-direct", "Fidelity-greedy", "EDF-greedy")
STYLE = {
    "TTA-lite": ("#0072B2", "o", "-"),
    "TTA-lite-noCP": ("#56B4E9", "s", "--"),
    "RTC-direct": ("#009E73", "D", "-."),
    "Fidelity-greedy": ("#D55E00", "^", "-"),
    "EDF-greedy": ("#7F7F7F", "v", ":"),
}
LABEL = {"TTA-lite-noCP": r"TTA-lite without $1/n_d$"}


def load(name):
    return json.load(open(os.path.join(HERE, "results", f"{name}_summary.json")))


def p_tex(p):
    if p >= 0.001:
        return f"{p:.3f}"
    m, e = f"{p:.1e}".split("e")
    return f"{m}\\times 10^{{{int(e)}}}"


def gain(c):
    return f"${c['rel_pct']:+.1f}$ (${p_tex(c['p_holm'])}$)"


def rows(summ, label_fmt, pairs):
    comps = {(str(c["value"]), c["a"], c["b"]): c for c in summ["comparisons"]}
    out = []
    for v in summ["meta"]["values"]:
        pv = summ["per_value"][str(v)]
        cells = [label_fmt(v)] + [f"{pv[p]['mean']:.3f}" for p in POLS]
        cells += [gain(comps[(str(v), a, b)]) for a, b in pairs]
        out.append(" & ".join(cells) + r" \\")
    return "\n".join(out) + "\n"


def main():
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(TAB, exist_ok=True)
    S = {k: load(k) for k in ("capacity", "shared", "info", "distance")}
    tta_pairs = [("TTA-lite", "Fidelity-greedy"), ("TTA-lite", "EDF-greedy"), ("TTA-lite", "TTA-lite-noCP")]
    rtc_pairs = [("RTC-direct", "TTA-lite")]
    open(os.path.join(TAB, "capacity.tex"), "w").write(rows(S["capacity"], str, tta_pairs + rtc_pairs))
    open(os.path.join(TAB, "shared.tex"), "w").write(rows(S["shared"], str, tta_pairs + rtc_pairs))
    open(os.path.join(TAB, "info.tex"), "w").write(
        rows(S["info"], lambda v: f"{v} ({v * 0.1:.1f})", tta_pairs + rtc_pairs))
    open(os.path.join(TAB, "distance.tex"), "w").write(
        rows(S["distance"], lambda v: f"{v:.1f}", tta_pairs + rtc_pairs))

    plt.rcParams.update({"font.family": "serif", "font.size": 8.5, "axes.linewidth": 0.6,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "pdf.fonttype": 42})
    fig, axes = plt.subplots(2, 2, figsize=(6.5, 5.0))
    panels = [
        (axes[0, 0], "capacity", "Memory at node C (qubits)", "(a) Capacity, policy-ranked generation", str),
        (axes[0, 1], "shared", "Memory at node C (qubits)", "(b) Capacity, shared generation order", str),
        (axes[1, 0], "info", r"Controller information delay $\Delta$ (slots)", "(c) Information delay, C = 3", str),
        (axes[1, 1], "distance", "Link-length scale factor", "(d) Link lengths, C = 3", lambda v: f"{v:g}"),
    ]
    for ax, key, xl, title, fmt in panels:
        summ = S[key]
        vals = summ["meta"]["values"]
        x = list(range(len(vals)))
        for pol in POLS:
            col, mk, ls = STYLE[pol]
            m = [summ["per_value"][str(v)][pol]["mean"] for v in vals]
            lo = [summ["per_value"][str(v)][pol]["ci95"][0] for v in vals]
            hi = [summ["per_value"][str(v)][pol]["ci95"][1] for v in vals]
            ax.errorbar(x, m, yerr=[[a - b for a, b in zip(m, lo)], [b - a for a, b in zip(m, hi)]],
                        color=col, marker=mk, ls=ls, lw=1.1, ms=3.2, capsize=1.5, label=LABEL.get(pol, pol))
        ax.set_xticks(x)
        ax.set_xticklabels([fmt(v) for v in vals])
        ax.set_xlabel(xl)
        ax.set_title(title, fontsize=8.5)
        ax.grid(axis="y", lw=0.3, alpha=0.5)
    for ax in axes[:, 0]:
        ax.set_ylabel("Weighted completion ratio")
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, frameon=False, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(os.path.join(FIG, "results.pdf"), bbox_inches="tight")
    fig.savefig(os.path.join(FIG, "results.png"), dpi=170, bbox_inches="tight")
    print("wrote", FIG, "and", TAB)


if __name__ == "__main__":
    main()
