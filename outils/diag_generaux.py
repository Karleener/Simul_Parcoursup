import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys, json, numpy as np, pandas as pd
import parcoursup_realiste as pr, parcoursup_reel as R
D = R.charger((sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv"))
d = R.generer(np.random.default_rng(R.SEED), D)
d["pc_nationale"] = R.pc_nationale
R.calibrer(d, D)
res = pr.simuler(d, [None, pr.K_PC])
cut = res["cutoffs"][0]
F, S = d["F"], d["S"]
K = np.minimum(d["K"], R.L)
P = np.arange(R.L)[None, :] < K[:, None]
above = (S >= cut[F]) & P
prop = above.any(1)
g = d["seg"] == 0
no = g & ~prop
print("généraux sans proposition :", no.sum(), "/", g.sum())
print("K moyen : sans prop", K[no].mean(), " avec", K[g & prop].mean())
print("part de K<=3 : sans prop", np.mean(K[no] <= 3), " avec", np.mean(K[g & prop] <= 3))
q = pd.qcut(d["talent"][g], 10, labels=False)
print("taux sans prop par décile de niveau :", np.round(pd.Series(~prop[g]).groupby(q).mean().values, 3))
full = np.isfinite(cut[F]) & P
print("part de vœux dans des formations pleines : sans prop", full[no].sum() / P[no].sum(), " avec", full[g & prop].sum() / P[g & prop].sum())
tn = np.array(D["tnames"])
ty = pd.Series(tn[D["ftype"][F[no][P[no]]]]).value_counts(normalize=True).round(3)
ty2 = pd.Series(tn[D["ftype"][F[g & prop][P[g & prop]]]]).value_counts(normalize=True).round(3)
print(pd.concat([ty.rename("sans prop"), ty2.rename("avec prop")], axis=1).to_string())
loc = (D["facad"][F] == d["acad"][:, None]) & P
print("part de vœux locaux : sans prop", loc[no].sum() / P[no].sum(), " avec", loc[g & prop].sum() / P[g & prop].sum())
ns = D["non_sel"][F] & P
print("a au moins une licence non sélective locale : sans prop", (ns & loc)[no].any(1).mean(), " avec", (ns & loc)[g & prop].any(1).mean())
# taux d'accès modèle vs réel par type (candidats de phase principale)
app = np.bincount(F[P], minlength=D["M"]).astype(float)
acc = np.bincount(F[above], minlength=D["M"]).astype(float)
df = pd.read_csv((sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv"), sep=";", low_memory=False); c = df.columns
cap = pd.to_numeric(df[c[17]], errors="coerce").fillna(0); df = df[cap > 0].reset_index(drop=True)
real_app = pd.to_numeric(df[c[20]], errors="coerce").fillna(0).values
real_rate = pd.to_numeric(df[c[112]], errors="coerce").values / 100
t = pd.DataFrame(dict(type=tn[D["ftype"]], app=app, acc=acc, rapp=real_app, racc=real_app * np.nan_to_num(real_rate, nan=0.8)))
gg = t.groupby("type").sum()
gg["taux_modele"] = gg.acc / gg.app; gg["taux_reel"] = gg.racc / gg.rapp
print(gg[["app", "rapp", "taux_modele", "taux_reel"]].round(2).to_string())
