import os
import math
import difflib
import re
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.api-tennis.com/tennis/"

st.set_page_config(
    page_title="BAZI Tennis V5.0 RELIABLE",
    page_icon="🎾",
    layout="wide",
)

# -----------------------------
# API KEY
# -----------------------------
def get_api_key():
    # 1) Streamlit secrets
    try:
        key = st.secrets.get("API_TENNIS_KEY", "")
        if key:
            return key
    except Exception:
        pass

    # 2) Environment variable
    key = os.getenv("API_TENNIS_KEY", "")
    if key:
        return key

    # 3) Temporary session input
    return st.session_state.get("api_tennis_key", "")


# -----------------------------
# API HELPERS
# -----------------------------
def api_call(method, **params):
    key = get_api_key()
    if not key:
        raise RuntimeError("API-Tennis key is missing.")

    payload = {
        "method": method,
        "APIkey": key,
        **params,
    }

    r = requests.get(API_BASE, params=payload, timeout=30)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected API-Tennis response.")

    if int(data.get("success", 0)) != 1:
        raise RuntimeError(str(data.get("error") or data))

    return data.get("result", [])


@st.cache_data(ttl=45, show_spinner=False)
def get_fixtures(day_string):
    return api_call(
        "get_fixtures",
        date_start=day_string,
        date_stop=day_string,
        timezone="America/Los_Angeles",
    )


@st.cache_data(ttl=15, show_spinner=False)
def get_live():
    return api_call(
        "get_livescore",
        timezone="America/Los_Angeles",
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_player(player_key):
    if not player_key:
        return None
    result = api_call("get_players", player_key=str(player_key))
    if isinstance(result, list) and result:
        return result[0]
    return None


@st.cache_data(ttl=300, show_spinner=False)
def get_h2h(first_key, second_key):
    if not first_key or not second_key:
        return {}
    result = api_call(
        "get_H2H",
        first_player_key=str(first_key),
        second_player_key=str(second_key),
    )
    return result if isinstance(result, dict) else {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_player_current_season_fixtures(player_key):
    """Fetch current-season completed singles for one player; cache for 1 hour."""
    if not player_key:
        return []

    year = date.today().year
    rows = api_call(
        "get_fixtures",
        date_start=f"{year}-01-01",
        date_stop=date.today().isoformat(),
        player_key=str(player_key),
        timezone="America/Los_Angeles",
    )

    out = []
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict):
            continue
        if "singles" not in str(m.get("event_type_type", "")).lower():
            continue
        if str(m.get("event_status", "")).strip().lower() != "finished":
            continue
        if not m.get("event_winner"):
            continue
        out.append(m)
    return out


def _safe_pct(w, n):
    return (100.0 * w / n) if n else None


def _season_fixture_profile(player_key):
    """Build current-season Set 1/2/3 and transition stats from completed API fixtures."""
    fixtures = get_player_current_season_fixtures(player_key)
    pkey = str(player_key)

    match_w = match_n = 0
    set_w = [0, 0, 0]
    set_n = [0, 0, 0]
    after_w1_w = after_w1_n = 0
    s2_w1_w = s2_w1_n = 0
    s2_l1_w = s2_l1_n = 0
    s3_w2_w = s3_w2_n = 0
    s3_l2_w = s3_l2_n = 0
    tb_w = tb_n = 0
    recent = []

    for m in fixtures:
        first = str(m.get("first_player_key", ""))
        second = str(m.get("second_player_key", ""))
        winner = str(m.get("event_winner", ""))

        is_first = first == pkey
        if not (is_first or second == pkey):
            continue

        won_match = (
            (is_first and winner == "First Player")
            or ((not is_first) and winner == "Second Player")
        )
        match_n += 1
        match_w += int(won_match)

        outcomes = []
        for s in m.get("scores", []) if isinstance(m.get("scores"), list) else []:
            try:
                a = int(float(s.get("score_first")))
                b = int(float(s.get("score_second")))
            except Exception:
                continue

            pg, og = (a, b) if is_first else (b, a)
            won_set = pg > og
            tb = (max(pg, og) == 7 and min(pg, og) == 6)
            outcomes.append((won_set, tb))

        for i in range(min(3, len(outcomes))):
            set_n[i] += 1
            set_w[i] += int(outcomes[i][0])
            if outcomes[i][1]:
                tb_n += 1
                tb_w += int(outcomes[i][0])

        if len(outcomes) >= 1 and outcomes[0][0]:
            after_w1_n += 1
            after_w1_w += int(won_match)

        if len(outcomes) >= 2:
            if outcomes[0][0]:
                s2_w1_n += 1
                s2_w1_w += int(outcomes[1][0])
            else:
                s2_l1_n += 1
                s2_l1_w += int(outcomes[1][0])

        if len(outcomes) >= 3:
            if outcomes[1][0]:
                s3_w2_n += 1
                s3_w2_w += int(outcomes[2][0])
            else:
                s3_l2_n += 1
                s3_l2_w += int(outcomes[2][0])

        opponent = (
            m.get("event_second_player", "Opponent")
            if is_first else m.get("event_first_player", "Opponent")
        )
        recent.append({
            "date": m.get("event_date", ""),
            "result": "W" if won_match else "L",
            "score": m.get("event_final_result", "—"),
            "opponent": opponent,
        })

    recent.sort(key=lambda x: str(x.get("date", "")), reverse=True)

    def t(w, n):
        return (_safe_pct(w, n), n)

    return {
        "matches": match_n,
        "win_rate": t(match_w, match_n),
        "set1": t(set_w[0], set_n[0]),
        "set2": t(set_w[1], set_n[1]),
        "set3": t(set_w[2], set_n[2]),
        "after_winning_set1": t(after_w1_w, after_w1_n),
        "set2_after_w1": t(s2_w1_w, s2_w1_n),
        "set2_after_l1": t(s2_l1_w, s2_l1_n),
        "set3_after_w2": t(s3_w2_w, s3_w2_n),
        "set3_after_l2": t(s3_l2_w, s3_l2_n),
        "tiebreak": t(tb_w, tb_n),
        "last10": recent[:10],
    }


def _shrunk_pct(value_tuple, prior_n=8.0):
    """Shrink tiny samples toward 50%; 100% (1) no longer acts like certainty."""
    if not isinstance(value_tuple, tuple):
        return None, 0
    pct, n = value_tuple
    n = int(n or 0)
    if pct is None or n <= 0:
        return None, n
    wins = (float(pct) / 100.0) * n
    adjusted = 100.0 * (wins + 0.5 * prior_n) / (n + prior_n)
    return adjusted, n


def _pairwise_rate(a_pct, b_pct):
    if a_pct is None or b_pct is None:
        return 0.5
    a = max(1.0, min(99.0, float(a_pct))) / 100.0
    b = max(1.0, min(99.0, float(b_pct))) / 100.0
    return a / (a + b)


def _rank_pair_probability(rank1, rank2):
    try:
        r1 = max(1.0, float(rank1))
        r2 = max(1.0, float(rank2))
    except Exception:
        return 0.5
    return 1.0 / (1.0 + (r1 / r2) ** 0.60)


def _reliability_grade(current_n, hist_n, h2h_n):
    score = (
        min(current_n, 20) / 20 * 50
        + min(hist_n, 50) / 50 * 35
        + min(h2h_n, 5) / 5 * 15
    )
    if score >= 80:
        return "A", score
    if score >= 65:
        return "B", score
    if score >= 50:
        return "C", score
    return "D", score


# -----------------------------
# DATA NORMALIZATION
# -----------------------------
def as_list(value):
    return value if isinstance(value, list) else []


def is_singles(match):
    return "singles" in str(match.get("event_type_type", "")).lower()


def match_state(match):
    status = str(match.get("event_status", "") or "").strip().lower()
    winner = match.get("event_winner")

    # Finished/terminal states override a stale event_live=1 flag.
    if winner or status in {
        "finished",
        "retired",
        "walkover",
        "cancelled",
        "canceled",
        "abandoned",
        "interrupted",
    }:
        return "COMPLETED"

    if str(match.get("event_live", "0")) == "1":
        return "LIVE"

    if status.startswith("set ") or status.startswith("game "):
        return "LIVE"

    return "UPCOMING"


def score_text(match):
    final_result = str(match.get("event_final_result", "") or "").strip()
    game_result = str(match.get("event_game_result", "") or "").strip()

    if final_result and final_result != "-":
        if game_result and game_result != "-":
            return f"{final_result}  | games {game_result}"
        return final_result

    scores = as_list(match.get("scores"))
    if scores:
        pieces = []
        for s in scores:
            a = s.get("score_first", "")
            b = s.get("score_second", "")
            if a != "" or b != "":
                pieces.append(f"{a}-{b}")
        if pieces:
            return ", ".join(pieces)

    return "—"


def display_name(match):
    p1 = match.get("event_first_player") or "Player 1"
    p2 = match.get("event_second_player") or "Player 2"
    return f"{p1} vs {p2}"


def merge_live_into_fixtures(fixtures, live_matches):
    by_key = {}
    for m in fixtures:
        if isinstance(m, dict):
            by_key[str(m.get("event_key"))] = m

    for live in live_matches:
        if not isinstance(live, dict):
            continue
        key = str(live.get("event_key"))
        if key in by_key:
            by_key[key] = {**by_key[key], **live}
        else:
            by_key[key] = live

    return list(by_key.values())


def serving_player_name(match):
    serve = str(match.get("event_serve", "") or "")
    if serve == "First Player":
        return match.get("event_first_player") or "First Player"
    if serve == "Second Player":
        return match.get("event_second_player") or "Second Player"
    return "—"


def live_statistics_table(match):
    stats = as_list(match.get("statistics"))
    p1_key = str(match.get("first_player_key", ""))
    p2_key = str(match.get("second_player_key", ""))
    p1_name = match.get("event_first_player") or "Player 1"
    p2_name = match.get("event_second_player") or "Player 2"

    preferred = [
        "Aces",
        "Double Faults",
        "1st Serve",
        "1st serve points won",
        "2nd serve points won",
        "Break Points Won",
        "Break points won",
        "Total Points Won",
        "Service Points Won",
        "Return Points Won",
        "Games Won",
    ]

    by_name = {}
    for row in stats:
        if not isinstance(row, dict):
            continue
        name = str(row.get("stat_name", "") or "").strip()
        if not name:
            continue

        player_key = str(row.get("player_key", "") or "")
        value = row.get("stat_value")
        won = row.get("stat_won")
        total = row.get("stat_total")

        if (value is None or str(value) == "") and won is not None and total is not None:
            value = f"{won}/{total}"

        entry = by_name.setdefault(name, {p1_name: "—", p2_name: "—"})
        if player_key == p1_key:
            entry[p1_name] = value if value not in (None, "") else "—"
        elif player_key == p2_key:
            entry[p2_name] = value if value not in (None, "") else "—"

    if not by_name:
        return pd.DataFrame()

    lower_map = {k.lower(): k for k in by_name}
    ordered_names = []
    for wanted in preferred:
        actual = lower_map.get(wanted.lower())
        if actual and actual not in ordered_names:
            ordered_names.append(actual)
    for name in by_name:
        if name not in ordered_names:
            ordered_names.append(name)

    return pd.DataFrame([
        {
            "Stat": name,
            p1_name: by_name[name][p1_name],
            p2_name: by_name[name][p2_name],
        }
        for name in ordered_names
    ])


def recent_point_by_point(match, limit_games=6):
    games = as_list(match.get("pointbypoint"))
    rows = []

    for game in games[-limit_games:]:
        if not isinstance(game, dict):
            continue

        server = str(game.get("player_served", "") or "")
        winner = str(game.get("serve_winner", "") or "")

        if server == "First Player":
            server = match.get("event_first_player") or server
        elif server == "Second Player":
            server = match.get("event_second_player") or server

        if winner == "First Player":
            winner = match.get("event_first_player") or winner
        elif winner == "Second Player":
            winner = match.get("event_second_player") or winner

        rows.append({
            "Set": game.get("set_number", "—"),
            "Game": game.get("number_game", "—"),
            "Score": game.get("score", "—"),
            "Server": server or "—",
            "Game winner": winner or "—",
        })

    return rows



def color_comparison_table(df, metric_col):
    """Green = better, red = worse; only value text is colored."""
    lower_is_better_terms = ("double fault", "losses", "errors", "lost")

    def number(v):
        if v is None:
            return None
        s = str(v).strip().replace("%", "").replace(",", "")
        if s in {"", "—", "-", "N/A", "None"}:
            return None
        if "/" in s:
            try:
                a, b = s.split("/", 1)
                b = float(b)
                return float(a) / b if b else None
            except Exception:
                return None
        try:
            return float(s)
        except Exception:
            return None

    def row_style(row):
        styles = pd.Series("", index=row.index)
        cols = [c for c in row.index if c != metric_col]
        if len(cols) != 2:
            return styles

        a = number(row[cols[0]])
        b = number(row[cols[1]])
        if a is None or b is None or a == b:
            return styles

        metric = str(row.get(metric_col, "")).lower()
        lower_better = metric == "rank" or any(x in metric for x in lower_is_better_terms)

        first_is_better = a < b if lower_better else a > b
        good = cols[0] if first_is_better else cols[1]
        bad = cols[1] if first_is_better else cols[0]

        styles[good] = "color: #32d583; font-weight: 800;"
        styles[bad] = "color: #ff5c5c; font-weight: 800;"
        return styles

    return df.style.apply(row_style, axis=1)


def _to_num(v):
    try:
        return float(str(v).replace("%", "").strip())
    except Exception:
        return None


def _set_and_game_state(match):
    """Estimate sets won and total games from API-Tennis set score rows."""
    p1_sets = p2_sets = 0
    p1_games = p2_games = 0

    for s in as_list(match.get("scores")):
        if not isinstance(s, dict):
            continue

        a = _to_num(s.get("score_first"))
        b = _to_num(s.get("score_second"))
        if a is None or b is None:
            continue

        a = int(a)
        b = int(b)
        p1_games += a
        p2_games += b

        # Completed-set test, including normal tiebreak sets.
        completed = (
            (max(a, b) >= 6 and abs(a - b) >= 2)
            or (max(a, b) >= 7 and abs(a - b) >= 1)
        )
        if completed:
            if a > b:
                p1_sets += 1
            elif b > a:
                p2_sets += 1

    return p1_sets, p2_sets, p1_games, p2_games



def _current_set_score(match):
    """
    Return only the current set's game score from API-Tennis scores.
    This avoids summing games from earlier completed sets.
    """
    scores = as_list(match.get("scores"))
    if not scores:
        return (0, 0)

    for s in reversed(scores):
        if not isinstance(s, dict):
            continue
        try:
            a = int(float(s.get("score_first")))
            b = int(float(s.get("score_second")))
            return (a, b)
        except Exception:
            continue

    return (0, 0)


def _live_stat_advantage(match):
    """Return a bounded live-performance signal in roughly [-1, 1]."""
    stats = as_list(match.get("statistics"))
    if not stats:
        return 0.0

    p1_key = str(match.get("first_player_key", ""))
    p2_key = str(match.get("second_player_key", ""))

    # Weight only stats that are useful in-match and reasonably comparable.
    weights = {
        "total points won": 1.00,
        "last 10 balls": 0.90,
        "service games won": 0.60,
        "return games won": 0.70,
        "break points converted": 0.80,
        "break points won": 0.80,
        "1st serve points won": 0.50,
        "2nd serve points won": 0.50,
        "1st return points won": 0.50,
        "2nd return points won": 0.50,
        "aces": 0.20,
        "double faults": -0.20,  # lower is better
    }

    paired = {}
    for row in stats:
        if not isinstance(row, dict):
            continue

        name = str(row.get("stat_name", "") or "").strip().lower()
        if name not in weights:
            continue

        value = row.get("stat_value")
        if value in (None, ""):
            won = row.get("stat_won")
            total = row.get("stat_total")
            if won not in (None, "") and total not in (None, "", 0, "0"):
                try:
                    value = 100.0 * float(won) / float(total)
                except Exception:
                    value = None

        val = _to_num(value)
        if val is None:
            continue

        pk = str(row.get("player_key", "") or "")
        item = paired.setdefault(name, {})
        if pk == p1_key:
            item["p1"] = val
        elif pk == p2_key:
            item["p2"] = val

    weighted_sum = 0.0
    total_weight = 0.0

    for name, vals in paired.items():
        if "p1" not in vals or "p2" not in vals:
            continue

        a, b = vals["p1"], vals["p2"]
        scale = max(abs(a), abs(b), 1.0)
        diff = (a - b) / scale

        w = weights[name]
        if w < 0:
            diff = -diff
            w = abs(w)

        weighted_sum += w * max(-1.0, min(1.0, diff))
        total_weight += w

    if total_weight == 0:
        return 0.0

    return max(-1.0, min(1.0, weighted_sum / total_weight))


def _historical_situation_edge(match):
    """Situation-aware 2024-2025 historical signal. Positive favors Player 1."""
    try:
        hist = load_historical_matches()
        if hist.empty:
            return 0.0, {}

        p1 = match.get("event_first_player") or "Player 1"
        p2 = match.get("event_second_player") or "Player 2"
        surface = current_surface_name(match)

        hp1 = historical_profile(hist, p1, surface)
        hp2 = historical_profile(hist, p2, surface)

        p1_sets, p2_sets, _, _ = _set_and_game_state(match)
        current_set = p1_sets + p2_sets + 1

        def pct(profile, key):
            value = profile.get(key)
            return value[0] if isinstance(value, tuple) else None

        metrics = [
            ("surface", pct(hp1, "surface"), pct(hp2, "surface"), 0.15),
            ("overall", pct(hp1, "win_rate"), pct(hp2, "win_rate"), 0.10),
            ("tiebreak", pct(hp1, "tiebreak"), pct(hp2, "tiebreak"), 0.10),
        ]

        if current_set <= 1:
            metrics.append(("set1", pct(hp1, "set1"), pct(hp2, "set1"), 0.30))

        elif current_set == 2:
            metrics.append(("set2", pct(hp1, "set2"), pct(hp2, "set2"), 0.30))

            if p1_sets > p2_sets:
                metrics.append(("p1_after_winning_set1", pct(hp1, "set2_after_w1"), 50.0, 0.20))
                metrics.append(("p2_after_losing_set1", 50.0, pct(hp2, "set2_after_l1"), 0.20))
            elif p2_sets > p1_sets:
                metrics.append(("p1_after_losing_set1", pct(hp1, "set2_after_l1"), 50.0, 0.20))
                metrics.append(("p2_after_winning_set1", 50.0, pct(hp2, "set2_after_w1"), 0.20))

        else:
            # Deciding set: this is where Set-3 history matters heavily.
            metrics.append(("set3", pct(hp1, "set3"), pct(hp2, "set3"), 0.50))

            completed_sets = []
            for s in as_list(match.get("scores")):
                try:
                    a = int(float(s.get("score_first", 0)))
                    b = int(float(s.get("score_second", 0)))
                except Exception:
                    continue

                completed = (
                    (max(a, b) >= 6 and abs(a - b) >= 2)
                    or (max(a, b) >= 7 and abs(a - b) >= 1)
                )
                if completed:
                    completed_sets.append((a, b))

            # Set 2 winner/loser history becomes specifically relevant for Set 3.
            if len(completed_sets) >= 2:
                a2, b2 = completed_sets[1]
                if a2 > b2:
                    metrics.append(("p1_set3_after_winning_set2", pct(hp1, "set3_after_w2"), 50.0, 0.35))
                    metrics.append(("p2_set3_after_losing_set2", 50.0, pct(hp2, "set3_after_l2"), 0.35))
                elif b2 > a2:
                    metrics.append(("p1_set3_after_losing_set2", pct(hp1, "set3_after_l2"), 50.0, 0.35))
                    metrics.append(("p2_set3_after_winning_set2", 50.0, pct(hp2, "set3_after_w2"), 0.35))

        weighted = 0.0
        total_weight = 0.0
        details = {}

        for name, a, b, weight in metrics:
            if a is None or b is None:
                continue

            # A 50 percentage-point gap maps to a full +/-1 signal.
            edge = max(-1.0, min(1.0, (float(a) - float(b)) / 50.0))
            weighted += weight * edge
            total_weight += weight
            details[name] = {
                "p1": float(a),
                "p2": float(b),
                "weight": weight,
                "edge": edge,
            }

        if total_weight == 0:
            return 0.0, details

        return max(-1.0, min(1.0, weighted / total_weight)), details

    except Exception:
        return 0.0, {}


def live_adjusted_prediction(match, base_result):
    """
    Situation-aware LIVE BAZI.

    As the match gets deeper, live stats and the current-set historical profile
    receive more weight while the pre-match prior receives less.
    """
    base_p = float(base_result["p1_prob"])
    base_p = min(0.98, max(0.02, base_p))
    base_logit = math.log(base_p / (1.0 - base_p))

    p1_sets, p2_sets, _, _ = _set_and_game_state(match)
    current_set = p1_sets + p2_sets + 1
    current_p1_games, current_p2_games = _current_set_score(match)

    set_diff = p1_sets - p2_sets
    game_diff = current_p1_games - current_p2_games
    stat_adv = _live_stat_advantage(match)
    hist_edge, hist_details = _historical_situation_edge(match)

    serve = str(match.get("event_serve", "") or "")
    serve_signal = (
        0.06 if serve == "First Player"
        else -0.06 if serve == "Second Player"
        else 0.0
    )

    if current_set <= 1:
        prior_weight = 0.75
        set_weight = 0.70
        game_weight = 0.30
        stat_weight = 0.45
        history_weight = 0.30

    elif current_set == 2:
        prior_weight = 0.55
        set_weight = 0.95
        game_weight = 0.45
        stat_weight = 0.70
        history_weight = 0.45

    else:
        # Deciding set: today's live match + Set-3 history dominate.
        prior_weight = 0.25
        set_weight = 0.80
        game_weight = 0.75
        stat_weight = 1.05
        history_weight = 0.75

    # Tight late Set 3: reduce ranking/pre-match influence even more.
    if current_set >= 3 and abs(game_diff) <= 2 and max(current_p1_games, current_p2_games) >= 4:
        prior_weight = 0.15
        stat_weight = 1.20
        history_weight = 0.85

    set_signal = set_weight * set_diff
    game_signal = max(-0.95, min(0.95, game_weight * 0.10 * game_diff))
    stat_signal = stat_weight * stat_adv
    history_signal = history_weight * hist_edge

    live_logit = (
        prior_weight * base_logit
        + set_signal
        + game_signal
        + stat_signal
        + history_signal
        + serve_signal
    )

    p1_live = 1.0 / (1.0 + math.exp(-live_logit))
    p1_live = min(0.98, max(0.02, p1_live))
    p2_live = 1.0 - p1_live

    p1_name = base_result["p1_name"]
    p2_name = base_result["p2_name"]
    pick = p1_name if p1_live >= p2_live else p2_name

    return {
        "p1_prob": p1_live,
        "p2_prob": p2_live,
        "pick": pick,
        "confidence": max(p1_live, p2_live),
        "pre_p1_prob": base_result["p1_prob"],
        "pre_p2_prob": base_result["p2_prob"],
        "sets": (p1_sets, p2_sets),
        "games": (current_p1_games, current_p2_games),
        "current_set": current_set,
        "stat_advantage": stat_adv,
        "historical_situation_edge": hist_edge,
        "historical_situation_details": hist_details,
        "weights": {
            "prior": prior_weight,
            "set": set_weight,
            "games": game_weight,
            "live_stats": stat_weight,
            "situation_history": history_weight,
        },
    }


def render_live_match_details(match):
    p1 = match.get("event_first_player") or "Player 1"
    p2 = match.get("event_second_player") or "Player 2"

    st.markdown("---")
    st.subheader(f"📊 {p1} vs {p2}")

    a, b, c = st.columns(3)
    a.metric("Match score", score_text(match))
    b.metric("Status", match.get("event_status") or "LIVE")
    c.metric("Serving", serving_player_name(match))

    set_scores = as_list(match.get("scores"))
    if set_scores:
        st.markdown("#### Set scores")
        st.dataframe(
            pd.DataFrame([
                {
                    "Set": s.get("score_set", ""),
                    p1: s.get("score_first", ""),
                    p2: s.get("score_second", ""),
                }
                for s in set_scores
            ]),
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("#### Live match statistics")
    stats_df = live_statistics_table(match)
    if stats_df.empty:
        st.info("API-Tennis has not published match statistics for this match yet.")
    else:
        st.dataframe(color_comparison_table(stats_df, "Stat"), hide_index=True, use_container_width=True)

    st.markdown("#### Recent games")
    recent = recent_point_by_point(match)
    if recent:
        st.dataframe(pd.DataFrame(recent), hide_index=True, use_container_width=True)
    else:
        st.caption("Point-by-point data is not available yet.")

    # Side-by-side deep profile like a scouting card.
    with st.expander("🧠 Open deep matchup analysis", expanded=False):
        render_deep_analysis(match)

    if match.get("first_player_key") and match.get("second_player_key"):
        if st.button(
            "Run BAZI analysis for this live match",
            key=f"live_analyze_{match.get('event_key')}",
            type="primary",
            use_container_width=True,
        ):
            try:
                with st.spinner("Loading player form and H2H..."):
                    result = analyze_match(match)

                live_result = live_adjusted_prediction(match, result)

                st.caption(
                    f"Pre-match/base: {result['p1_name']} "
                    f"{result['p1_prob']*100:.1f}% • "
                    f"{result['p2_name']} {result['p2_prob']*100:.1f}% "
                    f"• Reliability {result['reliability_grade']} "
                    f"({result['coverage_score']:.0f}/100 coverage)"
                )

                x, y = st.columns(2)
                x.metric(
                    f"{result['p1_name']} LIVE",
                    f"{live_result['p1_prob'] * 100:.1f}%",
                )
                y.metric(
                    f"{result['p2_name']} LIVE",
                    f"{live_result['p2_prob'] * 100:.1f}%",
                )

                st.success(
                    f"LIVE BAZI lean: {live_result['pick']} "
                    f"({live_result['confidence'] * 100:.1f}% live model confidence)"
                )

                s_a, s_b, s_c = st.columns(3)
                s_a.metric(
                    "Sets",
                    f"{live_result['sets'][0]}–{live_result['sets'][1]}",
                )
                s_b.metric(
                    f"Current Set {live_result['current_set']}",
                    f"{live_result['games'][0]}–{live_result['games'][1]}",
                )
                s_c.metric(
                    "Live-stat edge",
                    f"{live_result['stat_advantage']*100:+.0f}%",
                    help="Positive favors the first player; negative favors the second player.",
                )

                h1, h2 = st.columns(2)
                h1.metric(
                    "Situation-history edge",
                    f"{live_result['historical_situation_edge']*100:+.0f}%",
                    help="Uses the historical stats most relevant to the current set.",
                )
                h2.metric(
                    "Live-data weight",
                    f"{live_result['weights']['live_stats']:.2f}×",
                    help="Live statistics get more weight later in the match.",
                )

                if live_result["current_set"] >= 3:
                    st.info(
                        "🎯 Deciding-set mode: BAZI is emphasizing Set 3 history, "
                        "performance after winning/losing Set 2, live serve/return stats, "
                        "recent momentum, current games and server."
                    )

                s1 = result["p1_stats"]
                s2 = result["p2_stats"]
                comparison_df = pd.DataFrame({
                    "Metric": ["Rank", "Season wins", "Season losses", "Win rate", "H2H wins"],
                    result["p1_name"]: [
                        s1["rank"] or "N/A",
                        s1["wins"],
                        s1["losses"],
                        f"{s1['win_rate']*100:.1f}%",
                        result["h2h_p1"],
                    ],
                    result["p2_name"]: [
                        s2["rank"] or "N/A",
                        s2["wins"],
                        s2["losses"],
                        f"{s2['win_rate']*100:.1f}%",
                        result["h2h_p2"],
                    ],
                })
                st.dataframe(
                    color_comparison_table(comparison_df, "Metric"),
                    hide_index=True,
                    use_container_width=True,
                )
                st.caption("Live BAZI is situation-aware: later sets increase the weight of current live stats and set-specific history such as Set 3 performance. It is an analytical estimate, not a guaranteed outcome.")
            except Exception as e:
                st.error(f"Live analysis error: {e}")


# -----------------------------
# SIMPLE BAZI MATCH ANALYSIS
# Uses API-Tennis player stats + H2H.
# This does not claim guaranteed probability.
# -----------------------------
def latest_singles_stats(player):
    if not player:
        return {}

    rows = [
        x for x in as_list(player.get("stats"))
        if str(x.get("type", "")).lower() == "singles"
    ]

    if not rows:
        return {}

    def season_value(x):
        try:
            return int(x.get("season", 0))
        except Exception:
            return 0

    rows.sort(key=season_value, reverse=True)
    return rows[0]


def safe_int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def player_strength(player):
    stats = latest_singles_stats(player)

    rank = safe_int(stats.get("rank"), 9999)
    wins = safe_int(stats.get("matches_won"), 0)
    losses = safe_int(stats.get("matches_lost"), 0)

    total = wins + losses
    win_rate = wins / total if total else 0.50

    # Ranking score: stronger reward at the top, but bounded.
    rank_score = max(0.0, min(1.0, 1.0 - ((rank - 1) / 1000.0)))

    # 60% recent/season win record + 40% ranking signal.
    strength = (0.60 * win_rate) + (0.40 * rank_score)

    return {
        "rank": rank if rank < 9999 else None,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "strength": strength,
        "stats": stats,
    }


def analyze_match(match):
    p1_key = match.get("first_player_key")
    p2_key = match.get("second_player_key")
    p1_name = match.get("event_first_player") or "Player 1"
    p2_name = match.get("event_second_player") or "Player 2"

    p1 = get_player(p1_key)
    p2 = get_player(p2_key)
    s1 = player_strength(p1)
    s2 = player_strength(p2)

    # Current-season completed match-by-match history from API-Tennis.
    season1 = _season_fixture_profile(p1_key)
    season2 = _season_fixture_profile(p2_key)

    # 2024-25 local DB.
    hist = load_historical_matches()
    surface = current_surface_name(match)
    hp1 = historical_profile(hist, p1_name, surface)
    hp2 = historical_profile(hist, p2_name, surface)

    # H2H.
    h2h = get_h2h(p1_key, p2_key)
    h2h_matches = as_list(h2h.get("H2H"))
    h2h_p1 = h2h_p2 = 0

    for h in h2h_matches:
        winner = str(h.get("event_winner", ""))
        winner_key = None
        if winner == "First Player":
            winner_key = str(h.get("first_player_key", ""))
        elif winner == "Second Player":
            winner_key = str(h.get("second_player_key", ""))

        if winner_key == str(p1_key):
            h2h_p1 += 1
        elif winner_key == str(p2_key):
            h2h_p2 += 1

    total_h2h = h2h_p1 + h2h_p2

    # Sample-size-adjusted rates.
    cur1, cur1_n = _shrunk_pct(season1["win_rate"], prior_n=8)
    cur2, cur2_n = _shrunk_pct(season2["win_rate"], prior_n=8)
    hist1, hist1_n = _shrunk_pct(hp1.get("win_rate"), prior_n=12)
    hist2, hist2_n = _shrunk_pct(hp2.get("win_rate"), prior_n=12)

    p_current = _pairwise_rate(cur1, cur2)
    p_hist = _pairwise_rate(hist1, hist2)
    p_rank = _rank_pair_probability(s1["rank"] or 9999, s2["rank"] or 9999)
    p_h2h = (h2h_p1 + 2.0) / (total_h2h + 4.0)

    # Conservative blend: current season matters most, rank cannot dominate.
    raw_p1 = (
        0.45 * p_current
        + 0.25 * p_rank
        + 0.20 * p_hist
        + 0.10 * p_h2h
    )

    grade, coverage_score = _reliability_grade(
        min(cur1_n, cur2_n),
        min(hist1_n, hist2_n),
        total_h2h,
    )

    # Low coverage = probability stays closer to 50/50.
    coverage_factor = 0.50 + 0.50 * (coverage_score / 100.0)
    p1_prob = 0.5 + (raw_p1 - 0.5) * coverage_factor
    p1_prob = max(0.10, min(0.90, p1_prob))
    p2_prob = 1.0 - p1_prob

    pick = p1_name if p1_prob >= p2_prob else p2_name

    return {
        "p1_name": p1_name,
        "p2_name": p2_name,
        "p1_prob": p1_prob,
        "p2_prob": p2_prob,
        "pick": pick,
        "confidence": max(p1_prob, p2_prob),
        "p1_stats": s1,
        "p2_stats": s2,
        "h2h_p1": h2h_p1,
        "h2h_p2": h2h_p2,
        "h2h_n": total_h2h,
        "season1": season1,
        "season2": season2,
        "historical1": hp1,
        "historical2": hp2,
        "reliability_grade": grade,
        "coverage_score": coverage_score,
    }


# -----------------------------
# DEEP HISTORICAL ANALYSIS (2024-2025 CSV + 2026 API)
# -----------------------------
HISTORICAL_FILES = [
    ("ATP", 2024, "atp_matches_2024.csv"),
    ("ATP", 2025, "atp_matches_2025.csv"),
    ("WTA", 2024, "wta_matches_2024.csv"),
    ("WTA", 2025, "wta_matches_2025.csv"),
]


@st.cache_data(show_spinner=False)
def load_historical_matches():
    frames = []
    for tour, season, filename in HISTORICAL_FILES:
        base_dir = os.path.dirname(__file__)
        candidates = [
            filename,
            os.path.join(base_dir, filename),
            os.path.join(base_dir, "data", filename),
            os.path.join("data", filename),
            os.path.join("/mnt/data", filename),
            os.path.join("/mnt/data", "data", filename),
        ]
        found = next((p for p in candidates if os.path.exists(p)), None)
        if not found:
            continue
        try:
            df = pd.read_csv(found, low_memory=False)
            df["_tour"] = tour
            df["_season"] = season
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _clean_name(name):
    return re.sub(r"[^a-z ]+", " ", str(name or "").lower()).strip()


def _player_name_pool(hist):
    if hist.empty:
        return []
    return sorted(
        set(hist["winner_name"].dropna().astype(str))
        | set(hist["loser_name"].dropna().astype(str))
    )


def resolve_historical_name(api_name, hist):
    """
    Match API-Tennis abbreviated names (e.g. 'Y. Putintseva',
    'L. Samsonova') to full Jeff Sack CSV names.
    """
    if hist.empty or not api_name:
        return None

    pool = _player_name_pool(hist)
    api_clean = _clean_name(api_name)
    if not api_clean:
        return None

    # Exact normalized match.
    exact = [n for n in pool if _clean_name(n) == api_clean]
    if exact:
        return exact[0]

    parts = api_clean.split()
    if not parts:
        return None

    # API-Tennis commonly sends "Y. Putintseva".  The CSV has
    # "Yulia Putintseva". Match surname first, then first-name initial.
    api_last = parts[-1]
    api_first = parts[0]
    api_initial = api_first[0] if api_first else ""

    surname_matches = []
    for full_name in pool:
        clean = _clean_name(full_name)
        fp = clean.split()
        if not fp:
            continue

        full_last = fp[-1]
        full_initial = fp[0][0] if fp[0] else ""

        if full_last == api_last:
            score = 10.0
            if api_initial and full_initial == api_initial:
                score += 10.0
            score += difflib.SequenceMatcher(None, api_clean, clean).ratio()
            surname_matches.append((score, full_name))

    if surname_matches:
        surname_matches.sort(key=lambda x: x[0], reverse=True)
        best_score, best_name = surname_matches[0]

        # Require the initial when the API name is abbreviated.
        if len(api_first) == 1:
            best_parts = _clean_name(best_name).split()
            if best_parts and best_parts[0].startswith(api_initial):
                return best_name
        elif best_score >= 10.5:
            return best_name

    # Fallback for punctuation/transliteration differences.
    scored = []
    for full_name in pool:
        clean = _clean_name(full_name)
        ratio = difflib.SequenceMatcher(None, api_clean, clean).ratio()
        if ratio >= 0.68:
            scored.append((ratio, full_name))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    return None


def player_rows(hist, player_name):
    if hist.empty or not player_name:
        return pd.DataFrame()
    return hist[
        (hist["winner_name"] == player_name) | (hist["loser_name"] == player_name)
    ].copy().sort_values("tourney_date")


def _score_sets(score):
    """Parse normal ATP/WTA score tokens into (winner_games, loser_games, tiebreak?)."""
    if not isinstance(score, str):
        return []
    bad = {"RET", "W/O", "DEF", "ABD", "BYE"}
    out = []
    for token in score.upper().split():
        if any(x in token for x in bad):
            continue
        m = re.match(r"^(\d+)-(\d+)(?:\((\d+)\))?$", token)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        out.append((a, b, m.group(3) is not None))
    return out


def _match_set_outcomes(row, player_name):
    sets = _score_sets(row.get("score"))
    if not sets:
        return []
    is_winner = row.get("winner_name") == player_name
    vals = []
    for w_games, l_games, tb in sets:
        if is_winner:
            vals.append((w_games > l_games, tb))
        else:
            vals.append((l_games > w_games, tb))
    return vals


def _pct(num, den):
    return (100.0 * num / den) if den else None


def _fmt_pct(v, n=None):
    if v is None:
        return "—"
    base = f"{v:.0f}%"
    if n is not None:
        base += f" ({n})"
    return base


def historical_profile(hist, api_name, surface=None):
    resolved = resolve_historical_name(api_name, hist)
    rows = player_rows(hist, resolved)
    if rows.empty:
        return {
            "resolved": resolved,
            "matches": 0,
            "last10": [],
        }

    wins = rows["winner_name"].eq(resolved)
    total = len(rows)
    win_rate = _pct(int(wins.sum()), total)

    set_w = [0, 0, 0]
    set_n = [0, 0, 0]
    after_w1_w = after_w1_n = 0
    set2_after_w1_w = set2_after_w1_n = 0
    set2_after_l1_w = set2_after_l1_n = 0
    set3_after_w2_w = set3_after_w2_n = 0
    set3_after_l2_w = set3_after_l2_n = 0
    tb_w = tb_n = 0

    for _, row in rows.iterrows():
        outcomes = _match_set_outcomes(row, resolved)
        for i in range(min(3, len(outcomes))):
            set_n[i] += 1
            set_w[i] += int(outcomes[i][0])
            if outcomes[i][1]:
                tb_n += 1
                tb_w += int(outcomes[i][0])

        if len(outcomes) >= 1 and outcomes[0][0]:
            after_w1_n += 1
            after_w1_w += int(row["winner_name"] == resolved)

        if len(outcomes) >= 2:
            if outcomes[0][0]:
                set2_after_w1_n += 1
                set2_after_w1_w += int(outcomes[1][0])
            else:
                set2_after_l1_n += 1
                set2_after_l1_w += int(outcomes[1][0])

        if len(outcomes) >= 3:
            if outcomes[1][0]:
                set3_after_w2_n += 1
                set3_after_w2_w += int(outcomes[2][0])
            else:
                set3_after_l2_n += 1
                set3_after_l2_w += int(outcomes[2][0])

    # Surface
    surface_rows = rows
    if surface:
        s = str(surface).lower()
        filtered = rows[rows["surface"].astype(str).str.lower().str.contains(s, na=False)]
        if not filtered.empty:
            surface_rows = filtered
    surface_wins = int(surface_rows["winner_name"].eq(resolved).sum())
    surface_rate = _pct(surface_wins, len(surface_rows))

    # Versus handedness
    vs = {}
    for hand_code, label in [("L", "vs_left"), ("R", "vs_right")]:
        opp_hand = []
        results = []
        for _, r in rows.iterrows():
            if r["winner_name"] == resolved:
                opp_hand.append(str(r.get("loser_hand", "")))
                results.append(True)
            else:
                opp_hand.append(str(r.get("winner_hand", "")))
                results.append(False)
        mask = [h == hand_code for h in opp_hand]
        den = sum(mask)
        num = sum(int(res) for res, keep in zip(results, mask) if keep)
        vs[label] = (_pct(num, den), den)

    # Age/current rank from most recent appearance in CSV.
    last = rows.iloc[-1]
    if last["winner_name"] == resolved:
        age = last.get("winner_age")
        rank = last.get("winner_rank")
    else:
        age = last.get("loser_age")
        rank = last.get("loser_rank")

    # Last 10 with opponent ranks.
    last10 = []
    for _, r in rows.tail(10).iloc[::-1].iterrows():
        if r["winner_name"] == resolved:
            result = "W"
            opp = r["loser_name"]
            opp_rank = r.get("loser_rank")
        else:
            result = "L"
            opp = r["winner_name"]
            opp_rank = r.get("winner_rank")
        rank_txt = None if pd.isna(opp_rank) else int(float(opp_rank))
        last10.append(
            {
                "result": result,
                "score": str(r.get("score", "—")),
                "opponent": str(opp),
                "rank": rank_txt,
                "surface": str(r.get("surface", "")),
            }
        )

    return {
        "resolved": resolved,
        "matches": total,
        "win_rate": (win_rate, total),
        "after_winning_set1": (_pct(after_w1_w, after_w1_n), after_w1_n),
        "set1": (_pct(set_w[0], set_n[0]), set_n[0]),
        "set2": (_pct(set_w[1], set_n[1]), set_n[1]),
        "set3": (_pct(set_w[2], set_n[2]), set_n[2]),
        "set2_after_w1": (_pct(set2_after_w1_w, set2_after_w1_n), set2_after_w1_n),
        "set2_after_l1": (_pct(set2_after_l1_w, set2_after_l1_n), set2_after_l1_n),
        "set3_after_w2": (_pct(set3_after_w2_w, set3_after_w2_n), set3_after_w2_n),
        "set3_after_l2": (_pct(set3_after_l2_w, set3_after_l2_n), set3_after_l2_n),
        "surface": (surface_rate, len(surface_rows)),
        "tiebreak": (_pct(tb_w, tb_n), tb_n),
        "vs_left": vs["vs_left"],
        "vs_right": vs["vs_right"],
        "age": None if pd.isna(age) else float(age),
        "rank": None if pd.isna(rank) else int(float(rank)),
        "last10": last10,
    }


def api_recent_results(h2h, which, player_key):
    key = "firstPlayerResults" if which == 1 else "secondPlayerResults"
    rows = as_list(h2h.get(key))
    out = []
    for r in rows[:10]:
        first_key = str(r.get("first_player_key", ""))
        second_key = str(r.get("second_player_key", ""))
        winner = str(r.get("event_winner", ""))
        player_key = str(player_key)

        won = (
            (winner == "First Player" and first_key == player_key)
            or (winner == "Second Player" and second_key == player_key)
        )
        if first_key == player_key:
            opp = r.get("event_second_player", "Opponent")
        elif second_key == player_key:
            opp = r.get("event_first_player", "Opponent")
        else:
            opp = "Opponent"

        out.append({
            "result": "W" if won else "L",
            "score": r.get("event_final_result", "—"),
            "opponent": opp,
            "rank": None,
            "surface": "",
        })
    return out


def api_last10_summary(results):
    if not results:
        return "—"
    wins = sum(r["result"] == "W" for r in results[:10])
    losses = len(results[:10]) - wins
    streak = 0
    if results:
        first = results[0]["result"]
        for r in results:
            if r["result"] == first:
                streak += 1
            else:
                break
        return f"{wins}-{losses} ({first}{streak})"
    return f"{wins}-{losses}"


def current_surface_name(match):
    name = str(match.get("tournament_name", "")).lower()
    # Cincinnati and most unspecified ATP/WTA summer events are hard; but do not
    # invent a surface when it cannot be inferred safely.
    hard_tokens = ("cincinnati", "us open", "canada", "toronto", "montreal", "washington")
    clay_tokens = ("roland garros", "rome", "madrid", "monte carlo")
    grass_tokens = ("wimbledon", "halle", "queen", "eastbourne")
    if any(x in name for x in hard_tokens):
        return "Hard"
    if any(x in name for x in clay_tokens):
        return "Clay"
    if any(x in name for x in grass_tokens):
        return "Grass"
    return None


def deep_matchup_table(match):
    hist = load_historical_matches()
    p1 = match.get("event_first_player") or "Player 1"
    p2 = match.get("event_second_player") or "Player 2"
    p1_key = match.get("first_player_key")
    p2_key = match.get("second_player_key")
    surface = current_surface_name(match)

    h2h = get_h2h(p1_key, p2_key)
    api1 = api_recent_results(h2h, 1, p1_key)
    api2 = api_recent_results(h2h, 2, p2_key)

    hp1 = historical_profile(hist, p1, surface)
    hp2 = historical_profile(hist, p2, surface)

    prof1 = get_player(p1_key)
    prof2 = get_player(p2_key)
    s1 = player_strength(prof1)
    s2 = player_strength(prof2)
    season1 = _season_fixture_profile(p1_key)
    season2 = _season_fixture_profile(p2_key)

    rows = [
        ("Last 10 (2026 API)", api_last10_summary(api1), api_last10_summary(api2)),
        ("2026 win %", _fmt_pct(*season1["win_rate"]), _fmt_pct(*season2["win_rate"])),
        ("2026 Set 1 win %", _fmt_pct(*season1["set1"]), _fmt_pct(*season2["set1"])),
        ("2026 Set 2 win %", _fmt_pct(*season1["set2"]), _fmt_pct(*season2["set2"])),
        ("2026 Set 3 win %", _fmt_pct(*season1["set3"]), _fmt_pct(*season2["set3"])),
        ("2026 after winning Set 1", _fmt_pct(*season1["after_winning_set1"]), _fmt_pct(*season2["after_winning_set1"])),
        ("2026 Set 2 after winning Set 1", _fmt_pct(*season1["set2_after_w1"]), _fmt_pct(*season2["set2_after_w1"])),
        ("2026 Set 2 after losing Set 1", _fmt_pct(*season1["set2_after_l1"]), _fmt_pct(*season2["set2_after_l1"])),
        ("2026 Set 3 after winning Set 2", _fmt_pct(*season1["set3_after_w2"]), _fmt_pct(*season2["set3_after_w2"])),
        ("2026 Set 3 after losing Set 2", _fmt_pct(*season1["set3_after_l2"]), _fmt_pct(*season2["set3_after_l2"])),
        ("2026 tiebreak win %", _fmt_pct(*season1["tiebreak"]), _fmt_pct(*season2["tiebreak"])),
        ("2026 rank", s1["rank"] or "—", s2["rank"] or "—"),
        ("2024-25 win %", _fmt_pct(*(hp1.get("win_rate") or (None, None))), _fmt_pct(*(hp2.get("win_rate") or (None, None)))),
        ("Win after winning Set 1", _fmt_pct(*(hp1.get("after_winning_set1") or (None, None))), _fmt_pct(*(hp2.get("after_winning_set1") or (None, None)))),
        ("Set 1 win %", _fmt_pct(*(hp1.get("set1") or (None, None))), _fmt_pct(*(hp2.get("set1") or (None, None)))),
        ("Set 2 win %", _fmt_pct(*(hp1.get("set2") or (None, None))), _fmt_pct(*(hp2.get("set2") or (None, None)))),
        ("Set 3 win %", _fmt_pct(*(hp1.get("set3") or (None, None))), _fmt_pct(*(hp2.get("set3") or (None, None)))),
        ("Set 2 after winning Set 1", _fmt_pct(*(hp1.get("set2_after_w1") or (None, None))), _fmt_pct(*(hp2.get("set2_after_w1") or (None, None)))),
        ("Set 2 after losing Set 1", _fmt_pct(*(hp1.get("set2_after_l1") or (None, None))), _fmt_pct(*(hp2.get("set2_after_l1") or (None, None)))),
        ("Set 3 after winning Set 2", _fmt_pct(*(hp1.get("set3_after_w2") or (None, None))), _fmt_pct(*(hp2.get("set3_after_w2") or (None, None)))),
        ("Set 3 after losing Set 2", _fmt_pct(*(hp1.get("set3_after_l2") or (None, None))), _fmt_pct(*(hp2.get("set3_after_l2") or (None, None)))),
        (f"{surface or 'Surface'} win % (2024-25)", _fmt_pct(*(hp1.get("surface") or (None, None))), _fmt_pct(*(hp2.get("surface") or (None, None)))),
        ("Tiebreak win %", _fmt_pct(*(hp1.get("tiebreak") or (None, None))), _fmt_pct(*(hp2.get("tiebreak") or (None, None)))),
        ("Career matches in local DB", hp1.get("matches", 0) or "—", hp2.get("matches", 0) or "—"),
        ("Vs lefty", _fmt_pct(*(hp1.get("vs_left") or (None, None))), _fmt_pct(*(hp2.get("vs_left") or (None, None)))),
        ("Vs righty", _fmt_pct(*(hp1.get("vs_right") or (None, None))), _fmt_pct(*(hp2.get("vs_right") or (None, None)))),
    ]

    table = pd.DataFrame(rows, columns=["Question", p1, p2])
    return table, hp1, hp2, api1, api2


def _extract_leading_percent(value):
    m = re.search(r"(-?\d+(?:\.\d+)?)%", str(value))
    return float(m.group(1)) if m else None


def style_deep_table(df):
    """Green/red on comparable values; rank/loss-like rows invert direction."""
    pcols = list(df.columns[1:])

    def style_row(row):
        s = pd.Series("", index=row.index)
        if len(pcols) != 2:
            return s

        label = str(row["Question"]).lower()
        a = _extract_leading_percent(row[pcols[0]])
        b = _extract_leading_percent(row[pcols[1]])

        if a is None or b is None:
            try:
                a = float(str(row[pcols[0]]).replace("#", ""))
                b = float(str(row[pcols[1]]).replace("#", ""))
            except Exception:
                return s

        if a == b:
            return s

        lower_better = "rank" in label or "loss" in label
        first_better = a < b if lower_better else a > b
        good = pcols[0] if first_better else pcols[1]
        bad = pcols[1] if first_better else pcols[0]
        s[good] = "color: #32d583; font-weight: 800;"
        s[bad] = "color: #ff5c5c; font-weight: 800;"
        return s

    return df.style.apply(style_row, axis=1)


def render_last10_cards(p1, p2, hp1, hp2, api1, api2):
    st.markdown("#### LAST 10 • OPPONENTS")
    c1, c2 = st.columns(2)

    def render_side(col, player, api_rows, hist_profile):
        with col:
            st.markdown(f"**{player}**")
            rows = api_rows if api_rows else hist_profile.get("last10", [])
            if not rows:
                st.caption("No recent-match list available.")
                return
            for r in rows[:10]:
                icon = "🟢 W" if r["result"] == "W" else "🔴 L"
                rank = f" #{r['rank']}" if r.get("rank") else ""
                st.markdown(
                    f"{icon}  **{r.get('score','—')}**  "
                    f"{r.get('opponent','Opponent')}{rank}"
                )

    render_side(c1, p1, api1, hp1)
    render_side(c2, p2, api2, hp2)


def render_deep_analysis(match):
    p1 = match.get("event_first_player") or "Player 1"
    p2 = match.get("event_second_player") or "Player 2"

    st.markdown("### 🧠 BAZI DEEP MATCHUP")
    st.caption(
        "2026 API form + 2024–2025 ATP/WTA history. "
        "Green = stronger comparison, red = weaker."
    )

    try:
        table, hp1, hp2, api1, api2 = deep_matchup_table(match)

        resolved1 = hp1.get("resolved")
        resolved2 = hp2.get("resolved")
        if resolved1 or resolved2:
            st.caption(
                "Historical match: "
                f"{p1} → {resolved1 or 'not found'} • "
                f"{p2} → {resolved2 or 'not found'}"
            )

        st.dataframe(
            style_deep_table(table),
            hide_index=True,
            use_container_width=True,
        )
        render_last10_cards(p1, p2, hp1, hp2, api1, api2)

        hist = load_historical_matches()
        if hist.empty:
            st.warning(
                "Historical CSV files were not found beside app.py, so the "
                "2024–2025 rows cannot be calculated on this deployment."
            )
    except Exception as e:
        st.warning(f"Deep historical analysis unavailable: {e}")



def render_prematch_prediction(match):
    """Show a full pre-match review directly under an upcoming match card."""
    p1 = match.get("event_first_player") or "Player 1"
    p2 = match.get("event_second_player") or "Player 2"

    st.markdown("---")
    st.subheader(f"🔵 PRE-MATCH BAZI • {p1} vs {p2}")

    try:
        with st.spinner("Building pre-match prediction..."):
            result = analyze_match(match)

        c1, c2 = st.columns(2)
        c1.metric(p1, f"{result['p1_prob']*100:.1f}%")
        c2.metric(p2, f"{result['p2_prob']*100:.1f}%")

        st.success(
            f"PRE-MATCH BAZI lean: {result['pick']} "
            f"({result['confidence']*100:.1f}% model confidence)"
        )

        r1, r2 = st.columns(2)
        r1.metric(
            "Data reliability",
            result["reliability_grade"],
            help="Coverage grade from current-season history, 2024-25 history and H2H sample size. It is not an accuracy grade.",
        )
        r2.metric("Coverage", f"{result['coverage_score']:.0f}/100")

        if result["reliability_grade"] in {"C", "D"}:
            st.warning(
                "Small/limited samples detected. BAZI is automatically pulling "
                "the probability closer to 50/50 instead of over-trusting the data."
            )

        s1 = result["p1_stats"]
        s2 = result["p2_stats"]
        comparison_df = pd.DataFrame({
            "Metric": ["Rank", "2026 wins", "2026 losses", "2026 win rate", "H2H wins"],
            p1: [
                s1["rank"] or "N/A",
                s1["wins"],
                s1["losses"],
                f"{s1['win_rate']*100:.1f}%",
                result["h2h_p1"],
            ],
            p2: [
                s2["rank"] or "N/A",
                s2["wins"],
                s2["losses"],
                f"{s2['win_rate']*100:.1f}%",
                result["h2h_p2"],
            ],
        })
        st.dataframe(
            color_comparison_table(comparison_df, "Metric"),
            hide_index=True,
            use_container_width=True,
        )

        with st.expander("🧠 Deep pre-match matchup", expanded=True):
            render_deep_analysis(match)

        st.caption(
            "This is the pre-match model. Once the match goes live, "
            "BAZI switches to the live situation-aware model."
        )

    except Exception as e:
        st.error(f"Pre-match analysis error: {e}")



# -----------------------------
# UI
# -----------------------------
st.title("🎾 BAZI Tennis V5.0 RELIABLE")
st.caption("API-Tennis live feed • completed 2026 match history • small-sample protection • prematch + live analysis")

with st.sidebar:
    st.subheader("API-Tennis")

    if not get_api_key():
        entered = st.text_input(
            "API key",
            type="password",
            help="Stored only in this Streamlit session unless you add it to secrets.",
        )
        if entered:
            st.session_state["api_tennis_key"] = entered.strip()
            st.rerun()
    else:
        st.success("API key loaded")

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption(
        'Permanent key: create .streamlit/secrets.toml and add:\n'
        'API_TENNIS_KEY = "your_key_here"'
    )


if not get_api_key():
    st.warning("Enter your API-Tennis key in the sidebar to load today's matches.")
    st.stop()


today = date.today()
selected_day = st.date_input("Date", value=today)

try:
    with st.spinner("Loading API-Tennis data..."):
        fixtures = as_list(get_fixtures(selected_day.isoformat()))
        live_matches = as_list(get_live())

    matches = merge_live_into_fixtures(fixtures, live_matches)

except Exception as e:
    st.error(f"API-Tennis error: {e}")
    st.stop()


# Keep singles by default, because BAZI player-vs-player analysis is intended for singles.
singles_only = st.toggle("Singles only", value=True)

if singles_only:
    matches = [m for m in matches if is_singles(m)]

live = [m for m in matches if match_state(m) == "LIVE"]
upcoming = [m for m in matches if match_state(m) == "UPCOMING"]
completed = [m for m in matches if match_state(m) == "COMPLETED"]

tabs = st.tabs(["Today / Live", "Analyze Match", "Player Profile", "Tournaments"])


# -----------------------------
# TODAY / LIVE TAB
# -----------------------------
with tabs[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Live", len(live))
    c2.metric("Upcoming", len(upcoming))
    c3.metric("Completed", len(completed))
    c4.metric("Total", len(matches))

    if live:
        st.subheader("🔴 LIVE NOW")

        for m in live:
            match_key = str(m.get("event_key"))
            is_open = st.session_state.get("selected_live_match") == match_key

            with st.container(border=True):
                a, b = st.columns([3, 1])
                with a:
                    st.markdown(f"### {display_name(m)}")
                    st.caption(
                        f"{m.get('tournament_name', '')} • "
                        f"{m.get('event_type_type', '')} • "
                        f"{m.get('event_status', 'LIVE')}"
                    )
                    st.caption(f"Serving: **{serving_player_name(m)}**")
                with b:
                    st.metric("Score", score_text(m))

                button_label = "✖ Close live stats" if is_open else "📊 Open live stats"

                if st.button(
                    button_label,
                    key=f"open_live_{match_key}",
                    use_container_width=True,
                ):
                    if is_open:
                        st.session_state.pop("selected_live_match", None)
                    else:
                        st.session_state["selected_live_match"] = match_key
                    st.rerun()

                # Render the selected match INSIDE its own card,
                # directly below the button instead of at the bottom of the page.
                if st.session_state.get("selected_live_match") == match_key:
                    render_live_match_details(m)

    st.subheader("Upcoming")
    if not upcoming:
        st.info("No upcoming singles returned for this date.")
    else:
        for m in upcoming:
            match_key = str(m.get("event_key"))
            is_open = st.session_state.get("selected_upcoming_match") == match_key

            with st.container(border=True):
                st.markdown(f"### {display_name(m)}")
                st.caption(
                    f"{m.get('event_time', '—')} • "
                    f"{m.get('tournament_name', '')} • "
                    f"{m.get('event_type_type', '')}"
                )

                label = "✖ Close prediction" if is_open else "🔵 View data & prediction"
                if st.button(
                    label,
                    key=f"open_upcoming_{match_key}",
                    use_container_width=True,
                ):
                    if is_open:
                        st.session_state.pop("selected_upcoming_match", None)
                    else:
                        st.session_state["selected_upcoming_match"] = match_key
                    st.rerun()

                # Open directly under the match card, same behavior as live stats.
                if st.session_state.get("selected_upcoming_match") == match_key:
                    render_prematch_prediction(m)

    with st.expander("Completed"):
        if not completed:
            st.write("No completed singles returned for this date.")
        else:
            for m in completed:
                st.write(
                    f"**{display_name(m)}** — {score_text(m)} "
                    f"• {m.get('tournament_name', '')}"
                )


# -----------------------------
# ANALYZE MATCH TAB
# -----------------------------
with tabs[1]:
    analyzable = [m for m in matches if m.get("first_player_key") and m.get("second_player_key")]

    if not analyzable:
        st.info("No analyzable matches returned for this date.")
    else:
        labels = {
            f"{display_name(m)} • {m.get('tournament_name', '')} • {m.get('event_time', '')}": m
            for m in analyzable
        }

        choice = st.selectbox("Choose match", list(labels.keys()))
        selected_match = labels[choice]

        with st.expander("🧠 Deep matchup profile", expanded=False):
            render_deep_analysis(selected_match)

        if st.button("Analyze Match", type="primary", use_container_width=True):
            try:
                with st.spinner("Loading player profiles and H2H..."):
                    result = analyze_match(selected_match)

                st.subheader(f"{result['p1_name']} vs {result['p2_name']}")

                a, b = st.columns(2)
                a.metric(
                    result["p1_name"],
                    f"{result['p1_prob'] * 100:.1f}%",
                    help="BAZI model estimate from available API-Tennis ranking, record and H2H data.",
                )
                b.metric(
                    result["p2_name"],
                    f"{result['p2_prob'] * 100:.1f}%",
                )

                st.success(
                    f"BAZI lean: {result['pick']} "
                    f"({result['confidence'] * 100:.1f}% model confidence)"
                )

                s1 = result["p1_stats"]
                s2 = result["p2_stats"]

                rows = pd.DataFrame(
                    {
                        "Metric": ["Rank", "Season wins", "Season losses", "Win rate", "H2H wins"],
                        result["p1_name"]: [
                            s1["rank"] or "N/A",
                            s1["wins"],
                            s1["losses"],
                            f"{s1['win_rate']*100:.1f}%",
                            result["h2h_p1"],
                        ],
                        result["p2_name"]: [
                            s2["rank"] or "N/A",
                            s2["wins"],
                            s2["losses"],
                            f"{s2['win_rate']*100:.1f}%",
                            result["h2h_p2"],
                        ],
                    }
                )
                st.dataframe(color_comparison_table(rows, "Metric"), hide_index=True, use_container_width=True)
                st.caption(
                    "Model confidence is an analytical estimate, not a guaranteed outcome."
                )

            except Exception as e:
                st.error(f"Analysis error: {e}")


# -----------------------------
# PLAYER PROFILE TAB
# -----------------------------
with tabs[2]:
    player_map = {}
    for m in matches:
        p1 = m.get("event_first_player")
        p2 = m.get("event_second_player")
        if p1 and m.get("first_player_key"):
            player_map[p1] = m.get("first_player_key")
        if p2 and m.get("second_player_key"):
            player_map[p2] = m.get("second_player_key")

    if not player_map:
        st.info("No players available for this date.")
    else:
        selected_player_name = st.selectbox(
            "Player",
            sorted(player_map.keys()),
            key="profile_player",
        )

        try:
            profile = get_player(player_map[selected_player_name])
            if profile:
                c1, c2 = st.columns([1, 3])

                with c1:
                    logo = profile.get("player_logo")
                    if logo:
                        st.image(logo, width=150)

                with c2:
                    st.subheader(profile.get("player_name", selected_player_name))
                    st.write(f"Country: **{profile.get('player_country') or 'N/A'}**")
                    st.write(f"Birthday: **{profile.get('player_bday') or 'N/A'}**")

                stats = as_list(profile.get("stats"))
                if stats:
                    st.dataframe(pd.DataFrame(stats), use_container_width=True, hide_index=True)
                else:
                    st.info("No player statistics returned.")
            else:
                st.info("No profile returned.")

        except Exception as e:
            st.error(f"Player profile error: {e}")


# -----------------------------
# TOURNAMENTS TAB
# -----------------------------
with tabs[3]:
    tournament_rows = []
    seen = set()

    for m in matches:
        key = (
            str(m.get("tournament_key", "")),
            str(m.get("tournament_name", "")),
        )
        if key in seen:
            continue
        seen.add(key)

        tournament_rows.append(
            {
                "Tournament": m.get("tournament_name", ""),
                "Type": m.get("event_type_type", ""),
                "Season": m.get("tournament_season", ""),
                "Tournament Key": m.get("tournament_key", ""),
            }
        )

    if tournament_rows:
        st.dataframe(
            pd.DataFrame(tournament_rows).sort_values("Tournament"),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No tournaments returned for this date.")


st.divider()
st.caption(
    "Data source: API-Tennis • tap any live match to open live stats and point-by-point details."
)
