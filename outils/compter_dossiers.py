"""Décompte détaillé des dossiers déposés par tour, pour chaque variante (comportements informés/naïfs)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys, numpy as np
import parcoursup_realiste as pr, parcoursup_strategies as ps, parcoursup_reel as R
path = (sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
D = R.charger(path); rng = np.random.default_rng(R.SEED); d = R.generer(rng, D)
d["pc_nationale"] = R.pc_nationale
R.calibrer(d, D); ps.SIGMA = float(np.sqrt(R.NOISE_SD ** 2 + 0.2 ** 2))
informe = R.tirer_informes(d, rng)
print(f"Somme des K (vœux formulés si liste complète) : {np.minimum(d['K'], R.L).sum():,}  | réel PP 2025 : 13 052 195".replace(",", " "))
print(f"Somme de min(K,5) : {np.minimum(d['K'], 5).sum():,} ; de min(K,2) : {np.minimum(d['K'], 2).sum():,}".replace(",", " "))
for nom, (tours, nat) in {"A1": ([None, pr.K_PC], False), "A2": ([None, pr.K_PC], True), "B": ([5, pr.K_PC], True),
                          "C": ([2, 5, pr.K_PC], True), "D": ([5, 10, pr.K_PC], True)}.items():
    d["pc_nationale"] = R.pc_nationale if nat else R.pc_locale
    ref = ps.run_reference(d, tours)
    res = pr.simuler(d, tours, ps.fabrique_choix(d, informe, ref, tours))
    parts = [i["participants"] for i in res["tours"]]
    so = res["soumis"]
    print(f"{nom}: " + " | ".join(f"tour {t+1}: {p:,} candidats, {v:,} dossiers ({v/p:.1f}/cand.)" for t, (p, v) in enumerate(zip(parts, so)))
          .replace(",", " ") + f" || TOTAL {sum(so):,} ({sum(so)/1e6:.2f} M) ; ancien décompte {res['dossiers']/1e6:.2f} M".replace(",", " "), flush=True)
