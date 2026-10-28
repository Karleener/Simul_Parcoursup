"""Compare la phase complémentaire simulée (système actuel, candidats sincères) aux données réelles 2025."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # modules à la racine du dépôt
import sys
import numpy as np
import pandas as pd
import parcoursup_realiste as pr
import parcoursup_reel as R

REEL = {"candidatures": 450_190, "cand_gen": 97_376, "cand_techno": 56_177, "cand_pro": 36_105,
        "cand_autres": 260_532, "admis": 58_174}
REEL_ADMIS_TYPE = {"Licence": 37_611, "BTS": 10_580, "Autre formation": 3_303, "BUT": 1_871,
                   "Ecole de Commerce": 1_242, "Licence_Las": 1_027, "CPGE": 954, "Ecole d'Ingénieur": 630,
                   "EFTS": 546, "PASS": 410, "IFSI": 0}


def mesurer(d, D):
    rec = {}

    def espion(t, ouvert, kmax, active, allow):
        return allow
    # on capture la liste PC via un wrapper autour de pc_nationale
    orig = d["pc_nationale"]

    def wrap(dd, cap, active, allow, kmax):
        Ft, St, A = orig(dd, cap, active, allow, kmax)
        rec["Ft"], rec["A"], rec["active"] = Ft, A, active
        return Ft, St, A
    d["pc_nationale"] = wrap
    res = pr.simuler(d, [None, pr.K_PC], espion)
    d["pc_nationale"] = orig
    A = d["pc_stats"]["deposees"] if "pc_stats" in d else rec["A"]
    seg = d["seg"]
    out = {"candidatures": int(A.sum())}
    for k, nm in enumerate(["cand_gen", "cand_techno", "cand_pro", "cand_autres"]):
        out[nm] = int(A[seg == k].sum())
    pc = res["tours"][1]["affectes"]
    out["admis"] = int(pc)
    newly = (res["pos"] >= 0) & rec["active"]
    ft = D["ftype"][res["fid"][newly]]
    adm_type = {t: int((ft == i).sum()) for i, t in enumerate(D["tnames"])}
    out["participants"] = int((A.sum(1) > 0).sum())
    out["classees"] = int(rec["A"].sum())
    dem = d["lacc"] == R.L
    out["sans_sol_dem"] = int((dem & (res["pos"] < 0)).sum())
    return out, adm_type


def rapport(out, adm_type, titre):
    print(f"\n=== {titre} ===")
    print(f"  {'indicateur':<28}{'modèle':>12}{'réel 2025':>12}")
    for k in ["candidatures", "cand_gen", "cand_techno", "cand_pro", "cand_autres", "admis"]:
        print(f"  {k:<28}{out[k]:>12,}{REEL[k]:>12,}".replace(",", " "))
    print(f"  candidatures classées : {out.get('classees', 0):,} (réel 171 365)".replace(",", " "))
    print(f"  participants (≥1 vœu en PC) : {out['participants']:,} ; demandeurs sans solution : {out['sans_sol_dem']:,}"
          .replace(",", " "))
    print("  admis en PC par filière (modèle | réel) :")
    for t, v in sorted(REEL_ADMIS_TYPE.items(), key=lambda x: -x[1]):
        print(f"    {t:<20}{adm_type.get(t, 0):>8,} | {v:,}".replace(",", " "))


if __name__ == "__main__":
    path = (sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
    mode = sys.argv[2] if len(sys.argv) > 2 else "stable"
    D = R.charger(path)
    d = R.generer(np.random.default_rng(R.SEED), D)
    R.PC_MODE = mode
    d["pc_nationale"] = R.pc_nationale
    R.calibrer(d, D)
    out, adm = mesurer(d, D)
    rapport(out, adm, f"Phase complémentaire — mode {mode}")
