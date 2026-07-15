r"""
validate_dashboard.py -- deterministic pre-flight gate for BOTH dive JSONs.
VERSION: v2.0 (2026-07-01 remediation)   [pinned in 01_Dive_Manifest.md]

WHAT IT GATES
  finalize_dashboard(obj) -> the Phase-8 dashboard JSON (contract: phase8.v2, rev v2.1)
  finalize_phase1(obj)    -> the Phase-1 fact JSON      (contract: phase1.v1)
STEP 10(a) must call BOTH; the turn may not end, neither inline JSON may print,
and <ticker>.md may not be written unless BOTH return ok=True.

DESIGN (live-first, self-healing)
  * PRIMARY: fetches the live contract from CONTRACT_URL (public raw.githubusercontent
    path the dashboard publishes on deploy -- the same file the importer uses, so they
    cannot drift). The phase1 contract is fetched from the sibling path. NOTE (fix C6):
    the live fetch IS the normal path and is verified working; the old "repo is
    private / fetch deliberately not used" claim was stale and is retired.
  * FALLBACK: on any fetch failure it validates against the EMBEDDED copies below (rev
    printed in validated_against; regenerated from repo HEAD 2026-07-12 -- F2/C1) and
    returns a LOUD `contract_warning` field -- print it; a silent fallback is how
    drift hides. The importer stays final authority either way.
  * v2 walker additions (fix C1):
      - `pattern` is now ENFORCED (the v1 walker silently skipped it, so the schema's
        no-angle-bracket XSS defense and all format regexes were dead locally).
      - EVERY string is sanitized (angle brackets stripped, whitespace collapsed)
        regardless of length, with a repair note -- not only on the over-length path.
      - string clamps to maxLength kept (trim to whole word).
  * HOUSE-REQUIRED overlay (fix M4/DEC-5): the site contract stays lenient/additive so
    historical .md files keep importing; THIS gate additionally requires presence
    (value may be null/[]/{}) of the no-silent-omission set:
      detail.smartMoney, detail.valuationMultiples, detail.sectorKPIs, detail.trapType,
      detail.entryPlan, detail.sentiment.{high,low,midpoint,pricePctOfRange},
      external.morningstar.uncertainty, headline.fxToUsd;
    plus the conditional pair: score.mode=='speculative' <=> speculative is an object
    (and score.label non-null); score.mode=='value' => speculative is null.
  * Zero dependencies (stdlib only).

REGENERATE the embedded copies when a contract changes:
    python -c "import json;s=json.load(open('phase8.schema.json'));\
    p=lambda n:{k:p(v) for k,v in n.items() if k not in('description','title','\$schema','\$id','\$comment','examples')} if isinstance(n,dict) else [p(x) for x in n] if isinstance(n,list) else n;\
    print(json.dumps(p(s),separators=(',',':')))"
    -> paste as _CONTRACT_JSON (and same recipe for phase1 -> _PHASE1_CONTRACT_JSON).
"""
import json, os, re, urllib.request

__version__ = "2.1"

# -- set ONCE: the public raw URL the dashboard publishes its contracts to -----------
CONTRACT_URL = os.environ.get("DIVE_CONTRACT_URL",
    "https://raw.githubusercontent.com/gqglitch/dive-contract/refs/heads/main/phase8.schema.json")
PHASE1_CONTRACT_URL = os.environ.get("DIVE_PHASE1_CONTRACT_URL",
    CONTRACT_URL.rsplit("/", 1)[0] + "/phase1.schema.json")

_EMBED_REV = {"phase8": "v2.3 (2026-07-12)", "phase1": "phase1.v1 + optional keys (2026-07-12)"}  # F2: the rev of the EMBEDDED copies below -- bump when regenerating
_CONTRACT_CACHE = {}          # {kind: {"schema":..., "source":..., "fallback":bool}}
_RX_CACHE = {}

def _fetch_live(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())

def _load_contract(kind="phase8"):
    """Live contract first; embedded fallback on any failure. Cached per run."""
    if kind in _CONTRACT_CACHE:
        c = _CONTRACT_CACHE[kind]
        return c["schema"], c["source"], c["fallback"]
    override = f"/mnt/project/{kind}.schema.json"
    url = CONTRACT_URL if kind == "phase8" else PHASE1_CONTRACT_URL
    embedded = _CONTRACT_JSON if kind == "phase8" else _PHASE1_CONTRACT_JSON
    fallback = False
    if os.path.exists(override):
        sch, src = json.load(open(override)), f"disk override {kind}.schema.json"
    else:
        try:
            sch, src = _fetch_live(url), f"LIVE {kind} contract (fetched)"
        except Exception as e:
            sch, src = json.loads(embedded), f"embedded {kind} fallback rev {_EMBED_REV.get(kind,'?')} (live fetch failed: {type(e).__name__})"
            fallback = True
    _CONTRACT_CACHE[kind] = dict(schema=sch, source=src, fallback=fallback)
    return sch, src, fallback


def _clean(s):                       # strip angle brackets + collapse whitespace
    return re.sub(r"\s+", " ", re.sub(r"[<>]", "", s)).strip()

def _trim(s, n):                     # trim to last whole word <= n
    s = _clean(s)
    if len(s) <= n: return s
    cut = s[:n].rstrip()
    if " " in cut: cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-")

def _rx(p):
    if p not in _RX_CACHE:
        _RX_CACHE[p] = re.compile(p)
    return _RX_CACHE[p]

# ---- zero-dependency draft-07 subset validator (sanitizes + clamps + patterns) -----
_JTYPES = {"null":(type(None),),"boolean":(bool,),"string":(str,),
          "array":(list,),"object":(dict,),
          "number":(int,float),"integer":(int,)}
def _typematch(val, t):
    types = t if isinstance(t, list) else [t]
    for name in types:
        py = _JTYPES.get(name, ())
        if name in ("number","integer") and isinstance(val, bool):
            continue                                   # bool is not a number here
        if isinstance(val, py):
            return True
    return False

def _walk(node, sch, path, parent, key, repairs, errors):
    if not isinstance(sch, dict):
        return
    t = sch.get("type")
    if t is not None and not _typematch(node, t):
        errors.append(f"{path or '<root>'}: expected type {t}, got {type(node).__name__}")
        return
    if "const" in sch and node != sch["const"]:
        errors.append(f"{path or '<root>'}: must equal {sch['const']!r} (got {node!r})")
    if "enum" in sch and node not in sch["enum"]:
        errors.append(f"{path or '<root>'}: {node!r} not in {sch['enum']}")
    if isinstance(node, str):
        cleaned = _clean(node)                          # C1: sanitize EVERY string
        if cleaned != node and parent is not None:
            parent[key] = cleaned
            repairs.append(f"{path}: sanitized (angle brackets/whitespace)")
            node = cleaned
        ml = sch.get("maxLength")
        if ml is not None and len(node) > ml and parent is not None:
            new = _trim(node, ml); parent[key] = new
            repairs.append(f"{path}: clamped {len(node)}->{len(new)} chars (maxLength {ml})")
            node = new
        if "minLength" in sch and len(node) < sch["minLength"]:
            errors.append(f"{path}: shorter than minLength {sch['minLength']}")
        pat = sch.get("pattern")                        # C1: patterns now ENFORCED
        if pat is not None and not _rx(pat).search(node):
            errors.append(f"{path}: {node!r} does not match pattern {pat!r}")
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in sch and node < sch["minimum"]:
            errors.append(f"{path}: {node} < minimum {sch['minimum']}")
        if "maximum" in sch and node > sch["maximum"]:
            errors.append(f"{path}: {node} > maximum {sch['maximum']}")
    if isinstance(node, dict):
        props = sch.get("properties", {})
        for r in sch.get("required", []):
            if r not in node:
                errors.append(f"{(path+'.' if path else '')}{r}: required field missing")
        if sch.get("additionalProperties", True) is False:
            for k in node:
                if k not in props:
                    errors.append(f"{(path+'.' if path else '')}{k}: unexpected key (additionalProperties:false)")
        for k, sub in props.items():
            if k in node:
                _walk(node[k], sub, f"{path+'.' if path else ''}{k}", node, k, repairs, errors)
    if isinstance(node, list):
        if "minItems" in sch and len(node) < sch["minItems"]:
            errors.append(f"{path}: fewer than minItems {sch['minItems']}")
        if "maxItems" in sch and len(node) > sch["maxItems"]:
            errors.append(f"{path}: more than maxItems {sch['maxItems']}")
        items = sch.get("items")
        if isinstance(items, dict):
            for i, el in enumerate(node):
                _walk(el, items, f"{path}[{i}]", node, i, repairs, errors)


# ---- HOUSE-REQUIRED overlay (M4/DEC-5): presence, not non-emptiness ----------------
_HOUSE_PATHS_PHASE8 = [
    ("detail", "smartMoney"), ("detail", "valuationMultiples"), ("detail", "sectorKPIs"),
    ("detail", "trapType"), ("detail", "entryPlan"),
    ("detail", "reunderwriteTriggers"),   # DEC-35 (F2/C2): emitted every run; [] never absent
    ("detail", "sentiment", "high"), ("detail", "sentiment", "low"),
    ("detail", "sentiment", "midpoint"), ("detail", "sentiment", "pricePctOfRange"),
    ("external", "morningstar", "uncertainty"), ("headline", "fxToUsd"),
]

def _house_phase8(obj, errors):
    for path in _HOUSE_PATHS_PHASE8:
        node = obj
        ok = True
        for k in path:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                ok = False; break
        if not ok:
            errors.append("HOUSE-REQUIRED: " + ".".join(path) +
                          " key missing (value may be null/[]/{} -- the KEY must exist; no silent omission)")
    score = obj.get("score", {}) if isinstance(obj.get("score"), dict) else {}
    mode = score.get("mode")
    spec = obj.get("speculative", "MISSING")
    if mode == "speculative":
        if not score.get("label"):
            errors.append("HOUSE-REQUIRED: score.label must be non-null in speculative mode (thresholds 22)")
        if not isinstance(spec, dict):
            errors.append("HOUSE-REQUIRED: speculative must be an object when score.mode=='speculative'")
    elif mode == "value":
        if spec is not None and spec != "MISSING":
            errors.append("HOUSE-REQUIRED: speculative must be null when score.mode=='value'")


# ---- public gates -------------------------------------------------------------------
def _finalize(obj, kind, house=None):
    repairs, errors = [], []
    schema, src, fallback = _load_contract(kind)
    _walk(obj, schema, "", None, None, repairs, errors)
    if house:
        house(obj, errors)
    seen = set(); errors = [e for e in errors if not (e in seen or seen.add(e))]
    out = {"ok": len(errors) == 0, "errors": errors, "repairs": repairs,
           "validated_against": src + " (pure-python strict, patterns enforced)"}
    if fallback:
        out["contract_warning"] = ("LIVE CONTRACT FETCH FAILED -- validated against the embedded copy. "
                                   "PRINT THIS LINE and verify CONTRACT_URL / the published schema before trusting a green.")
    return out

def finalize_dashboard(obj):
    """Sanitize+clamp, strict-validate the Phase-8 object (live contract or embedded
    fallback), then apply the house-required overlay. ok=False -> DO NOT emit."""
    return _finalize(obj, "phase8", house=_house_phase8)

def finalize_phase1(obj):
    """Sanitize+clamp and strict-validate the Phase-1 fact JSON against phase1.v1.
    ok=False -> DO NOT emit."""
    return _finalize(obj, "phase1")


_CONTRACT_JSON = r"""{"$schema":"http://json-schema.org/draft-07/schema#","$id":"https://equity.desk/schemas/phase8.v2.json","title":"Phase-8 Coverage Export (#01 - Dive)","description":"Single card-ready object per ticker. Validates the paste before Firestore write. No-silent-omission: required keys must be present (value, null, '\u2014', or 'No data found'). | v1.1: string maxLength + no-angle-bracket pattern added as defense-in-depth (AUDIT-02); the SITE must still HTML-escape/sanitize on render and before write. | v2: additive & NON-BREAKING (new fields are OPTIONAL \u2014 not added to any 'required' array \u2014 so v1-format pastes still validate; they validate the moment the \u00a720 generator starts emitting them). Added: detail.smartMoney (13D activists / 13F notable holders / short-seller theses), detail.valuationMultiples (trailing & forward P/E, PEG, earnings yield, peer-median P/E), and 52-week range (high/low/midpoint/pricePctOfRange) in detail.sentiment. No v1 field removed, changed, or newly-required. Generator should emit schemaVersion 'phase8.v2' once it populates these.","type":"object","additionalProperties":false,"required":["schemaVersion","ticker","name","exchange","sector","tags","analysisDate","dataQuality","verdict","score","speculative","external","analystConsensus","headline","metrics","grades","flags","catalysts","watch","report","detail","meta"],"properties":{"schemaVersion":{"const":"phase8.v2"},"ticker":{"type":"string","pattern":"^[A-Z0-9.\\-]{1,10}$","maxLength":10},"name":{"type":"string","minLength":1,"maxLength":600},"exchange":{"type":"string","minLength":1,"maxLength":600},"sector":{"type":"string","minLength":1,"maxLength":600},"tags":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"analysisDate":{"type":"string","pattern":"^\\d{4}-\\d{2}-\\d{2}$","maxLength":600},"dataQuality":{"enum":["High","Medium","Low"]},"verdict":{"type":"object","additionalProperties":false,"required":["pill","detailed","confidence","oneLiner"],"properties":{"pill":{"enum":["BULL","NEUTRAL","BEAR"]},"detailed":{"enum":["BUY-CANDIDATE","WATCH","PASS","AVOID","NOT-ANALYZABLE"]},"confidence":{"enum":["High","Moderate","Low"]},"oneLiner":{"type":"string","minLength":1,"maxLength":160,"pattern":"^[^<>]*$"}}},"headline":{"type":"object","additionalProperties":false,"required":["price","currency","entryPrice","entryPremiumPct","bullFV","bullUpsidePct"],"properties":{"price":{"type":"number"},"currency":{"type":"string","maxLength":600},"entryPrice":{"type":["number","null"]},"entryPremiumPct":{"type":["number","null"]},"bullFV":{"type":["number","null"]},"bullUpsidePct":{"type":["number","null"]},"fxToUsd":{"type":["number","null"]}}},"metrics":{"type":"object","additionalProperties":false,"required":["revenue","avgDailyVol","shortInterest"],"properties":{"revenue":{"type":"string","maxLength":600},"avgDailyVol":{"type":"string","maxLength":600},"shortInterest":{"type":"string","maxLength":600}}},"grades":{"type":"object","additionalProperties":false,"required":["mgmt","aiStress","cashStatus","survival"],"properties":{"mgmt":{"type":"object","additionalProperties":false,"required":["grade","note"],"properties":{"grade":{"type":"string","pattern":"^([A-F][+\\-]?|\u2014)$","maxLength":600},"note":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"aiStress":{"type":"object","additionalProperties":false,"required":["result","note"],"properties":{"result":{"enum":["PASS","FAIL","N/A"]},"note":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"cashStatus":{"enum":["FCF+","SELF-FUNDING","CASH-BURN","PRE-REV"]},"survival":{"type":"object","additionalProperties":false,"required":["score","label","risk"],"properties":{"score":{"type":["integer","null"],"minimum":0,"maximum":100},"label":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"risk":{"type":"boolean"}}}}},"flags":{"type":"object","additionalProperties":false,"required":["board","up","down"],"properties":{"board":{"type":"array","minItems":5,"maxItems":5,"items":{"type":"object","additionalProperties":false,"required":["key","grade","color"],"properties":{"key":{"enum":["MGMT","CASH","VALUE","AI","UPSIDE"]},"grade":{"type":"string","pattern":"^([A-F][+\\-]?|\u2014)$","maxLength":600},"color":{"enum":["green","amber","red","grey"]}}}},"up":{"type":"integer","minimum":0,"maximum":5},"down":{"type":"integer","minimum":0,"maximum":5}}},"catalysts":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["date","label"],"properties":{"date":{"type":"string","maxLength":600},"label":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}}},"watch":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"report":{"type":"object","additionalProperties":false,"required":["filename","format"],"properties":{"filename":{"type":"string","maxLength":600},"format":{"enum":["md","pdf"]}}},"detail":{"type":"object","additionalProperties":false,"required":["valuation","distribution","sizing","screens","management","corporateActions","earningsQualityFlags","sentiment","ai","evidence","keyQuestions","assumptions","politicalTies","trumpFamilyMentions","regulatoryExposure","notBacktested"],"properties":{"valuation":{"type":"object","additionalProperties":false,"required":["bear","base","bull","probWeighted","spread","dcfBites","gateImpliedGrowthPct","deliveredGrowthPct","marginOfSafetyPct","frame"],"properties":{"bear":{"type":["number","null"]},"base":{"type":["number","null"]},"bull":{"type":["number","null"]},"probWeighted":{"type":["number","null"]},"spread":{"type":["number","null"]},"dcfBites":{"type":"boolean"},"gateImpliedGrowthPct":{"type":["number","null"]},"deliveredGrowthPct":{"type":["number","null"]},"marginOfSafetyPct":{"type":["number","null"]},"frame":{"enum":["DCF","NAV/EBITDA","story-to-numbers","not-analyzable"]}}},"distribution":{"type":"object","additionalProperties":false,"required":["p10","p25","p50","p75","p90","pValueGtPrice"],"properties":{"p10":{"type":["number","null"]},"p25":{"type":["number","null"]},"p50":{"type":["number","null"]},"p75":{"type":["number","null"]},"p90":{"type":["number","null"]},"pValueGtPrice":{"type":["number","null"]}}},"sizing":{"type":"object","additionalProperties":false,"required":["kellySizePct","track","note"],"properties":{"kellySizePct":{"type":["number","null"]},"track":{"enum":["core","optionality"]},"note":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"screens":{"type":"object","additionalProperties":false,"required":["moat","roicVsWacc","altmanZ","grossMarginPct","fcfMarginPct","ruleOf40","revenueGrowthPct","netDebt"],"properties":{"moat":{"type":"string","maxLength":80,"pattern":"^[^<>]*$"},"roicVsWacc":{"type":"string","maxLength":600},"altmanZ":{"type":["number","null"]},"grossMarginPct":{"type":["number","null"]},"fcfMarginPct":{"type":["number","null"]},"ruleOf40":{"type":["number","null"]},"revenueGrowthPct":{"type":["number","null"]},"netDebt":{"type":["integer","number","null"]}}},"management":{"type":"object","additionalProperties":false,"required":["grade","promote","demote","insider"],"properties":{"grade":{"type":"string","pattern":"^([A-F][+\\-]?|\u2014)$","maxLength":600},"promote":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"demote":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"insider":{"type":"string","maxLength":600}}},"corporateActions":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"earningsQualityFlags":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"sentiment":{"type":"object","additionalProperties":false,"required":["deRating","priceVs52wHighPct","analystAvgTarget","analystVsPrice"],"properties":{"deRating":{"type":"string","maxLength":600},"priceVs52wHighPct":{"type":["number","null"]},"analystAvgTarget":{"type":["number","null"]},"analystVsPrice":{"type":"string","maxLength":600},"high":{"type":["number","null"]},"low":{"type":["number","null"]},"midpoint":{"type":["number","null"]},"pricePctOfRange":{"type":["number","null"]}}},"ai":{"type":"object","additionalProperties":false,"required":["dependency","floor"],"properties":{"dependency":{"enum":["Immune","Tangential","Moderate","High","Existential","Unassessed"]},"floor":{"type":"string","maxLength":600}}},"evidence":{"type":"object","additionalProperties":false,"required":["bull","bear"],"properties":{"bull":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"bear":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"keyQuestions":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"assumptions":{"type":"object","additionalProperties":false,"required":["growth","termFcfMargin","wacc","weights","authorStress"],"properties":{"growth":{"type":"string","maxLength":600},"termFcfMargin":{"type":"string","maxLength":600},"wacc":{"type":"string","maxLength":600},"weights":{"type":"string","maxLength":600},"authorStress":{"type":"string","maxLength":600}}},"politicalTies":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"trumpFamilyMentions":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"regulatoryExposure":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"notBacktested":{"type":"boolean"},"smartMoney":{"type":"object","additionalProperties":false,"required":["activists","notableHolders","shortTheses"],"properties":{"activists":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"notableHolders":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"shortTheses":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"valuationMultiples":{"type":"object","additionalProperties":false,"required":["peTtm","peForward","peg","earningsYield","peerMedianPe"],"properties":{"peTtm":{"type":["number","null"]},"peForward":{"type":["number","null"]},"peg":{"type":["number","null"]},"earningsYield":{"type":["number","null"]},"peerMedianPe":{"type":["number","null"]}}},"sectorKPIs":{"type":"object","additionalProperties":{"type":["string","number","null"],"maxLength":600}},"trapType":{"type":["string","null"],"maxLength":120,"pattern":"^[^<>]*$"},"entryPlan":{"type":["object","null"],"additionalProperties":false,"required":["mode","anchor","premiumPct","tranches","reentryLevel","deepLevel","note"],"properties":{"mode":{"enum":["buy-zone","staged","watch","no-entry","speculative"]},"anchor":{"type":["number","null"]},"premiumPct":{"type":["number","null"]},"tranches":{"type":"array","maxItems":4,"items":{"type":"object","additionalProperties":false,"required":["level","weightPct","trigger"],"properties":{"level":{"type":["number","null"]},"weightPct":{"type":"number"},"trigger":{"type":"string","maxLength":200,"pattern":"^[^<>]*$"}}}},"reentryLevel":{"type":["number","null"]},"deepLevel":{"type":["number","null"]},"note":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"trendTrack":{"type":["object","null"],"additionalProperties":false,"properties":{"eligible":{"type":"boolean"},"status":{"enum":["IN-TREND","NOT-ELIGIBLE","NO-DATA"]},"entryMarket":{"type":["string","null"],"maxLength":20,"pattern":"^[^<>]*$"},"entry":{"type":["string","null"],"maxLength":20,"pattern":"^[^<>]*$"},"entryPullback":{"type":["number","null"]},"sizeCapPct":{"type":["number","null"]},"exitRule":{"type":["string","null"],"maxLength":200,"pattern":"^[^<>]*$"},"ma50":{"type":["number","null"]},"ma200":{"type":["number","null"]},"priceVsMa200Pct":{"type":["number","null"]},"note":{"type":["string","null"],"maxLength":400,"pattern":"^[^<>]*$"}}},"fwdIrrPct":{"type":["number","null"]},"hurdlePct":{"type":["number","null"]},"deepValueP25":{"type":["number","null"]},"anchorBasis":{"type":["string","null"],"maxLength":120,"pattern":"^[^<>]*$"}}},"reunderwriteTriggers":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"meta":{"type":"object","additionalProperties":false,"required":["generatedBy","engineVersion","estimatedFields"],"properties":{"generatedBy":{"type":"string","maxLength":600},"engineVersion":{"type":"string","maxLength":600},"estimatedFields":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"score":{"type":"object","additionalProperties":false,"required":["mode","diveScore","letter","quality","value","components","notes"],"properties":{"diveScore":{"type":["integer","null"],"minimum":0,"maximum":100},"letter":{"type":"string","pattern":"^([A-F]|SPEC|NA)$","maxLength":600},"quality":{"type":["integer","null"],"minimum":0,"maximum":100},"value":{"type":["integer","null"],"minimum":0,"maximum":100},"components":{"type":"object","additionalProperties":false,"required":["moat","returns","solvency","mgmt","ai"],"properties":{"moat":{"type":["number","null"]},"returns":{"type":["number","null"]},"solvency":{"type":["number","null"]},"mgmt":{"type":["number","null"]},"ai":{"type":["number","null"]}}},"notes":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"mode":{"enum":["value","speculative"]},"label":{"type":["string","null"]}}},"external":{"type":"object","additionalProperties":false,"required":["morningstar","esgRisk","asOf"],"properties":{"morningstar":{"type":"object","additionalProperties":false,"required":["star","moat","fairValue"],"properties":{"star":{"type":["integer","null"],"minimum":1,"maximum":5},"moat":{"enum":["None","Narrow","Wide","No data found"]},"fairValue":{"type":["number","null"]},"uncertainty":{"type":["string","null"],"maxLength":80,"pattern":"^[^<>]*$"},"fairValueNote":{"type":["string","null"],"maxLength":200,"pattern":"^[^<>]*$"}}},"esgRisk":{"type":"object","additionalProperties":false,"required":["rating","score"],"properties":{"rating":{"enum":["Negligible","Low","Medium","High","Severe","No data found"]},"score":{"type":["number","null"]}}},"asOf":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"analystConsensus":{"type":"object","additionalProperties":false,"required":["consensus","avgTarget","targetUpsidePct","high","low","analystCount","asOf","ratings","ratingsNote"],"properties":{"consensus":{"enum":["Strong Buy","Buy","Hold","Sell","Strong Sell","No data found"]},"avgTarget":{"type":["number","null"]},"targetUpsidePct":{"type":["number","null"]},"high":{"type":["number","null"]},"low":{"type":["number","null"]},"analystCount":{"type":["integer","null"]},"asOf":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"ratings":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["firm","rating","target","date"],"properties":{"firm":{"type":"string","maxLength":80,"pattern":"^[^<>]*$"},"rating":{"type":"string","maxLength":80,"pattern":"^[^<>]*$"},"target":{"type":["number","null"]},"date":{"type":"string","maxLength":600}}}},"ratingsNote":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"speculative":{"type":["object","null"],"additionalProperties":false,"required":["payoffTree","ev","evVsPricePct","marketImpliedTopProb","yourTopProb","qualitative","read"],"properties":{"payoffTree":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["label","value","prob"],"properties":{"label":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"},"value":{"type":"number"},"prob":{"type":"number"}}}},"ev":{"type":["number","null"]},"evVsPricePct":{"type":["number","null"]},"marketImpliedTopProb":{"type":["number","null"]},"yourTopProb":{"type":["number","null"]},"qualitative":{"type":"object","additionalProperties":false,"required":["founder","tam","moatFormation","missionCriticality","downsideSurvivable"],"properties":{"founder":{"type":"string","maxLength":600},"tam":{"type":"string","maxLength":600},"moatFormation":{"type":"string","maxLength":600},"missionCriticality":{"type":"string","maxLength":600},"downsideSurvivable":{"type":"string","maxLength":600}}},"read":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}}},"$comment":"rev v2.3 (2026-07-12) -- additive over v2.2-CONSOLIDATED: detail.reunderwriteTriggers (DEC-35 wire slot, F1); ticker maxLength 10 (F23). No const bump; historical objects remain valid."}"""

_PHASE1_CONTRACT_JSON = r"""{"$comment":"2026-07-12 additive (F18): optional interestCoverage (EBIT/interest) + sbcPctFcf (SBC as % of FCF) -- properties only, NOT in required[]; the 61-required-keys contract is unchanged.","type":"object","additionalProperties":false,"properties":{"schemaVersion":{"const":"phase1.v1"},"ticker":{"type":"string","pattern":"^[A-Z0-9.\\-]{1,10}$","maxLength":10},"name":{"type":"string","maxLength":600,"pattern":"^[^<>]*$","minLength":1},"exchange":{"type":"string","maxLength":600,"pattern":"^[^<>]*$","minLength":1},"analysisDate":{"type":"string","pattern":"^\\d{4}-\\d{2}-\\d{2}$","maxLength":10},"priceCurrency":{"type":"string","maxLength":10,"pattern":"^[^<>]*$","minLength":1},"fxToUsd":{"type":["number","null"]},"priceAtAnalysis":{"type":"number"},"sharesOutstandingDiluted":{"type":["integer","number","null"]},"stage":{"enum":["Pre-revenue","Growth","Mature","Turnaround"]},"sector":{"type":"string","maxLength":120,"pattern":"^[^<>]*$","minLength":1},"moat":{"type":"object","additionalProperties":false,"required":["sources","durability","basis"],"properties":{"sources":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"durability":{"enum":["Wide","Narrow","None"]},"basis":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}},"revenue":{"type":["integer","number","null"]},"revenuePriorYear":{"type":["integer","number","null"]},"grossMargin":{"type":["number","null"]},"netIncome":{"type":["integer","number","null"]},"ebit":{"type":["integer","number","null"]},"ebitda":{"type":["integer","number","null"]},"freeCashFlow":{"type":["integer","number","null"]},"netDebt":{"type":["integer","number","null"]},"sbcPctRevenue":{"type":["number","null"]},"roic":{"type":["number","null"]},"wacc":{"type":["number","null"]},"economicProfit":{"type":["integer","number","null"]},"piotroskiF":{"type":["integer","null"],"minimum":0,"maximum":9},"beneishM":{"type":["number","null"]},"altmanZ":{"type":["number","null"]},"distressZone":{"enum":["Safe","Grey","Distress",null]},"netDebtToEbitda":{"type":["number","null"]},"cashOnHand":{"type":["integer","number","null"]},"quarterlyBurn":{"type":["integer","number","null"]},"runwayQuarters":{"type":["number","null"]},"cashStatus":{"enum":["Profitable","Self-funding","Burning"]},"sectorKPIs":{"type":"object","additionalProperties":{"type":["string","number","null"],"maxLength":600}},"range52w":{"type":"object","additionalProperties":false,"required":["high","low","midpoint","pricePctOfRange"],"properties":{"high":{"type":["number","null"]},"low":{"type":["number","null"]},"midpoint":{"type":["number","null"]},"pricePctOfRange":{"type":["number","null"]}}},"shortInterestPct":{"type":["number","null"]},"prevShortInterestPct":{"type":["number","null"]},"insiderNet6mUsd":{"type":["integer","number","null"]},"insiderActivity":{"type":"string","maxLength":600,"pattern":"^[^<>]*$","minLength":1},"avgDailyVolumeUsd":{"type":["integer","number","null"]},"priceVs52wHighPct":{"type":["number","null"]},"priceChangeVsEpsChange":{"type":["string","null"],"maxLength":400,"pattern":"^[^<>]*$"},"currentMultipleVsHistory":{"type":["string","null"],"maxLength":400,"pattern":"^[^<>]*$"},"valuationMultiples":{"type":"object","additionalProperties":false,"required":["peTtm","peForward","peg","earningsYield","peerMedianPe"],"properties":{"peTtm":{"type":["number","null"]},"peForward":{"type":["number","null"]},"peg":{"type":["number","null"]},"earningsYield":{"type":["number","null"]},"peerMedianPe":{"type":["number","null"]}}},"analystSnapshot":{"type":"object","additionalProperties":false,"required":["buy","hold","sell","medianTarget","targetDispersion","recentChanges"],"properties":{"buy":{"type":["integer","null"]},"hold":{"type":["integer","null"]},"sell":{"type":["integer","null"]},"medianTarget":{"type":["number","null"]},"targetDispersion":{"type":["string","null"],"maxLength":200,"pattern":"^[^<>]*$"},"recentChanges":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"corporateActions":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"reverseSplitRisk":{"type":["boolean","null"]},"mgmtTriggers":{"type":"object","additionalProperties":false,"required":["promote","demote"],"properties":{"promote":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"demote":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"aiDependency":{"enum":["Immune","Tangential","Moderate","High","Existential"]},"pctRevenueNonAI":{"type":["number","null"]},"rateSensitivity":{"enum":["High","Moderate","Low","Inverse"]},"govRevenuePct":{"type":["number","null"]},"politicalTies":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"trumpFamilyMentions":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"regulatoryExposure":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"smartMoney":{"type":"object","additionalProperties":false,"required":["activists","notableHolders","shortTheses"],"properties":{"activists":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"notableHolders":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"shortTheses":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}}}},"catalysts":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["date","label"],"properties":{"date":{"type":"string","maxLength":60,"pattern":"^[^<>]*$"},"label":{"type":"string","maxLength":400,"pattern":"^[^<>]*$"}}}},"bullEvidence":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"bearEvidence":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"dataQuality":{"enum":["High","Medium","Low"]},"estimatedFields":{"type":"array","items":{"type":"string","maxLength":600,"pattern":"^[^<>]*$"}},"interestCoverage":{"type":["number","null"]},"sbcPctFcf":{"type":["number","null"]}},"required":["schemaVersion","ticker","name","exchange","analysisDate","priceCurrency","fxToUsd","priceAtAnalysis","sharesOutstandingDiluted","stage","sector","moat","revenue","revenuePriorYear","grossMargin","netIncome","ebit","ebitda","freeCashFlow","netDebt","sbcPctRevenue","roic","wacc","economicProfit","piotroskiF","beneishM","altmanZ","distressZone","netDebtToEbitda","cashOnHand","quarterlyBurn","runwayQuarters","cashStatus","sectorKPIs","range52w","shortInterestPct","prevShortInterestPct","insiderNet6mUsd","insiderActivity","avgDailyVolumeUsd","priceVs52wHighPct","priceChangeVsEpsChange","currentMultipleVsHistory","valuationMultiples","analystSnapshot","corporateActions","reverseSplitRisk","mgmtTriggers","aiDependency","pctRevenueNonAI","rateSensitivity","govRevenuePct","politicalTies","trumpFamilyMentions","regulatoryExposure","smartMoney","catalysts","bullEvidence","bearEvidence","dataQuality","estimatedFields"]}"""
