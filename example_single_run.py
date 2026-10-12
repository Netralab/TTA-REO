"""
Small worked example: run every policy once on one seed, then show how to
change a parameter. Takes a few seconds.

    python example_single_run.py
"""
import copy

from tta_sim import PARAMS, POLICIES, simulate, make_workload, derived_table

SEED = 1000
CAP_C = 3          # memory qubits at the shared node C

# 1. Quantities derived from the physical parameters (Table 5 of the paper)
d = derived_table(PARAMS)
print("Link  length(km)  p_success  herald(slots)")
for e, v in d["links"].items():
    print(f"{e:4s}  {v['length_km']:9.0f}  {v['p']:9.3f}  {v['herald_slots']:13d}")
print("Correction delay (slots):", d["correction_slots"], "\n")

# 2. One run per policy on the same workload (common random numbers)
wl = make_workload(SEED, PARAMS)
print(f"Seed {SEED}, C capacity {CAP_C}, {len(wl)} requests")
print("Policy             WCR    d1     d2     d3")
for pol in POLICIES:
    r = simulate(pol, SEED, CAP_C, PARAMS, wl)
    print(f"{pol:16s} {r['wcr']:.3f}  {r['cr_d1']:.2f}   {r['cr_d2']:.2f}   {r['cr_d3']:.2f}")

# 3. Changing a parameter: make the controller's information 4 slots old
P = copy.deepcopy(PARAMS)
P["INFO_DELAY"] = 4
wl = make_workload(SEED, P)
print("\nSame seed with INFO_DELAY = 4 slots (0.4 ms):")
for pol in ("TTA-lite", "RTC-direct", "Fidelity-greedy"):
    r = simulate(pol, SEED, CAP_C, P, wl)
    print(f"{pol:16s} {r['wcr']:.3f}")
