"""Envoie sur Telegram UN SEUL pronostic par match (le meilleur choix du robot).
Lit le fichier pronos.md produit par le robot d'analyse."""
import os
import re
import sys
import time

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
if not TOKEN or not CHAT_ID:
    sys.exit("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant (ajoute-les dans les Secrets du dépôt)")

SEUIL_PARI = 0.58     # en dessous, le robot ne conseille aucun pari sur ce match
SEUIL_COUPON = 0.62   # seuls les paris très probables entrent dans le coupon
NB_COUPON = 5

# Poids par type de pari : les paris à petite cote (double chance) ou aux stats
# très variables (fautes, cartons) sont moins intéressants à probabilité égale.
POIDS = {"victoire": 1.0, "buts": 1.0, "btts": 0.97, "tirs": 0.95,
         "corners": 0.95, "double": 0.85, "fautes": 0.88, "cartons": 0.88}
STATS = {"Tirs cadrés": ("tirs", "tirs cadrés"), "Corners": ("corners", "corners"),
         "Fautes": ("fautes", "fautes"), "Cartons jaunes": ("cartons", "cartons jaunes")}


def envoyer(texte):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": texte, "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        sys.exit(f"Erreur Telegram {r.status_code} : {r.text}")
    time.sleep(1.5)


def lire_matchs(texte):
    matchs, comp, cur = [], "", None
    for ligne in texte.splitlines():
        if ligne.startswith("## "):
            comp = ligne[3:].strip()
        elif ligne.startswith("- **"):
            m = re.match(r"- \*\*(.+?)\*\* \((.+?)\)", ligne)
            cur = {"comp": comp, "nom": m.group(1), "heure": m.group(2), "details": []} if m else None
            if cur:
                matchs.append(cur)
        elif cur and ligne.startswith("  - "):
            cur["details"].append(ligne[4:].strip())
    return matchs


def meilleur_pari(match):
    """Choisit UN pari : le plus probable, après pondération. Renvoie (proba, libellé) ou None."""
    texte = "\n".join(match["details"])
    dom, _, ext = match["nom"].partition(" - ")
    options = []  # (score, proba, libellé)

    def ajouter(proba, libelle, type_pari):
        options.append((proba * POIDS[type_pari], proba, libelle))

    m = re.search(r"1: (\d+)% \| N: (\d+)% \| 2: (\d+)%", texte)
    if m:
        p1, pn, p2 = (int(x) / 100 for x in m.groups())
        ajouter(p1, f"Victoire {dom}", "victoire")
        ajouter(p2, f"Victoire {ext}", "victoire")
        ajouter(p1 + pn, f"{dom} ou match nul", "double")
        ajouter(pn + p2, f"{ext} ou match nul", "double")
    m = re.search(r"\+2,5 : (\d+)% \| 2 marquent : (\d+)%", texte)
    if m:
        over, btts = int(m.group(1)) / 100, int(m.group(2)) / 100
        ajouter(over, "Plus de 2,5 buts", "buts")
        ajouter(1 - over, "Moins de 2,5 buts", "buts")
        ajouter(btts, "Les 2 équipes marquent", "btts")
        ajouter(1 - btts, "Les 2 équipes ne marquent pas", "btts")
    for ligne in match["details"]:
        m = re.match(r"(Tirs cadrés|Corners|Fautes|Cartons jaunes) : .*\| \+(\d+,5) : (\d+)%", ligne)
        if m:
            type_pari, nom = STATS[m.group(1)]
            p = int(m.group(3)) / 100
            ajouter(p, f"Plus de {m.group(2)} {nom}", type_pari)
            ajouter(1 - p, f"Moins de {m.group(2)} {nom}", type_pari)
    if not options:
        return None
    _, proba, libelle = max(options)
    return proba, libelle


def fiabilite(match):
    for d in match["details"]:
        if d.startswith("Tendance"):
            return d.split("fiabilité :")[-1].strip()
    return ""


def main():
    with open("pronos.md", encoding="utf-8") as f:
        texte = f.read()
    date = re.search(r"Pronostics du (\S+)", texte)
    date = date.group(1) if date else ""
    matchs = lire_matchs(texte)
    if not matchs:
        envoyer(f"⚽ Pronostics du {date}\n\nAucun match aujourd'hui.")
        return

    for m in matchs:
        m["pari"] = meilleur_pari(m)

    # 1) Le coupon : les paris les plus sûrs du jour (1 par match)
    sûrs = [m for m in matchs if m["pari"] and m["pari"][0] >= SEUIL_COUPON
            and not fiabilite(m).startswith("faible")]
    sûrs.sort(key=lambda m: m["pari"][0], reverse=True)
    coupon = [f"🎟️ COUPON DU JOUR - {date}\n"]
    if sûrs:
        for i, m in enumerate(sûrs[:NB_COUPON], 1):
            coupon.append(f"{i}. {m['nom']} ({m['heure']})\n   ➜ {m['pari'][1]} ({m['pari'][0]:.0%})\n")
    else:
        coupon.append("Aucun pari assez solide aujourd'hui, mieux vaut s'abstenir.\n")
    coupon.append("⚠️ Estimations statistiques, aucune garantie. Joue avec modération.")
    envoyer("\n".join(coupon))

    # 2) Un pronostic par match, par compétition
    par_comp = {}
    for m in matchs:
        par_comp.setdefault(m["comp"], []).append(m)
    for comp, liste in par_comp.items():
        morceau = f"⚽ {comp}\n"
        for m in liste:
            if m["pari"] and m["pari"][0] >= SEUIL_PARI:
                ligne = f"➜ {m['pari'][1]} ({m['pari'][0]:.0%})"
            else:
                ligne = "➜ Pas de pari conseillé (match trop incertain)"
            fi = fiabilite(m)
            bloc = f"\n{m['nom']} ({m['heure']})\n{ligne}\n" + (f"Fiabilité : {fi}\n" if fi else "")
            if len(morceau) + len(bloc) > 3800:
                envoyer(morceau)
                morceau = f"⚽ {comp} (suite)\n"
            morceau += bloc
        envoyer(morceau)
    print("Messages envoyés sur Telegram")


if __name__ == "__main__":
    main()
