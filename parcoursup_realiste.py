"""
Simulation Parcoursup « réaliste » : segments de candidats, académies, types de formation.

Compare trois schémas (le dernier tour = phase complémentaire, sur les places restantes) :
  A. actuel      : phase principale à ~14,6 vœux (nb individuel) + complémentaire 10 vœux
  B. 5 + PC      : phase principale limitée à 5 vœux + complémentaire 10 vœux
  C. 2 → 5 → 10  : trois tours ; seuls les non-affectés passent au tour suivant

Ordres de grandeur (hypothèses, inspirées des notes SIES / bilans Parcoursup 2025-2026) :
  - ~640 000 lycéens (voies générale / techno / pro) + ~230 000 réorientations, reprises d'études, etc.
  - ~17 000 formations hors apprentissage, dans 30 académies
  - Types : licence (non sélective, sectorisée), BTS, BUT, CPGE, écoles, sanitaire & social, autres
  - Quotas implicites : bonus bac pro en BTS, bac techno en BUT ; priorité académique en licence
Le modèle est calibré (CAP_SCALE) pour retrouver ~87 % de lycéens avec une proposition
à la fin de la phase principale et ~5,5 propositions par candidat ayant reçu une proposition.

Mécanique : acceptation différée côté candidats (Gale-Shapley) à chaque tour = état final
vers lequel converge un tour Parcoursup une fois les désistements terminés.
Chaque candidat a une liste de L formations acceptables classées par préférence ;
si aucune n'a de place libre au dernier tour, il est « sans solution acceptable ».

Dépendances : numpy, numba (pip install numba).
"""
import time
import numpy as np
from numba import njit

# ======================= PARAMÈTRES =======================
SEED = 1
N_ACAD = 30
L = 40                         # longueur de la liste de formations acceptables par candidat
CAP_SCALE = 1.00               # facteur global sur les capacités (calibrage)
K_PC = 10                      # vœux en phase complémentaire / dernier tour

# segments : effectif, talent moyen, nb moyen de vœux (phase principale actuelle)
SEGMENTS = {
    "Générale":      dict(n=385_000, talent=0.30, k=15.5),
    "Techno":        dict(n=125_000, talent=-0.30, k=14.5),
    "Pro":           dict(n=130_000, talent=-0.90, k=10.0),
    "Réorientation": dict(n=230_000, talent=0.00, k=12.0),
}
# types : nb de formations, capacité moyenne, sélectivité (poids du niveau dans le classement)
TYPES = {
    "Licence": dict(n=3600, cap=115, w=0.15),
    "BTS":     dict(n=8000, cap=26,  w=0.70),
    "BUT":     dict(n=1100, cap=55,  w=0.80),
    "CPGE":    dict(n=850,  cap=45,  w=1.00),
    "Écoles":  dict(n=1300, cap=50,  w=0.80),
    "SanSoc":  dict(n=1200, cap=60,  w=0.60),
    "Autres":  dict(n=1000, cap=30,  w=0.60),
}
# répartition des vœux par type selon le segment
PREF = {
    "Générale":      [0.40, 0.05, 0.13, 0.12, 0.14, 0.10, 0.06],
    "Techno":        [0.18, 0.38, 0.25, 0.02, 0.04, 0.10, 0.03],
    "Pro":           [0.08, 0.75, 0.04, 0.00, 0.01, 0.08, 0.04],
    "Réorientation": [0.35, 0.20, 0.10, 0.02, 0.08, 0.18, 0.07],
}
# bonus de classement (quotas / priorités) : (type, segment) -> bonus
BONUS = {("BTS", "Pro"): 0.2, ("BTS", "Techno"): 0.15, ("BUT", "Techno"): 0.3}
BONUS_LICENCE_ACADEMIE = 1.0   # sectorisation de la licence
# nb de formations réellement acceptables (au-delà : le candidat préfère renoncer / CAES)
L_ACCEPT = {"Générale": 40, "Techno": 30, "Pro": 15, "Réorientation": 30}
LOCALITE = {"Générale": 5.0, "Techno": 7.0, "Pro": 10.0, "Réorientation": 6.0}
POP_SD = 0.6                   # dispersion d'attractivité des formations au sein d'un type
NOISE_SD = 0.5                 # bruit du classement par les formations
N_DRAWS = 220                  # tirages pour construire les listes


# ======================= GÉNÉRATION =======================
def generer(rng):
    tnames = list(TYPES)
    ftype = np.concatenate([np.full(TYPES[t]["n"], k) for k, t in enumerate(tnames)])
    M = len(ftype)
    facad = rng.integers(0, N_ACAD, M)
    capm = np.array([TYPES[t]["cap"] for t in tnames])[ftype]
    cap = np.maximum(1, np.round(capm * rng.lognormal(-0.125, 0.5, M) * CAP_SCALE)).astype(np.int64)
    w = np.array([TYPES[t]["w"] for t in tnames])[ftype]
    pop = rng.normal(0, POP_SD, M)
    ntype = np.array([TYPES[t]["n"] for t in tnames])

    snames = list(SEGMENTS)
    N = sum(s["n"] for s in SEGMENTS.values())
    seg = np.concatenate([np.full(SEGMENTS[s]["n"], k, np.int8) for k, s in enumerate(snames)])
    acad = rng.integers(0, N_ACAD, N)
    talent = np.array([SEGMENTS[s]["talent"] for s in snames])[seg] + rng.normal(0, 1, N)
    kmean = np.array([SEGMENTS[s]["k"] for s in snames])[seg]
    K = np.clip(rng.poisson(rng.gamma(4.0, kmean / 4.0)), 1, 30)   # nb de vœux individuel

    F = np.empty((N, L), np.int32)
    for si, s in enumerate(snames):
        share = np.array(PREF[s])
        base = share[ftype] / ntype[ftype] * np.exp(pop)
        for a in range(N_ACAD):
            idx = np.where((seg == si) & (acad == a))[0]
            wgt = base * np.where(facad == a, LOCALITE[s], 1.0)
            p = wgt / wgt.sum()
            logw = np.log(wgt + 1e-300)
            for c in range(0, len(idx), 20000):
                ii = idx[c:c + 20000]
                d = rng.choice(M, size=(len(ii), N_DRAWS), p=p)
                d.sort(axis=1)
                dup = np.zeros_like(d, bool)
                dup[:, 1:] = d[:, 1:] == d[:, :-1]
                u = logw[d] + rng.gumbel(size=d.shape)          # Plackett-Luce
                u[dup] = -np.inf
                o = np.argsort(-u, axis=1)[:, :L]
                F[ii] = np.take_along_axis(d, o, axis=1)

    # score de classement des candidats par chaque formation de leur liste
    bonus = np.zeros((len(tnames), len(snames)))
    for (t, s), b in BONUS.items():
        bonus[tnames.index(t), snames.index(s)] = b
    # partie prévisible du score (connue d'un candidat bien informé) + aléa du dossier / du jury
    S_det = (w[F] * talent[:, None] + bonus[ftype[F], seg[:, None]]
             + np.where((ftype[F] == tnames.index("Licence")) & (facad[F] == acad[:, None]),
                        BONUS_LICENCE_ACADEMIE, 0.0)).astype(np.float32)
    S = (S_det + rng.normal(0, NOISE_SD, F.shape)).astype(np.float32)
    lacc = np.array([L_ACCEPT[s] for s in snames])[seg]
    return dict(F=F, S=S, S_det=S_det, cap=cap, K=K, seg=seg, lacc=lacc, ftype=ftype, N=N, M=M,
                snames=snames, tnames=tnames)


# ======================= AFFECTATION (Gale-Shapley, numba) =======================
@njit(cache=True)
def _heap_down(hs, hi, base, size, k):
    while True:
        l = 2 * k + 1
        if l >= size:
            return
        m = l
        if l + 1 < size and hs[base + l + 1] < hs[base + l]:
            m = l + 1
        if hs[base + m] < hs[base + k]:
            hs[base + m], hs[base + k] = hs[base + k], hs[base + m]
            hi[base + m], hi[base + k] = hi[base + k], hi[base + m]
            k = m
        else:
            return


@njit(cache=True)
def _heap_up(hs, hi, base, k):
    while k > 0:
        p = (k - 1) // 2
        if hs[base + k] < hs[base + p]:
            hs[base + k], hs[base + p] = hs[base + p], hs[base + k]
            hi[base + k], hi[base + p] = hi[base + p], hi[base + k]
            k = p
        else:
            return


@njit(cache=True)
def deferred_acceptance(F, S, allow, cap, active):
    """F,S,allow: (N,L). active: candidats participant au tour. cap: capacités du tour.
    Renvoie la position (rang dans la liste) obtenue par chaque candidat (-1 sinon) et les seuils."""
    N, Lm = F.shape
    M = cap.shape[0]
    off = np.zeros(M + 1, np.int64)
    for j in range(M):
        off[j + 1] = off[j] + max(cap[j], 0)
    hs = np.empty(off[M], np.float32)
    hi = np.empty(off[M], np.int64)          # code = i * Lm + pos
    size = np.zeros(M, np.int64)
    nxt = np.zeros(N, np.int64)
    stack = np.empty(N, np.int64)
    top = 0
    for i in range(N):
        if active[i]:
            stack[top] = i
            top += 1
    while top > 0:
        top -= 1
        i = stack[top]
        while nxt[i] < Lm:
            p = nxt[i]
            nxt[i] += 1
            if not allow[i, p]:
                continue
            j = F[i, p]
            c = cap[j]
            if c <= 0:
                continue
            s = S[i, p]
            b = off[j]
            if size[j] < c:
                k = size[j]
                hs[b + k] = s
                hi[b + k] = i * Lm + p
                size[j] += 1
                _heap_up(hs, hi, b, k)
                break
            if s > hs[b]:
                r = hi[b] // Lm
                hs[b] = s
                hi[b] = i * Lm + p
                _heap_down(hs, hi, b, size[j], 0)
                stack[top] = r
                top += 1
                break
    pos = -np.ones(N, np.int64)
    cutoff = np.full(M, -np.inf, np.float32)
    for j in range(M):
        b = off[j]
        for k in range(size[j]):
            code = hi[b + k]
            pos[code // Lm] = code % Lm
        if size[j] > 0 and size[j] >= cap[j]:
            cutoff[j] = hs[b]
    return pos, cutoff


# ======================= SCÉNARIOS =======================
def simuler(d, tours, choix=None):
    """tours: liste de limites de vœux ; None = nombre individuel K (système actuel).
    Chaque tour porte sur les non-affectés et les formations ayant encore des places,
    en prenant les vœux les plus préférés parmi celles-ci.
    choix(t, ouvert, kmax, active, allow) peut remplacer la liste soumise (comportement stratégique)."""
    F, S, cap, K, N = d["F"], d["S"], d["cap"].copy(), d["K"], d["N"]
    pos = -np.ones(N, np.int64)
    out = dict(dossiers=0, tours=[], cutoffs=[])
    fid = -np.ones(N, np.int64)            # formation obtenue
    for t, lim in enumerate(tours):
        active = pos < 0
        kmax = K if lim is None else np.minimum(K, lim) if t == 0 else np.full(N, lim)
        ouvert = cap[F] > 0
        acceptable = np.arange(F.shape[1])[None, :] < d["lacc"][:, None]
        ouvert &= acceptable
        rang_ouvert = np.cumsum(ouvert, axis=1)
        allow = ouvert & (rang_ouvert <= kmax[:, None]) & active[:, None]
        Ft, St = F, S
        if choix is not None:
            allow = choix(t, ouvert, kmax, active, allow)
        if t == len(tours) - 1 and d.get("pc_nationale") is not None:
            # phase complémentaire élargie : colonnes supplémentaires hors liste initiale
            Ft, St, allow = d["pc_nationale"](d, cap, active, allow, kmax)
        # dossiers DÉPOSÉS (tous les candidats déposent leurs vœux, y compris ceux qui ont une
        # alternative ; en PC : candidatures déposées, classées ou non)
        ouv_all = cap[F] > 0
        soumis = int((ouv_all & (np.cumsum(ouv_all, axis=1) <= kmax[:, None]) & active[:, None]).sum())
        if t == len(tours) - 1 and d.get("pc_stats") is not None and d.get("pc_nationale") is not None:
            soumis = int(d["pc_stats"]["deposees"].sum())
        out.setdefault("soumis", []).append(soumis)
        p, cut = deferred_acceptance(Ft, St, allow, cap, active)
        nprop = ((St >= cut[Ft]) & allow).sum(axis=1)
        newly = p >= 0
        fid[newly] = Ft[newly, p[newly]]
        pos[newly] = np.minimum(p[newly], F.shape[1])     # rang ≥ L = formation hors liste initiale
        taken = np.bincount(fid[newly], minlength=d["M"])
        cap = cap - taken
        out["dossiers"] += int(allow.sum())
        out["cutoffs"].append(cut)
        lyc = d["seg"] < 3
        out["tours"].append(dict(participants=int(active.sum()), affectes=int(newly.sum()),
                                 lyc_affectes_cumul=float(np.mean(pos[lyc] >= 0)),
                                 par_segment={sn: (float(np.mean(pos[d["seg"] == k] >= 0)),
                                                   float(nprop[(d["seg"] == k) & (nprop > 0)].mean()))
                                              for k, sn in enumerate(d["snames"])},
                                 props_moy=float(nprop[nprop > 0].mean()) if (nprop > 0).any() else 0,
                                 places_vacantes=int(np.maximum(cap, 0).sum())))
    out["pos"] = pos
    out["fid"] = fid
    return out


def resume(d, res, nom):
    pos, seg = res["pos"], d["seg"]
    r = np.where(pos >= 0, pos + 1, 10 ** 6)
    lyc = seg < 3
    def ligne(mask):
        rr = r[mask]
        ok = rr < 10 ** 6
        return [np.mean(rr == 1), np.mean(rr <= 2), np.mean(rr <= 5), np.mean(rr <= 10),
                np.mean(~ok), rr[ok].mean()]
    print(f"\n=== {nom} ===")
    for t, info in enumerate(res["tours"]):
        print(f"  tour {t + 1}: {info['participants']:>7} participants, {info['affectes']:>7} affectés, "
              f"{info['props_moy']:.1f} prop./candidat servi, lycéens servis (cumul) {info['lyc_affectes_cumul']:.1%}, "
              f"places vacantes après : {info['places_vacantes']:>7}")
        print("          servis (cumul) / prop. moy. : " + ", ".join(
            f"{sn} {v[0]:.0%} / {v[1]:.1f}" for sn, v in info["par_segment"].items()))
    print(f"  dossiers examinés : {res['dossiers']:,}".replace(",", " "))
    print(f"  {'':<15}{'1er':>7}{'top2':>7}{'top5':>7}{'top10':>7}{'sans sol.':>11}{'rang moy':>10}")
    groupes = [("Tous", np.ones_like(lyc)), ("Lycéens", lyc)] + \
              [(s, seg == k) for k, s in enumerate(d["snames"])]
    for g, m in groupes:
        v = ligne(m)
        print(f"  {g:<15}" + "".join(f"{x:>7.1%}" for x in v[:4]) + f"{v[4]:>11.2%}{v[5]:>10.2f}")


if __name__ == "__main__":
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    d = generer(rng)
    print(f"{d['N']:,} candidats, {d['M']:,} formations, {d['cap'].sum():,} places "
          f"(génération {time.time() - t0:.0f} s)".replace(",", " "))
    scen = {"A. Actuel (≈14,6 + PC 10)": [None, K_PC],
            "B. 5 vœux + PC 10": [5, K_PC],
            "C. 3 tours : 2 → 5 → 10": [2, 5, K_PC]}
    for nom, tours in scen.items():
        res = simuler(d, tours)
        resume(d, res, nom)
    print(f"\n(temps total {time.time() - t0:.0f} s)")
