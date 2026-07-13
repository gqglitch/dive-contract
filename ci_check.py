#!/usr/bin/env python3
"""
ci_check.py -- dive-contract repo CI drift gate (F25, 2026-07-12).

Kills the C1/C2 defect class structurally: the wire-format truth must be ONE
thing. This script fails the push when any of the following drift apart:
  (1) the repo schemas at HEAD (the files in this repo),
  (2) the validator's EMBEDDED fallback copies (validate_dashboard.py),
  (3) a pair of known-good fixtures (built inline below) that every deployed
      run is expected to produce.

Usage (repo root, with validate_dashboard.py present or passed via env):
    python ci_check.py
Env:
    DIVE_VALIDATOR_PATH  path to validate_dashboard.py   (default ./validate_dashboard.py)
Exit codes: 0 = green · 1 = drift/failure (messages on stdout).

Wire into CI (GitHub Actions):
    - run: python ci_check.py
on every push touching *.schema.json or validate_dashboard.py.
"""
import json, os, sys, pathlib, importlib.util

ROOT = pathlib.Path(__file__).resolve().parent
FAILS = []

def fail(msg): FAILS.append(msg); print("CI-FAIL:", msg)
def ok(msg):   print("CI-OK  :", msg)

# ---------------------------------------------------------------- load schemas
try:
    p8 = json.load(open(ROOT / "phase8.schema.json"))
    p1 = json.load(open(ROOT / "phase1.schema.json"))
    ok("repo schemas parse")
except Exception as e:
    fail(f"repo schemas unreadable: {e}"); print("RESULT: FAIL"); sys.exit(1)

# ---------------------------------------------------------------- structural invariants
def expect(cond, msg):
    (ok if cond else fail)(msg)

expect(p8["properties"]["schemaVersion"].get("const") == "phase8.v2", "phase8 const == phase8.v2")
ep = p8["properties"]["detail"]["properties"]["entryPlan"]
expect("speculative" in ep["properties"]["mode"]["enum"], "entryPlan.mode carries 'speculative'")
expect(all(k in ep["properties"] for k in ("fwdIrrPct", "hurdlePct", "deepValueP25", "anchorBasis")),
       "four return-frame fields present in entryPlan")
expect("trendTrack" in ep["properties"], "entryPlan.trendTrack present")
expect("reunderwriteTriggers" in p8["properties"]["detail"]["properties"],
       "detail.reunderwriteTriggers present (rev v2.3 / DEC-35 wire slot)")
expect(p1["properties"]["schemaVersion"].get("const") == "phase1.v1", "phase1 const == phase1.v1")
expect(len(p1["required"]) == 61, f"phase1 required keys == 61 (got {len(p1['required'])})")

# ---------------------------------------------------------------- load validator
vpath = pathlib.Path(os.environ.get("DIVE_VALIDATOR_PATH", ROOT / "validate_dashboard.py"))
if not vpath.exists():
    fail(f"validator not found at {vpath} -- commit validate_dashboard.py to this repo or set DIVE_VALIDATOR_PATH")
    print("RESULT: FAIL"); sys.exit(1)

# point the validator's LIVE fetch at the repo files (file:// URLs), so this
# check validates against HEAD exactly as a deployed run validates against raw.
os.environ["DIVE_CONTRACT_URL"] = (ROOT / "phase8.schema.json").as_uri()
os.environ["DIVE_PHASE1_CONTRACT_URL"] = (ROOT / "phase1.schema.json").as_uri()
spec = importlib.util.spec_from_file_location("vd", vpath)
vd = importlib.util.module_from_spec(spec); spec.loader.exec_module(vd)
ok(f"validator loaded (__version__ {vd.__version__})")

# ---------------------------------------------------------------- embeds-vs-HEAD diff (the C1 gate)
def keypaths(sch, path=""):
    out = set()
    for k, v in sch.get("properties", {}).items():
        out.add(path + k)
        if isinstance(v, dict):
            out |= keypaths(v, path + k + ".")
    return out

emb8 = json.loads(vd._CONTRACT_JSON)
emb1 = json.loads(vd._PHASE1_CONTRACT_JSON)
d8 = keypaths(p8) ^ keypaths(emb8)
d1 = keypaths(p1) ^ keypaths(emb1)
expect(not d8, f"validator phase8 embed matches HEAD (symmetric diff: {sorted(d8) or 'none'})")
expect(not d1, f"validator phase1 embed matches HEAD (symmetric diff: {sorted(d1) or 'none'})")
expect(json.loads(vd._CONTRACT_JSON)["properties"]["detail"]["properties"].get("reunderwriteTriggers") is not None,
       "embedded phase8 carries reunderwriteTriggers")
m8 = emb8["properties"]["detail"]["properties"]["entryPlan"]["properties"]["mode"]["enum"]
expect("speculative" in m8, "embedded entryPlan.mode carries 'speculative'")

# ---------------------------------------------------------------- known-good fixtures (the C2 gate)
P8_FIXTURE = {
    "schemaVersion": "phase8.v2", "ticker": "CIFX", "name": "CI Fixture Corp", "exchange": "NASDAQ",
    "sector": "SaaS", "tags": ["ci", "fixture"], "analysisDate": "2026-07-12", "dataQuality": "High",
    "verdict": {"pill": "NEUTRAL", "detailed": "WATCH", "confidence": "Moderate",
                "oneLiner": "CI fixture: fairly priced quality; published re-entry at the hurdle anchor."},
    "score": {"mode": "value", "label": None, "diveScore": 62, "letter": "C", "quality": 66, "value": 60,
              "components": {"moat": 60, "returns": 75, "solvency": 80, "mgmt": 80, "ai": 70}, "notes": []},
    "speculative": None,
    "external": {"morningstar": {"star": 3, "moat": "Narrow", "fairValue": None,
                                 "uncertainty": "Medium", "fairValueNote": "STALE/SECONDARY -- 2024"},
                 "esgRisk": {"rating": "Low", "score": 14.2}, "asOf": "2026-07-12"},
    "analystConsensus": {"consensus": "Hold", "avgTarget": 105.0, "targetUpsidePct": 5.0, "high": 130.0,
                         "low": 80.0, "analystCount": 12, "asOf": "2026-07-12", "ratings": [],
                         "ratingsNote": "No data found -- firm-level list not sourceable"},
    "headline": {"price": 100.0, "currency": "USD", "fxToUsd": None, "entryPrice": 97.5,
                 "entryPremiumPct": 2.56, "bullFV": 140.0, "bullUpsidePct": 40.0},
    "metrics": {"revenue": "1.20B TTM", "avgDailyVol": "$85M", "shortInterest": "3.1% float"},
    "grades": {"mgmt": {"grade": "B", "note": "counted triggers 1 up / 0 down"},
               "aiStress": {"result": "PASS", "note": "Tangential; floor holds"},
               "cashStatus": "FCF+",
               "survival": {"score": 88, "label": "strong", "risk": False}},
    "flags": {"board": [{"key": "MGMT", "grade": "B", "color": "green"},
                        {"key": "CASH", "grade": "A", "color": "green"},
                        {"key": "VALUE", "grade": "C", "color": "amber"},
                        {"key": "AI", "grade": "B", "color": "green"},
                        {"key": "UPSIDE", "grade": "B", "color": "green"}], "up": 4, "down": 1},
    "catalysts": [{"date": "2026-08-05", "label": "Q2 print"}],
    "watch": "re-check at the published re-entry or the Q2 print",
    "report": {"filename": "cifx.md", "format": "md"},
    "detail": {
        "valuation": {"bear": 70.0, "base": 105.0, "bull": 140.0, "probWeighted": 105.0, "spread": 2.0,
                      "dcfBites": True, "gateImpliedGrowthPct": 12.0, "deliveredGrowthPct": 14.0,
                      "marginOfSafetyPct": 5.0, "frame": "DCF"},
        "distribution": {"p10": 68.0, "p25": 82.0, "p50": 104.0, "p75": 122.0, "p90": 141.0, "pValueGtPrice": 0.55},
        "sizing": {"kellySizePct": 2.5, "track": "core", "note": "half-Kelly, cap not binding"},
        "screens": {"moat": "Narrow", "roicVsWacc": "ROIC>WACC", "altmanZ": 4.2, "grossMarginPct": 74.0,
                    "fcfMarginPct": 22.0, "ruleOf40": 36.0, "revenueGrowthPct": 14.0, "netDebt": -250000000},
        "management": {"grade": "B", "promote": ["insider buying"], "demote": [], "insider": "net buying, 6m"},
        "corporateActions": [], "earningsQualityFlags": [],
        "sentiment": {"deRating": "multiple mid-range vs own 5yr", "priceVs52wHighPct": -12.0,
                      "analystAvgTarget": 105.0, "analystVsPrice": "+5%",
                      "high": 118.0, "low": 74.0, "midpoint": 96.0, "pricePctOfRange": 59.1},
        "ai": {"dependency": "Tangential", "floor": "declining-multiple floor holds"},
        "evidence": {"bull": ["NRR 118% (10-Q)"], "bear": ["decelerating net-new ARR (10-Q)"]},
        "keyQuestions": "does NRR hold above 115 through FY27",
        "assumptions": {"growth": "14% fading to 3%", "termFcfMargin": "24%", "wacc": "9.5% built",
                        "weights": "25/50/25 (DEC-25 default)", "authorStress": "verdict stable at all corners"},
        "politicalTies": [], "trumpFamilyMentions": [], "regulatoryExposure": [],
        "smartMoney": {"activists": [], "notableHolders": [], "shortTheses": []},
        "valuationMultiples": {"peTtm": 33.0, "peForward": 27.0, "peg": 1.9,
                               "earningsYield": 0.030, "peerMedianPe": 35.0},
        "sectorKPIs": {"nrr": "118%", "ruleOf40": 36},
        "trapType": "none dominant (secondary: re-rating)",
        "entryPlan": {"mode": "staged", "anchor": 97.5, "premiumPct": 2.56,
                      "tranches": [{"level": 100.0, "weightPct": 25, "trigger": "at market -- all core gates passed (miss-by-inaction guard)"},
                                   {"level": 97.5, "weightPct": 40, "trigger": "limit at the valuation anchor (hurdle entry, DEC-23)"},
                                   {"level": 70.0, "weightPct": 35, "trigger": "limit at the bear case -- thesis-intact add only"}],
                      "reentryLevel": None, "deepLevel": 70.0, "note": "price +2.6% above anchor: staged ladder",
                      "fwdIrrPct": 8.9, "hurdlePct": 8.8, "deepValueP25": 82.0,
                      "anchorBasis": "hurdle-entry (base-case PV at hurdle; DEC-23)",
                      "trendTrack": {"eligible": False, "status": "NOT-ELIGIBLE", "entryMarket": None,
                                     "entry": None, "entryPullback": None, "sizeCapPct": 1.0,
                                     "exitRule": "close below the 200-day MA", "ma50": None, "ma200": None,
                                     "priceVsMa200Pct": None, "note": "section-25 mandate did not fire"}},
        "reunderwriteTriggers": ["NRR stays above 115 (now 118)",
                                 "net-new ARR growth stays positive YoY (now +6%)",
                                 "F4: if the Q2 print re-accelerates ARR and price holds, a pass here is a Miss"],
        "notBacktested": True},
    "meta": {"generatedBy": "#01-Dive Phase 2", "engineVersion": "v4.5", "estimatedFields": []},
}

P1_FIXTURE = {
    "schemaVersion": "phase1.v1", "ticker": "CIFX", "name": "CI Fixture Corp", "exchange": "NASDAQ",
    "analysisDate": "2026-07-12", "priceCurrency": "USD", "fxToUsd": None, "priceAtAnalysis": 100.0,
    "sharesOutstandingDiluted": 100000000, "stage": "Growth", "sector": "SaaS",
    "moat": {"sources": ["switching costs"], "durability": "Narrow", "basis": "multi-year contracts, 118% NRR"},
    "revenue": 1200000000, "revenuePriorYear": 1052000000, "grossMargin": 74.0, "netIncome": 90000000,
    "ebit": 120000000, "ebitda": 180000000, "freeCashFlow": 264000000, "netDebt": -250000000,
    "sbcPctRevenue": 6.0, "roic": 14.0, "wacc": 9.5, "economicProfit": 38000000, "piotroskiF": 7,
    "beneishM": -2.4, "altmanZ": 4.2, "distressZone": "Safe", "netDebtToEbitda": None,
    "cashOnHand": 400000000, "quarterlyBurn": None, "runwayQuarters": None, "cashStatus": "Profitable",
    "sectorKPIs": {"nrr": "118%", "ruleOf40": 36},
    "range52w": {"high": 118.0, "low": 74.0, "midpoint": 96.0, "pricePctOfRange": 59.1},
    "shortInterestPct": 3.1, "prevShortInterestPct": 3.4, "insiderNet6mUsd": 1200000,
    "insiderActivity": "net buying; CEO open-market add in May", "avgDailyVolumeUsd": 85000000,
    "priceVs52wHighPct": -12.0, "priceChangeVsEpsChange": "px -8% / EPS +12% over 12mo",
    "currentMultipleVsHistory": "EV/EBITDA 18x vs 5yr range 16-30x, peer median 21x",
    "valuationMultiples": {"peTtm": 33.0, "peForward": 27.0, "peg": 1.9, "earningsYield": 0.030, "peerMedianPe": 35.0},
    "analystSnapshot": {"buy": 4, "hold": 7, "sell": 1, "medianTarget": 105.0,
                        "targetDispersion": "80-130", "recentChanges": []},
    "corporateActions": [], "reverseSplitRisk": False,
    "mgmtTriggers": {"promote": ["sustained insider buying"], "demote": []},
    "aiDependency": "Tangential", "pctRevenueNonAI": 85.0, "rateSensitivity": "Moderate",
    "govRevenuePct": 0.0, "politicalTies": [], "trumpFamilyMentions": [], "regulatoryExposure": [],
    "smartMoney": {"activists": [], "notableHolders": [], "shortTheses": []},
    "catalysts": [{"date": "2026-08-05", "label": "Q2 print"}],
    "bullEvidence": ["NRR 118% (10-Q)"], "bearEvidence": ["net-new ARR decelerating (10-Q)"],
    "dataQuality": "High", "estimatedFields": [],
    "interestCoverage": 24.0, "sbcPctFcf": 27.3,
}

r8 = vd.finalize_dashboard(P8_FIXTURE)
r1 = vd.finalize_phase1(P1_FIXTURE)
expect(r8["ok"], f"phase8 fixture validates against HEAD ({r8['validated_against']}): {r8['errors'][:4]}")
expect(r1["ok"], f"phase1 fixture validates against HEAD ({r1['validated_against']}): {r1['errors'][:4]}")
expect("LIVE" in r8["validated_against"], "phase8 validated against the repo files (file:// LIVE path), not the embed")

print()
if FAILS:
    print(f"RESULT: FAIL ({len(FAILS)} problem(s)) -- do not merge this push")
    sys.exit(1)
print("RESULT: PASS -- schemas, validator embeds, and fixtures are one truth")
