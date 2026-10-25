"""Market-aware training rows: Chainlink reference features + the order book at the same decision moment (PMData,
2026-08-08..21). Output is the trainer's archive format (`market-features.parquet`) for the *_mkt tasks.
Executable prices: $5 budget walked through visible depth up to 0.99 with the official fee formula (research_execution),
for both outcomes (Down mirrors Up). Rows without a two-sided executable book are dropped, as the live collector does.
"""
from decimal import Decimal as D
from pathlib import Path
import hashlib,json,sys
import polars as pl
from research_execution import buy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

RESEARCH=Path('D:/POLYMARKET/research')
REF=['spot_open_bps','twap_open_bps','spot_twap_bps','return_5s_bps','return_15s_bps','return_60s_bps','vol_60s_bps']
QUARANTINE={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}

def side(state,name):
    if name=='Up':return {'valid':True,'ask_prices':list(state['ask_prices'] or []),'ask_sizes':list(state['ask_sizes'] or []),'bid_prices':[],'bid_sizes':[]}
    return {'valid':True,'ask_prices':[round(1-p,6) for p in state['bid_prices'] or []],'ask_sizes':list(state['bid_sizes'] or []),'bid_prices':[],'bid_sizes':[]}

def build(out):
    books={}
    for root in ('btc5m-v1','btc5m-v2-extended'):
        for p in sorted((RESEARCH/root/'checkpoint-books').glob('*.parquet')):
            for r in pl.read_parquet(p).iter_rows(named=True):
                if r['reason']!='reconstructed_top_mismatch':books[(r['market_slug'],r['at_us'])]=r
    feats=pl.read_parquet(RESEARCH/'btc5m-v2-extended/reference-features.parquet').filter(pl.col('label_up').is_not_null()&~pl.col('market_slug').is_in(list(QUARANTINE)))
    rows=[];missing=0;onesided=0
    for f in feats.iter_rows(named=True):
        state=books.get((f['market_slug'],f['decision_us']))
        if state is None:missing+=1;continue
        if not state['bid_prices'] or not state['ask_prices']:onesided+=1;continue
        up=buy(side(state,'Up'),D('.99'));down=buy(side(state,'Down'),D('.99'))
        if not up or not down:onesided+=1;continue
        rows.append({'market_slug':f['market_slug'],'start_s':f['start_s'],'decision_us':f['decision_us'],'offset':f['offset'],'label_up':f['label_up'],
            **{k:f[k] for k in REF},'mkt_bid':float(max(state['bid_prices'])),'mkt_ask':float(min(state['ask_prices'])),
            'up_shares':float(up['shares']),'up_total':float(up['total']),'down_shares':float(down['shares']),'down_total':float(down['total'])})
    frame=pl.DataFrame(rows);out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);frame.write_parquet(out)
    meta={'rows':frame.height,'markets':frame['market_slug'].n_unique(),'by_offset':frame.group_by('offset').len().sort('offset').to_dicts(),
          'dropped_no_book_state':missing,'dropped_one_sided_or_too_thin':onesided,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
          'sources':'PMData CC-BY-4.0 rev acb8cecf (Chainlink streams + L2), official outcomes','basis':'$5 budget incl. fees, depth walked to 0.99; paper only'}
    out.with_suffix('.json').write_text(json.dumps(meta,indent=1),encoding='utf-8');print(meta)

if __name__=='__main__':build(sys.argv[1] if len(sys.argv)>1 else RESEARCH/'market-rows/market-features.parquet')
