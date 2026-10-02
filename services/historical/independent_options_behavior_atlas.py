"""Descriptive behavior atlas for independent NIFTY options research.

Consumes the raw options research JSON and summarizes dynamic ATM +/-4 behavior.
No strategy signals, parameter optimization, or P&L logic are produced.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import pandas as pd
import numpy as np

def build_atlas(payload: dict) -> dict:
    u=pd.DataFrame(payload["underlying_market_rows"])
    o=pd.DataFrame(payload["option_candles"])
    u["timestamp"]=pd.to_datetime(u["timestamp"]); o["timestamp"]=pd.to_datetime(o["timestamp"])
    u["date"]=u.timestamp.dt.date
    u["atm"]=(np.floor(u.futures_close/50+0.5)*50).astype(int)
    contracts=payload["contract_by_date"]
    u["expiry"]=u.date.map(lambda d: contracts[str(d)])
    u["dte"]=[(pd.Timestamp(e).date()-d).days for d,e in zip(u.date,u.expiry)]
    dates=sorted(u.date.unique()); blocks={d:i//10+1 for i,d in enumerate(dates)}
    u["block"]=u.date.map(blocks)
    u=u.sort_values("timestamp")
    u["fut_ret_bps"]=u.groupby("date").futures_close.pct_change(fill_method=None)*10000

    o=o.sort_values(["expiry","strike","right","timestamp"])
    g=o.groupby(["expiry","strike","right"],group_keys=False)
    o["prev_ts"]=g.timestamp.shift()
    o["opt_ret_pct"]=g.close.pct_change(fill_method=None)*100
    o["opt_oi_chg"]=g.open_interest.diff()
    valid=(o.timestamp-o.prev_ts==pd.Timedelta(minutes=5))&(o.timestamp.dt.date==o.prev_ts.dt.date)
    o.loc[~valid,["opt_ret_pct","opt_oi_chg"]]=np.nan

    x=o.merge(u[["timestamp","date","atm","expiry","dte","block","fut_ret_bps"]],on="timestamp",suffixes=("","_wanted"))
    x["moneyness_steps"]=((x.strike-x.atm)/50).astype(int)
    x=x[(x.expiry==x.expiry_wanted)&x.moneyness_steps.between(-4,4)].copy()
    counts=x.groupby("timestamp").size()

    dte=[]
    for (dte,right),z in x.groupby(["dte","right"]):
        dte.append({"dte":int(dte),"right":right,"n":int(len(z)),
                    "median_volume":float(z.volume.median()),
                    "median_open_interest":float(z.open_interest.median()),
                    "median_abs_5m_return_pct":float(z.opt_ret_pct.abs().median()),
                    "median_abs_5m_oi_change":float(z.opt_oi_chg.abs().median())})

    concentration=[]
    vs=x.groupby(["dte","right","moneyness_steps"]).volume.sum().reset_index()
    vs["share"]=vs.volume/vs.groupby(["dte","right"]).volume.transform("sum")
    for (dte,right),z in vs.groupby(["dte","right"]):
        z=z.sort_values("share",ascending=False).head(3)
        concentration.append({"dte":int(dte),"right":right,
            "top_volume_moneyness":[{"steps":int(r.moneyness_steps),"share":float(r.share)} for r in z.itertuples()]})

    stability=[]
    for (block,right),z in x.groupby(["block","right"]):
        near=z[z.dte==0]; far=z[z.dte.isin([5,6])]
        stability.append({"block":int(block),"right":right,
            "expiry_vs_dte5_6_median_volume_ratio":float(near.volume.median()/far.volume.median()),
            "expiry_vs_dte5_6_median_abs_return_ratio":float(near.opt_ret_pct.abs().median()/far.opt_ret_pct.abs().median()),
            "volume_weighted_moneyness_steps":float(np.average(z.moneyness_steps,weights=z.volume))})

    return {"research_type":"NIFTY_OPTIONS_BEHAVIOR_ATLAS_V1","research_only":True,
        "method":{"dynamic_moneyness_steps":[-4,-3,-2,-1,0,1,2,3,4],
                  "contract_returns":"exact expiry+strike+right only; no stitching",
                  "chronological_blocks":"8 x 10 sessions","strategy_logic":False},
        "qa":{"sessions":int(u.date.nunique()),"underlying_rows":int(len(u)),
              "dynamic_option_rows":int(len(x)),"min_contracts_per_timestamp":int(counts.min()),
              "max_contracts_per_timestamp":int(counts.max())},
        "dte_summary":dte,"volume_concentration":concentration,"block_stability":stability}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    a=p.parse_args(); payload=json.loads(a.input.read_text(encoding="utf-8"))
    out=build_atlas(payload); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(out["qa"],indent=2))

if __name__=="__main__": main()
