"""Détail par spécialité de l'allocation ciblée (250 et 500 M€) et de la demande bloquée."""
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
ii, jj, sc, gain, pp = bl
first = pd.DataFrame(dict(i=ii, j=jj, p=pp)).sort_values(["i", "p"]).drop_duplicates("i")
seg_first = d["seg"][first.i.values]
df = pd.DataFrame(dict(type=[D["tnames"][t] for t in D["ftype"]], filiere=info["filiere"], region=info["region"],
                       cap=cap0, voeux=info["voeux"]))
df["bloques"] = np.bincount(first.j, minlength=D["M"])
for k, s in enumerate(R.SNAMES):
    df[f"bloques_{s}"] = np.bincount(first.j[seg_first == k], minlength=D["M"])
for B in (250, 500):
    a, _ = O.allocation_ciblee(D, info, cap0, B * 1e6, bl, cout)
    df[f"ajout_{B}"] = a
df.to_csv("detail_formations.csv", index=False)
g = df.groupby("filiere").agg(cap=("cap", "sum"), bloques=("bloques", "sum"), bl_pro=("bloques_Pro", "sum"),
                              bl_techno=("bloques_Techno", "sum"), a250=("ajout_250", "sum"),
                              a500=("ajout_500", "sum")).sort_values("a250", ascending=False)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)
print(g.head(25).to_string())
seg_tot = {s: int(df[f"bloques_{s}"].sum()) for s in R.SNAMES}
print(seg_tot)
t = df.groupby("type")[[f"bloques_{s}" for s in R.SNAMES]].sum()
print(t.to_string())
