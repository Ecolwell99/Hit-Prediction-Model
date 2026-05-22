import math
from datetime import date, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Hit Matchup Pricer", layout="wide")

LEAGUE_HIT_PROB = 0.238

# Prototype database.
# Next production step: replace these dictionaries with an automatic data loader from
# an internal table, MLB Stats API, pybaseball cache, or scheduled ETL job.
PLAYER_DATABASE = {
    "hitters": {
        "Aaron Judge": {
            "hand": "R",
            "team": "NYY",
            "hit_skill": 0.275,
            "k_rate": 0.255,
            "bb_hbp_rate": 0.145,
            "hr_rate": 0.070,
            "bip_hit_rate": 0.360,
            "hard_hit": 0.565,
            "pitch_type_score": 0.035,
            "recent_form": 0.010,
        },
        "Shohei Ohtani": {
            "hand": "L",
            "team": "LAD",
            "hit_skill": 0.305,
            "k_rate": 0.225,
            "bb_hbp_rate": 0.125,
            "hr_rate": 0.065,
            "bip_hit_rate": 0.380,
            "hard_hit": 0.570,
            "pitch_type_score": 0.025,
            "recent_form": 0.015,
        },
        "Mookie Betts": {
            "hand": "R",
            "team": "LAD",
            "hit_skill": 0.290,
            "k_rate": 0.155,
            "bb_hbp_rate": 0.115,
            "hr_rate": 0.040,
            "bip_hit_rate": 0.335,
            "hard_hit": 0.455,
            "pitch_type_score": 0.020,
            "recent_form": 0.005,
        },
    },
    "pitchers": {
        "Gerrit Cole": {
            "hand": "R",
            "team": "NYY",
            "k_rate_allowed": 0.265,
            "bb_hbp_rate_allowed": 0.075,
            "hr_rate_allowed": 0.030,
            "bip_hit_rate_allowed": 0.285,
            "hard_hit_allowed": 0.365,
            "pitcher_quality_adj": -0.025,
        },
        "Framber Valdez": {
            "hand": "L",
            "team": "HOU",
            "k_rate_allowed": 0.230,
            "bb_hbp_rate_allowed": 0.095,
            "hr_rate_allowed": 0.020,
            "bip_hit_rate_allowed": 0.275,
            "hard_hit_allowed": 0.345,
            "pitcher_quality_adj": -0.020,
        },
        "Average Pitcher": {
            "hand": "R",
            "team": "MLB",
            "k_rate_allowed": 0.225,
            "bb_hbp_rate_allowed": 0.085,
            "hr_rate_allowed": 0.030,
            "bip_hit_rate_allowed": 0.300,
            "hard_hit_allowed": 0.395,
            "pitcher_quality_adj": 0.000,
        },
    },
}

PARK_ADJUSTMENTS = {
    "Neutral": 0.000,
    "Hitter Friendly": 0.015,
    "Pitcher Friendly": -0.015,
    "Coors-like Boost": 0.030,
}


def american_to_prob(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def prob_to_american(prob: float) -> int:
    prob = min(max(prob, 0.001), 0.999)
    if prob < 0.5:
        return round((100 / prob) - 100)
    return round(-100 * prob / (1 - prob))


def blend(a: float, b: float, wa: float = 0.55) -> float:
    return wa * a + (1 - wa) * b


def get_platoon_adjustment(hitter_hand: str, pitcher_hand: str) -> float:
    if hitter_hand == "L" and pitcher_hand == "R":
        return 0.012
    if hitter_hand == "R" and pitcher_hand == "L":
        return 0.010
    if hitter_hand == "L" and pitcher_hand == "L":
        return -0.018
    if hitter_hand == "R" and pitcher_hand == "R":
        return -0.006
    return 0.000


def matchup_hit_probability(hitter, pitcher, park_adj, uncertainty_shrink):
    k_prob = blend(hitter["k_rate"], pitcher["k_rate_allowed"], 0.60)
    bb_hbp_prob = blend(hitter["bb_hbp_rate"], pitcher["bb_hbp_rate_allowed"], 0.60)
    hr_prob = blend(hitter["hr_rate"], pitcher["hr_rate_allowed"], 0.65)
    hit_given_bip = blend(hitter["bip_hit_rate"], pitcher["bip_hit_rate_allowed"], 0.60)

    raw_bip_prob = 1 - k_prob - bb_hbp_prob - hr_prob
    raw_bip_prob = min(max(raw_bip_prob, 0.35), 0.78)

    raw_hit_prob = hr_prob + raw_bip_prob * hit_given_bip

    platoon_adj = get_platoon_adjustment(hitter["hand"], pitcher["hand"])

    matchup_adj = (
        hitter["pitch_type_score"]
        + hitter["recent_form"]
        + pitcher["pitcher_quality_adj"]
        + park_adj
        + platoon_adj
    )

    adjusted_prob = raw_hit_prob + matchup_adj

    final_prob = (1 - uncertainty_shrink) * adjusted_prob + uncertainty_shrink * LEAGUE_HIT_PROB
    final_prob = min(max(final_prob, 0.05), 0.60)

    components = {
        "K probability": k_prob,
        "BB/HBP probability": bb_hbp_prob,
        "HR probability": hr_prob,
        "BIP probability": raw_bip_prob,
        "Hit | BIP": hit_given_bip,
        "Platoon adjustment": platoon_adj,
        "Park adjustment": park_adj,
        "Pitch-type adjustment": hitter["pitch_type_score"],
        "Pitcher quality adjustment": pitcher["pitcher_quality_adj"],
        "Recent form adjustment": hitter["recent_form"],
        "Raw hit probability": raw_hit_prob,
        "Final hit probability": final_prob,
    }
    return final_prob, components


def edge_signal(edge: float) -> str:
    if edge >= 0.03:
        return "Value"
    if edge <= -0.03:
        return "No Value / Overpriced"
    return "Pass / Near Fair"


st.title("Hit Matchup Pricer")
st.caption("No uploads. Select hitter, pitcher, and book odds. The model prices the true hit probability.")

with st.sidebar:
    st.header("Inputs")
    hitter_name = st.selectbox("Hitter", sorted(PLAYER_DATABASE["hitters"].keys()))
    pitcher_name = st.selectbox("Pitcher", sorted(PLAYER_DATABASE["pitchers"].keys()))
    book_odds = st.number_input("Sportsbook odds", min_value=-500, max_value=1000, value=150, step=5)
    park = st.selectbox("Park context", list(PARK_ADJUSTMENTS.keys()))
    uncertainty_shrink = st.slider("Uncertainty shrink toward league avg", 0.00, 0.60, 0.25, 0.05)

hitter = PLAYER_DATABASE["hitters"][hitter_name]
pitcher = PLAYER_DATABASE["pitchers"][pitcher_name]
park_adj = PARK_ADJUSTMENTS[park]

model_prob, components = matchup_hit_probability(
    hitter=hitter,
    pitcher=pitcher,
    park_adj=park_adj,
    uncertainty_shrink=uncertainty_shrink,
)

book_prob = american_to_prob(int(book_odds))
fair_odds = prob_to_american(model_prob)
edge = model_prob - book_prob
signal = edge_signal(edge)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Model True Prob", f"{model_prob:.1%}")
col2.metric("Fair Odds", f"{fair_odds:+d}")
col3.metric("Book Implied Prob", f"{book_prob:.1%}")
col4.metric("Edge", f"{edge:+.1%}")

st.subheader(signal)

left, right = st.columns([1.1, 1])

with left:
    st.markdown("### Matchup Components")
    comp_df = pd.DataFrame(
        [{"Component": k, "Value": f"{v:.1%}"} for k, v in components.items()]
    )
    st.dataframe(comp_df, hide_index=True, use_container_width=True)

with right:
    st.markdown("### Player Context")
    st.write(f"**Hitter:** {hitter_name}, {hitter['team']}, bats {hitter['hand']}")
    st.write(f"**Pitcher:** {pitcher_name}, {pitcher['team']}, throws {pitcher['hand']}")
    st.write(f"**Book odds:** {int(book_odds):+d}")
    st.write(f"**Fair odds:** {fair_odds:+d}")
    st.write(f"**Model edge:** {edge:+.1%}")

    st.markdown("### Why")
    notes = [
        f"Platoon adjustment: {components['Platoon adjustment']:+.1%}.",
        f"Pitch-type adjustment: {components['Pitch-type adjustment']:+.1%}.",
        f"Pitcher quality adjustment: {components['Pitcher quality adjustment']:+.1%}.",
        f"Park adjustment: {components['Park adjustment']:+.1%}.",
        f"Shrinkage toward league average: {uncertainty_shrink:.0%}.",
    ]
    for note in notes:
        st.write("-", note)

st.divider()

st.markdown("### Production Data Path")
st.write(
    "The app should not require uploads. In production, this same interface can read from an automatic player table "
    "that updates daily. The user only selects players and enters the sportsbook price."
)

st.code(
    """
# Production direction:
# 1. Nightly job pulls batter, pitcher, pitch-type, platoon, park, and recent-form data.
# 2. Save a clean player_matchup_features table.
# 3. Streamlit reads that table directly.
# 4. User only inputs hitter, pitcher, and sportsbook odds.
""".strip(),
    language="python",
)

