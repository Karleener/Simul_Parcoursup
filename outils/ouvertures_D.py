"""Réforme D (5 → 10 → PC nationale), seule et combinée à l'ouverture ciblée de 250 M€."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys, numpy as np, pandas as pd
import parcoursup_realiste as pr, parcoursup_strategies as ps, parcoursup_reel as R, parcoursup_ouvertures as O
path = (sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
D = R.charger(path); info = O.infos_formations(path)
rng = np.random.default_rng(R.SEED); d = R.generer(rng, D); d["pc_nationale"] = R.pc_nationale
R.calibrer(d, D); ps.SIGMA = float(np.sqrt(R.NOISE_SD ** 2 + 0.2 ** 2))
informe = R.tirer_informes(d, rng)
cap0 = D["cap"].copy(); cout = O.cout_place(D)
base, _ = O.simuler_A2(d, informe, cap0.copy())
bl = O.demande_bloquee(d, base)
ajout, dep = O.allocation_ciblee(D, info, cap0, 250e6, bl, cout)
rows = []
for nom, cap, tours in (("Réforme D seule", cap0.copy(), [5, 10, pr.K_PC]),
                        ("Réforme D + ciblée 250 M€", cap0 + ajout, [5, 10, pr.K_PC])):
    res, _ = O.simuler_A2(d, informe, cap, tours=tours)
    rows.append(dict(politique=nom, **O.indicateurs(d, res, informe, base["pos"])))
out = pd.DataFrame(rows)
print(out[["politique", "utilite", "1er", "top5", "top10", "sans_sol", "sans_sol_n", "prime_info", "u_Pro", "u_Techno"]].round(4).to_string(index=False))
out.to_csv("ouvertures_D.csv", index=False)
