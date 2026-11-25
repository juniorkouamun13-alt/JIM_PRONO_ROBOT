"""Envoie le coupon du jour et les pronostics (fichier pronos.md) sur un groupe Telegram."""
import os
import re
import sys
import time

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
if not TOKEN or not CHAT_ID:
    sys.exit("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant (ajoute-les dans les Secrets du dépôt)")

SEUIL = 0.60      # un pari n'entre dans le coupon que s'il dépasse ce seuil
NB_MAX = 5        # nombre maximum de paris dans le coupon


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
    """Le pari le plus probable parmi : victoire dom., victoire ext., +2,5 buts, 2 équipes marquent."""
    texte = "\n".join(match["details"])
    dom, _, ext = match["nom"].partition(" - ")
    options = []
    m = re.search(r"1: (\d+)% \| N: (\d+)% \| 2: (\d+)%", texte)
    if m:
        options.append((int(m.group(1)) / 100, f"Victoire {dom}"))
        options.append((int(m.group(3)) / 100, f"Victoire {ext}"))
    m = re.search(r"\+2,5 : (\d+)%", texte)
    if m:
        options.append((int(m.group(1)) / 100, "Plus de 2,5 buts"))
    m = re.search(r"2 marquent : (\d+)%", texte)
    if m:
        options.append((int(m.group(1)) / 100, "Les 2 équipes marquent"))
    return max(options) if options else None


def main():
    with open("pronos.md", encoding="utf-8") as f:
        texte = f.read()
    date = re.search(r"Pronostics du (\S+)", texte)
    date = date.group(1) if date else ""
    matchs = lire_matchs(texte)
    if not matchs:
        envoyer(f"⚽ Pronostics du {date}\n\nAucun match aujourd'hui.")
        return

    # 1) Le coupon
    choix = []
    for m in matchs:
        p = meilleur_pari(m)
        if p and p[0] >= SEUIL:
            choix.append((p[0], p[1], m))
    choix.sort(key=lambda x: x[0], reverse=True)
    coupon = [f"🎟️ COUPON DU JOUR - {date}\n"]
    if choix:
        for i, (proba, pari, m) in enumerate(choix[:NB_MAX], 1):
            coupon.append(f"{i}. {m['nom']} ({m['comp']}, {m['heure']})\n   ➜ {pari} : {proba:.0%}\n")
    else:
        coupon.append("Aucun pari assez solide aujourd'hui, mieux vaut s'abstenir.\n")
    coupon.append("⚠️ Estimations statistiques, aucune garantie. Joue avec modération.")
    envoyer("\n".join(coupon))

    # 2) Le détail de chaque compétition
    par_comp = {}
    for m in matchs:
        par_comp.setdefault(m["comp"], []).append(m)
    for comp, liste in par_comp.items():
        morceau = f"⚽ {comp}\n"
        for m in liste:
            bloc = f"\n{m['nom']} ({m['heure']})\n" + "\n".join(f"• {d}" for d in m["details"]) + "\n"
            if len(morceau) + len(bloc) > 3800:
                envoyer(morceau)
                morceau = f"⚽ {comp} (suite)\n"
            morceau += bloc
        envoyer(morceau)
    print("Messages envoyés sur Telegram")


if __name__ == "__main__":
    main()
