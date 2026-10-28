import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys, numpy as np, pandas as pd
import parcoursup_realiste as pr, parcoursup_reel as R
path = (sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
D = R.charger(path); d = R.generer(np.random.default_rng(R.SEED), D); d["pc_nationale"] = R.pc_nationale
R.calibrer(d, D)
res = pr.simuler(d, [None, pr.K_PC]); cut = res["cutoffs"][0]
F, S = d["F"], d["S"]; Kc = np.minimum(d["K"], R.L); P = np.arange(R.L)[None, :] < Kc[:, None]
above = (S >= cut[F]) & P
df = pd.read_csv(path, sep=";", low_memory=False); c = df.columns
cap = pd.to_numeric(df[c[17]], errors="coerce").fillna(0); df = df[cap > 0].reset_index(drop=True)
t = pd.DataFrame(dict(type=np.array(D["tnames"])[D["ftype"]], ns=D["non_sel"], cap=D["cap"],
                      app_m=np.bincount(F[P], minlength=D["M"]), acc_m=np.bincount(F[above], minlength=D["M"]),
                      app_r=pd.to_numeric(df[c[20]], errors="coerce").fillna(0).values,
                      prop_r=pd.to_numeric(df[c[45]], errors="coerce").fillna(0).values,
                      adm_r=pd.to_numeric(df[c[46]], errors="coerce").fillna(0).values,
                      taux_r=pd.to_numeric(df[c[112]], errors="coerce").values / 100,
                      pleine_m=np.isfinite(cut)))
t["adm_m"] = np.bincount(res["fid"][res["fid"] >= 0], minlength=D["M"])
g = t.groupby(["type", "ns"]).agg(n=("cap", "size"), cap=("cap", "sum"), app_m=("app_m", "sum"), app_r=("app_r", "sum"),
                                   acc_m=("acc_m", "sum"), prop_r=("prop_r", "sum"), adm_m=("adm_m", "sum"),
                                   adm_r=("adm_r", "sum"), pleine_m=("pleine_m", "mean"))
g["prop/place m"] = g.acc_m / g.cap; g["prop/place r"] = g.prop_r / g.cap
g["adm/place m"] = g.adm_m / g.cap; g["adm/place r"] = g.adm_r / g.cap
pd.set_option("display.width", 250)
print(g.round(2).to_string())
# licences non sélectives : distribution du ratio admis/capacité réel
ln = t[(t.type == "Licence") & t.ns]
print("Licences non sélectives : part avec admis réels > capacité :", (ln.adm_r > ln.cap).mean().round(3),
      " admis réels / capacité (somme) :", round(ln.adm_r.sum() / ln.cap.sum(), 3))
print("part de formations où admis réels > capacité, toutes filières :", (t.adm_r > t.cap).mean().round(3))
print("admis réels / capacité par type :", (t.groupby("type").adm_r.sum() / t.groupby("type").cap.sum()).round(2).to_dict())
