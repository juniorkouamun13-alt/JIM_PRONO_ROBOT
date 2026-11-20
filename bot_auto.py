import os, time, math, datetime as dt
import requests

API_KEY = os.environ.get("FOOTBALL_API_KEY") or os.environ.get("FOOTBALL_DATA_KEY")
TOKEN = os.environ.get("BOT_TOKEN")
missing = [n for n, v in [("FOOTBALL_API_KEY", API_KEY), ("BOT_TOKEN", TOKEN)] if not v]
if missing:
    raise SystemExit("Secrets manquants dans Settings > Secrets > Actions : " + ", ".join(missing))

H = {"X-Auth-Token": API_KEY}
BASE = "https://api.football-data.org/v4"
CHAT_FILE = "chat_id.txt"

COMPETITIONS = {
    "CL": "🏆 Ligue des champions", "FL1": "🇫🇷 Ligue 1",
    "BL1": "🇩🇪 Bundesliga", "PD": "🇪🇸 La Liga", "SA": "🇮🇹 Serie A",
    "PL": "🏴 Premier League", "ELC": "🏴 Championship",
    "PPL": "🇵🇹 Liga Portugal", "BSA": "🇧🇷 Brasileirão",
}

def get_chat_id():
    if os.path.exists(CHAT_FILE):
        v = open(CHAT_FILE).read().strip()
        if v:
            return v
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=30).json()
    for u in reversed(r.get("result", [])):
        for k in ("message", "channel_post", "my_chat_member"):
            if k in u:
                cid = str(u[k]["chat"]["id"])
                open(CHAT_FILE, "w").write(cid)
                return cid
    raise SystemExit("Chat introuvable : envoie /start à ton bot sur Telegram "
                     "(ou écris dans ton canal où le bot est admin), puis relance.")

CHAT_ID = get_chat_id()

def get(path, **params):
    for _ in range(3):
        r = requests.get(BASE + path, headers=H, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(60); continue
        r.raise_for_status()
        return r.json()
    return {}

def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)

def load_stats(code):
    stats = {}
    try:
        data = get(f"/competitions/{code}/standings")
    except Exception:
        return stats
    for table in data.get("standings", []):
        for row in table["table"]:
            stats.setdefault(row["team"]["id"], {})[table["type"]] = row
    return stats

def analyse(match, stats):
    h, a = match["homeTeam"]["id"], match["awayTeam"]["id"]
    hs, as_ = stats.get(h, {}), stats.get(a, {})
    try:
        hh, aa = hs["HOME"], as_["AWAY"]
        if hh["playedGames"] < 3 or aa["playedGames"] < 3:
            raise KeyError
    except KeyError:
        return "ℹ️ Pas assez de données pour analyser."
    xg_h = (hh["goalsFor"] / hh["playedGames"] + aa["goalsAgainst"] / aa["playedGames"]) / 2
    xg_a = (aa["goalsFor"] / aa["playedGames"] + hh["goalsAgainst"] / hh["playedGames"]) / 2
    p1 = px = p2 = over = btts = 0.0
    for i in range(9):
        for j in range(9):
            p = poisson(i, xg_h) * poisson(j, xg_a)
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
            if i + j > 2: over += p
            if i > 0 and j > 0: btts += p
    tot = p1 + px + p2
    p1, px, p2 = p1 / tot, px / tot, p2 / tot
    best = max((p1, "1 (domicile)"), (px, "N (nul)"), (p2, "2 (extérieur)"))[1]
    ht, at = hs.get("TOTAL", {}), as_.get("TOTAL", {})
    pos = f"Classement : {ht.get('position','?')}e vs {at.get('position','?')}e"
    return (f"{pos}\n"
            f"⚽ Buts attendus : {xg_h:.1f} - {xg_a:.1f}\n"
            f"📊 1: {p1:.0%} | N: {px:.0%} | 2: {p2:.0%}\n"
            f"🥅 +2,5 buts : {over:.0%} | BTTS : {btts:.0%}\n"
            f"👉 Tendance : {best}")

def send(text):
    for i in range(0, len(text), 4000):
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text[i:i+4000]}, timeout=30)

def main():
    now = dt.datetime.utcnow()
    today = now.date()
    tomorrow = today + dt.timedelta(days=1)
    data = get("/matches", dateFrom=today.isoformat(), dateTo=tomorrow.isoformat(),
               competitions=",".join(COMPETITIONS))
    start = today.isoformat() + "T00:00:00Z"
    limit = (now + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    matches = [m for m in data.get("matches", []) if start <= m["utcDate"] < limit]
    print("API :", len(data.get("matches", [])), "matchs reçus,", len(matches), "gardés")
    if not matches:
        send(f"📅 {today} : aucun match aujourd'hui dans tes compétitions.")
        return
    by_comp = {}
    for m in matches:
        by_comp.setdefault(m["competition"]["code"], []).append(m)
    send(f"📅 Analyses du {today} — {len(matches)} match(s)")
    for code, ms in by_comp.items():
        time.sleep(7)
        stats = load_stats(code)
        lines = [COMPETITIONS.get(code, code), ""]
        for m in ms:
            hour = m["utcDate"][11:16]
            lines.append(f"🕐 {hour} UTC — {m['homeTeam']['name']} vs {m['awayTeam']['name']}")
            lines.append(analyse(m, stats))
            lines.append("")
        send("\n".join(lines))

if __name__ == "__main__":
    main()
