"""Impact du plan « 15 000 places » (10 000 BTS, 4 000 BUT, 1 000 santé = IFSI ; ~60 % en Île-de-France)
dans les scénarios A2 (actuel), B (5 + PC), C (2 → 5 → PC) et D (5 → 10 → PC).

Répartition des places du plan (hypothèses) :
  - quota par filière : BTS 10 000, BUT 4 000, IFSI 1 000 ;
  - PART_IDF des places de chaque filière en Île-de-France, le reste dans les autres régions ;
  - dans chaque (filière, zone), les places vont aux formations publiques / sous contrat en proportion
    de leur demande bloquée (système actuel), par blocs de 10, au plus +50 % de la capacité.
Référence : répartition « ciblée » optimisée (demande bloquée par euro) au même coût annuel.
Usage : python plan_gouvernement.py fr-esr-parcoursup.csv
"""
import sys
import time
import numpy as np
import pandas as pd
import parcoursup_realiste as pr
import parcoursup_strategies as ps
import parcoursup_reel as R
import parcoursup_ouvertures as O

PLAN = {"BTS": 10_000, "BUT": 4_000, "IFSI": 1_000}
PART_IDF = 0.60
SCEN = {"A2 actuel": [None, pr.K_PC], "B 5 + PC": [5, pr.K_PC],
        "C 2 → 5 → PC": [2, 5, pr.K_PC], "D 5 → 10 → PC": [5, 10, pr.K_PC]}


def repartir_plan(D, info, cap, bl):
    ii, jj, sc, gain, pp = bl
    bloques = np.bincount(jj, minlength=D["M"]).astype(float)
    ajout = np.zeros(D["M"], np.int64)
    idf = info["region"] == "Ile-de-France"
    maxadd = np.where(info["ouvrable"], np.maximum(O.BLOC, (O.MAX_HAUSSE * cap // O.BLOC) * O.BLOC), 0).astype(np.int64)
    for t, total in PLAN.items():
        ti = D["tnames"].index(t)
        for zone, q in ((idf, PART_IDF * total), (~idf, (1 - PART_IDF) * total)):
            m = (D["ftype"] == ti) & zone & info["ouvrable"]
            reste = int(round(q / O.BLOC))                   # en blocs de 10
            w = bloques * m + 1e-9 * m                        # demande bloquée (≥ 0)
            while reste > 0:
                libre = m & (ajout + O.BLOC <= maxadd)
                if not libre.any():
                    break
                ww = np.where(libre, w, 0)
                if ww.sum() <= 0:
                    ww = np.where(libre, cap, 0).astype(float)
                part = ww / ww.sum() * reste
                blocs = np.floor(part).astype(np.int64)
                blocs = np.minimum(blocs, (maxadd - ajout) // O.BLOC)
                if blocs.sum() == 0:                          # restes : plus forts restes d'abord
                    j = np.argmax(np.where(libre, part, -1))
                    blocs[j] = 1
                ajout += blocs * O.BLOC
                reste -= int(blocs.sum())
    return ajout


def remplissage(res, cap0, ajout):
    adm = np.bincount(res["fid"][res["fid"] >= 0], minlength=len(cap0))
    return int(np.minimum(ajout, np.maximum(adm - cap0, 0)).sum())


def main(path):
    t0 = time.time()
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
    ajout = repartir_plan(D, info, cap0, bl)
    cout_plan = float((ajout * cout).sum())
    tab = pd.DataFrame(dict(type=[D["tnames"][t] for t in D["ftype"]], region=info["region"], places=ajout))
    tab = tab[tab.places > 0]
    print(f"Plan : {ajout.sum():,} places, coût {cout_plan / 1e6:.0f} M€/an (régime permanent), "
          f"{(ajout > 0).sum()} formations".replace(",", " "))
    print(tab.groupby("type").places.sum().to_dict())
    print(tab.groupby("region").places.sum().sort_values(ascending=False).to_dict())
    tab.groupby(["region", "type"]).places.sum().reset_index().to_csv("plan_repartition.csv", index=False)
    opt, dep_opt = O.allocation_ciblee(D, info, cap0, cout_plan, bl, cout)

    lignes = []
    for nom, tours in SCEN.items():
        ref_pos = None
        for variante, cap in (("sans plan", cap0.copy()), ("avec plan", cap0 + ajout)) + \
                (((f"optimisé {cout_plan / 1e6:.0f} M€", cap0 + opt),) if nom.startswith("A2") else ()):
            res, _ = O.simuler_A2(d, informe, cap, tours=tours)
            ind = O.indicateurs(d, res, informe, ref_pos)
            if ref_pos is None:
                ref_pos = res["pos"]
            ind["places_ajoutees"] = int(cap.sum() - cap0.sum())
            ind["places_nouvelles_occupees"] = remplissage(res, cap0, cap - cap0) if cap.sum() > cap0.sum() else 0
            ind["admis_total"] = int((res["pos"] >= 0).sum())
            ind["dossiers"] = int(sum(res["soumis"]))
            lignes.append(dict(scenario=nom, variante=variante, **ind))
            print(f"  {nom:<16}{variante:<16} utilité {ind['utilite']:.4f}  1er {ind['1er']:.1%}  top5 {ind['top5']:.1%}"
                  f"  sans sol. {ind['sans_sol_n']:,}  admis {ind['admis_total']:,}  "
                  f"nouvelles places occupées {ind['places_nouvelles_occupees']:,} ({time.time() - t0:.0f} s)"
                  .replace(",", " "), flush=True)
    out = pd.DataFrame(lignes)
    out.to_csv("plan_resultats.csv", index=False)
    cols = ["scenario", "variante", "utilite", "1er", "top5", "top10", "sans_sol_n", "admis_total",
            "places_nouvelles_occupees", "u_Générale", "u_Techno", "u_Pro", "u_Autres", "ss_Générale", "ss_Techno",
            "ss_Pro", "ss_Autres", "prime_info", "ameliores", "degrades", "dossiers"]
    pd.set_option("display.width", 300)
    print(out[cols].round(4).to_string(index=False))
    print(f"(temps total {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "donnees/fr-esr-parcoursup.csv")
