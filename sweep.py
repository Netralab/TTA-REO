"""
Experiments for Section 8.

  python sweep.py capacity   # Exp. A: node-C capacity 4..1, policy-ranked generation
  python sweep.py shared     # Exp. B: as A, one random generation order for all policies
  python sweep.py info       # Exp. C: controller information delay 0..8 slots (C capacity 3)
  python sweep.py distance   # Exp. D: all link lengths scaled by 0.5..2 (C capacity 3)

Calibration: ARRIVAL_RATE in tta_sim.PARAMS was chosen by looking only at the
Fidelity-greedy baseline (C capacity 4, information delay 1) on pilot seeds
0-14, aiming at a weighted completion ratio near 0.5. Nothing else was tuned.
Reported runs use seeds 1000-1299.

Each experiment writes results/<name>_raw.json and results/<name>_summary.json.
Within an experiment, all pairwise tests are Holm-corrected together.
"""
import copy
import itertools
import json
import os
import sys
from multiprocessing import Pool

import numpy as np
from scipy.stats import wilcoxon

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # results/ always next to this file

from tta_sim import PARAMS, POLICIES, simulate, make_workload, derived_table

SEEDS = list(range(1000, 1300))
COMPARISONS = [
    ("TTA-lite", "Fidelity-greedy"),
    ("TTA-lite", "EDF-greedy"),
    ("TTA-lite", "TTA-lite-noCP"),
    ("RTC-direct", "Fidelity-greedy"),
    ("RTC-direct", "EDF-greedy"),
    ("RTC-direct", "TTA-lite"),
]
N_BOOT = 10000

EXPERIMENTS = {
    "capacity": dict(var="cap", values=[4, 3, 2, 1], base=dict(GEN_RANK="policy", INFO_DELAY=1), cap=None),
    "shared": dict(var="cap", values=[4, 3, 2, 1], base=dict(GEN_RANK="shared", INFO_DELAY=1), cap=None),
    "info": dict(var="INFO_DELAY", values=[0, 1, 2, 4, 8], base=dict(GEN_RANK="policy"), cap=3),
    "distance": dict(var="LSCALE", values=[0.5, 1.0, 1.5, 2.0], base=dict(GEN_RANK="policy", INFO_DELAY=1), cap=3),
}


def params_for(exp, value):
    P = copy.deepcopy(PARAMS)
    P.update(EXPERIMENTS[exp]["base"])
    if EXPERIMENTS[exp]["var"] == "INFO_DELAY":
        P["INFO_DELAY"] = value
    if EXPERIMENTS[exp]["var"] == "LSCALE":
        P["EDGES"] = {e: (L * value, q) for e, (L, q) in P["EDGES"].items()}
    return P


def run_one(args):
    exp, value, seed = args
    P = params_for(exp, value)
    cap = value if EXPERIMENTS[exp]["var"] == "cap" else EXPERIMENTS[exp]["cap"]
    wl = make_workload(seed, P)
    return value, seed, {pol: simulate(pol, seed, cap, P, wl) for pol in POLICIES}


def boot_ci(x, rng):
    x = np.asarray(x)
    idx = rng.integers(0, len(x), size=(N_BOOT, len(x)))
    m = x[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def holm(p):
    order = np.argsort(p)
    m, adj, run = len(p), np.empty(len(p)), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * p[i]))
        adj[i] = run
    return adj


def main(exp):
    spec = EXPERIMENTS[exp]
    os.makedirs("results", exist_ok=True)
    jobs = list(itertools.product([exp], spec["values"], SEEDS))
    with Pool() as pool:
        out = pool.map(run_one, jobs, chunksize=10)
    raw = {str(v): {} for v in spec["values"]}
    for v, s, res in out:
        raw[str(v)][str(s)] = res
    meta = dict(experiment=exp, variable=spec["var"], values=spec["values"], fixed_cap_C=spec["cap"],
                base=spec["base"], seeds=SEEDS, policies=POLICIES,
                params={k: v for k, v in PARAMS.items() if k not in ("EDGES", "FMIN")},
                derived_at_base=derived_table(params_for(exp, spec["values"][0] if spec["var"] != "LSCALE" else 1.0)))
    with open(f"results/{exp}_raw.json", "w") as f:
        json.dump(dict(meta=meta, results=raw), f)

    rng = np.random.default_rng(12345)
    summ = dict(meta=meta, per_value={}, comparisons=[])
    pv = []
    for v in spec["values"]:
        r = raw[str(v)]
        w = {pol: np.array([r[str(s)][pol]["wcr"] for s in SEEDS]) for pol in POLICIES}
        summ["per_value"][str(v)] = {
            pol: dict(mean=float(w[pol].mean()), ci95=boot_ci(w[pol], rng),
                      per_type={k: float(np.nanmean([r[str(s)][pol][f"cr_{k}"] for s in SEEDS]))
                                for k in ("d1", "d2", "d3")})
            for pol in POLICIES}
        for a, b in COMPARISONS:
            d = w[a] - w[b]
            p = 1.0 if np.all(d == 0) else float(wilcoxon(w[a], w[b]).pvalue)
            pv.append(p)
            summ["comparisons"].append(dict(value=v, a=a, b=b, mean_a=float(w[a].mean()), mean_b=float(w[b].mean()),
                                            diff=float(d.mean()), diff_ci95=boot_ci(d, rng),
                                            rel_pct=float(100 * (w[a].mean() / w[b].mean() - 1)),
                                            wins=int((d > 0).sum()), losses=int((d < 0).sum()),
                                            ties=int((d == 0).sum()), p=p))
    for c, pa in zip(summ["comparisons"], holm(np.array(pv))):
        c["p_holm"] = float(pa)
    with open(f"results/{exp}_summary.json", "w") as f:
        json.dump(summ, f, indent=1)

    for v in spec["values"]:
        print(f"{spec['var']}={v}: " + "  ".join(f"{p}={summ['per_value'][str(v)][p]['mean']:.3f}" for p in POLICIES))
    for c in summ["comparisons"]:
        print(f"{spec['var']}={c['value']} {c['a']} vs {c['b']}: {c['rel_pct']:+.1f}% "
              f"W/L/T {c['wins']}/{c['losses']}/{c['ties']} p_holm={c['p_holm']:.2g}")


if __name__ == "__main__":
    unknown = [a for a in sys.argv[1:] if a not in EXPERIMENTS]
    if unknown:
        sys.exit(f"unknown experiment(s) {unknown}; choose from {list(EXPERIMENTS)}")
    for exp in (sys.argv[1:] or list(EXPERIMENTS)):
        main(exp)
