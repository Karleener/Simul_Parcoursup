"""
Où ouvrir des places ? Simulation de politiques d'ouverture de places sur Parcoursup 2025.

1. Diagnostic (système actuel A2 : ≈14 vœux + phase complémentaire nationale) :
   « demande bloquée » = candidats demandeurs refusés dans une formation pleine qu'ils préféraient
   à leur affectation finale ; gain de satisfaction qu'ils auraient eu en y entrant.
2. Trois politiques d'ouverture à budget égal (coût annuel en régime permanent) :
   - Uniforme      : +x % de places dans toutes les formations publiques ou sous contrat
   - Tension       : formations au plus fort ratio vœux/place (indicateur « classique »)
   - Ciblée        : allocation gloutonne sur la demande bloquée par euro (chaque candidat ne compte qu'une fois)
   Ouvertures par blocs de 10 places, au plus +50 % de la capacité d'une formation,
   uniquement dans les formations publiques ou privées sous contrat.
3. Chaque politique est re-simulée (affectation complète, effets en chaîne compris) avec le même
   comportement des candidats (informés / naïfs) que l'étude principale.

Coût d'une place de 1re année en régime permanent = coût annuel par étudiant × durée du cursus.
Usage : python parcoursup_ouvertures.py fr-esr-parcoursup.csv
"""
import sys
import time
import heapq
import json
import numpy as np
import pandas as pd
import parcoursup_realiste as pr
import parcoursup_strategies as ps
import parcoursup_reel as R

# coût annuel par étudiant (€) et durée (années) — sources dans le rapport ; (h) = hypothèse
COUTS = {
    "Licence": (12_250, 3), "Licence_Las": (12_250, 3), "PASS": (12_250, 3),  # INSEE/DEPP 2023, université
    "BTS": (16_730, 2),                                                     # INSEE/DEPP 2023, STS
    "CPGE": (18_560, 2),                                                    # INSEE/DEPP 2023
    "BUT": (14_500, 3),                                                     # (h) entre université et STS
    "Ecole d'Ingénieur": (16_000, 5),                                       # (h) écoles publiques post-bac
    "Ecole de Commerce": (12_000, 3),                                       # (h) peu concerné (privé)
    "IFSI": (9_000, 3),                                                     # coût pédagogique publié 7 600-9 350 €
    "EFTS": (9_000, 3),                                                     # (h) comparable IFSI
    "Autre formation": (13_000, 3),                                         # (h) DN MADE, DE…
}
BLOC = 10
MAX_HAUSSE = 0.5
BUDGETS_M = [100, 250, 500]        # M€ par an en régime permanent


def infos_formations(path):
    df = pd.read_csv(path, sep=";", low_memory=False)
    c = df.columns
    col = lambda debut: next(x for x in c if x.startswith(debut))
    cap = pd.to_numeric(df[col("Capacité de l’établissement")], errors="coerce").fillna(0)
    df = df[cap > 0].reset_index(drop=True)
    statut = df[col("Statut de l’établissement")].astype(str).values
    ouvrable = np.isin(statut, ["Public", "Privé sous contrat d'association"])
    return dict(region=df[col("Région de l’établissement")].astype(str).values,
                academie=df[col("Académie de l’établissement")].astype(str).values,
                voeux=pd.to_numeric(df[col("Effectif total des candidats en phase principale")],
                                    errors="coerce").fillna(0).values,
                ouvrable=ouvrable, nom=df[col("Établissement")].astype(str).values,
                filiere=df[col("Filière de formation")].astype(str).values)


def cout_place(D):
    tn = D["tnames"]
    par_type = np.array([COUTS[t][0] * COUTS[t][1] for t in tn], float)
    return par_type[D["ftype"]]


def simuler_A2(d, informe, cap, tours=None):
    tours = tours or [None, pr.K_PC]
    d["cap"] = cap
    d["pc_nationale"] = R.pc_nationale
    ref = ps.run_reference(d, tours)
    return pr.simuler(d, tours, ps.fabrique_choix(d, informe, ref, tours)), ref


def indicateurs(d, res, informe, base_pos=None):
    m = d["lacc"] == R.L
    pos = res["pos"]
    r = np.where(pos >= 0, pos + 1, 10 ** 6)
    u = ps.valeur(pos, None)
    out = {"utilite": u[m].mean(), "1er": np.mean(r[m] == 1), "top5": np.mean(r[m] <= 5),
           "top10": np.mean(r[m] <= 10), "sans_sol": np.mean(r[m] == 10 ** 6),
           "sans_sol_n": int(np.sum(r[m] == 10 ** 6)), "demandeurs": int(m.sum())}
    for k, s in enumerate(R.SNAMES):
        mk = m & (d["seg"] == k)
        out[f"u_{s}"] = u[mk].mean()
        out[f"ss_{s}"] = np.mean(r[mk] == 10 ** 6)
        out[f"top5_{s}"] = np.mean(r[mk] <= 5)
    ecarts, w = [], []
    for k in range(len(R.SNAMES)):
        mi, mn = m & (d["seg"] == k) & informe, m & (d["seg"] == k) & ~informe
        ecarts.append(u[mi].mean() - u[mn].mean())
        w.append(mi.sum() + mn.sum())
    out["prime_info"] = float(np.average(ecarts, weights=w))
    if base_pos is not None:
        ub = ps.valeur(base_pos, None)
        out["ameliores"] = int(np.sum(u[m] > ub[m] + 1e-9))
        out["degrades"] = int(np.sum(u[m] < ub[m] - 1e-9))
        out["ss_sortis"] = int(np.sum(m & (base_pos < 0) & (pos >= 0)))
        out["ss_entrants"] = int(np.sum(m & (base_pos >= 0) & (pos < 0)))
    return out


def demande_bloquee(d, res):
    """Pour chaque demandeur, les formations pleines (seuil fini) qu'il préférait à son affectation
    et où il était sous le seuil : (formation, score, gain de satisfaction)."""
    F, S = d["F"], d["S"]
    pos = res["pos"]
    cut = res["cutoffs"][0]
    m = d["lacc"] == R.L
    rang = np.where(pos >= 0, pos, 10 ** 6)
    V = np.exp(-np.arange(R.L) / ps.TAU)
    u_fin = ps.valeur(pos, None)
    P = np.arange(R.L)[None, :]
    prefere = (P < np.minimum(rang, np.minimum(d["K"], R.L))[:, None]) & m[:, None]
    bloque = prefere & np.isfinite(cut[F]) & (S < cut[F])
    ii, pp = np.nonzero(bloque)
    gain = V[pp] - u_fin[ii]
    return ii, F[ii, pp], S[ii, pp], gain, pp


def allocation_ciblee(D, info, cap, budget, bl, cout):
    """Glouton : bloc de 10 places dont le gain (candidats non encore servis, du meilleur score au
    moins bon) par euro est maximal. Un candidat servi ne compte plus ailleurs."""
    ii, jj, sc, gain, _ = bl
    order = np.lexsort((-sc, jj))
    ii, jj, sc, gain = ii[order], jj[order], sc[order], gain[order]
    starts = np.searchsorted(jj, np.arange(D["M"]))
    ends = np.searchsorted(jj, np.arange(D["M"]), side="right")
    servi = np.zeros(int(ii.max()) + 1 if len(ii) else 1, bool)
    ptr = starts.copy()
    ajout = np.zeros(D["M"], np.int64)
    maxadd = np.where(info["ouvrable"], np.maximum(BLOC, (MAX_HAUSSE * cap // BLOC) * BLOC), 0)

    def valeur_bloc(j):
        k, g, p = 0, 0.0, ptr[j]
        while p < ends[j] and k < BLOC:
            if not servi[ii[p]]:
                g += gain[p]
                k += 1
            p += 1
        return g, p

    heap = []
    for j in np.where((ends > starts) & (maxadd > 0))[0]:
        g, _ = valeur_bloc(j)
        if g > 0:
            heapq.heappush(heap, (-g / (BLOC * cout[j]), j))
    depense = 0.0
    while heap:
        negv, j = heapq.heappop(heap)
        g, pend = valeur_bloc(j)
        if g <= 0 or ajout[j] + BLOC > maxadd[j]:
            continue
        v = g / (BLOC * cout[j])
        if heap and v < -heap[0][0] - 1e-15:
            heapq.heappush(heap, (-v, j))
            continue
        c = BLOC * cout[j]
        if depense + c > budget:
            continue
        depense += c
        ajout[j] += BLOC
        k, p = 0, ptr[j]
        while p < ends[j] and k < BLOC:
            if not servi[ii[p]]:
                servi[ii[p]] = True
                k += 1
            p += 1
        ptr[j] = p
        g2, _ = valeur_bloc(j)
        if g2 > 0 and ajout[j] + BLOC <= maxadd[j]:
            heapq.heappush(heap, (-g2 / (BLOC * cout[j]), j))
    return ajout, depense


def allocation_sans_solution(d, D, info, cap, budget, base, cout):
    """Priorité aux sans-solution. Pour chaque formation pleine, file d'attente des candidats refusés
    qui la préféraient à leur affectation (demandeurs ET candidats à option extérieure, qui prendraient
    aussi une place libérée), classés par score. Pour faire entrer un sans-solution classé q-ième parmi
    les refusés encore non servis, il faut ouvrir q places (les mieux classés passent avant lui).
    Glouton : à chaque étape, l'ouverture (arrondie à des blocs de 10) qui fait entrer le plus de
    sans-solution par euro. Les candidats servis sont retirés de toutes les files."""
    F, S = d["F"], d["S"]
    pos, cut = base["pos"], base["cutoffs"][0]
    N, Lm = F.shape
    dem = d["lacc"] == R.L
    lim = np.where(dem, np.minimum(d["K"], Lm), np.minimum(d["lacc"], np.minimum(d["K"], Lm)))
    rang = np.where(pos >= 0, pos, 10 ** 6)
    P = np.arange(Lm)[None, :]
    m = (P < np.minimum(rang, lim)[:, None]) & np.isfinite(cut[F]) & (S < cut[F]) & info["ouvrable"][F]
    ii, pp = np.nonzero(m)
    jj, sc = F[ii, pp], S[ii, pp]
    o = np.lexsort((-sc, jj))
    ii, jj = ii[o], jj[o]
    ss = dem & (pos < 0)
    is_ss = ss[ii]
    M = D["M"]
    starts = np.searchsorted(jj, np.arange(M))
    ends = np.searchsorted(jj, np.arange(M), side="right")
    servi = np.zeros(N, bool)
    ptr = starts.copy()
    ajout = np.zeros(M, np.int64)
    maxadd = np.where(info["ouvrable"], np.maximum(BLOC, (MAX_HAUSSE * cap // BLOC) * BLOC), 0)

    def etape(j):
        k, p, need = 0, ptr[j], None
        while p < ends[j]:
            if not servi[ii[p]]:
                k += 1
                if is_ss[p]:
                    need = k
                    break
            p += 1
        if need is None:
            return None
        places = -(-need // BLOC) * BLOC
        if ajout[j] + places > maxadd[j]:
            return None
        n_ss, k, p = 0, 0, ptr[j]
        while p < ends[j] and k < places:
            if not servi[ii[p]]:
                k += 1
                n_ss += is_ss[p]
            p += 1
        return n_ss / (places * cout[j]), places

    heap = []
    for j in np.unique(jj[is_ss]):
        e = etape(j)
        if e:
            heapq.heappush(heap, (-e[0], int(j)))
    depense, sortis = 0.0, 0
    while heap:
        _, j = heapq.heappop(heap)
        e = etape(j)
        if not e:
            continue
        v, places = e
        if heap and v < -heap[0][0] - 1e-15:
            heapq.heappush(heap, (-v, j))
            continue
        c = places * cout[j]
        if depense + c > budget:
            continue
        depense += c
        ajout[j] += places
        k, p = 0, ptr[j]
        while p < ends[j] and k < places:
            if not servi[ii[p]]:
                servi[ii[p]] = True
                sortis += is_ss[p]
                k += 1
            p += 1
        ptr[j] = p
        e2 = etape(j)
        if e2:
            heapq.heappush(heap, (-e2[0], j))
    joignables = int(len(np.unique(ii[is_ss])))
    return ajout, depense, dict(ss_vises=sortis, ss_total=int(ss.sum()), ss_joignables=joignables)


def allocation_tension(D, info, cap, budget, cout):
    ratio = np.where(info["ouvrable"], info["voeux"] / np.maximum(cap, 1), -1)
    maxadd = np.where(info["ouvrable"], np.maximum(BLOC, (MAX_HAUSSE * cap // BLOC) * BLOC), 0)
    ajout = np.zeros(D["M"], np.int64)
    depense = 0.0
    for j in np.argsort(-ratio):
        if ratio[j] <= 0:
            break
        while ajout[j] + BLOC <= maxadd[j] and depense + BLOC * cout[j] <= budget:
            ajout[j] += BLOC
            depense += BLOC * cout[j]
        if depense + BLOC * cout.min() > budget:
            break
    return ajout, depense


def allocation_uniforme(D, info, cap, budget, cout):
    base = np.where(info["ouvrable"], cap, 0).astype(float)
    x = budget / float(np.sum(base * cout))
    ajout = np.floor(base * x + np.random.default_rng(0).random(D["M"])).astype(np.int64)   # arrondi aléatoire
    return ajout, float(np.sum(ajout * cout))


def resume_allocation(D, info, ajout, cout):
    df = pd.DataFrame(dict(type=[D["tnames"][t] for t in D["ftype"]], region=info["region"],
                           places=ajout, cout=ajout * cout))
    return df[df.places > 0]


def main(path):
    t0 = time.time()
    D = R.charger(path)
    info = infos_formations(path)
    assert len(info["region"]) == D["M"]
    rng = np.random.default_rng(R.SEED)
    d = R.generer(rng, D)
    d["pc_nationale"] = R.pc_nationale
    print(f"Génération : {time.time() - t0:.0f} s ; calibrage…", flush=True)
    R.calibrer(d, D)
    ps.SIGMA = float(np.sqrt(R.NOISE_SD ** 2 + 0.2 ** 2))
    informe = R.tirer_informes(d, rng)
    cap0 = D["cap"].copy()
    cout = cout_place(D)

    base, _ = simuler_A2(d, informe, cap0.copy())
    ind0 = indicateurs(d, base, informe)
    print(f"Référence A2 : utilité {ind0['utilite']:.3f}, sans sol. {ind0['sans_sol']:.2%} ({time.time() - t0:.0f} s)",
          flush=True)

    # ---------- diagnostic ----------
    bl = demande_bloquee(d, base)
    ii, jj, sc, gain, pp = bl
    # attribution de chaque candidat insatisfait à son meilleur vœu bloqué
    first = pd.DataFrame(dict(i=ii, j=jj, p=pp, g=gain)).sort_values(["i", "p"]).drop_duplicates("i")
    diag = pd.DataFrame(dict(type=[D["tnames"][t] for t in D["ftype"]], region=info["region"],
                             cap=cap0, voeux=info["voeux"]))
    diag["bloques_1er"] = np.bincount(first.j, minlength=D["M"])
    diag["gain_bloque"] = np.bincount(jj, weights=gain, minlength=D["M"])
    diag["pleine"] = np.isfinite(base["cutoffs"][0])
    agg_t = diag.groupby("type").agg(cap=("cap", "sum"), voeux=("voeux", "sum"), bloques=("bloques_1er", "sum"),
                                     gain=("gain_bloque", "sum"), pleines=("pleine", "mean"))
    agg_t["voeux_par_place"] = agg_t.voeux / agg_t.cap
    agg_t["bloques_pour_100_places"] = 100 * agg_t.bloques / agg_t.cap
    agg_r = diag.groupby("region").agg(cap=("cap", "sum"), bloques=("bloques_1er", "sum"), gain=("gain_bloque", "sum"))
    agg_r["bloques_pour_100_places"] = 100 * agg_r.bloques / agg_r.cap
    agg_rt = diag.groupby(["region", "type"]).agg(cap=("cap", "sum"), bloques=("bloques_1er", "sum")).reset_index()
    agg_rt["pour100"] = 100 * agg_rt.bloques / agg_rt.cap
    print("\nDiagnostic par filière :\n", agg_t.sort_values("bloques_pour_100_places", ascending=False).round(2).to_string())
    print("\nDiagnostic par région :\n", agg_r.sort_values("bloques_pour_100_places", ascending=False).round(1).to_string())
    print(f"Candidats demandeurs insatisfaits (bloqués sur un vœu préféré) : {len(first):,}".replace(",", " "))

    # ---------- politiques ----------
    resultats = [dict(politique="Référence (A2)", budget=0, places=0, depense=0, **ind0)]
    allocs = {}
    for B in BUDGETS_M:
        budget = B * 1e6
        for nom, fn in (("Uniforme", lambda: allocation_uniforme(D, info, cap0, budget, cout)),
                        ("Tension (vœux/place)", lambda: allocation_tension(D, info, cap0, budget, cout)),
                        ("Ciblée (demande bloquée)", lambda: allocation_ciblee(D, info, cap0, budget, bl, cout)),
                        ("Priorité sans-solution", lambda: allocation_sans_solution(d, D, info, cap0, budget, base, cout))):
            r_ = fn()
            ajout, dep = r_[0], r_[1]
            if len(r_) > 2:
                print(f"    planification sans-solution : {r_[2]}", flush=True)
            res, _ = simuler_A2(d, informe, cap0 + ajout)
            ind = indicateurs(d, res, informe, base["pos"])
            resultats.append(dict(politique=nom, budget=B, places=int(ajout.sum()), depense=dep / 1e6, **ind))
            allocs[(nom, B)] = resume_allocation(D, info, ajout, cout)
            print(f"  {nom:<26} {B:>4} M€ : +{ajout.sum():>6} places, utilité {ind['utilite']:.4f}, "
                  f"sans sol. {ind['sans_sol']:.2%} (sortis {ind['ss_sortis']}, entrants {ind['ss_entrants']}), améliorés {ind['ameliores']:,} ({time.time() - t0:.0f} s)"
                  .replace(",", " "), flush=True)
    # paquet complet : réforme B (5 vœux) + ouverture ciblée 250 M€ (allocation calculée sur la référence)
    ajout_c, dep_c = allocation_ciblee(D, info, cap0, 250e6, bl, cout)
    resB, _ = simuler_A2(d, informe, cap0.copy(), tours=[5, pr.K_PC])
    indB = indicateurs(d, resB, informe, base["pos"])
    resultats.append(dict(politique="Réforme B seule (5 vœux)", budget=0, places=0, depense=0, **indB))
    resBC, _ = simuler_A2(d, informe, cap0 + ajout_c, tours=[5, pr.K_PC])
    indBC = indicateurs(d, resBC, informe, base["pos"])
    resultats.append(dict(politique="Réforme B + ciblée 250 M€", budget=250, places=int(ajout_c.sum()),
                          depense=dep_c / 1e6, **indBC))

    out = pd.DataFrame(resultats)
    pd.set_option("display.width", 250)
    cols = ["politique", "budget", "places", "depense", "utilite", "1er", "top5", "top10", "sans_sol", "sans_sol_n",
            "prime_info", "ameliores", "degrades", "ss_sortis", "ss_entrants", "ss_Techno", "ss_Pro", "u_Techno", "u_Pro"]
    print("\n", out[cols].round(4).to_string(index=False))
    out.to_csv("ouvertures_resultats.csv", index=False)
    agg_t.to_csv("diag_filieres.csv")
    agg_r.to_csv("diag_regions.csv")
    agg_rt.to_csv("diag_region_filiere.csv", index=False)
    for (nom, B), a in allocs.items():
        a.groupby(["region", "type"]).agg(places=("places", "sum"), cout=("cout", "sum")).reset_index().to_csv(
            f"alloc_{nom.split()[0].lower()}_{B}.csv", index=False)
    print(f"\n(temps total {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
