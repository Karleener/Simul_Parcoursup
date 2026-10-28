"""
Coût de l'information : candidats « informés » (stratégiques) vs « naïfs » (sincères),
dans les trois schémas A (actuel), B (5 + PC) et C (2 → 5 → 10).

- Naïf : soumet ses k formations préférées (parmi celles ouvertes et acceptables).
- Informé : connaît la partie prévisible de son dossier (S_det) et les seuils d'admission
  de « l'année précédente » (ici : un run où tout le monde est sincère). Il estime
  p_ij = Φ((S_det_ij − seuil_j) / σ) et choisit les k vœux qui maximisent son utilité
  espérée (sélection gloutonne), sachant ce que vaut un échec au tour (valeur des tours
  suivants, estimée sur le run de référence par segment).
- Au dernier tour (phase complémentaire, places visibles en temps réel) tout le monde est sincère.

Utilité : v(rang) = exp(−rang / TAU), rang 0 = 1er choix.
Les parts d'informés par segment sont des hypothèses (cf. INFORMES).
Dépend de parcoursup_realiste.py (même dossier).
"""
import math
import time
import numpy as np
from numba import njit, prange
from scipy.special import erf
import parcoursup_realiste as pr

INFORMES = {"Générale": 0.60, "Techno": 0.35, "Pro": 0.25, "Réorientation": 0.50}
TAU = 6.0
SIGMA = math.sqrt(pr.NOISE_SD ** 2 + 0.2 ** 2)   # aléa du jury + incertitude d'une année sur l'autre
P_SI_PAS_PLEINE = 0.97                           # formation non pleine l'an dernier


@njit(cache=True)
def _ev(sel, n, P, V, i, vfail):
    ev, surv = 0.0, 1.0
    for m in range(n):
        l = sel[m]
        ev += surv * P[i, l] * V[l]
        surv *= 1.0 - P[i, l]
    return ev + surv * vfail


@njit(parallel=True, cache=True)
def choisir(idx, ouvert, P, V, kmax, vfail, out_allow):
    """Sélection gloutonne des vœux maximisant l'utilité espérée, pour les candidats idx."""
    Lm = ouvert.shape[1]
    for q in prange(idx.shape[0]):
        i = idx[q]
        k = kmax[i]
        sel = np.empty(Lm, np.int64)
        tmp = np.empty(Lm, np.int64)
        n = 0
        cur = vfail[i]
        for _ in range(k):
            best, best_l = cur, -1
            for c in range(Lm):
                if not ouvert[i, c]:
                    continue
                dup = False
                for m in range(n):
                    if sel[m] == c:
                        dup = True
                        break
                if dup:
                    continue
                # insertion triée de c dans sel -> tmp
                m2 = 0
                ins = False
                for m in range(n):
                    if not ins and c < sel[m]:
                        tmp[m2] = c
                        m2 += 1
                        ins = True
                    tmp[m2] = sel[m]
                    m2 += 1
                if not ins:
                    tmp[m2] = c
                    m2 += 1
                e = _ev(tmp, m2, P, V, i, vfail[i])
                if e > best + 1e-12:
                    best, best_l = e, c
            if best_l < 0:
                break
            # ajoute best_l
            m2 = 0
            ins = False
            for m in range(n):
                if not ins and best_l < sel[m]:
                    tmp[m2] = best_l
                    m2 += 1
                    ins = True
                tmp[m2] = sel[m]
                m2 += 1
            if not ins:
                tmp[m2] = best_l
                m2 += 1
            for m in range(m2):
                sel[m] = tmp[m]
            n = m2
            cur = best
        for c in range(Lm):
            out_allow[i, c] = False
        for m in range(n):
            out_allow[i, sel[m]] = True


def valeur(pos, lacc):
    return np.where(pos >= 0, np.exp(-np.maximum(pos, 0) / TAU), 0.0)


def fabrique_choix(d, informe, ref, tours):
    """Construit la fonction de choix des vœux pour les candidats informés."""
    F, S_det, seg = d["F"], d["S_det"], d["seg"]
    V = np.exp(-np.arange(F.shape[1]) / TAU)
    pos_ref = ref["pos"]
    # valeur d'un échec au tour t = utilité moyenne finale (run de réf.) des candidats du même
    # segment encore actifs au tour t+1
    actifs_ref = ref["actifs"]
    vfail_tour = []
    for t in range(len(tours)):
        vf = np.zeros(d["N"])
        if t + 1 < len(tours):
            for k in range(len(d["snames"])):
                m = (seg == k) & actifs_ref[t + 1]
                vf[seg == k] = valeur(pos_ref[m], None).mean() if m.any() else 0.0
        vfail_tour.append(vf)

    def choix(t, ouvert, kmax, active, allow):
        if t == len(tours) - 1:
            return allow                                  # dernier tour : sincère
        cut = ref["cutoffs"][t]
        c = cut[F]
        z = (S_det - np.where(np.isfinite(c), c, 0)) / (SIGMA * math.sqrt(2))
        P = np.where(np.isfinite(c), 0.5 * (1 + erf(z)), P_SI_PAS_PLEINE).astype(np.float64)
        idx = np.where(informe & active)[0]
        new = allow.copy()
        choisir(idx, ouvert & active[:, None], P, V, kmax.astype(np.int64),
                vfail_tour[t], new)
        return new
    return choix


def run():
    t0 = time.time()
    rng = np.random.default_rng(pr.SEED)
    d = pr.generer(rng)
    seg = d["seg"]
    informe = np.zeros(d["N"], bool)
    for k, s in enumerate(d["snames"]):
        m = seg == k
        informe[m] = rng.random(m.sum()) < INFORMES[s]
    print(f"{d['N']:,} candidats ; part d'informés : {informe.mean():.0%} (génération {time.time()-t0:.0f} s)"
          .replace(",", " "))

    scen = {"A. Actuel (≈14,6 + PC 10)": [None, pr.K_PC],
            "B. 5 vœux + PC 10": [5, pr.K_PC],
            "C. 3 tours : 2 → 5 → 10": [2, 5, pr.K_PC]}
    synthese = []
    for nom, tours in scen.items():
        ref = run_reference(d, tours)
        res = pr.simuler(d, tours, fabrique_choix(d, informe, ref, tours))
        r = np.where(res["pos"] >= 0, res["pos"] + 1, 10 ** 6)
        r0 = np.where(ref["pos"] >= 0, ref["pos"] + 1, 10 ** 6)
        print(f"\n=== {nom} ===  (dossiers : {res['dossiers']:,})".replace(",", " "))
        print(f"  {'segment':<14}{'groupe':<9}{'1er':>7}{'top5':>7}{'top10':>7}{'sans sol.':>11}{'utilité':>9}")
        ecarts = []
        for k, s in enumerate(d["snames"]):
            for g, gm in (("informés", informe), ("naïfs", ~informe)):
                m = (seg == k) & gm
                rr = r[m]
                u = valeur(res["pos"][m], None).mean()
                print(f"  {s:<14}{g:<9}{np.mean(rr == 1):>7.1%}{np.mean(rr <= 5):>7.1%}"
                      f"{np.mean(rr <= 10):>7.1%}{np.mean(rr == 10**6):>11.2%}{u:>9.3f}")
            mi, mn = (seg == k) & informe, (seg == k) & ~informe
            ecarts.append((mi.sum() + mn.sum(),
                           np.mean(r[mi] <= 5) - np.mean(r[mn] <= 5),
                           np.mean(r[mi] == 1) - np.mean(r[mn] == 1),
                           valeur(res["pos"][mi], None).mean() - valeur(res["pos"][mn], None).mean()))
        w = np.array([e[0] for e in ecarts], float)
        e5, e1, eu = (np.average([e[j] for e in ecarts], weights=w) for j in (1, 2, 3))
        tot = (np.mean(r == 1), np.mean(r <= 5), np.mean(r == 10**6))
        tot0 = (np.mean(r0 == 1), np.mean(r0 <= 5), np.mean(r0 == 10**6))
        print(f"  Écart informés − naïfs (à segment égal) : top5 {e5:+.1%}, 1er choix {e1:+.1%}, utilité {eu:+.3f}")
        print(f"  Ensemble, tous sincères → avec stratèges : 1er {tot0[0]:.1%}→{tot[0]:.1%}, "
              f"top5 {tot0[1]:.1%}→{tot[1]:.1%}, sans sol. {tot0[2]:.2%}→{tot[2]:.2%}")
        synthese.append((nom, e5, e1, eu, tot))
    print("\n=== SYNTHÈSE : prime à l'information (informés − naïfs, à segment égal) ===")
    for nom, e5, e1, eu, tot in synthese:
        print(f"  {nom:<28} top5 {e5:+6.1%}   1er choix {e1:+6.1%}   utilité {eu:+.3f}")
    print(f"\n(temps total {time.time()-t0:.0f} s)")


def run_reference(d, tours):
    """Run « tous sincères » (= l'année de référence), en notant qui participe à chaque tour."""
    actifs = []
    def espion(t, ouvert, kmax, active, allow):
        actifs.append(active.copy())
        return allow
    ref = pr.simuler(d, tours, espion)
    ref["actifs"] = actifs
    return ref


if __name__ == "__main__":
    run()
