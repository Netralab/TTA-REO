# TTA-REO simulation code

Simulation code and data for the paper

> S. Roy and R. Mukherjee, "तत् त्वम् असि (*Tat Tvam Asi*)-Inspired Relational Entanglement
> Orchestration for Quantum Communication Networks," preprint, 2026.

The code simulates a small quantum network in which several entanglement requests compete
for limited quantum memory. It compares five allocation policies and produces every number,
table and figure reported in the paper's experimental sections.

---

## 1. What is in this folder

| File | Purpose |
|---|---|
| `tta_sim.py` | The simulator: network model, request generator, the five policies, and `simulate()`. All parameters are in the `PARAMS` dictionary at the top. |
| `sweep.py` | Runs the four experiments of the paper (300 seeds each) and does the statistics. |
| `make_figures.py` | Turns the experiment summaries into the results figure and the LaTeX table rows. |
| `example_single_run.py` | A short worked example: one seed, all policies, then one changed parameter. Start here. |
| `results/*_raw.json` | Per-seed outcomes of every policy in every setting (the data behind the paper). |
| `results/*_summary.json` | Means, 95% bootstrap intervals and Holm-corrected Wilcoxon tests. |
| `requirements.txt` | Python packages needed. |

---

## 2. Installation

Python 3.9 or later. Tested with Python 3.13, NumPy 2.5, SciPy 1.18, Matplotlib 3.11.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

No GPU or special hardware is needed.

---

## 3. Quick start

```bash
python example_single_run.py
```

This prints the link quantities derived from the physical parameters, the weighted completion
ratio (WCR) of each policy on one seed, and the effect of making the controller's information
older. It runs in a few seconds. Results on a single seed vary; the paper's conclusions rest
on 300 seeds per setting.

---

## 4. Reproducing the paper

```bash
python sweep.py              # all four experiments, about 6 minutes on 2 CPU cores
python make_figures.py       # writes output/figs/results.pdf and output/tables/*.tex
```

You can also run a single experiment:

| Command | Experiment | What varies | Research question |
|---|---|---|---|
| `python sweep.py capacity` | A | Memory at node C: 4, 3, 2, 1 qubits | RQ1, RQ2 |
| `python sweep.py shared` | B | As A, but all policies share one random generation order | RQ2 |
| `python sweep.py info` | C | Controller report delay: 0, 1, 2, 4, 8 slots (C = 3) | RQ3, RQ4 |
| `python sweep.py distance` | D | All link lengths × 0.5, 1, 1.5, 2 (C = 3) | RQ3, RQ4 |

Each experiment writes `results/<name>_raw.json` and `results/<name>_summary.json`, replacing
the shipped files. The simulation is deterministic: rerunning reproduces the shipped per-seed
results exactly (this was checked for Experiment A).

To write the figure and tables straight into a LaTeX project:

```bash
python make_figures.py --out ../paper     # creates ../paper/figs and ../paper/tables
```

---

## 5. The model in brief

**Network.** Six nodes A–F. Links AC (20 km), CD (25 km), BE (15 km), EF (10 km),
CF (30 km) and BC (20 km). Every node has 4 memory qubits except C, whose memory is varied.

**Requests.** Each arrival is one of three types, chosen with equal probability:

| Type | Target | Built from | Operation |
|---|---|---|---|
| d1 | A–D Bell pair | pairs on AC and CD | entanglement swap at C (needs 2 qubits at C at once) |
| d2 | B–E–F GHZ state | pairs on BE and EF | fusion at E |
| d3 | C–F Bell pair | one pair on CF | none |

Each request has a fidelity threshold, a deadline and a priority weight (1, 2 or 3).

**Time and distance.** One slot is 0.1 ms; signals travel 200 km per ms in fibre.

- Link success probability per slot: `p = 0.9 × 10^(−0.2·L/10)`
- Heralding delay: `ceil(L / 20 km)` slots. The attempt holds a memory qubit at both ends until then.
- After a swap or fusion, the result must reach the end nodes before the request counts as complete (2 slots for d1, 1 slot for d2).

**Decay.** Stored quality falls as `Q0·exp(−age/100)`, and pairs are discarded after 20 slots.

**Controller information.** The controller learns a link outcome `INFO_DELAY` slots after the
endpoints do, and cannot assign a pair before then. Nodes enforce their own memory limits with
current local information.

**Metric.** Weighted completion ratio = (sum of weights of completed requests) / (sum of
weights of all requests).

---

## 6. The five policies

All policies see the same arrivals and the same random outcomes for a given seed. Each policy
gives one score, used both to decide which links to try to generate and which known pair goes
to which request.

| Name in code | Rule |
|---|---|
| `Fidelity-greedy` | Highest-quality pair–request match first. |
| `EDF-greedy` | Earliest-deadline request first. |
| `TTA-lite` | `w / n_missing × (1 + 1/(time left + 1)) − 0.05 × age/lifetime`. Favours requests that are close to completion, important and urgent. |
| `TTA-lite-noCP` | TTA-lite without the `1/n_missing` factor (ablation). |
| `RTC-direct` | Weight × relational task contribution, i.e. the change in the request's estimated completion probability if it receives the pair. The estimate accounts for link success rates, heralding, report and correction delays, and deadlines, but not for competition for memory. |

---

## 7. Changing parameters

All parameters are in `PARAMS` at the top of `tta_sim.py`. To experiment without editing the
file, copy and modify the dictionary:

```python
import copy
from tta_sim import PARAMS, simulate, make_workload

P = copy.deepcopy(PARAMS)
P["INFO_DELAY"] = 2                       # older controller information
P["EDGES"]["CF"] = (40.0, 0.92)           # longer CF link: (length km, initial quality)
P["ARRIVAL_RATE"] = 0.5                   # heavier load

wl = make_workload(1000, P)               # seed 1000
result = simulate("TTA-lite", 1000, 3, P, wl)   # policy, seed, memory at C, params, workload
print(result)   # {'wcr': ..., 'n': ..., 'cr_d1': ..., 'cr_d2': ..., 'cr_d3': ...}
```

Main parameters:

| Key | Meaning | Paper value |
|---|---|---|
| `EDGES` | Link: (length km, initial quality) | see Section 5 |
| `P_MAX`, `ALPHA_DB_PER_KM` | Success at zero length; fibre loss | 0.90; 0.2 dB/km |
| `TAU_SLOT_MS`, `C_FIBRE_KM_PER_MS` | Slot length; signal speed | 0.1 ms; 200 km/ms |
| `MEM_DEFAULT` | Qubits per node (except C) | 4 |
| `TAU_COH`, `LIFETIME` | Decay constant; discard age (slots) | 100; 20 |
| `P_SWAP`, `P_FUSE`, `LOSS` | Operation success; quality loss per operation | 0.90, 0.85, 0.98 |
| `ARRIVAL_RATE` | Requests per slot (Poisson) | 0.4 |
| `DEADLINE` | Relative deadline range (slots) | 8–25 |
| `INFO_DELAY` | Report delay to the controller (slots) | 1 |
| `GEN_RANK` | `"policy"` or `"shared"` generation order | `"policy"` |

`ARRIVAL_RATE` was the only value tuned, using the Fidelity-greedy baseline alone on pilot
seeds 0–14. The reported runs use seeds 1000–1299.

---

## 8. Output format

`simulate()` returns a dictionary:

- `wcr`: weighted completion ratio for the run
- `n`: number of requests that arrived
- `cr_d1`, `cr_d2`, `cr_d3`: fraction of each request type completed

`results/<exp>_summary.json` contains, for every setting and policy, the mean WCR, its 95%
bootstrap interval and per-type completion rates. It also lists every pairwise comparison with
the relative difference (%), wins/losses/ties over seeds, the raw Wilcoxon p-value and the
Holm-adjusted p-value.

---

## 9. Limitations

This is an abstract discrete-time model intended to compare allocation rules. Quality is a
scalar stand-in for fidelity (density matrices are not tracked), operation success rates are
fixed, the report delay is the same for every node, and only one topology is studied. It is not
a physical-layer simulator; for that, see NetSquid or SeQUeNCe.

---

## 10. Citation

If you use this code, please cite the paper above. Contact: Swarup Roy (swarup@tezu.ernet.in),
Rupak Mukherjee (rmukherjee@cus.ac.in).
