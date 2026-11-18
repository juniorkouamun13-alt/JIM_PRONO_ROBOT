"""Robot automatique JIM || PRONO : analyses du jour, bilans et bilan de la semaine,
pour la Ligue des champions et les grands championnats.

Il lit les calendriers sur football-data.org (clé gratuite), estime les chances de chaque
match avec un modèle statistique simple (buts marqués et encaissés au classement),
publie un message par compétition les jours de match, puis le bilan du lendemain,
ratés compris. Il lance aussi les messages manuels de posts.json.

Garde-fous : mention « 18+ » et « aucun gain garanti » dans chaque message, message
présenté comme une analyse automatique, pas de pronostic quand le match est trop ouvert.

  python robot_pronos.py --demo      -> test à blanc avec des données fictives
  python robot_pronos.py --dry-run   -> test avec les vraies données, rien n'est envoyé
  python robot_pronos.py --once      -> un passage (tâche planifiée)
  python robot_pronos.py             -> tourne en continu
Variables : BOT_TOKEN, FOOTBALL_DATA_KEY, CHANNEL (par défaut @JIMPRONO),
COMPETITIONS (par défaut toutes celles de la liste ci-dessous, séparées par des virgules).
Toutes les heures sont en GMT.
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.request

import bot_telegram as bt

NOMS = {
    "CL": "Ligue des champions", "PL": "Premier League", "FL1": "Ligue 1",
    "BL1": "Bundesliga", "SA": "Serie A", "PD": "Liga", "DED": "Eredivisie",
    "PPL": "Liga Portugal", "ELC": "Championship", "BSA": "Brasileirão",
}
DEMO = "--demo" in sys.argv
if DEMO:
    bt.DRY = True
ONCE = bt.ONCE or DEMO
KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
CODES = [c.strip() for c in os.environ.get("COMPETITIONS", ",".join(NOMS)).split(",") if c.strip()]
DIR = bt.DIR
PICKS = os.path.join(DIR, "picks.json")
STATE = os.path.join(DIR, "state.json")
HEURE_ANALYSE, HEURE_BILAN, HEURE_SEMAINE = (0, 0, 0) if DEMO else (9, 8, 18)
SEUIL = 0.45          # en dessous, le match est jugé trop ouvert : pas de pronostic
PAR_MESSAGE = 6       # matchs par message
PAUSE = 0 if DEMO else 7   # le compte gratuit accepte 10 requêtes par minute
MENTION = "⚠️ Estimation statistique simple, aucun gain garanti. Mise petit. 18+ · Jouer comporte des risques."


# ---------- données ----------
def api(chemin):
    if DEMO:
        return demo(chemin)
    req = urllib.request.Request("https://api.football-data.org/v4" + chemin, headers={"X-Auth-Token": KEY})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def demo(chemin):
    now = dt.datetime.now(dt.timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    eq = lambda i, n: {"id": i, "name": n}
    ligne = lambda i, n, j, gf, ga: {"team": eq(i, n), "playedGames": j, "goalsFor": gf, "goalsAgainst": ga}
    if "standings" in chemin:
        return {"standings": [{"type": "TOTAL", "table": [
            ligne(1, "Équipe A (démo)", 4, 11, 3), ligne(2, "Équipe B (démo)", 4, 6, 5),
            ligne(3, "Équipe C (démo)", 4, 5, 6), ligne(4, "Équipe D (démo)", 4, 3, 10),
            ligne(5, "Club E (démo)", 12, 25, 9), ligne(6, "Club F (démo)", 12, 14, 16)]}]}
    fini = {"id": 900, "status": "FINISHED", "utcDate": iso(now - dt.timedelta(hours=20)),
            "competition": {"code": "CL"}, "homeTeam": eq(1, "Équipe A (démo)"), "awayTeam": eq(4, "Équipe D (démo)"),
            "score": {"winner": "HOME_TEAM", "fullTime": {"home": 3, "away": 0}}}
    if "dateFrom=" + (now - dt.timedelta(days=1)).strftime("%Y-%m-%d") in chemin:
        return {"matches": [fini]}
    return {"matches": [fini,
        {"id": 901, "status": "TIMED", "utcDate": iso(now + dt.timedelta(hours=3)), "competition": {"code": "CL"},
         "homeTeam": eq(2, "Équipe B (démo)"), "awayTeam": eq(3, "Équipe C (démo)"), "score": {}},
        {"id": 902, "status": "TIMED", "utcDate": iso(now + dt.timedelta(hours=5)), "competition": {"code": "PL"},
         "homeTeam": eq(5, "Club E (démo)"), "awayTeam": eq(6, "Club F (démo)"), "score": {}}]}


def classement(code):
    tables = api("/competitions/%s/standings" % code)["standings"]
    table = next((s for s in tables if s.get("type") == "TOTAL"), tables[0])["table"]
    return {r["team"]["id"]: {"j": r["playedGames"], "gf": r["goalsFor"], "ga": r["goalsAgainst"]} for r in table}


def matchs(d1, d2):
    ch = "/matches?dateFrom=%s&dateTo=%s&competitions=%s" % (d1, d2, ",".join(CODES))
    return api(ch)["matches"]


# ---------- modèle ----------
def poisson(l, k):
    return math.exp(-l) * l ** k / math.factorial(k)


def probas(h, a, moy):
    k = 3  # on tire les équipes vers la moyenne : peu de matchs joués

    def force(t):
        n = t["j"]
        return (t["gf"] + k * moy) / (n + k) / moy, (t["ga"] + k * moy) / (n + k) / moy

    ah, dh = force(h)
    aa, da = force(a)
    lh, la = moy * 1.15 * ah * da, moy * 0.9 * aa * dh
    ph = pn = pa = 0.0
    for x in range(9):
        for y in range(9):
            p = poisson(lh, x) * poisson(la, y)
            if x > y: ph += p
            elif x == y: pn += p
            else: pa += p
    s = ph + pn + pa
    return ph / s, pn / s, pa / s


def libelle(code, dom, ext):
    return {"H": "Victoire " + dom, "D": "Match nul", "A": "Victoire " + ext}[code]


def decoupe(lignes):
    return [lignes[i:i + PAR_MESSAGE] for i in range(0, len(lignes), PAR_MESSAGE)]


# ---------- messages ----------
def analyses_du_jour(jour, picks):
    """Renvoie {clé: texte}, un message par compétition (et par paquet de 6 matchs)."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    par_comp = {}
    for m in matchs(jour, jour):
        debut = dt.datetime.strptime(m["utcDate"], "%Y-%m-%dT%H:%M:%SZ")
        if m["status"] in ("SCHEDULED", "TIMED") and debut > now:
            par_comp.setdefault(m["competition"]["code"], []).append((debut, m))
    sortie = {}
    for code, liste in par_comp.items():
        try:
            time.sleep(PAUSE)
            cl = classement(code)
        except Exception as e:
            print("Classement indisponible pour", code, ":", e)
            continue
        moy = sum(t["gf"] for t in cl.values()) / max(1, sum(t["j"] for t in cl.values())) or 1.3
        lignes = []
        for debut, m in sorted(liste, key=lambda x: x[0]):
            h, a = m["homeTeam"], m["awayTeam"]
            if h["id"] not in cl or a["id"] not in cl:
                continue
            ph, pn, pa = probas(cl[h["id"]], cl[a["id"]], moy)
            meilleur = max((ph, "H"), (pn, "D"), (pa, "A"))
            pick = meilleur[1] if meilleur[0] >= SEUIL else None
            avis = libelle(pick, h["name"], a["name"]) if pick else "match ouvert, pas de pronostic"
            picks[str(m["id"])] = {"home": h["name"], "away": a["name"], "pick": pick, "date": jour,
                                   "comp": code, "ok": None}
            lignes.append("⚽ %s – %s · %s GMT\n   Dom. %d %% · Nul %d %% · Ext. %d %%\n   🎯 Avis du modèle : %s"
                          % (h["name"], a["name"], debut.strftime("%H:%M"), round(ph * 100), round(pn * 100),
                             round(pa * 100), avis))
        for i, paquet in enumerate(decoupe(lignes)):
            sortie["analyse-%s-%s-%d" % (jour, code, i)] = (
                "🤖 Analyse automatique · %s\n\n%s\n\n%s" % (NOMS.get(code, code), "\n\n".join(paquet), MENTION))
    return sortie


def bilans(jour, picks):
    par_comp = {}
    for m in matchs(jour, jour):
        p = picks.get(str(m["id"]))
        if not p or m["status"] != "FINISHED":
            continue
        sc = m["score"]["fullTime"]
        reel = {"HOME_TEAM": "H", "AWAY_TEAM": "A", "DRAW": "D"}.get(m["score"].get("winner"))
        score = "%s %s-%s %s" % (p["home"], sc["home"], sc["away"], p["away"])
        if p["pick"] is None:
            ligne = "➖ %s · pas de pronostic" % score
        else:
            p["ok"] = (p["pick"] == reel)
            ligne = "%s %s · avis : %s" % ("✅" if p["ok"] else "❌", score, libelle(p["pick"], p["home"], p["away"]))
        par_comp.setdefault(p.get("comp", "CL"), []).append(ligne)
    sortie = {}
    for code, lignes in par_comp.items():
        for i, paquet in enumerate(decoupe(lignes)):
            sortie["bilan-%s-%s-%d" % (jour, code, i)] = (
                "📊 Bilan de la veille · %s\n\n%s\n\nJe publie aussi mes ratés : personne ne gagne à tous les coups. "
                "18+ · Aucun gain garanti." % (NOMS.get(code, code), "\n".join(paquet)))
    return sortie


def bilan_semaine(picks, today):
    debut = (today - dt.timedelta(days=6)).isoformat()
    suivis = [p for p in picks.values() if p["date"] >= debut and p["ok"] is not None]
    if not suivis:
        return None
    bons = sum(1 for p in suivis if p["ok"])
    return ("📈 Bilan de la semaine : %d avis juste(s) sur %d.\nCe sont des estimations, pas des certitudes : "
            "reste prudent, fixe-toi un budget. 18+ · Aucun gain garanti." % (bons, len(suivis)))


# ---------- boucle ----------
def passage_auto():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    picks, faits = bt.charger(PICKS, {}), set(bt.charger(STATE, []))

    def publier(cle, texte):
        if cle in faits:
            return True
        if texte is None:
            faits.add(cle)
            return True
        if "18+" in texte and bt.envoyer(texte):
            faits.add(cle)
            print("envoyé :", cle)
            return True
        return False

    def lot(cle_finie, messages):
        if all([publier(c, t) for c, t in messages.items()]):
            faits.add(cle_finie)

    try:
        cle = "analyse-%s" % today
        if now.hour >= HEURE_ANALYSE and cle not in faits:
            lot(cle, analyses_du_jour(today.isoformat(), picks))
        veille = today - dt.timedelta(days=1)
        cle = "bilan-%s" % veille
        if now.hour >= HEURE_BILAN and cle not in faits:
            time.sleep(PAUSE)
            lot(cle, bilans(veille.isoformat(), picks))
        cle = "semaine-%s" % today
        if (today.weekday() == 6 or DEMO) and now.hour >= HEURE_SEMAINE and cle not in faits:
            publier(cle, bilan_semaine(picks, today))
    except Exception as e:
        print("Erreur automatique :", e)
    if not DEMO and not bt.DRY:
        json.dump(picks, open(PICKS, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(sorted(faits), open(STATE, "w", encoding="utf-8"))


if __name__ == "__main__":
    if not bt.DRY and not (bt.TOKEN and KEY):
        sys.exit("Il manque BOT_TOKEN ou FOOTBALL_DATA_KEY.")
    while True:
        bt.un_passage()
        passage_auto()
        if ONCE:
            break
        time.sleep(300)
