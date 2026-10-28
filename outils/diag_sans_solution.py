"""Pourquoi +14 600 places ne réduisent-elles que peu les sans-solution ?
Compare la référence (A2) et la politique ciblée 500 M€ : chaînes de vacances, profil des sans-solution."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys
import numpy as np
import pandas as pd
import parcoursup_realiste as pr
import parcoursup_strategies as ps
import parcoursup_reel as R
import parcoursup_ouvertures as O

path = (sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
D = R.charger(path)
info = O.infos_formations(path)
rng = np.random.default_rng(R.SEED)
d = R.generer(rng, D)
d["pc_nationale"] = R.pc_nationale
R.calibrer(d, D)
ps.SIGMA = float(np.sqrt(R.NOISE_SD ** 2 + 0.2 ** 2))
informe = R.tirer_informes(d, rng)
cap0 = D["cap"].copy()
cout = O.cout_place(D)

base, _ = O.simuler_A2(d, informe, cap0.copy())
bl = O.demande_bloquee(d, base)
ajout, _ = O.allocation_ciblee(D, info, cap0, 500e6, bl, cout)
pol, _ = O.simuler_A2(d, informe, cap0 + ajout)

dem = d["lacc"] == R.L
f0, f1 = base["fid"], pol["fid"]
a0, a1 = f0 >= 0, f1 >= 0
print(f"Places ajoutées : {ajout.sum():,}")
print(f"Admis (tous) : {a0.sum():,} -> {a1.sum():,}  (+{a1.sum() - a0.sum():,})")
print(f"Admis demandeurs : {(a0 & dem).sum():,} -> {(a1 & dem).sum():,}")
print(f"Admis 'hésitants' (option extérieure) : {(a0 & ~dem).sum():,} -> {(a1 & ~dem).sum():,}")
vac0 = int(np.maximum(cap0 - np.bincount(f0[a0], minlength=D['M']), 0).sum())
vac1 = int(np.maximum(cap0 + ajout - np.bincount(f1[a1], minlength=D['M']), 0).sum())
print(f"Places vacantes : {vac0:,} -> {vac1:,}  (+{vac1 - vac0:,})")

# où partent les nouvelles places : qui les occupe ?
opened = ajout > 0
occ_new = a1 & opened[np.maximum(f1, 0)]
moved_in = occ_new & (f1 != f0)
print(f"\nÉtudiants entrés dans une formation agrandie : {moved_in.sum():,}")
print(f"  dont venaient d'une autre affectation : {(moved_in & a0).sum():,}")
print(f"  dont étaient sans solution : {(moved_in & ~a0 & dem).sum():,}")
print(f"  dont 'hésitants' auparavant non admis : {(moved_in & ~a0 & ~dem).sum():,}")
changed = (f1 != f0)
print(f"Étudiants dont l'affectation change : {changed.sum():,}")

# profil des sans-solution de la référence
ss = dem & ~a0
q = pd.qcut(d["talent"], 10, labels=False)
print(f"\nSans solution (référence) : {ss.sum():,}")
print("  par segment :", {s: int((ss & (d['seg'] == k)).sum()) for k, s in enumerate(R.SNAMES)})
print(f"  informés : {(ss & informe).sum():,} / naïfs : {(ss & ~informe).sum():,}")
print("  par décile de niveau (0 = plus faible) :", np.bincount(q[ss], minlength=10).tolist())
print(f"  taux de sans-solution, décile le plus faible vs médian : "
      f"{ss[q == 0].sum() / max(dem[q == 0].sum(), 1):.2%} vs {ss[q == 5].sum() / max(dem[q == 5].sum(), 1):.2%}")
# leurs vœux touchent-ils des formations agrandies ? écart au seuil
F, S = d["F"], d["S"]
cut = base["cutoffs"][0]
P = np.arange(R.L)[None, :] < np.minimum(d["K"], R.L)[:, None]
ii = np.where(ss)[0]
touche = (opened[F[ii]] & P[ii]).any(axis=1)
print(f"  ont au moins un vœu dans une formation agrandie : {touche.mean():.1%}")
gap = np.where(np.isfinite(cut[F[ii]]) & P[ii], cut[F[ii]] - S[ii], np.inf).min(axis=1)
print(f"  écart minimal au seuil (σ du bruit = {R.NOISE_SD}) : médiane {np.median(gap[np.isfinite(gap)]):.2f}")
# rang des sans-solution parmi les refusés de leurs vœux agrandis
m = opened[F] & P & dem[:, None] & (S < cut[F])
rj, rs = F[m], S[m]
o = np.lexsort((rs, rj))
rj, rs = rj[o], rs[o]
st, en = np.searchsorted(rj, np.arange(D["M"])), np.searchsorted(rj, np.arange(D["M"]), side="right")
ouv = opened[F[ii]] & P[ii]
fr_mieux, ratio = [], []
for n, i in enumerate(ii):
    for p in np.where(ouv[n])[0]:
        j = F[i, p]
        nref = en[j] - st[j]
        mieux = nref - np.searchsorted(rs[st[j]:en[j]], S[i, p], side="right")
        fr_mieux.append(mieux / max(nref, 1))
        ratio.append(ajout[j] / max(nref, 1))
print(f"  dans leurs vœux agrandis : part des refusés mieux classés qu'eux, médiane {np.median(fr_mieux):.0%} ; "
      f"places ajoutées / refusés, médiane {np.median(ratio):.1%}")
still = ss & ~a1
saved = ss & a1
print(f"\nSans-solution sauvés : {saved.sum():,} ; restés sans solution : {still.sum():,}")
print("  sauvés par segment :", {s: int((saved & (d['seg'] == k)).sum()) for k, s in enumerate(R.SNAMES)})
new_ss = dem & a0 & ~a1
print(f"Nouveaux sans-solution (avaient une place avant) : {new_ss.sum():,}")
