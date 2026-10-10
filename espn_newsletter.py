#!/usr/bin/env python3
"""
Weekly ESPN Fantasy Hockey newsletter generator.

Usage:
  python espn_newsletter.py                 # latest completed week, reads config.json
  python espn_newsletter.py --week 5        # a specific matchup period
  python espn_newsletter.py --note "text"   # commissioner's note for the letter
  python espn_newsletter.py --mock          # render with fake data (no ESPN needed)

config.json:
  {"league_id": 123456, "season": 2027, "espn_s2": "...", "swid": "{...}"}
  (espn_s2 / swid only needed for private leagues. Season = the year the season ENDS.)

Output: newsletter_week_N.html  (open in browser, or paste into an email / Gmail
"insert HTML" tool; all styles are inline for email clients).
"""
import argparse, html, json, random, sys
from collections import defaultdict
from pathlib import Path

API = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl/seasons/{season}"
       "/segments/0/leagues/{league_id}")


# ----------------------------------------------------------------- data fetch
def fetch(cfg):
    import requests
    cookies = {}
    if cfg.get("espn_s2") and cfg.get("swid"):
        cookies = {"espn_s2": cfg["espn_s2"], "SWID": cfg["swid"]}
    url = API.format(season=cfg["season"], league_id=cfg["league_id"])
    params = [("view", v) for v in
              ("mTeam", "mStandings", "mMatchup", "mMatchupScore", "mBoxscore", "mSettings")]
    r = requests.get(url, params=params, cookies=cookies, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def parse(data):
    """Normalise the raw ESPN payload into plain dicts."""
    divisions = {d["id"]: d["name"] for d in
                 data.get("settings", {}).get("scheduleSettings", {}).get("divisions", [])}
    teams = {}
    for t in data["teams"]:
        rec = t.get("record", {}).get("overall", {})
        teams[t["id"]] = {
            "id": t["id"],
            "name": t.get("name") or f'{t.get("location","")} {t.get("nickname","")}'.strip(),
            "owner": _owner(t, data),
            "division": divisions.get(t.get("divisionId"), "League"),
            "w": rec.get("wins", 0), "l": rec.get("losses", 0), "t": rec.get("ties", 0),
            "pf": rec.get("pointsFor", 0.0), "pa": rec.get("pointsAgainst", 0.0),
            "rank": t.get("playoffSeed") or 0,
        }
    games = []
    for m in data.get("schedule", []):
        if "away" not in m:            # bye
            continue
        side = lambda s: {"team": m[s]["teamId"], "pts": m[s].get("totalPoints", 0.0),
                          "players": _players(m[s])}
        games.append({"week": m["matchupPeriodId"], "home": side("home"), "away": side("away")})
    current = data.get("status", {}).get("currentMatchupPeriod", 1)
    return teams, games, current


def _owner(t, data):
    oid = (t.get("owners") or [None])[0]
    for mem in data.get("members", []):
        if mem.get("id") == oid:
            return f'{mem.get("firstName","")} {mem.get("lastName","")}'.strip()
    return ""


def _players(side):
    out = []
    roster = side.get("rosterForCurrentScoringPeriod") or side.get("rosterForMatchupPeriod") or {}
    for e in roster.get("entries", []):
        p = e.get("playerPoolEntry", {})
        pts = p.get("appliedStatTotal")
        if pts is None:
            continue
        out.append({"name": p.get("player", {}).get("fullName", "?"), "pts": pts,
                    "slot": e.get("lineupSlotId")})
    return out


# ------------------------------------------------------------------ analysis
def played(games, week):
    return [g for g in games if g["week"] <= week and (g["home"]["pts"] or g["away"]["pts"])]


def analyse(teams, games, week):
    scores = defaultdict(dict)                    # team -> {week: pts}
    for g in played(games, week):
        scores[g["home"]["team"]][g["week"]] = g["home"]["pts"]
        scores[g["away"]["team"]][g["week"]] = g["away"]["pts"]

    # all-play record (beat every team's score each week) -> separates luck from skill
    allplay = {t: [0, 0] for t in teams}
    for wk in range(1, week + 1):
        wk_scores = {t: s[wk] for t, s in scores.items() if wk in s}
        for t, s in wk_scores.items():
            for o, os_ in wk_scores.items():
                if o != t:
                    allplay[t][0 if s > os_ else 1] += (1 if s != os_ else 0)

    # power score: 50% all-play, 30% last-3-weeks scoring, 20% record
    recent = {t: _avg([s[w] for w in sorted(s)[-3:]]) for t, s in scores.items()}
    hi, lo = max(recent.values(), default=1), min(recent.values(), default=0)
    power = {}
    for t, tm in teams.items():
        gp = tm["w"] + tm["l"] + tm["t"] or 1
        ap = allplay[t][0] / ((sum(allplay[t])) or 1)
        rc = (recent.get(t, lo) - lo) / ((hi - lo) or 1)
        power[t] = 0.5 * ap + 0.3 * rc + 0.2 * (tm["w"] + tm["t"] / 2) / gp
    return scores, allplay, power


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------------- HTML
NAVY, ICE, RED, GREY = "#0b2545", "#8da9c4", "#c8102e", "#555"
FONT = "font-family:'Helvetica Neue',Arial,sans-serif;"


def h(s):
    return html.escape(str(s))


def section(title, body):
    return (f'<tr><td style="padding:22px 28px 6px;{FONT}">'
            f'<div style="font-size:13px;letter-spacing:2px;color:{RED};font-weight:700">{h(title.upper())}</div>'
            f'<div style="height:2px;background:{NAVY};margin:6px 0 12px"></div>{body}</td></tr>')


def table(headers, rows, align=None):
    align = align or ["left"] + ["right"] * (len(headers) - 1)
    th = "".join(f'<th style="text-align:{a};padding:6px 8px;font-size:12px;color:#fff;background:{NAVY}">{h(c)}</th>'
                 for c, a in zip(headers, align))
    trs = ""
    for i, r in enumerate(rows):
        bg = "#f3f6fa" if i % 2 else "#fff"
        tds = "".join(f'<td style="text-align:{a};padding:6px 8px;font-size:13px;border-bottom:1px solid #e4e9f0">{c}</td>'
                      for c, a in zip(r, align))
        trs += f'<tr style="background:{bg}">{tds}</tr>'
    return f'<table width="100%" cellspacing="0" cellpadding="0" style="{FONT}border-collapse:collapse"><tr>{th}</tr>{trs}</table>'


def build(teams, games, week, note=None, season_label="", logo=""):
    scores, allplay, power = analyse(teams, games, week)
    name = lambda t: h(teams[t]["name"])
    wk_games = [g for g in games if g["week"] == week]
    parts = []

    # Commissioner's note
    if note:
        parts.append(section("From the Commissioner", f'<p style="font-size:14px;line-height:1.5;margin:0">{h(note)}</p>'))

    # Scoreboard
    rows = []
    for g in sorted(wk_games, key=lambda g: -abs(g["home"]["pts"] - g["away"]["pts"])):
        a, b = g["home"], g["away"]
        w, l = (a, b) if a["pts"] >= b["pts"] else (b, a)
        rows.append([f'<b>{name(w["team"])}</b>', f'<b>{w["pts"]:.1f}</b>',
                     name(l["team"]), f'{l["pts"]:.1f}', f'{w["pts"]-l["pts"]:.1f}'])
    parts.append(section(f"Week {week} Scoreboard",
                         table(["Winner", "Pts", "Loser", "Pts", "Margin"], rows,
                               ["left", "right", "left", "right", "right"])))

    # Headlines: team of the week, closest game, biggest blowout, top player
    sides = [(s["pts"], s["team"]) for g in wk_games for s in (g["home"], g["away"])]
    best = max(sides)
    worst = min(sides)
    close = min(wk_games, key=lambda g: abs(g["home"]["pts"] - g["away"]["pts"]))
    blow = max(wk_games, key=lambda g: abs(g["home"]["pts"] - g["away"]["pts"]))
    pl = [(p["pts"], p["name"], s["team"]) for g in wk_games for s in (g["home"], g["away"])
          for p in s["players"]]
    items = [
        ("Team of the Week", f'{name(best[1])} led the league with <b>{best[0]:.1f}</b>.'),
        ("Closest Finish", f'{name(close["home"]["team"])} vs {name(close["away"]["team"])} '
                           f'was decided by <b>{abs(close["home"]["pts"]-close["away"]["pts"]):.1f}</b>.'),
        ("Biggest Blowout", f'{name(blow["home"]["team"])} vs {name(blow["away"]["team"])}: '
                            f'<b>{abs(blow["home"]["pts"]-blow["away"]["pts"]):.1f}</b>-point margin.'),
        ("Low Score", f'{name(worst[1])} managed just <b>{worst[0]:.1f}</b>.'),
    ]
    if pl:
        top = max(pl)
        items.append(("Player of the Week", f'{h(top[1])} ({name(top[2])}) put up <b>{top[0]:.1f}</b>.'))
    body = "".join(f'<p style="font-size:14px;line-height:1.45;margin:0 0 8px"><b>{h(t)}:</b> {d}</p>' for t, d in items)
    parts.append(section("Week in Review", body))

    # Top performers
    if pl:
        rows = [[h(n), name(t), f"{p:.1f}"] for p, n, t in sorted(pl, reverse=True)[:10]]
        parts.append(section("Top 10 Performers", table(["Player", "Team", "Pts"], rows, ["left", "left", "right"])))

    # Power rankings
    prev = None
    if week > 1:
        _, _, prev_power = analyse(teams, [g for g in games if g["week"] < week], week - 1)
        prev = {t: i + 1 for i, (t, _) in enumerate(sorted(prev_power.items(), key=lambda x: -x[1]))}
    rows = []
    for i, (t, sc) in enumerate(sorted(power.items(), key=lambda x: -x[1]), 1):
        move = ""
        if prev:
            d = prev[t] - i
            move = (f'<span style="color:#1a7f37">▲{d}</span>' if d > 0 else
                    f'<span style="color:{RED}">▼{-d}</span>' if d < 0 else "–")
        tm = teams[t]
        rows.append([i, f'<b>{name(t)}</b>', f'{tm["w"]}-{tm["l"]}-{tm["t"]}',
                     f'{allplay[t][0]}-{allplay[t][1]}', move])
    parts.append(section("Power Rankings",
                         table(["#", "Team", "Record", "All-Play", "Move"], rows,
                               ["left", "left", "center", "center", "center"]) +
                         f'<p style="font-size:11px;color:{GREY};margin:8px 0 0">Formula: 50% all-play record, '
                         f'30% last-3-week scoring, 20% actual record.</p>'))

    # Standings by division
    divs = defaultdict(list)
    for t in teams.values():
        divs[t["division"]].append(t)
    block = ""
    for d, ts in sorted(divs.items()):
        ts.sort(key=lambda t: (-t["w"], -t["pf"]))
        rows = [[f'{i}. {h(t["name"])}', f'{t["w"]}-{t["l"]}-{t["t"]}', f'{t["pf"]:.1f}', f'{t["pa"]:.1f}']
                for i, t in enumerate(ts, 1)]
        block += (f'<div style="font-size:13px;font-weight:700;margin:12px 0 4px;color:{NAVY}">{h(d)}</div>'
                  + table(["Team", "W-L-T", "PF", "PA"], rows))
    parts.append(section("Division Standings", block))

    # Luck report
    rows = []
    for t, tm in teams.items():
        gp = tm["w"] + tm["l"] + tm["t"] or 1
        ap_w, ap_l = allplay[t]
        exp = ap_w / ((ap_w + ap_l) or 1) * gp
        rows.append((tm["w"] - exp, t, exp))
    rows.sort(reverse=True)
    fmt = lambda r: [name(r[1]), str(teams[r[1]]["w"]), f"{r[2]:.1f}", f"{r[0]:+.1f}"]
    parts.append(section("Luck Report",
                         table(["Team", "Wins", "Expected", "Luck"], [fmt(r) for r in rows[:3] + rows[-3:]]) +
                         f'<p style="font-size:11px;color:{GREY};margin:8px 0 0">Expected wins = all-play win % × games played. '
                         f'Top 3 luckiest and bottom 3 unluckiest shown.</p>'))

    # Next week
    nxt = [g for g in games if g["week"] == week + 1]
    if nxt:
        rows = []
        for g in nxt:
            a, b = g["home"]["team"], g["away"]["team"]
            ra, rb = (power[a], power[b])
            fav = a if ra >= rb else b
            rows.append([name(a), name(b), name(fav)])
        parts.append(section(f"Week {week+1} Matchups",
                             table(["Home", "Away", "Power-Rank Edge"], rows, ["left", "left", "left"])))

    title = f"Week {week} Newsletter"
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>'
            f'<body style="margin:0;background:#e9eef4"><table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">'
            f'<table width="640" cellspacing="0" cellpadding="0" style="background:#fff;max-width:100%">'
            f'<tr><td style="background:{NAVY};padding:20px 28px;{FONT}">'
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td width="96" valign="middle">{logo}</td>'
            f'<td valign="middle" style="padding-left:{16 if logo else 0}px">'
            f'<div style="color:{ICE};font-size:12px;letter-spacing:3px">{h(season_label)}</div>'
            f'<div style="color:#fff;font-size:28px;font-weight:800;margin-top:4px">Week {week} Recap</div>'
            f'</td></tr></table></td></tr>'
            + "".join(parts) +
            f'<tr><td style="padding:20px 28px;{FONT}font-size:11px;color:{GREY}">Generated from ESPN Fantasy data.</td></tr>'
            f'</table></td></tr></table></body></html>')


# ---------------------------------------------------------------------- mock
def mock():
    names = ["Austin Angels", "Briar U Bottoms", "Carry the Flames", "Foxyclean Styles",
             "Elevated Cupcake Experience", "Albanian Mob", "Jaws on Ice", "Beginner's Puck",
             "What Does the Fox Say", "Jimmy's Puck Shack", "Indy Slashers", "Quinn's Tremendous Team"]
    divs = ["Manhattan", "Queens", "Brooklyn"]
    random.seed(7)
    teams = {i + 1: {"id": i + 1, "name": n, "owner": "", "division": divs[i // 4],
                     "w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "rank": 0}
             for i, n in enumerate(names)}
    games = []
    ids = list(teams)
    for wk in range(1, 6):
        random.shuffle(ids)
        for i in range(0, 12, 2):
            row = {}
            for side, tid in (("home", ids[i]), ("away", ids[i + 1])):
                pl = [{"name": f"Player {tid}-{k}", "pts": round(random.uniform(5, 45), 1), "slot": k}
                      for k in range(8)]
                row[side] = {"team": tid, "pts": round(sum(p["pts"] for p in pl) + random.uniform(150, 250), 1),
                             "players": pl}
            games.append({"week": wk, **row})
    for g in games:
        if g["week"] > 4:
            continue
        a, b = g["home"], g["away"]
        for s, o in ((a, b), (b, a)):
            t = teams[s["team"]]
            t["pf"] += s["pts"]; t["pa"] += o["pts"]
            t["w" if s["pts"] > o["pts"] else "l"] += 1
    return teams, games, 5


def load_logo(cfg, height=96):
    """logo_url in config wins (needed for Gmail, which strips embedded images);
    otherwise embed logo.png (or logo.svg) from next to the script."""
    if cfg.get("logo_url"):
        return f'<img src="{h(cfg["logo_url"])}" height="{height}" alt="logo" style="display:block;height:{height}px;width:auto">'
    here = Path(__file__).parent
    png = here / "logo.png"
    if png.exists():
        import base64
        data = base64.b64encode(png.read_bytes()).decode()
        return (f'<img src="data:image/png;base64,{data}" height="{height}" alt="logo" '
                f'style="display:block;height:{height}px;width:auto">')
    svg = here / "logo.svg"
    if svg.exists():
        return svg.read_text().replace("<svg ", f'<svg height="{height}" ', 1)
    return ""


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--week", type=int)
    ap.add_argument("--note")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()

    if a.mock:
        teams, games, current = mock()
        label, logo = "NYCHL - MOCK DATA", load_logo({})
    else:
        cfg = json.loads(Path(a.config).read_text())
        teams, games, current = parse(fetch(cfg))
        label = f'{cfg.get("league_name", "FANTASY HOCKEY")} - {cfg["season"]-1}-{str(cfg["season"])[2:]}'
        logo = load_logo(cfg)
    week = a.week or max(1, current - 1)
    out = Path(f"newsletter_week_{week}.html")
    out.write_text(build(teams, games, week, a.note, label.upper(), logo), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
