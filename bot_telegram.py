"""Robot de publication pour la chaîne Telegram JIM || PRONO.

Il lit posts.json et publie chaque message à l'heure prévue (heure GMT).
Garde-fous : un message qui contient encore des [crochets] ou qui n'a pas
la mention « 18+ » n'est JAMAIS envoyé.

Utilisation :
  python bot_telegram.py --dry-run   -> test, rien n'est envoyé
  python bot_telegram.py --once      -> un seul passage (pour une tâche planifiée)
  python bot_telegram.py             -> tourne en continu
Le jeton du robot se donne par la variable BOT_TOKEN (jamais écrit dans le fichier).
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@JIMPRONO")
DRY = "--dry-run" in sys.argv
ONCE = DRY or "--once" in sys.argv
DIR = os.path.dirname(os.path.abspath(__file__))
POSTS = os.path.join(DIR, "posts.json")
SENT = os.path.join(DIR, "sent.json")
MAX_RETARD = dt.timedelta(hours=3)  # trop tard = on n'envoie plus


def charger(chemin, defaut):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defaut


def envoyer(texte):
    if DRY:
        print("[TEST] enverrait :\n" + texte + "\n")
        return True
    donnees = urllib.parse.urlencode(
        {"chat_id": CHANNEL, "text": texte, "disable_web_page_preview": "true"}
    ).encode()
    url = "https://api.telegram.org/bot" + TOKEN + "/sendMessage"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=donnees), timeout=20) as r:
            return bool(json.load(r).get("ok"))
    except Exception as e:
        print("Erreur d'envoi :", e)
        return False


def un_passage():
    posts = charger(POSTS, [])
    faits = set(charger(SENT, []))
    maintenant = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for p in posts:
        pid, texte = p["id"], p["text"]
        if pid in faits:
            continue
        heure = dt.datetime.fromisoformat(p["time"])
        if heure > maintenant:
            continue
        if maintenant - heure > MAX_RETARD:
            print(pid, ": heure dépassée de plus de 3 h, non envoyé")
            faits.add(pid)
            continue
        if "[" in texte or "]" in texte:
            print(pid, ": texte pas terminé (il reste des [crochets]), non envoyé")
            continue
        if "18+" not in texte:
            print(pid, ": la mention 18+ manque, non envoyé")
            continue
        if envoyer(texte):
            faits.add(pid)
            print("envoyé :", pid)
    if not DRY:
        with open(SENT, "w", encoding="utf-8") as f:
            json.dump(sorted(faits), f)


if __name__ == "__main__":
    if not DRY and not TOKEN:
        sys.exit("Il manque le jeton : définis la variable BOT_TOKEN.")
    while True:
        un_passage()
        if ONCE:
            break
        time.sleep(60)
