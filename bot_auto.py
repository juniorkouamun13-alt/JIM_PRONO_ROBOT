"""Robot de pronostics v2 : buts, tirs cadrés, corners, fautes, cartons.
Calendrier : football-data.org. Statistiques détaillées : football-data.co.uk (gratuit).
Ligue des champions et Brasileirão : buts seulement (pas de stats détaillées gratuites).
"""
import csv
import datetime as dt
import difflib
import io
import math
import os
import re
import sys
import time
import unicodedata

import requests

TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN")
if not TOKEN:
    sys.exit("FOOTBALL_DATA_TOKEN manquant (ajoute-le dans les Secrets du dépôt)")

BASE = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": TOKEN}
COMPS = {
    "CL": ("Ligue des champions", None),
    "FL1": ("Ligue 1", "F1"),
    "BL1": ("Bundesliga", "D1"),
    "PD": ("La Liga", "SP1"),
    "SA": ("Série A", "I1"),
    "PL": ("Premier League", "E0"),
    "ELC": ("Championship", "E1"),
    "PPL": ("Liga Portugal", "P1"),
    "BSA": ("Brasileirão", None),
}
STATS = {
    "buts": ("FTHG", "FTAG"),
    "tirs_cadres": ("HST", "AST"),
    "corners": ("HC", "AC"),
    "fautes": ("HF", "AF"),
    "jaunes": ("HY", "AY"),
}
NOMS = {
    "tirs_cadres": "Tirs cadrés",
    "corners": "Corners",
    "fautes": "Fautes",
    "jaunes": "Cartons jaunes",
}
ALIAS = {
    "paris saint germain": "paris sg",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "borussia monchengladbach": "m gladbach",
    "borussia dortmund": "dortmund",
    "bayer 04 leverkusen": "leverkusen",
    "eintracht frankfurt": "ein frankfurt",
    "manchester city": "man city",
    "manchester united": "man united",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "nottingham forest": "nott m forest",
    "atletico madrid": "ath madrid",
    "athletic club": "ath bilbao",
    "real betis": "betis",
    "rayo vallecano": "vallecano",
    "internazionale milano": "inter",
    "sporting clube portugal": "sp lisbon",
    "sporting braga": "sp braga",
}
MOTS_INUTILES = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "sv", "vfl", "fsv", "bsc", "rc", "ud", "cd", "de", "calcio"}


def get(path, params=None):
    """Appel API avec pause (limite gratuite : 10 requêtes/minute)."""
    for _ in range(3):
        r = requests.get(BASE + path, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(65)
            continue
        time.sleep(6.5)
        if r.ok:
            return r.json()
        print(f"Erreur {r.status_code} sur {path}")
        return None
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    mots = [m for m in re.sub(r"[^a-z0-9]+", " ", s).split() if m not in MOTS_INUTILES]
    return " ".join(mots)


def trouver_equipe(noms, equipes):
    """Retrouve le nom de l'équipe dans le fichier CSV (noms différents selon les sources)."""
    cles = {norm(e): e for e in equipes}
    for nom in noms:
        n = norm(nom or "")
        n = ALIAS.get(n, n)
        if n in cles:
            return cles[n]
        proche = difflib.get_close_matches(n, list(cles), n=1, cutoff=0.72)
        if proche:
            return cles[proche[0]]
    return None


def charger_ligue(code_csv):
    """Télécharge la saison en cours et calcule les stats de chaque équipe."""
    auj = dt.date.today()
    an = auj.year if auj.month >= 7 else auj.year - 1
    saison = f"{an % 100:02d}{(an + 1) % 100:02d}"
    url = f"https://www.football-data.co.uk/mmz4281/{saison}/{code_csv}.csv"
    try:
        r = requests.get(url, timeout=30)
    except requests.RequestException:
        return None
    if not r.ok:
        print(f"CSV introuvable : {url}")
        return None
    lecteur = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", errors="replace")))
    equipes = {}
    somme = {k: [0.0, 0.0, 0] for k in STATS}
    for row in lecteur:
        dom, ext = row.get("HomeTeam"), row.get("AwayTeam")
        if not dom or not ext or not row.get("FTHG"):
            continue
        vals_dom, vals_ext = {}, {}
        for k, (ch, ca) in STATS.items():
            try:
                h, a = float(row[ch]), float(row[ca])
            except (KeyError, ValueError, TypeError):
                continue
            vals_dom[k] = (h, a)
            vals_ext[k] = (a, h)
            somme[k][0] += h
            somme[k][1] += a
            somme[k][2] += 1
        equipes.setdefault(dom, []).append(vals_dom)
        equipes.setdefault(ext, []).append(vals_ext)
    moyennes = {}
    for k, (sd, se, n) in somme.items():
        if n >= 10 and (sd + se) > 0:
            moyennes[k] = (sd / n, se / n)
    if not moyennes:
        return None
    return equipes, moyennes


def taux(matchs, k, idx, moy):
    """Moyenne pondérée : saison + forme des 5 derniers matchs, lissée vers la moyenne."""
    v = [m[k][idx] for m in matchs if k in m]
    saison = (sum(v) + moy * 4) / (len(v) + 4)
    r = v[-5:]
    forme = (sum(r) + moy * 3) / (len(r) + 3)
    return 0.75 * saison + 0.25 * forme


def attendus(ligue, nom_dom, nom_ext):
    equipes, moyennes = ligue
    res = {}
    for k, (m_dom, m_ext) in moyennes.items():
        moy = (m_dom + m_ext) / 2
        att_d = taux(equipes[nom_dom], k, 0, moy) / moy
        def_d = taux(equipes[nom_dom], k, 1, moy) / moy
        att_e = taux(equipes[nom_ext], k, 0, moy) / moy
        def_e = taux(equipes[nom_ext], k, 1, moy) / moy
        res[k] = (m_dom * att_d * def_e, m_ext * att_e * def_d)
    return res


def poisson(k, lam):
    return math.exp(-lam) * lam**k / math.factorial(k)


def proba_plus(lam, ligne):
    return 1 - sum(poisson(i, lam) for i in range(int(ligne) + 1))


def buts_classement(code):
    """Solution de secours : buts attendus d'après le classement."""
    data = get(f"/competitions/{code}/standings")
    stats, gf_tot, pg_tot = {}, 0, 0
    for table in (data or {}).get("standings", []):
        if table.get("type") != "TOTAL":
            continue
        for row in table["table"]:
            stats[row["team"]["id"]] = (row["playedGames"], row["goalsFor"], row["goalsAgainst"])
            gf_tot += row["goalsFor"]
            pg_tot += row["playedGames"]
    return stats, (gf_tot / pg_tot if pg_tot else 1.3)


def buts_attendus_classement(stats, avg, id_dom, id_ext):
    def force(i):
        pg, gf, ga = stats.get(i, (0, 0, 0))
        return (gf + avg * 5) / (pg + 5) / avg, (ga + avg * 5) / (pg + 5) / avg

    att_d, def_d = force(id_dom)
    att_e, def_e = force(id_ext)
    return avg * att_d * def_e * 1.12, avg * att_e * def_d * 0.92


def resultat_buts(lh, la):
    p1 = pn = p2 = over = btts = 0.0
    best, best_p = (0, 0), 0.0
    for i in range(10):
        for j in range(10):
            p = poisson(i, lh) * poisson(j, la)
            if i > j:
                p1 += p
            elif i == j:
                pn += p
            else:
                p2 += p
            if i + j > 2:
                over += p
            if i > 0 and j > 0:
                btts += p
            if p > best_p:
                best, best_p = (i, j), p
    return p1, pn, p2, over, btts, best


def tendance(p1, pn, p2):
    if p1 >= 0.5:
        return "Victoire domicile"
    if p2 >= 0.5:
        return "Victoire extérieur"
    if p1 + pn >= p2 + pn:
        return "Domicile ou nul (1N)"
    return "Extérieur ou nul (N2)"


def ligne_stat(nom, eh, ea):
    total = eh + ea
    ligne = math.floor(total) + 0.5
    p = proba_plus(total, ligne)
    return (f"  - {nom} : {eh:.1f} - {ea:.1f} (total {total:.1f}) | "
            f"+{str(ligne).replace('.', ',')} : {p:.0%}")


def main():
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    lines = [f"# Pronostics du {today}\n"]
    total = 0
    for code, (nom_comp, code_csv) in COMPS.items():
        data = get(f"/competitions/{code}/matches", {"dateFrom": today, "dateTo": today})
        matches = (data or {}).get("matches", [])
        if not matches:
            continue
        ligue = charger_ligue(code_csv) if code_csv else None
        secours = None
        lines.append(f"\n## {nom_comp}\n")
        for m in matches:
            h, a = m["homeTeam"], m["awayTeam"]
            if not h.get("id") or not a.get("id"):
                continue
            heure = m["utcDate"][11:16] + " UTC"
            att, nb_matchs = None, 0
            if ligue:
                nd = trouver_equipe([h.get("name"), h.get("shortName")], ligue[0])
                ne = trouver_equipe([a.get("name"), a.get("shortName")], ligue[0])
                if nd and ne and "buts" in ligue[1]:
                    att = attendus(ligue, nd, ne)
                    nb_matchs = min(len(ligue[0][nd]), len(ligue[0][ne]))
            if att:
                lh, la = att["buts"]
            else:
                if secours is None:
                    secours = buts_classement(code)
                lh, la = buts_attendus_classement(secours[0], secours[1], h["id"], a["id"])
            p1, pn, p2, over, btts, best = resultat_buts(lh, la)
            bloc = [
                f"- **{h['name']} - {a['name']}** ({heure})",
                f"  - 1: {p1:.0%} | N: {pn:.0%} | 2: {p2:.0%}",
                f"  - Buts : {lh:.1f} - {la:.1f} | score probable : {best[0]}-{best[1]} | "
                f"+2,5 : {over:.0%} | 2 marquent : {btts:.0%}",
            ]
            if att:
                for k, nom in NOMS.items():
                    if k in att:
                        bloc.append(ligne_stat(nom, *att[k]))
                if nb_matchs >= 8:
                    fiabilite = "bonne"
                elif nb_matchs >= 4:
                    fiabilite = "moyenne"
                else:
                    fiabilite = "faible (peu de matchs joués)"
            else:
                fiabilite = "indicative (buts seulement)"
            bloc.append(f"  - Tendance : {tendance(p1, pn, p2)} | fiabilité : {fiabilite}")
            lines.append("\n".join(bloc))
            total += 1
    if total == 0:
        lines.append("\nAucun match aujourd'hui.")
    lines.append("\n\n_Estimations statistiques, aucune garantie de résultat._")
    with open("pronos.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"{total} match(s) analysé(s)")


if __name__ == "__main__":
    main()
