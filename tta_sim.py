"""
TTA-REO discrete-time simulation with link distances and causal delays.

Six-node example network (Fig. 3 of the paper). All model parameters are in
PARAMS. Run `python sweep.py` to regenerate every number in Section 8.

Time
----
One slot = TAU_SLOT_MS milliseconds. Classical signals travel in fibre at
C_FIBRE_KM_PER_MS (about 2/3 of c), so a distance L km costs
ceil(L / (C_FIBRE_KM_PER_MS * TAU_SLOT_MS)) slots.

Physics (abstract)
------------------
* Link e has length L_e. Per-slot heralded success probability
  p_e = P_MAX * 10^(-ALPHA_DB_PER_KM * L_e / 10)   (fibre loss).
* Heralding delay h_e = slots(L_e): the outcome of an attempt made at slot t is
  known at both endpoints at t + h_e. During that time the attempt holds one
  memory qubit at each endpoint, whether it succeeds or not.
* Stored quality Q(age) = Q0 * exp(-age / TAU_COH); pairs are discarded at age
  >= LIFETIME (age counts from the attempt slot).
* Swap (d1, at C) and fusion (d2, at E) succeed with P_SWAP / P_FUSE and
  multiply quality by LOSS. Their measurement outcomes must reach the end
  nodes before the delivered state can be used: correction delay
  delta_d = slots(max distance from the operating node to the end nodes).
  The delivered state keeps occupying the end-node qubits and keeps decaying
  until then. A request counts as completed only if correction finishes by
  its deadline with quality >= Fmin.

Control and information (causality)
-----------------------------------
* A controller makes generation and assignment decisions. It learns an
  attempt's outcome INFO_DELAY slots after the herald (time for the report to
  reach it). Until then the pair cannot be assigned, and the controller counts
  the attempt as "pending" when deciding whether more generation is needed.
* Nodes enforce their own memory limits with fresh local information: an
  ordered attempt that finds no free qubit at an endpoint is not made.
* Arrivals are known immediately (request signalling delay is not modelled).

Requests (templates drawn uniformly)
------------------------------------
  d1  A-D Bell pair    pieces {AC, CD}  swap at C
  d2  B-E-F GHZ state  pieces {BE, EF}  fusion at E
  d3  C-F Bell pair    piece  {CF}      no operation
  BC is a physical link that no request uses.

Slot order: arrivals; local expiry and herald processing; finished
corrections; request expiry; generation; assignment; operations.
Common random numbers: arrivals, per-(slot, edge) attempt outcomes, and
operation outcomes come from seed-specific streams independent of policy.
"""
from __future__ import annotations
import math
import numpy as np

PARAMS = dict(
    NODES="ABCDEF",
    EDGES={  # edge: (length km, initial quality Q0)
        "AC": (20.0, 0.95),
        "CD": (25.0, 0.95),
        "BE": (15.0, 0.93),
        "EF": (10.0, 0.97),
        "CF": (30.0, 0.92),
        "BC": (20.0, 0.94),
    },
    P_MAX=0.90,             # per-slot success at zero length (batched attempts)
    ALPHA_DB_PER_KM=0.2,    # fibre attenuation
    TAU_SLOT_MS=0.1,        # slot length
    C_FIBRE_KM_PER_MS=200.0,
    MEM_DEFAULT=4,          # qubits at every node except C
    TAU_COH=100.0,          # slots (10 ms)
    LIFETIME=20,            # slots (2 ms)
    P_SWAP=0.90,
    P_FUSE=0.85,
    LOSS=0.98,
    ARRIVAL_RATE=0.4,       # Poisson requests per slot (calibrated, see sweep.py)
    HORIZON=500,            # arrivals in slots [0, HORIZON)
    DEADLINE=(8, 25),       # relative deadline ~ U{8..25} slots
    WEIGHTS=(1, 2, 3),
    FMIN={"d1": (0.70, 0.85), "d2": (0.68, 0.82), "d3": (0.80, 0.90)},
    STALE_EPS=0.05,         # TTA-lite staleness penalty
    INFO_DELAY=1,           # slots from herald to controller knowledge
    GEN_RANK="policy",      # "policy" or "shared" (identical random order)
    FEASIBILITY_FILTER=True,
)

TEMPLATES = {
    "d1": dict(pieces=("AC", "CD"), op="swap", at="C", ends="AD"),
    "d2": dict(pieces=("BE", "EF"), op="fuse", at="E", ends="BF"),
    "d3": dict(pieces=("CF",), op=None, at=None, ends="CF"),
}
POLICIES = ("EDF-greedy", "Fidelity-greedy", "TTA-lite", "TTA-lite-noCP", "RTC-direct")


# --------------------------------------------------------------------------
# Derived quantities
# --------------------------------------------------------------------------
def km_to_slots(L, P):
    return int(math.ceil(L / (P["C_FIBRE_KM_PER_MS"] * P["TAU_SLOT_MS"]) - 1e-12))


def p_link(e, P):
    L = P["EDGES"][e][0]
    return P["P_MAX"] * 10 ** (-P["ALPHA_DB_PER_KM"] * L / 10)


def herald_delay(e, P):
    return km_to_slots(P["EDGES"][e][0], P)


def node_distances(P):
    """All-pairs shortest fibre distance (km) on the physical graph."""
    nodes = P["NODES"]
    D = {a: {b: (0.0 if a == b else math.inf) for b in nodes} for a in nodes}
    for e, (L, _) in P["EDGES"].items():
        a, b = e
        D[a][b] = D[b][a] = min(D[a][b], L)
    for k in nodes:
        for i in nodes:
            for j in nodes:
                if D[i][k] + D[k][j] < D[i][j]:
                    D[i][j] = D[i][k] + D[k][j]
    return D


def correction_delay(kind, P, D=None):
    t = TEMPLATES[kind]
    if t["op"] is None:
        return 0
    D = D or node_distances(P)
    return km_to_slots(max(D[t["at"]][n] for n in t["ends"]), P)


def p_op(kind, P):
    return {"swap": P["P_SWAP"], "fuse": P["P_FUSE"], None: 1.0}[TEMPLATES[kind]["op"]]


def loss(kind, P):
    return P["LOSS"] if TEMPLATES[kind]["op"] else 1.0


def qubits_needed(kind):
    need = {}
    for e in TEMPLATES[kind]["pieces"]:
        for n in e:
            need[n] = need.get(n, 0) + 1
    return need


def derived_table(P=PARAMS):
    D = node_distances(P)
    return dict(
        links={e: dict(length_km=P["EDGES"][e][0], p=p_link(e, P), herald_slots=herald_delay(e, P),
                       q0=P["EDGES"][e][1]) for e in P["EDGES"]},
        correction_slots={k: correction_delay(k, P, D) for k in TEMPLATES})


# --------------------------------------------------------------------------
# Objects
# --------------------------------------------------------------------------
class Pair:
    __slots__ = ("edge", "q0", "birth", "herald_t", "known_t", "ok", "owner")

    def __init__(self, edge, q0, birth, herald_t, known_t, ok):
        self.edge, self.q0, self.birth = edge, q0, birth
        self.herald_t, self.known_t, self.ok, self.owner = herald_t, known_t, ok, None

    def age(self, t):
        return t - self.birth

    def q(self, t, P):
        return self.q0 * math.exp(-self.age(t) / P["TAU_COH"])


class Request:
    __slots__ = ("rid", "kind", "arrival", "deadline", "w", "fmin", "held", "done", "deliver_t", "out_q")

    def __init__(self, rid, kind, arrival, deadline, w, fmin):
        self.rid, self.kind, self.arrival, self.deadline = rid, kind, arrival, deadline
        self.w, self.fmin = w, fmin
        self.held = {}
        self.done = False
        self.deliver_t = None
        self.out_q = None

    @property
    def pieces(self):
        return TEMPLATES[self.kind]["pieces"]

    def missing(self):
        return [e for e in self.pieces if e not in self.held]


# --------------------------------------------------------------------------
# Workload (policy-independent)
# --------------------------------------------------------------------------
def make_workload(seed, P):
    rng = np.random.default_rng([seed, 1])
    reqs, rid = [], 0
    for t in range(P["HORIZON"]):
        for _ in range(rng.poisson(P["ARRIVAL_RATE"])):
            kind = ("d1", "d2", "d3")[rng.integers(3)]
            lo, hi = P["FMIN"][kind]
            reqs.append(dict(rid=rid, kind=kind, arrival=t,
                             deadline=t + int(rng.integers(P["DEADLINE"][0], P["DEADLINE"][1] + 1)),
                             w=int(rng.choice(P["WEIGHTS"])), fmin=float(rng.uniform(lo, hi))))
            rid += 1
    return reqs


# --------------------------------------------------------------------------
# Delay-aware completion value for RTC-direct
# --------------------------------------------------------------------------
def completion_value(req, held_q, t, P, cap, delta):
    """V_d(s) under continuation model pi0: each missing edge e is attempted
    every slot, independently, at its nominal rate, and must herald and be
    reported (h_e + INFO_DELAY) in time for the correction to finish by the
    deadline. Fresh pieces arrive with quality Q0 decayed over h_e + INFO_DELAY;
    the output then decays over the correction delay. Memory contention is
    ignored. Zero if the template is memory-infeasible or quality cannot reach
    Fmin."""
    for n, k in qubits_needed(req.kind).items():
        if k > cap[n]:
            return 0.0
    latest = req.deadline - delta           # latest slot at which the op may run
    if latest < t:
        return 0.0
    qprod = loss(req.kind, P)
    v = p_op(req.kind, P)
    for e in req.pieces:
        if e in held_q:
            qprod *= held_q[e]
        else:
            wait = herald_delay(e, P) + P["INFO_DELAY"]
            n_try = latest - t - wait + 1
            if n_try <= 0:
                return 0.0
            qprod *= P["EDGES"][e][1] * math.exp(-wait / P["TAU_COH"])
            v *= 1.0 - (1.0 - p_link(e, P)) ** n_try
    qprod *= math.exp(-delta / P["TAU_COH"])
    return v if qprod >= req.fmin else 0.0


def rtc(req, edge, q_new, t, P, cap, delta):
    held = {e: p.q(t, P) for e, p in req.held.items()}
    base = completion_value(req, held, t, P, cap, delta)
    held[edge] = q_new
    return completion_value(req, held, t, P, cap, delta) - base


# --------------------------------------------------------------------------
# Policy scores (higher is better)
# --------------------------------------------------------------------------
def match_score(policy, pair_q, pair_age, req, t, P, cap, edge, delta):
    urg = 1.0 + 1.0 / max(req.deadline - t + 1, 1)
    stale = P["STALE_EPS"] * pair_age / P["LIFETIME"]
    if policy == "EDF-greedy":
        return (-req.deadline, pair_q)
    if policy == "Fidelity-greedy":
        return (pair_q, -req.rid)
    if policy == "TTA-lite":
        return (req.w / len(req.missing()) * urg - stale,)
    if policy == "TTA-lite-noCP":
        return (req.w * urg - stale,)
    if policy == "RTC-direct":
        return (req.w * rtc(req, edge, pair_q, t, P, cap, delta),)
    raise ValueError(policy)


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
def simulate(policy, seed, cap_c, P=PARAMS, workload=None):
    edges = list(P["EDGES"])
    e_idx = {e: i for i, e in enumerate(edges)}
    cap = {n: P["MEM_DEFAULT"] for n in P["NODES"]}
    cap["C"] = cap_c
    D = node_distances(P)
    delta = {k: correction_delay(k, P, D) for k in TEMPLATES}
    h = {e: herald_delay(e, P) for e in edges}
    pe = {e: p_link(e, P) for e in edges}
    wl = workload if workload is not None else make_workload(seed, P)
    t_end = P["HORIZON"] + P["DEADLINE"][1] + 2
    rng_gen = np.random.default_rng([seed, 2]).random((t_end, len(edges)))
    gen_order = np.random.default_rng([seed, 4]).random((t_end, len(edges))).argsort(axis=1).argsort(axis=1)
    by_arrival = {}
    for r in wl:
        by_arrival.setdefault(r["arrival"], []).append(r)

    active, delivering, finished = [], [], []
    pool = []                       # every attempt/pair currently holding memory
    used = {n: 0 for n in P["NODES"]}
    held_out = {n: 0 for n in P["NODES"]}   # end-node qubits held during correction

    def release(p):
        pool.remove(p)
        for n in p.edge:
            used[n] -= 1

    for t in range(t_end):
        # arrivals
        for r in by_arrival.get(t, []):
            active.append(Request(r["rid"], r["kind"], r["arrival"], r["deadline"], r["w"], r["fmin"]))
        # local: failed attempts free memory at herald; pairs expire at lifetime
        for p in list(pool):
            if (not p.ok and t >= p.herald_t) or p.age(t) >= P["LIFETIME"]:
                if p.owner is not None:
                    del p.owner.held[p.edge]
                release(p)
        # corrections that finish now
        for r in [r for r in delivering if r.deliver_t <= t]:
            delivering.remove(r)
            for n in TEMPLATES[r.kind]["ends"]:
                held_out[n] -= 1
            r.done = r.out_q * math.exp(-(t - (r.deliver_t - delta[r.kind])) / P["TAU_COH"]) >= r.fmin - 1e-12
            finished.append(r)
        # request expiry
        for r in [r for r in active if t > r.deadline]:
            for p in r.held.values():
                p.owner = None
            r.held.clear()
            active.remove(r)
            finished.append(r)

        servable = [r for r in active if not P["FEASIBILITY_FILTER"] or
                    all(k <= cap[n] for n, k in qubits_needed(r.kind).items())]

        # generation (controller decides with delayed knowledge; nodes check memory)
        cand = []
        for e in edges:
            needing = [r for r in servable if e in r.missing()]
            if not needing:
                continue
            known_spare = sum(1 for p in pool if p.edge == e and p.owner is None and p.ok and t >= p.known_t)
            pending = sum(1 for p in pool if p.edge == e and t < p.known_t)
            if len(needing) > known_spare + pending:
                q0 = P["EDGES"][e][1]
                best = max(match_score(policy, q0, 0, r, t, P, cap, e, delta[r.kind]) for r in needing)
                if policy == "RTC-direct":
                    best = (best[0] * pe[e],)
                    if best[0] <= 0:
                        continue
                cand.append((best, e))
        if P["GEN_RANK"] == "shared":
            cand.sort(key=lambda x: gen_order[t][e_idx[x[1]]])
        else:
            cand.sort(key=lambda x: x[0], reverse=True)
        for _, e in cand:
            a, b = e
            if used[a] + held_out[a] < cap[a] and used[b] + held_out[b] < cap[b]:
                ok = rng_gen[t, e_idx[e]] < pe[e]
                pool.append(Pair(e, P["EDGES"][e][1], t, t + h[e], t + h[e] + P["INFO_DELAY"], ok))
                used[a] += 1
                used[b] += 1

        # assignment: only pairs whose herald has reached the controller
        while True:
            best = None
            for p in pool:
                if p.owner is not None or not p.ok or t < p.known_t:
                    continue
                q = p.q(t, P)
                for r in servable:
                    if p.edge in r.held or p.edge not in r.pieces:
                        continue
                    if q < r.fmin / loss(r.kind, P):
                        continue
                    s = match_score(policy, q, p.age(t), r, t, P, cap, p.edge, delta[r.kind])
                    if policy == "RTC-direct" and s[0] <= 0:
                        continue
                    if best is None or s > best[0]:
                        best = (s, p, r)
            if best is None:
                break
            _, p, r = best
            p.owner = r
            r.held[p.edge] = p

        # operations
        for r in list(active):
            if r.missing():
                continue
            dl = delta[r.kind]
            qout = loss(r.kind, P)
            for p in r.held.values():
                qout *= p.q(t, P)
            if t + dl > r.deadline:
                # correction cannot finish in time; release and drop the request
                for p in r.held.values():
                    p.owner = None
                r.held.clear()
                active.remove(r)
                finished.append(r)
                continue
            if qout * math.exp(-dl / P["TAU_COH"]) >= r.fmin:
                ok = np.random.default_rng([seed, 3, r.rid, t]).random() < p_op(r.kind, P)
                for p in list(r.held.values()):
                    release(p)
                r.held.clear()
                active.remove(r)
                if ok:
                    if dl == 0:
                        r.done = True
                        finished.append(r)
                    else:
                        r.out_q, r.deliver_t = qout, t + dl
                        for n in TEMPLATES[r.kind]["ends"]:
                            held_out[n] += 1
                        delivering.append(r)
                else:
                    finished.append(r)
            else:
                worst = min(r.held.values(), key=lambda p: p.q(t, P))
                del r.held[worst.edge]
                release(worst)
    finished.extend(active)
    finished.extend(delivering)
    assert len(finished) == len(wl)

    tot_w = sum(r.w for r in finished)
    out = dict(wcr=sum(r.w for r in finished if r.done) / tot_w if tot_w else float("nan"), n=len(finished))
    for k in TEMPLATES:
        rs = [r for r in finished if r.kind == k]
        out[f"cr_{k}"] = sum(r.done for r in rs) / len(rs) if rs else float("nan")
    return out
