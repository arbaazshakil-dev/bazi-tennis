import os
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.api-tennis.com/tennis/"

st.set_page_config(
    page_title="BAZI Tennis V3.3",
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


# -----------------------------
# DATA NORMALIZATION
# -----------------------------
def as_list(value):
    return value if isinstance(value, list) else []


def is_singles(match):
    return "singles" in str(match.get("event_type_type", "")).lower()


def match_state(match):
    if str(match.get("event_live", "0")) == "1":
        return "LIVE"

    status = str(match.get("event_status", "")).strip().lower()
    winner = match.get("event_winner")

    if winner or status in {
        "finished",
        "retired",
        "walkover",
        "cancelled",
        "canceled",
        "abandoned",
    }:
        return "COMPLETED"

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

    p1 = get_player(p1_key)
    p2 = get_player(p2_key)

    s1 = player_strength(p1)
    s2 = player_strength(p2)

    h2h = get_h2h(p1_key, p2_key)
    h2h_matches = as_list(h2h.get("H2H"))

    h2h_p1 = 0
    h2h_p2 = 0

    for h in h2h_matches:
        winner = str(h.get("event_winner", ""))
        # API H2H results preserve First/Second Player relative to each event,
        # so compare winner's player key where possible.
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

    base1 = s1["strength"]
    base2 = s2["strength"]

    if total_h2h:
        h1 = h2h_p1 / total_h2h
        h2 = h2h_p2 / total_h2h
        base1 = 0.80 * base1 + 0.20 * h1
        base2 = 0.80 * base2 + 0.20 * h2

    denom = base1 + base2
    p1_prob = base1 / denom if denom else 0.50
    p1_prob = max(0.05, min(0.95, p1_prob))
    p2_prob = 1.0 - p1_prob

    p1_name = match.get("event_first_player") or "Player 1"
    p2_name = match.get("event_second_player") or "Player 2"

    pick = p1_name if p1_prob >= p2_prob else p2_name
    confidence = max(p1_prob, p2_prob)

    return {
        "p1_name": p1_name,
        "p2_name": p2_name,
        "p1_prob": p1_prob,
        "p2_prob": p2_prob,
        "pick": pick,
        "confidence": confidence,
        "p1_stats": s1,
        "p2_stats": s2,
        "h2h_p1": h2h_p1,
        "h2h_p2": h2h_p2,
        "h2h_n": total_h2h,
    }


# -----------------------------
# UI
# -----------------------------
st.title("🎾 BAZI Tennis V3.3")
st.caption("API-Tennis live feed • fixtures • live scores • player stats • H2H")

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
            with st.container(border=True):
                a, b = st.columns([3, 1])
                with a:
                    st.markdown(f"### {display_name(m)}")
                    st.caption(
                        f"{m.get('tournament_name', '')} • "
                        f"{m.get('event_type_type', '')} • "
                        f"{m.get('event_status', 'LIVE')}"
                    )
                with b:
                    st.metric("Score", score_text(m))

    st.subheader("Upcoming")
    if not upcoming:
        st.info("No upcoming singles returned for this date.")
    else:
        for m in upcoming:
            with st.container(border=True):
                st.markdown(f"**{display_name(m)}**")
                st.caption(
                    f"{m.get('event_time', '—')} • "
                    f"{m.get('tournament_name', '')} • "
                    f"{m.get('event_type_type', '')}"
                )

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
                st.dataframe(rows, hide_index=True, use_container_width=True)
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
    "Data source: API-Tennis • live scores refresh from the API cache every ~15 seconds."
)
