"""
Simulation Parcoursup sur les DONNÉES RÉELLES (open data fr-esr-parcoursup, session 2025).

Formations (réel, par ligne du fichier, 14 252 formations) :
  - capacité            : « Capacité de l'établissement par formation »
  - académie, filière   : « Académie de l'établissement », « Filière de formation très agrégée »
  - sélectivité w_j     : dérivée du « Taux d'accès » : w = 0,1 + 0,9 × (1 − taux/100)
  - demande par segment : nb de candidats néo-bac généraux / techno / pro / autres en phase principale
  - ancrage local ρ_j   : « % d'admis néo-bacheliers issus de la même académie »
Candidats (bilan Parcoursup 2025 / note SIES 2025-08) :
  - 373 849 généraux, 139 835 technos, 116 924 pros (lycéens ayant confirmé ≥1 vœu)
  - ~300 000 « autres » (réorientation, reprise d'études, scolarité étrangère) — hypothèse
  - nb moyen de vœux par segment = vœux du fichier / effectif
  - les listes de vœux sont tirées pour reproduire, formation par formation, la demande observée
    par segment et la part de candidats locaux.
Option extérieure : une part q_s des candidats n'accepterait que ses tout premiers vœux
(apprentissage, privé hors Parcoursup, emploi…) ; q_s est calibrée pour retrouver les admis
réels par segment (331 739 / 94 552 / 58 959 / 175 502).
Validation : taux de lycéens avec ≥1 proposition en phase principale (réel 96,5 / 89,4 / 79,6 %).

Usage : python parcoursup_reel.py chemin/vers/fr-esr-parcoursup.csv
Dépend de parcoursup_realiste.py et parcoursup_strategies.py (même dossier).
"""
import sys
import time
import numpy as np
import pandas as pd
import parcoursup_realiste as pr
import parcoursup_strategies as ps

SEED = 1
L = 40
SNAMES = ["Générale", "Techno", "Pro", "Autres"]
# part de candidats « informés » (qui composent leur liste selon leurs chances réelles) par type de bac
PARTS_INFORMES = {"Générale": 0.60, "Techno": 0.35, "Pro": 0.25, "Autres": 0.50}
N_SEG = {"Générale": 373_849, "Techno": 139_835, "Pro": 116_924, "Autres": 300_000}
TALENT = {"Générale": 0.30, "Techno": -0.30, "Pro": -0.90, "Autres": 0.0}
ADMIS_REELS = {"Générale": 331_739, "Techno": 94_552, "Pro": 58_959, "Autres": 175_502}
PROP_PP_REELS = {"Générale": 0.965, "Techno": 0.894, "Pro": 0.796}
BONUS = {("BTS", "Pro"): 0.3, ("BTS", "Techno"): 0.15, ("BUT", "Techno"): 0.3}
BONUS_NON_SELECTIVE_ACADEMIE = 1.0
NOISE_SD = 0.2  # part propre à chaque formation (spécialités, profil, quotas…)
N_DRAWS = 200
GAMMA = 2.0                    # poids du rendement réel (admis/propositions) dans l'ordre de préférence
BETA = 1.5                     # candidature assortative : les bons dossiers visent les formations sélectives
SECU_SEG = {"Générale": 1.0, "Techno": 0.1, "Pro": 0.0, "Autres": 0.0}  # dict segment -> probabilité d'avoir un « vœu de sécurité » (licence non sélective locale,
                  # taux d'accès réel >= SECU_TAUX) ; calée sur les taux de proposition réels en phase principale
SECU_TAUX = 90.0
PC_PARTICIPATION = {"Générale": 0.5, "Techno": 0.4, "Pro": 0.25, "Autres": 0.6}  # dict segment -> probabilité de participer à la PC si sans place (calage) ; None = 1
PC_HESITANTS = True  # les candidats ayant une alternative (apprentissage, réorientation…) participent aussi à la PC
BETA_SEG = {"Générale": 4.0, "Techno": 4.0, "Pro": 2.0, "Autres": 4.0}  # dict segment -> BETA (calage sur les taux de proposition réels) ; None = BETA
K_MAX = 40


def charger(path):
    df = pd.read_csv(path, sep=";", low_memory=False)
    c = df.columns
    col = lambda debut: next(x for x in c if x.startswith(debut))
    cap = pd.to_numeric(df[col("Capacité de l’établissement")], errors="coerce").fillna(0)
    df = df[cap > 0].reset_index(drop=True)
    cap = pd.to_numeric(df[col("Capacité de l’établissement")]).astype(np.int64).values
    demande = np.stack([
        df[col("Effectif des candidats néo bacheliers généraux en phase principale")],
        df[col("Effectif des candidats néo bacheliers technologiques en phase principale")],
        df[col("Effectif des candidats néo bacheliers professionnels en phase principale")],
        df[col("Effectif des autres candidats en phase principale")]], axis=1).astype(float)
    demande = np.nan_to_num(demande)
    ftype_lbl = df[col("Filière de formation très agrégée")].astype(str).values
    tnames = sorted(set(ftype_lbl))
    ftype = np.array([tnames.index(x) for x in ftype_lbl])
    anames = sorted(df[col("Académie de l’établissement")].astype(str).unique())
    facad = np.array([anames.index(x) for x in df[col("Académie de l’établissement")].astype(str)])
    taux = pd.to_numeric(df[col("Taux d’accès")], errors="coerce")
    taux = taux.fillna(taux.groupby(ftype_lbl).transform("median")).fillna(80).values
    w = 0.1 + 0.9 * (1 - np.clip(taux, 0, 100) / 100)
    rho = pd.to_numeric(df[col("% d’admis néo bacheliers issus de la même académie")], errors="coerce")
    rho = (rho.fillna(rho.groupby(ftype_lbl).transform("median")).fillna(70).values / 100).clip(0.02, 0.98)
    non_sel = (df[col("Sélectivité")].astype(str) == "formation non sélective").values
    admis_type = df.groupby(ftype_lbl)[[col("Effectif des admis néo bacheliers généraux"),
                                        col("Effectif des admis néo bacheliers technologiques"),
                                        col("Effectif des admis néo bacheliers professionnels"),
                                        col("Effectif des autres candidats admis")]].sum()
    pc_dem = np.nan_to_num(np.stack([
        pd.to_numeric(df[col("Effectif des candidats néo bacheliers généraux en phase complémentaire")], errors="coerce"),
        pd.to_numeric(df[col("Effectif des candidats néo bacheliers technologique en phase complémentaire")], errors="coerce"),
        pd.to_numeric(df[col("Effectif des candidats néo bacheliers professionnels en phase complémentaire")], errors="coerce"),
        pd.to_numeric(df[col("Effectifs des autres candidats en phase complémentaire")], errors="coerce")],
        axis=1).astype(float))
    pc_cand = pd.to_numeric(df[col("Effectif total des candidats en phase complémentaire")], errors="coerce").fillna(0)
    pc_cl = pd.to_numeric(df[col("Effectif des candidats classés par l’établissement en phase complémentaire")],
                          errors="coerce").fillna(0)
    r = (pc_cl / pc_cand.where(pc_cand > 0)).clip(0, 1)
    pc_classe = r.fillna(r.groupby(ftype_lbl).transform("median")).fillna(float(pc_cl.sum() / pc_cand.sum())).values
    admis = pd.to_numeric(df[col("Effectif total des candidats ayant accepté")], errors="coerce").fillna(0).values
    props = pd.to_numeric(df[col("Effectif total des candidats ayant reçu une proposition")], errors="coerce").fillna(0).values
    rendement = np.log((admis + 1) / (props + 2))           # attractivité révélée : part des propositions acceptées
    return dict(cap=cap, demande=demande, pc_dem=pc_dem, pc_classe=pc_classe, rendement=rendement, taux=taux, ftype=ftype, tnames=tnames, facad=facad, anames=anames,
                w=w, rho=rho, non_sel=non_sel, M=len(cap), admis_type=admis_type)


def tirer_informes(d, rng):
    """Tire les candidats « informés » selon PARTS_INFORMES (même ordre de tirage dans tous les scripts)."""
    informe = np.zeros(d["N"], bool)
    for k, s in enumerate(SNAMES):
        m = d["seg"] == k
        informe[m] = rng.random(m.sum()) < PARTS_INFORMES[s]
    return informe


def generer(rng, D):
    M, A = D["M"], len(D["anames"])
    seg = np.concatenate([np.full(N_SEG[s], k, np.int8) for k, s in enumerate(SNAMES)])
    N = len(seg)
    talent = np.array([TALENT[s] for s in SNAMES])[seg] + rng.normal(0, 1, N)
    kmean = D["demande"].sum(0) / np.array([N_SEG[s] for s in SNAMES])
    K = np.clip(rng.poisson(rng.gamma(4.0, kmean[seg] / 4.0)), 1, K_MAX)
    # répartition des candidats par académie ∝ demande locale du segment
    acad = np.empty(N, np.int64)
    F = np.empty((N, L), np.int32)
    for k, s in enumerate(SNAMES):
        n_sj = D["demande"][:, k]
        loc = np.bincount(D["facad"], weights=n_sj * D["rho"], minlength=A)
        part = loc / loc.sum()
        idx_s = np.where(seg == k)[0]
        acad[idx_s] = rng.choice(A, size=len(idx_s), p=part)
        N_sa = np.bincount(acad[idx_s], minlength=A).astype(float)
        for a in range(A):
            ii_all = idx_s[acad[idx_s] == a]
            if len(ii_all) == 0:
                continue
            same = D["facad"] == a
            # poids conservant en espérance la demande observée et la part locale ρ_j
            wgt = np.where(same, n_sj * D["rho"] / max(N_sa[a], 1.0),
                           n_sj * (1 - D["rho"]) / np.maximum(len(idx_s) - N_sa[D["facad"]], 1)) + 1e-12
            p = wgt / wgt.sum()
            logw = np.log(wgt)
            for c in range(0, len(ii_all), 20000):
                ii = ii_all[c:c + 20000]
                dr = rng.choice(M, size=(len(ii), N_DRAWS), p=p)
                dr.sort(axis=1)
                dup = np.zeros_like(dr, bool)
                dup[:, 1:] = dr[:, 1:] == dr[:, :-1]
                beta = BETA if BETA_SEG is None else BETA_SEG[s]
                u = (logw[dr] + beta * talent[ii, None] * (D["w"][dr] - D["w"].mean())
                     + rng.gumbel(size=dr.shape))
                u[dup] = -np.inf
                o = np.argsort(-u, axis=1)[:, :L]
                sel = np.take_along_axis(dr, o, axis=1)            # formations demandées (inclusion ∝ demande)
                # les K premiers (vœux réellement formulés) sont réordonnés selon l'attractivité révélée
                # par le rendement réel (admis/propositions) ; les suivants servent en complémentaire
                u2 = GAMMA * D["rendement"][sel] + rng.gumbel(size=sel.shape)
                formule = np.arange(L)[None, :] < K[ii, None]
                cle = np.where(formule, 0.0, 1e3) - u2
                F[ii] = np.take_along_axis(sel, np.argsort(cle, axis=1), axis=1)
    if SECU_SEG is not None:
        lic = np.array([t == "Licence" for t in D["tnames"]])[D["ftype"]]
        sur = lic & D["non_sel"] & (D["taux"] >= SECU_TAUX)
        u_secu = rng.random(N)
        for k, s in enumerate(SNAMES):
            for a in range(A):
                cand = np.where(sur & (D["facad"] == a))[0]
                ii = np.where((seg == k) & (acad == a) & (u_secu < SECU_SEG[s]))[0]
                if len(cand) == 0 or len(ii) == 0:
                    continue
                w = D["demande"][cand, k] + 1.0
                choix = cand[rng.choice(len(cand), size=len(ii), p=w / w.sum())]
                deja = (F[ii] == choix[:, None]).any(axis=1)
                ii, choix = ii[~deja], choix[~deja]
                F[ii, np.minimum(K[ii], L) - 1] = choix          # dernier vœu formulé = vœu de sécurité
    bonus = np.zeros((len(D["tnames"]), len(SNAMES)))
    for (t, s), b in BONUS.items():
        if t in D["tnames"]:
            bonus[D["tnames"].index(t), SNAMES.index(s)] = b
    S_core = (D["w"][F] * talent[:, None]
              + np.where(D["non_sel"][F] & (D["facad"][F] == acad[:, None]),
                         BONUS_NON_SELECTIVE_ACADEMIE, 0.0)).astype(np.float32)
    eps = rng.normal(0, NOISE_SD, F.shape).astype(np.float32)
    d = dict(F=F, S_core=S_core, eps=eps, talent=talent, acad=acad, D=D, cap=D["cap"], K=K, seg=seg, N=N, M=M,
                snames=SNAMES, ftype=D["ftype"], tnames=D["tnames"],
                u_out=rng.random(N), r_out=rng.random(N), lacc=np.full(N, L))
    maj_scores(d, bonus)
    return d


PC_NATIONALE = True
LOC_PC = 5.0      # préférence pour son académie en phase complémentaire


POOL_PC = 60      # taille du réservoir de formations « complémentaires » tiré une fois par candidat
PC_MODE = "reel"  # "reel" : calée sur les candidatures réelles en PC ; "stable" ; "tirage" (1re version)
LAMBDA_PP = 0.02  # part de la demande de phase principale ajoutée (formations pleines en 2025 mais ouvertes
                  # dans un autre scénario) ; PC_VOEUX_MOY est calé sur les 450 190 candidatures réelles
PC_VOEUX_MOY = 4.0  # nb moyen de vœux en complémentaire ; None = K_PC (10) pour tous


def construire_pool_pc(d):
    """Tire UNE FOIS pour chaque candidat un réservoir de POOL_PC formations (toute la France, dans
    les types de filière de ses vœux), classé par préférence, hors formations déjà dans sa liste,
    ainsi que le nombre de vœux qu'il formulera en complémentaire (moyenne PC_VOEUX_MOY, calée sur
    les ~58 000 admis réels en phase complémentaire). À chaque phase complémentaire, le candidat
    retient les premières formations du réservoir qui ont encore des places : ouvrir des places ne
    fait qu'ajouter des options (pas de re-tirage, scénarios directement comparables)."""
    D, F = d["D"], d["F"]
    rng = np.random.default_rng(777)
    N, Lm = F.shape
    A = len(D["anames"])
    k_i = np.minimum(d["K"], Lm)
    pool = np.empty((N, POOL_PC), np.int32)
    base = np.exp(GAMMA * D["rendement"])
    cdfs = {}
    for c in range(0, N, 100000):
        sl = slice(c, min(c + 100000, N))
        n = sl.stop - sl.start
        posr = (rng.random((n, POOL_PC)) * k_i[sl, None]).astype(np.int64)
        typ = D["ftype"][np.take_along_axis(F[sl], posr, 1)]
        key = typ.astype(np.int64) * A + d["acad"][sl, None]
        u01 = rng.random((n, POOL_PC))
        out = np.empty((n, POOL_PC), np.int32)
        for kk in np.unique(key):
            if kk not in cdfs:
                t, a = divmod(int(kk), A)
                ft = np.where(D["ftype"] == t)[0]
                wgt = base[ft] * np.where(D["facad"][ft] == a, LOC_PC, 1.0)
                cdf = np.cumsum(wgt)
                cdfs[kk] = (ft, cdf / cdf[-1])
            ft, cdf = cdfs[kk]
            m = key == kk
            out[m] = ft[np.minimum(np.searchsorted(cdf, u01[m]), len(ft) - 1)]
        pool[sl] = out
    u = np.empty((N, POOL_PC), np.float32)
    for c in range(0, N, 20000):
        sl = slice(c, min(c + 20000, N))
        p = pool[sl]
        same = D["facad"][p] == d["acad"][sl, None]
        uu = (GAMMA * D["rendement"][p] + np.log(LOC_PC) * same
              + rng.gumbel(size=p.shape)).astype(np.float32)
        dup_list = (p[:, :, None] == F[sl][:, None, :]).any(axis=2)
        o = np.argsort(p, axis=1, kind="stable")
        ps_ = np.take_along_axis(p, o, 1)
        rep = np.zeros_like(ps_, bool)
        rep[:, 1:] = ps_[:, 1:] == ps_[:, :-1]
        first = np.zeros_like(rep)
        np.put_along_axis(first, o, rep, 1)
        uu[dup_list | first] = -np.inf
        u[sl] = uu
    o = np.argsort(-u, axis=1)
    d["pc_pool"] = np.take_along_axis(pool, o, 1)
    d["pc_valid"] = np.isfinite(np.take_along_axis(u, o, 1))
    del pool, u, o
    d["pc_eps"] = rng.normal(0, NOISE_SD, d["pc_pool"].shape).astype(np.float32)
    d["pc_k"] = (np.full(N, pr.K_PC) if PC_VOEUX_MOY is None
                 else np.clip(rng.poisson(PC_VOEUX_MOY, N), 1, pr.K_PC))


def pc_nationale(d, cap, active, allow, kmax):
    if PC_MODE == "tirage":
        return pc_nationale_tirage(d, cap, active, allow, kmax)
    if PC_MODE == "reel" and "pc_pool" not in d:
        construire_pool_pc_reel(d)
    return pc_nationale_stable(d, cap, active, allow, kmax)


def construire_pool_pc_reel(d):
    """Réservoir de vœux de phase complémentaire tiré UNE FOIS par candidat, d'après la demande RÉELLE
    de 2025 : chaque formation est tirée en proportion du nombre réel de candidats de même type de bac
    qui l'ont demandée en phase complémentaire (+ LAMBDA_PP × sa demande de phase principale, pour
    qu'une formation pleine en 2025 mais ayant des places dans un autre scénario reste accessible),
    avec une préférence ×LOC_PC pour son académie. Les tirages successifs avec remise donnent l'ordre
    de préférence (Plackett-Luce). Les IFSI ne recrutent pas en complémentaire (0 candidature en 2025)."""
    D, F = d["D"], d["F"]
    rng = np.random.default_rng(778)
    N, Lm = F.shape
    A = len(D["anames"])
    ifsi = np.array([t == "IFSI" for t in D["tnames"]])[D["ftype"]]
    pool = np.empty((N, POOL_PC), np.int32)
    for k in range(len(SNAMES)):
        wbase = (D["pc_dem"][:, k] + LAMBDA_PP * D["demande"][:, k]) * ~ifsi
        for a in range(A):
            idx = np.where((d["seg"] == k) & (d["acad"] == a))[0]
            if len(idx) == 0:
                continue
            w = wbase * np.where(D["facad"] == a, LOC_PC, 1.0)
            cdf = np.cumsum(w)
            cdf /= cdf[-1]
            pool[idx] = np.minimum(np.searchsorted(cdf, rng.random((len(idx), POOL_PC))), D["M"] - 1)
    valid = np.ones((N, POOL_PC), bool)
    for c in range(0, N, 20000):
        sl = slice(c, min(c + 20000, N))
        p = pool[sl]
        dup_list = (p[:, :, None] == F[sl][:, None, :]).any(axis=2)
        o = np.argsort(p, axis=1, kind="stable")      # garde la 1re occurrence (ordre de tirage)
        ps_ = np.take_along_axis(p, o, 1)
        rep = np.zeros_like(ps_, bool)
        rep[:, 1:] = ps_[:, 1:] == ps_[:, :-1]
        first = np.zeros_like(rep)
        np.put_along_axis(first, o, rep, 1)
        valid[sl] = ~(dup_list | first)
    d["pc_pool"], d["pc_valid"] = pool, valid
    d["pc_eps"] = rng.normal(0, NOISE_SD, pool.shape).astype(np.float32)
    d["pc_k"] = (np.full(N, pr.K_PC) if PC_VOEUX_MOY is None
                 else np.clip(rng.poisson(PC_VOEUX_MOY, N), 1, pr.K_PC))


def pc_locale(d, cap, active, allow, kmax):
    """Phase complémentaire « locale » (scénario A1) : mêmes règles de participation, de nombre de vœux
    et de classement que la PC nationale, mais limitée aux formations de la liste initiale du candidat."""
    if PC_MODE == "reel" and "pc_pool" not in d:
        construire_pool_pc_reel(d)
    return pc_nationale_stable(d, cap, active, allow, kmax, nationale=False)


def pc_nationale_stable(d, cap, active, allow, kmax, nationale=True):
    """Phase complémentaire nationale (réservoir fixe par candidat, cf. construire_pool_pc)."""
    if "pc_pool" not in d:
        construire_pool_pc(d)
    D, F = d["D"], d["F"]
    N, Lm = F.shape
    kpc = pr.K_PC
    if "pc_part" not in d:
        pi = np.ones(len(SNAMES)) if PC_PARTICIPATION is None else np.array([PC_PARTICIPATION[x] for x in SNAMES])
        d["pc_part"] = np.random.default_rng(779).random(N) < pi[d["seg"]]
    dem = active & ((d["lacc"] >= Lm) | (PC_HESITANTS & (d["lacc"] > 0))) & d["pc_part"]
    allow = allow & (dem | ~active)[:, None]          # les non-participants ne formulent aucun vœu en PC
    # un demandeur formule au plus pc_k vœux en complémentaire (vœux de sa liste encore ouverts compris)
    allow = allow & ~(dem[:, None] & (np.cumsum(allow, axis=1) > d["pc_k"][:, None]))
    n0 = allow.sum(axis=1)
    idx = np.where(dem & (n0 < kpc))[0] if nationale else np.zeros(0, np.int64)
    Fx = np.zeros((N, kpc), np.int32)
    Ax = np.zeros((N, kpc), bool)
    Sx = np.full((N, kpc), -np.inf, np.float32)
    if len(idx):
        pool = d["pc_pool"][idx]
        ok = d["pc_valid"][idx] & (cap[pool] > 0)
        rang = np.cumsum(ok, axis=1)
        reste = np.maximum(d["pc_k"][idx] - n0[idx], 0)[:, None]
        pris = ok & (rang <= reste)
        cle = np.where(pris, np.arange(POOL_PC)[None, :], POOL_PC + np.arange(POOL_PC)[None, :])
        o = np.argsort(cle, axis=1)[:, :kpc]
        ch = np.take_along_axis(pool, o, 1)
        val = np.take_along_axis(pris, o, 1)
        eps = np.take_along_axis(d["pc_eps"][idx], o, 1)
        same = D["facad"][ch] == d["acad"][idx][:, None]
        seg = d["seg"][idx][:, None]
        sc = (D["w"][ch] * d["talent"][idx][:, None] + d["bonus"][D["ftype"][ch], seg]
              + np.where(D["non_sel"][ch] & same, BONUS_NON_SELECTIVE_ACADEMIE, 0.0) + eps)
        Fx[idx] = ch
        Sx[idx] = sc.astype(np.float32)
        Ax[idx] = val
    Ft = np.concatenate([F, Fx], 1)
    At = np.concatenate([allow, Ax], 1)
    # candidatures déposées (comptées comme dans les données réelles), puis filtre « classé » :
    # chaque formation ne classe qu'une part de ses candidatures de PC (taux réel 2025 par formation)
    if "pc_u" not in d:
        d["pc_u"] = np.random.default_rng(780).integers(0, 256, size=(N, Lm + kpc), dtype=np.uint8)
    d["pc_stats"] = {"deposees": At.copy(), "seg": d["seg"]}
    classe = d["pc_u"] < (D["pc_classe"][Ft] * 256)
    return Ft, np.concatenate([d["S"], Sx], 1), At & classe


def pc_nationale_tirage(d, cap, active, allow, kmax):
    """Phase complémentaire ouverte à toute la France, dans les types de filière déjà demandés.
    Chaque demandeur garde d'abord les formations ouvertes de sa liste, puis complète jusqu'à
    K_PC vœux avec des formations ouvertes tirées au niveau national dans ses types de filière
    (type tiré selon la répartition de ses vœux initiaux ; préférence ∝ rendement réel × proximité)."""
    D, F, rng = d["D"], d["F"], np.random.default_rng(12345)
    N, Lm = F.shape
    kpc = pr.K_PC
    dem = active & (d["lacc"] >= Lm)                       # demandeurs (pas d'option extérieure)
    n0 = allow.sum(axis=1)
    idx = np.where(dem & (n0 < kpc))[0]
    Fx = np.zeros((N, kpc), np.int32)
    Ax = np.zeros((N, kpc), bool)
    Sx = np.full((N, kpc), -np.inf, np.float32)
    if len(idx):
        # type de chaque vœu complémentaire : tiré parmi les types des vœux initiaux
        k_i = np.minimum(d["K"][idx], Lm)
        posr = (rng.random((len(idx), kpc)) * k_i[:, None]).astype(np.int64)
        typ = D["ftype"][F[idx[:, None], posr]]
        ac = d["acad"][idx][:, None].repeat(kpc, 1)
        ouvert = cap > 0
        base = np.exp(GAMMA * D["rendement"]) * ouvert
        choisi = np.zeros((len(idx), kpc), np.int64)
        valide = np.zeros((len(idx), kpc), bool)
        for t in range(len(D["tnames"])):
            ft = np.where((D["ftype"] == t) & ouvert)[0]
            if len(ft) == 0:
                continue
            mt = typ == t
            if not mt.any():
                continue
            for a in np.unique(ac[mt]):
                m = mt & (ac == a)
                wgt = base[ft] * np.where(D["facad"][ft] == a, LOC_PC, 1.0)
                cdf = np.cumsum(wgt)
                cdf /= cdf[-1]
                choisi[m] = ft[np.minimum(np.searchsorted(cdf, rng.random(m.sum())), len(ft) - 1)]
                valide[m] = True
        # ordre de préférence et score de classement des nouvelles candidatures
        same = D["facad"][choisi] == d["acad"][idx][:, None]
        u = GAMMA * D["rendement"][choisi] + np.log(LOC_PC) * same + rng.gumbel(size=choisi.shape)
        u[~valide] = -np.inf
        o = np.argsort(-u, axis=1)
        choisi = np.take_along_axis(choisi, o, 1)
        valide = np.take_along_axis(valide, o, 1)
        same = np.take_along_axis(same, o, 1)
        seg = d["seg"][idx][:, None]
        sc = (D["w"][choisi] * d["talent"][idx][:, None] + d["bonus"][D["ftype"][choisi], seg]
              + np.where(D["non_sel"][choisi] & same, BONUS_NON_SELECTIVE_ACADEMIE, 0.0)
              + rng.normal(0, NOISE_SD, choisi.shape))
        reste = (kpc - n0[idx])[:, None]
        Fx[idx] = choisi
        Sx[idx] = sc.astype(np.float32)
        Ax[idx] = valide & (np.arange(kpc)[None, :] < reste)
    return (np.concatenate([F, Fx], 1), np.concatenate([d["S"], Sx], 1),
            np.concatenate([allow, Ax], 1))


def maj_scores(d, bonus):
    """Score de classement = partie prévisible (niveau × sélectivité, priorité académique,
    bonus filière×segment) + aléa propre à chaque couple candidat-formation."""
    d["bonus"] = bonus
    d["S_det"] = d["S_core"] + bonus[d["ftype"][d["F"]], d["seg"][:, None]].astype(np.float32)
    d["S"] = d["S_det"] + d["eps"]


def appliquer_option_exterieure(d, q):
    """Avec proba q_s, le candidat n'accepte que ses o premiers vœux, o uniforme dans [0, min(K,5)]."""
    qs = np.array([q[s] for s in SNAMES])[d["seg"]]
    hesitant = d["u_out"] < qs
    o = np.floor(d["r_out"] * (np.minimum(d["K"], 5) + 1)).astype(np.int64)
    d["lacc"] = np.where(hesitant, o, L)
    d["participe"] = d["lacc"] > 0


def taux_proposition_pp(d, ref):
    """≥1 proposition en phase principale : un vœu soumis (K premiers) au-dessus du seuil final du tour 1."""
    cut = ref["cutoffs"][0]
    soumis = np.arange(L)[None, :] < np.minimum(d["K"], L)[:, None]
    return ((d["S"] >= cut[d["F"]]) & soumis).any(axis=1), ((d["S"] >= cut[d["F"]]) & soumis).sum(axis=1)


def calibrer(d, D, iters=7):
    """Calibre conjointement q_s (option extérieure) sur les admis par segment et les bonus
    filière × segment sur la composition réelle des admis (système actuel)."""
    q = {"Générale": 0.05, "Techno": 0.25, "Pro": 0.50, "Autres": 0.40}
    reel = D["admis_type"].values.astype(float)            # (types, segments)
    bonus = d["bonus"].copy()
    for it in range(iters):
        appliquer_option_exterieure(d, q)
        ref = pr.simuler(d, [None, pr.K_PC])
        pos = ref["pos"]
        ok = pos >= 0
        ft = d["ftype"][ref["fid"][ok]]
        model = np.zeros_like(reel)
        np.add.at(model, (ft, d["seg"][ok]), 1)
        adm = model.sum(0)
        cible = np.array([ADMIS_REELS[s] for s in SNAMES], float)
        err = np.abs(np.log((model + 50) / (reel + 50)))
        print(f"  calibrage {it + 1}: admis modèle/réel par segment : "
              + ", ".join(f"{s} {adm[k] / cible[k]:.2f}" for k, s in enumerate(SNAMES))
              + f" | écart moyen de composition filière×segment : {np.average(err, weights=reel + 1):.2f}")
        for k, s in enumerate(SNAMES):
            q[s] = float(np.clip(1 - (1 - q[s]) / (adm[k] / cible[k]), 0.0, 0.95))
        # bonus : on corrige la part de chaque filière dans les admis du segment
        part_m = (model + 50) / (model.sum(0, keepdims=True) + 50)
        part_r = (reel + 50) / (reel.sum(0, keepdims=True) + 50)
        bonus = np.clip(bonus + 0.6 * np.log(part_r / part_m), -2.5, 2.5)
        maj_scores(d, bonus)
    appliquer_option_exterieure(d, q)
    return q


def tableau(d, res, informe, titre):
    r = np.where(res["pos"] >= 0, res["pos"] + 1, 10 ** 6)
    part = d["lacc"] == L          # « demandeurs » : candidats sans option extérieure dominante
    print(f"\n=== {titre} ===  dossiers examinés : {res['dossiers']:,}".replace(",", " "))
    for t, info in enumerate(res["tours"]):
        print(f"  tour {t + 1}: {info['participants']:>7} participants, {info['affectes']:>7} affectés, "
              f"places vacantes après {info['places_vacantes']:>7}")
    print(f"  {'segment':<12}{'groupe':<10}{'1er':>7}{'top5':>7}{'top10':>7}{'sans sol.':>11}{'utilité':>9}")
    for k, s in enumerate(SNAMES):
        for g, gm in (("tous", np.ones_like(informe)), ("informés", informe), ("naïfs", ~informe)):
            m = (d["seg"] == k) & gm & part
            rr = r[m]
            u = ps.valeur(res["pos"][m], None).mean()
            print(f"  {s if g == 'tous' else '':<12}{g:<10}{np.mean(rr == 1):>7.1%}{np.mean(rr <= 5):>7.1%}"
                  f"{np.mean(rr <= 10):>7.1%}{np.mean(rr == 10 ** 6):>11.2%}{u:>9.3f}")
    m = part
    ecarts = []
    for k in range(len(SNAMES)):
        mi, mn = (d["seg"] == k) & informe & part, (d["seg"] == k) & ~informe & part
        ecarts.append((mi.sum() + mn.sum(), ps.valeur(res["pos"][mi], None).mean()
                       - ps.valeur(res["pos"][mn], None).mean(),
                       np.mean(r[mi] == 10 ** 6) - np.mean(r[mn] == 10 ** 6)))
    wts = np.array([e[0] for e in ecarts], float)
    eu = np.average([e[1] for e in ecarts], weights=wts)
    es = np.average([e[2] for e in ecarts], weights=wts)
    glob = (np.mean(r[m] == 1), np.mean(r[m] <= 5), np.mean(r[m] <= 10), np.mean(r[m] == 10 ** 6),
            ps.valeur(res["pos"][m], None).mean())
    hors = np.mean(res["pos"][m] == L)
    print(f"  affectés en complémentaire hors liste initiale : {hors:.1%} des demandeurs")
    print(f"  ENSEMBLE : 1er {glob[0]:.1%}, top5 {glob[1]:.1%}, top10 {glob[2]:.1%}, "
          f"sans sol. {glob[3]:.2%}, utilité {glob[4]:.3f} | prime à l'information : "
          f"utilité {eu:+.3f}, sans sol. {es:+.2%}")
    return glob, eu, es, sum(res["soumis"])


# ======================= SCÉNARIOS À COMPARER =======================
# nom : (liste des tours, phase complémentaire nationale ?)
# Chaque tour = nombre maximal de vœux ; None = nombre réel de vœux de chaque candidat (système actuel).
# Le dernier tour est toujours la phase complémentaire (K_PC = 10 vœux, sur les places restantes).
# Exemple pour ajouter une variante : "E. 3 → 8 → PC nationale": ([3, 8, pr.K_PC], True)
SCENARIOS = {
    "A1. Actuel, PC locale": ([None, pr.K_PC], False),
    "A2. Actuel, PC nationale": ([None, pr.K_PC], True),
    "B. 5 vœux + PC nationale": ([5, pr.K_PC], True),
    "C. 2 → 5 → PC nationale": ([2, 5, pr.K_PC], True),
    "D. 5 → 10 → PC nationale": ([5, 10, pr.K_PC], True),
}


def main(path):
    t0 = time.time()
    D = charger(path)
    print(f"{D['M']:,} formations réelles, {D['cap'].sum():,} places, {len(D['anames'])} académies"
          .replace(",", " "))
    rng = np.random.default_rng(SEED)
    d = generer(rng, D)
    print(f"{d['N']:,} candidats générés ({time.time() - t0:.0f} s)".replace(",", " "))

    # calibrage sur le système réel, dont la phase complémentaire est ouverte à toute la France
    d["pc_nationale"] = pc_nationale if PC_NATIONALE else None
    print(f"\nCalibrage avec phase complémentaire nationale : {'oui' if PC_NATIONALE else 'non'}")
    print("\nCalibrage de l'option extérieure sur les admis réels :")
    q = calibrer(d, D)
    print("  q_s final : " + ", ".join(f"{s} {q[s]:.2f}" for s in SNAMES))
    ref = pr.simuler(d, [None, pr.K_PC])
    has_prop, nprop = taux_proposition_pp(d, ref)
    print("\nValidation (système actuel, phase principale) :")
    for k, s in enumerate(SNAMES[:3]):
        m = d["seg"] == k
        print(f"  {s:<10} ≥1 proposition : modèle {has_prop[m].mean():.1%} / réel {PROP_PP_REELS[s]:.1%} ; "
              f"propositions moy. {nprop[m & has_prop].mean():.1f}")
    adm = {s: int(np.sum((ref["pos"] >= 0) & (d["seg"] == k))) for k, s in enumerate(SNAMES)}
    print("  admis modèle / réel : " + ", ".join(f"{s} {adm[s]:,}/{ADMIS_REELS[s]:,}" for s in SNAMES)
          .replace(",", " "))
    # composition des admis par filière (modèle vs réel)
    pos = ref["pos"]
    ok = pos >= 0
    ft = d["ftype"][ref["fid"][ok]]
    sg = d["seg"][ok]
    comp = pd.crosstab(pd.Series([D["tnames"][x] for x in ft], name="filière"),
                       pd.Series([SNAMES[x] for x in sg], name="segment"))[SNAMES]
    reel = D["admis_type"].copy()
    reel.columns = SNAMES
    print("\n  Admis par filière (modèle | réel), en milliers :")
    for t in D["tnames"]:
        mm = comp.loc[t] if t in comp.index else pd.Series(0, index=SNAMES)
        print(f"   {t:<20}" + " ".join(f"{mm[s] / 1000:6.1f}|{reel.loc[t, s] / 1000:<6.1f}" for s in SNAMES))

    # les informés estiment leurs chances avec l'aléa réel du modèle (+ incertitude interannuelle)
    ps.SIGMA = float(np.sqrt(NOISE_SD ** 2 + 0.2 ** 2))
    informe = tirer_informes(d, rng)

    # (tours, phase complémentaire nationale ?)
    synth = []
    for nom, (tours, nat) in SCENARIOS.items():
        d["pc_nationale"] = pc_nationale if nat else pc_locale
        ref_s = ps.run_reference(d, tours)
        choix = ps.fabrique_choix(d, informe, ref_s, tours)
        listes = {}
        def choix_trace(t, ouvert, kmax, active, allow, _c=choix):
            a = _c(t, ouvert, kmax, active, allow)
            listes[t] = a
            return a
        res = pr.simuler(d, tours, choix_trace)
        if nom.startswith("A2"):
            # validation avec comportements mixtes (informés + naïfs) = plus proche du réel
            vœux1 = listes[0] | ((np.arange(L)[None, :] < np.minimum(d["K"], L)[:, None]) & (d["lacc"] < L)[:, None])
            prop = (d["S"] >= res["cutoffs"][0][d["F"]]) & vœux1
            hp, npr = prop.any(axis=1), prop.sum(axis=1)
            print("\nValidation avec candidats informés/naïfs (phase principale) :")
            for k, sn in enumerate(SNAMES[:3]):
                m = d["seg"] == k
                print(f"  {sn:<10} ≥1 proposition : modèle {hp[m].mean():.1%} / réel {PROP_PP_REELS[sn]:.1%} ; "
                      f"propositions moy. {npr[m & hp].mean():.1f}")
        synth.append((nom,) + tableau(d, res, informe, nom + " — avec candidats informés/naïfs"))
    print("\n=== SYNTHÈSE (candidats « demandeurs », sans option extérieure) ===")
    print(f"  {'scénario':<28}{'1er':>7}{'top5':>7}{'top10':>7}{'sans sol.':>11}{'utilité':>9}"
          f"{'prime info':>12}{'dossiers':>12}")
    for nom, g, eu, es, dos in synth:
        print(f"  {nom:<28}{g[0]:>7.1%}{g[1]:>7.1%}{g[2]:>7.1%}{g[3]:>11.2%}{g[4]:>9.3f}{eu:>+12.3f}"
              f"{dos / 1e6:>10.1f} M")
    print(f"\n(temps total {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
