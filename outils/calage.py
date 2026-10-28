"""Compare le modèle aux données réelles 2025 (phase principale et complémentaire), avec les paramètres par défaut
ou des paramètres passés en JSON.
usage : python outils/calage.py donnees/fr-esr-parcoursup.csv '{"NOISE_SD": 0.3, "PC_VOEUX_MOY": 5}'"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys, json, time
import numpy as np
import parcoursup_realiste as pr
import parcoursup_reel as R
import valider_pc as V

REEL_PP = {"Générale": 0.965, "Techno": 0.894, "Pro": 0.796}


def mesurer_pp(d, res):
    cut = res["cutoffs"][0]
    K = np.minimum(d["K"], R.L)
    P = np.arange(R.L)[None, :] < K[:, None]
    prop = ((d["S"] >= cut[d["F"]]) & P).any(axis=1)
    return {s: float(prop[d["seg"] == k].mean()) for k, s in enumerate(R.SNAMES)}


if __name__ == "__main__":
    t0 = time.time()
    prm = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    R.BETA_SEG = prm.get("BETA_SEG", R.BETA_SEG)
    for k in ("GAMMA", "BONUS_NON_SELECTIVE_ACADEMIE", "NOISE_SD", "PC_VOEUX_MOY", "LOC_PC", "LAMBDA_PP",
              "SECU_SEG", "PC_HESITANTS", "PC_PARTICIPATION"):
        if k in prm:
            setattr(R, k, prm[k])
    D = R.charger((sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv"))
    d = R.generer(np.random.default_rng(R.SEED), D)
    d["pc_nationale"] = R.pc_nationale
    q = R.calibrer(d, D)
    out, adm = V.mesurer(d, D)
    res = pr.simuler(d, [None, pr.K_PC])
    pp = mesurer_pp(d, res)
    print(f"PARAMS={prm} q={ {k: round(v, 2) for k, v in q.items()} }")
    cut = res["cutoffs"][0]
    Kc = np.minimum(d["K"], R.L)
    P = np.arange(R.L)[None, :] < Kc[:, None]
    above = (d["S"] >= cut[d["F"]]) & P
    app = np.bincount(d["F"][P], minlength=D["M"]).astype(float)
    acc = np.bincount(d["F"][above], minlength=D["M"]).astype(float)
    tn = np.array(D["tnames"])
    taux = {t: acc[D["ftype"] == i].sum() / app[D["ftype"] == i].sum() for i, t in enumerate(tn)}
    REEL_T = {"Licence": .53, "PASS": .29, "IFSI": .37, "CPGE": .33, "BUT": .33, "BTS": .36, "Ecole d'Ingénieur": .59}
    print("  propositions totales : %s (réel 4 140 241)" % f"{int(above.sum()):,}".replace(",", " "))
    print("  taux d'accès : " + ", ".join(f"{t} {taux[t]:.0%}/{v:.0%}" for t, v in REEL_T.items()))
    print("  ≥1 proposition PP : " + ", ".join(f"{s} {pp[s]:.1%} (réel {REEL_PP[s]:.1%})" for s in REEL_PP))
    print(f"  admis PP (tour 1) : {res['tours'][0]['affectes']:,} (réel 602 578) ; PC : {out['admis']:,} (réel 58 174)".replace(",", " "))
    print("  candidatures PC : " + ", ".join(f"{k} {out[k]:,}/{V.REEL[k]:,}" for k in
          ["candidatures", "cand_gen", "cand_techno", "cand_pro", "cand_autres"]).replace(",", " "))
    print(f"  candidatures classées : {out['classees']:,} (réel 171 365) ; participants : {out['participants']:,}".replace(",", " "))
    print("  admis PC par filière : " + ", ".join(f"{t} {adm.get(t, 0)}/{v}" for t, v in V.REEL_ADMIS_TYPE.items()))
    print(f"  ({time.time() - t0:.0f} s)", flush=True)
