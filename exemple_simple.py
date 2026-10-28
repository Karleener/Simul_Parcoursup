"""
Simulation Parcoursup : phase principale à 14 vœux vs 5 vœux, suivie dans les deux cas
de la phase complémentaire (K2 vœux sur les places restantes).

Modèle
- N candidats, M formations, capacité totale = RATIO * N.
- Préférences élèves : U[i,j] = attractivité_j (commune, écart-type POP_SD) + bruit individuel N(0,1).
- Classement par les formations : S[i,j] = w_j * talent_i + bruit N(0, 0.5),
  w_j ∈ [0.2, 1] = sélectivité (0.2 ~ licence peu sélective, 1 ~ CPGE/école).
- Affectation : acceptation différée côté candidats (Gale-Shapley), = état final
  vers lequel converge Parcoursup une fois les désistements terminés.
- Scénario 2 tours :
    tour 1 : chaque élève soumet ses K1 vœux préférés ;
    tour 2 : les recalés soumettent K2 vœux parmi les formations ayant encore des places
             (ils voient les places restantes, comme en phase complémentaire).
Calibrage par défaut (POP_SD=0.5, RATIO=1.2) : ~5,8 propositions/candidat avec 14 vœux,
proche des ~5,6 observées dans les bilans Parcoursup 2025.
"""
import numpy as np

# ---------------- paramètres ----------------
N, M = 8000, 300
RATIO = 1.2          # places / candidats
POP_SD = 0.5         # concentration des préférences (tension)
K_ACTUEL = 14        # ≈ 14,6 vœux+sous-vœux moyens (SIES 2026)
K1, K2 = 5, 10       # vœux au tour 1, au tour 2
N_SEEDS = 5


def deferred_acceptance(prefs, score, cap):
    """prefs: liste d'arrays d'indices de formations (ordre de préférence).
    score: matrice (n_cand, M) des classements par les formations.
    cap: capacités (M,). Renvoie l'affectation (-1 = rien) et le score seuil de chaque formation."""
    n, m = len(prefs), len(cap)
    nxt = np.zeros(n, int)
    held = [[] for _ in range(m)]
    free = list(range(n))
    while free:
        i = free.pop()
        if nxt[i] >= len(prefs[i]):
            continue
        j = prefs[i][nxt[i]]
        nxt[i] += 1
        if cap[j] <= 0:
            free.append(i)
            continue
        held[j].append(i)
        if len(held[j]) > cap[j]:
            held[j].sort(key=lambda s: -score[s, j])
            free.append(held[j].pop())
    assign = -np.ones(n, int)
    cutoff = np.full(m, -np.inf)
    for j in range(m):
        for i in held[j]:
            assign[i] = j
        if held[j] and len(held[j]) >= cap[j]:
            cutoff[j] = min(score[i, j] for i in held[j])
    return assign, cutoff


def nb_propositions(prefs, score, cutoff):
    """Nombre de vœux où le candidat est au-dessus du seuil final (≈ propositions reçues)."""
    return np.array([(score[i, p] >= cutoff[p]).sum() for i, p in enumerate(prefs)])


def instance(seed):
    rng = np.random.default_rng(seed)
    pop = rng.normal(0, POP_SD, M)
    cap = rng.lognormal(0, 0.5, M)
    cap = np.maximum(1, np.round(cap / cap.sum() * N * RATIO)).astype(int)
    w = rng.uniform(0.2, 1.0, M)
    talent = rng.normal(0, 1, N)
    U = pop[None, :] + rng.normal(0, 1, (N, M))
    S = w[None, :] * talent[:, None] + rng.normal(0, 0.5, (N, M))
    order = np.argsort(-U, axis=1)                 # préférences complètes
    rank = np.empty_like(order)                    # rank[i, j] = rang vrai de j pour i (0 = 1er choix)
    rank[np.arange(N)[:, None], order] = np.arange(M)[None, :]
    return cap, talent, S, order, rank


def scenario_actuel(cap, S, order):
    """Phase principale à K_ACTUEL vœux + phase complémentaire à K2 vœux (= système réel)."""
    return principale_puis_complementaire(cap, S, order, K_ACTUEL)


def scenario_2tours(cap, S, order):
    """Phase principale réduite à K1 vœux + même phase complémentaire."""
    return principale_puis_complementaire(cap, S, order, K1)


def principale_puis_complementaire(cap, S, order, K1):
    prefs1 = [order[i, :K1] for i in range(N)]
    a1, cut1 = deferred_acceptance(prefs1, S, cap)
    props1 = nb_propositions(prefs1, S, cut1)

    restant = cap - np.bincount(a1[a1 >= 0], minlength=M)
    recales = np.where(a1 < 0)[0]
    ouvertes = restant > 0
    prefs2 = [order[i][ouvertes[order[i]]][:K2] for i in recales]
    a2, _ = deferred_acceptance(prefs2, S[recales], restant)

    a = a1.copy()
    a[recales] = a2
    return a, {"dossiers": N * K1 + len(recales) * K2,
               "props_moy": props1[props1 > 0].mean(),
               "sans_rien_tour1": len(recales) / N,
               "formations_ouvertes_t2": ouvertes.mean()}


def metriques(a, rank, talent, recales_mask=None):
    ok = a >= 0
    r = np.where(ok, rank[np.arange(N), np.maximum(a, 0)], M)   # M = non affecté
    q = np.digitize(talent, np.quantile(talent, [.25, .5, .75]))
    out = {"1er_choix": np.mean(r == 0), "top5": np.mean(r < 5), "top10": np.mean(r < 10),
           "top14": np.mean(r < 14), "non_affecte": np.mean(~ok),
           "rang_moyen_affectes": r[ok].mean() + 1}
    for k, nom in enumerate(["Q1(faible)", "Q2", "Q3", "Q4(fort)"]):
        out[f"top5_{nom}"] = np.mean(r[q == k] < 5)
    return out, r


if __name__ == "__main__":
    acc = {"actuel": [], "2tours": []}
    groupe_recale = []
    for seed in range(N_SEEDS):
        cap, talent, S, order, rank = instance(seed)
        a0, info0 = scenario_actuel(cap, S, order)
        a1, info1 = scenario_2tours(cap, S, order)
        m0, r0 = metriques(a0, rank, talent)
        m1, r1 = metriques(a1, rank, talent)
        acc["actuel"].append({**m0, **info0})
        acc["2tours"].append({**m1, **info1})
        # que deviennent les recalés du tour 1 par rapport au système actuel ?
        g = r1 >= K1                       # pas affectés dans leur top 5 avec 2 tours
        groupe_recale.append({
            "part_eleves": g.mean(),
            "rang_moyen_actuel": (r0[g & (r0 < M)] + 1).mean(),
            "non_affecte_actuel": np.mean(r0[g] == M),
            "rang_moyen_2tours": (r1[g & (r1 < M)] + 1).mean(),
            "non_affecte_2tours": np.mean(r1[g] == M),
        })

    keys = list(acc["actuel"][0].keys())
    print(f"N={N}, M={M}, places/candidats={RATIO}, POP_SD={POP_SD}, {N_SEEDS} tirages\n")
    print(f"{'indicateur':<26}{'actuel ('+str(K_ACTUEL)+'+'+str(K2)+')':>16}{'5 vœux ('+str(K1)+'+'+str(K2)+')':>18}")
    for k in keys:
        v0 = np.mean([d.get(k, np.nan) for d in acc["actuel"]])
        v1 = np.mean([d.get(k, np.nan) for d in acc["2tours"]])
        print(f"{k:<26}{v0:>16.3f}{v1:>18.3f}")
    g = {k: np.mean([d[k] for d in groupe_recale]) for k in groupe_recale[0]}
    print(f"\nÉlèves hors de leur top 5 avec 2 tours ({g['part_eleves']:.1%} des candidats) :")
    print(f"  système actuel : rang moyen {g['rang_moyen_actuel']:.1f}, non affectés {g['non_affecte_actuel']:.1%}")
    print(f"  2 tours        : rang moyen {g['rang_moyen_2tours']:.1f}, non affectés {g['non_affecte_2tours']:.1%}")
