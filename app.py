from pathlib import Path

import io
import math
import re
import time
import difflib
import requests
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="BAZI Tennis V3 Free", page_icon="🎾", layout="wide")

ATP="https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
WTA="https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"
ESPN="https://site.api.espn.com/apis/site/v2/sports/tennis"
YEAR=pd.Timestamp.utcnow().year
YEARS=list(range(max(2022,YEAR-4),YEAR+1))

def fetch_csv(url):
    r=requests.get(url,timeout=25,headers={"User-Agent":"bazi-tennis-free/3.0"})
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text),low_memory=False)

@st.cache_data(ttl=3600)
def load_matches(tour):
    base=ATP if tour=="ATP" else WTA
    frames=[]; loaded=[]
    for y in YEARS:
        try:
            local=f"data/{tour.lower()}_matches_{y}.csv"
            if y in (2024, 2025) and Path(local).exists():
                d=pd.read_csv(local,low_memory=False)
            else:
                d=fetch_csv(f"{base}/{tour.lower()}_matches_{y}.csv")
            d["season"]=y
            frames.append(d)
            loaded.append(y)
        except Exception:
            pass
    if not frames:
        raise RuntimeError("No online historical match files loaded.")
    return pd.concat(frames,ignore_index=True),loaded

def surf(s):
    s=str(s).lower()
    if "clay" in s:return "Clay"
    if "grass" in s:return "Grass"
    if "hard" in s:return "Hard"
    return "Unknown"

def player_rows(matches):
    rows=[]
    stats=["ace","df","svpt","1stIn","1stWon","2ndWon"]
    for _,m in matches.iterrows():
        dt=pd.to_datetime(str(int(m.get("tourney_date",0))),format="%Y%m%d",errors="coerce")
        for side,opp,pfx in [("winner","loser","w_"),("loser","winner","l_")]:
            name=m.get(f"{side}_name")
            if pd.isna(name):
                continue
            r={
                "date":dt,
                "player":str(name),
                "opponent":str(m.get(f"{opp}_name","")),
                "surface":surf(m.get("surface")),
                "won":1 if side=="winner" else 0,
                "rank":pd.to_numeric(m.get(f"{pfx}rank"),errors="coerce"),
            }
            for c in stats:
                r[c]=pd.to_numeric(m.get(f"{pfx}{c}"),errors="coerce")
            rows.append(r)
    return pd.DataFrame(rows).sort_values("date")

def profile(P,name,surface):
    x=P[P.player==name].copy()
    if x.empty:return None
    r10=x.tail(10)
    sx=x[x.surface==surface].tail(60)
    y=x[x.date>=pd.Timestamp.utcnow().tz_localize(None)-pd.Timedelta(days=365)]
    stat=r10.dropna(subset=["svpt"])
    serve=np.nan
    if len(stat):
        sv=stat.svpt.sum()
        ace=stat.ace.sum()/sv if sv else 0
        df=stat.df.sum()/sv if sv else 0
        f1=stat["1stWon"].sum()/stat["1stIn"].sum() if stat["1stIn"].sum() else .5
        second_attempt=(stat.svpt-stat["1stIn"]-stat.df).clip(lower=0).sum()
        f2=stat["2ndWon"].sum()/second_attempt if second_attempt else .5
        serve=float(np.clip(.45*f1+.35*f2+.20*(.5+2*ace-1.3*df),0,1))
    last=x.iloc[-1]
    return {
        "rank":last["rank"],
        "recent":r10.won.mean(),
        "surface":sx.won.mean() if len(sx) else np.nan,
        "year":y.won.mean() if len(y) else np.nan,
        "serve":serve,
        "matches":len(x),
        "last":last.date,
        "rows":r10
    }

def h2h(P,a,b,surface):
    x=P[((P.player==a)&(P.opponent==b))|((P.player==b)&(P.opponent==a))]
    aw=int(x[x.player==a].won.sum()); bw=int(x[x.player==b].won.sum())
    xs=x[x.surface==surface]
    asw=int(xs[xs.player==a].won.sum()); bsw=int(xs[xs.player==b].won.sum())
    return aw,bw,asw,bsw

def rs(rank):
    if pd.isna(rank) or rank<=0:return .5
    return float(np.clip(1-math.log1p(rank)/math.log1p(1000),0,1))

def val(x):
    return .5 if pd.isna(x) else float(np.clip(x,0,1))

def predict(pa,pb,hh):
    aw,bw,asw,bsw=hh
    h=(aw-bw)/(aw+bw) if aw+bw>=2 else 0
    sh=(asw-bsw)/(asw+bsw) if asw+bsw>=2 else 0
    comp={
        "Ranking":rs(pa["rank"])-rs(pb["rank"]),
        "Recent form":val(pa["recent"])-val(pb["recent"]),
        "Surface":val(pa["surface"])-val(pb["surface"]),
        "52-week":val(pa["year"])-val(pb["year"]),
        "Serve":0 if pd.isna(pa["serve"]) or pd.isna(pb["serve"]) else pa["serve"]-pb["serve"],
        "H2H":h,
        "Surface H2H":sh
    }
    w={"Ranking":.22,"Recent form":.23,"Surface":.20,"52-week":.12,"Serve":.10,"H2H":.08,"Surface H2H":.05}
    score=sum(comp[k]*w[k] for k in w)
    p=1/(1+math.exp(-3.1*score))
    return float(np.clip(p,.10,.90)),comp

@st.cache_data(ttl=3600)
def prepare(tour):
    m,yrs=load_matches(tour)
    return player_rows(m),yrs

# ---------------- FREE ESPN TODAY/LIVE ----------------

def norm(s):
    s=re.sub(r"[^a-zA-ZÀ-ÿ .'-]"," ",str(s))
    s=re.sub(r"\s+"," ",s).strip().lower()
    return s

def resolve_player(api_name,names):
    a=norm(api_name)
    if not a:return None
    parts=a.replace(".","").split()
    surname=parts[-1] if parts else ""
    initial=parts[0][0] if parts and parts[0] else ""

    candidates=[]
    for n in names:
        nn=norm(n)
        p=nn.split()
        if p and p[-1]==surname:
            score=2.0
            if initial and p[0].startswith(initial):
                score+=1.0
            score+=difflib.SequenceMatcher(None,a,nn).ratio()
            candidates.append((score,n))
    if candidates:
        candidates.sort(reverse=True)
        if len(candidates)==1 or candidates[0][0]-candidates[1][0]>.12:
            return candidates[0][1]
    close=difflib.get_close_matches(api_name,names,n=1,cutoff=.72)
    return close[0] if close else None

@st.cache_data(ttl=20)
def espn_scoreboard(tour,date_yyyymmdd):
    league=tour.lower()
    url=f"{ESPN}/{league}/scoreboard"
    r=requests.get(
        url,
        params={"dates":date_yyyymmdd,"limit":200},
        timeout=20,
        headers={"User-Agent":"Mozilla/5.0"}
    )
    r.raise_for_status()
    return r.json()

def parse_espn_events(data):
    out=[]
    for ev in data.get("events",[]) or []:
        comp=(ev.get("competitions") or [{}])[0]
        competitors=comp.get("competitors") or []
        if len(competitors)<2:
            continue

        p1=competitors[0]
        p2=competitors[1]
        n1=(p1.get("athlete") or {}).get("displayName") or p1.get("displayName") or ""
        n2=(p2.get("athlete") or {}).get("displayName") or p2.get("displayName") or ""

        status=(comp.get("status") or {})
        stype=(status.get("type") or {})
        state=stype.get("state","pre")
        detail=stype.get("shortDetail") or stype.get("detail") or ""

        venue=(comp.get("venue") or {}).get("fullName","")
        tourney=ev.get("name") or (comp.get("notes") or [{}])[0].get("headline","")

        try:
            start=pd.Timestamp(ev.get("date"))
        except Exception:
            start=pd.NaT

        score1=p1.get("score","")
        score2=p2.get("score","")

        out.append({
            "event_id":ev.get("id",""),
            "tournament":tourney,
            "player1":n1,
            "player2":n2,
            "state":state,
            "status":detail,
            "score1":score1,
            "score2":score2,
            "start":start,
            "venue":venue,
        })
    return out

def analyze_live_event(P,names,e,surface):
    a=resolve_player(e["player1"],names)
    b=resolve_player(e["player2"],names)
    if not a or not b:
        return {"prediction":"UNRESOLVED","p1":None,"p2":None}
    pa=profile(P,a,surface); pb=profile(P,b,surface)
    if not pa or not pb:
        return {"prediction":"UNRESOLVED","p1":None,"p2":None}
    p,_=predict(pa,pb,h2h(P,a,b,surface))
    q=1-p
    edge=max(p,q)
    pick=e["player1"] if p>=.5 else e["player2"]
    if edge>=.68:
        pred=f"PICK: {pick}"
    elif edge>=.60:
        pred=f"LEAN: {pick}"
    else:
        pred="WAIT"
    return {"prediction":pred,"p1":p,"p2":q}

st.title("🎾 BAZI Tennis V3.1 — Free")
st.caption("ATP/WTA all-player analysis + today's matches + free live scoreboard")

tour=st.sidebar.selectbox("Tour",["ATP","WTA"])
surface=st.sidebar.selectbox("Default surface",["Hard","Clay","Grass"])
auto_refresh=st.sidebar.toggle("Auto-refresh today/live",value=True)
refresh_seconds=st.sidebar.slider("Refresh seconds",15,90,30,5)

try:
    P,yrs=prepare(tour)
    names=sorted(P.player.dropna().unique())
    st.caption(f"Loaded {tour} seasons: {', '.join(map(str,yrs))} • {len(names):,} historical players")

    tabs=st.tabs(["Today & Live","Match predictor","All players","Player profile"])

    with tabs[0]:
        now_pt=pd.Timestamp.now(tz="America/Los_Angeles")
        date_key=now_pt.strftime("%Y%m%d")
        st.subheader(f"Today & Live — {now_pt.strftime('%b %d, %Y')}")

        try:
            raw=espn_scoreboard(tour,date_key)
            events=parse_espn_events(raw)
        except Exception as ex:
            events=[]
            st.error(f"Free live feed error: {ex}")

        live=[e for e in events if e["state"]=="in"]
        upcoming=[e for e in events if e["state"]=="pre"]
        completed=[e for e in events if e["state"]=="post"]

        st.caption(
            f"🟢 Free feed • {now_pt.strftime('%I:%M:%S %p PT')} • "
            f"{len(live)} live • {len(upcoming)} upcoming • {len(completed)} completed"
        )

        if live:
            st.markdown("### 🔴 Live now")
            for e in live:
                a=analyze_live_event(P,names,e,surface)
                with st.container(border=True):
                    c1,c2=st.columns([2,1])
                    c1.markdown(f"**{e['player1']} vs {e['player2']}**")
                    c1.caption(f"{e['status']} • score {e['score1']}–{e['score2']} • {e['tournament']}")
                    c2.markdown(f"**{a['prediction']}**")
                    if a["p1"] is not None:
                        c2.caption(f"{e['player1']}: {a['p1']*100:.1f}% • {e['player2']}: {a['p2']*100:.1f}%")
                    else:
                        c2.caption("Player history match unresolved")
        else:
            st.info("No live matches returned for this tour right now.")

        st.markdown("### Today's schedule")
        rows=[]
        for e in live+upcoming+completed:
            a=analyze_live_event(P,names,e,surface)
            rows.append({
                "Time":"" if pd.isna(e["start"]) else e["start"].tz_convert("America/Los_Angeles").strftime("%I:%M %p"),
                "Player 1":e["player1"],
                "Player 2":e["player2"],
                "Status":e["status"],
                "BAZI":a["prediction"],
                "P1":"" if a["p1"] is None else f"{a['p1']*100:.1f}%",
                "P2":"" if a["p2"] is None else f"{a['p2']*100:.1f}%",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        else:
            st.warning("No scoreboard events returned for today.")

        st.caption(
            "Today's schedule/live scores use ESPN's public website JSON feed with no API key. "
            "It is unofficial and can change without notice. BAZI predictions come from the historical ATP/WTA database."
        )

    with tabs[1]:
        c1,c2=st.columns(2)
        a=c1.selectbox("Player 1",names)
        b=c2.selectbox("Player 2",[x for x in names if x!=a])
        pa,pb=profile(P,a,surface),profile(P,b,surface)
        hh=h2h(P,a,b,surface)
        p,comp=predict(pa,pb,hh); q=1-p
        pick=a if p>=.5 else b; edge=max(p,q)
        status=f"✅ {pick}" if edge>=.68 else (f"🟡 Lean {pick}" if edge>=.60 else "🟡 WAIT — TOO CLOSE")
        st.header(status)
        st.subheader(f"{a}: {p*100:.1f}%  |  {b}: {q*100:.1f}%")
        m1,m2,m3,m4=st.columns(4)
        m1.metric(a+" rank","—" if pd.isna(pa["rank"]) else f"#{int(pa['rank'])}")
        m2.metric(b+" rank","—" if pd.isna(pb["rank"]) else f"#{int(pb['rank'])}")
        m3.metric(a+" last 10",f"{val(pa['recent'])*100:.0f}%")
        m4.metric(b+" last 10",f"{val(pb['recent'])*100:.0f}%")
        df=pd.DataFrame([
            {"Factor":k,"Edge":v,"Advantage":a if v>0 else (b if v<0 else "Even")}
            for k,v in comp.items()
        ])
        df["Edge"]=df.Edge.map(lambda x:f"{x:+.3f}")
        st.dataframe(df,use_container_width=True,hide_index=True)
        aw,bw,asw,bsw=hh
        st.write(f"**H2H:** {a} {aw}–{bw} {b}")
        st.write(f"**{surface} H2H:** {a} {asw}–{bsw} {b}")

    with tabs[2]:
        st.subheader(f"{tour} all-player analysis — {surface}")
        top=st.slider("Players to show",20,150,75,5)
        rows=[]
        for name in P.player.value_counts().head(800).index:
            pr=profile(P,name,surface)
            if pr["matches"]<8: continue
            score=.35*val(pr["recent"])+.30*val(pr["surface"])+.20*val(pr["year"])+.15*rs(pr["rank"])
            rows.append({
                "Player":name,
                "Rank":None if pd.isna(pr["rank"]) else int(pr["rank"]),
                "Recent 10":val(pr["recent"]),
                "Surface win%":val(pr["surface"]),
                "52w win%":val(pr["year"]),
                "BAZI score":score
            })
        board=pd.DataFrame(rows).sort_values("BAZI score",ascending=False).head(top)
        for c in ["Recent 10","Surface win%","52w win%","BAZI score"]:
            board[c]=board[c].map(lambda x:f"{x*100:.1f}%")
        st.dataframe(board,use_container_width=True,hide_index=True)

    with tabs[3]:
        name=st.selectbox("Player",names,key="profile")
        pr=profile(P,name,surface)
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Rank","—" if pd.isna(pr["rank"]) else f"#{int(pr['rank'])}")
        c2.metric("Last 10",f"{val(pr['recent'])*100:.1f}%")
        c3.metric(surface+" win%",f"{val(pr['surface'])*100:.1f}%")
        c4.metric("52-week",f"{val(pr['year'])*100:.1f}%")
        st.write(f"**Matches loaded:** {pr['matches']}")
        st.write(f"**Last match:** {pr['last'].date() if pd.notna(pr['last']) else '—'}")
        if pd.notna(pr["serve"]):
            st.write(f"**Recent serve score:** {pr['serve']*100:.1f}/100")
        r=pr["rows"][["date","opponent","surface","won","rank"]].copy()
        r["Result"]=r.won.map({1:"W",0:"L"})
        r=r.drop(columns="won").sort_values("date",ascending=False)
        st.dataframe(r,use_container_width=True,hide_index=True)

except Exception as e:
    st.error(f"BAZI Tennis error: {e}")

if auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
