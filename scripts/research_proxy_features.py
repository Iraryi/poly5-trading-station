"""Proxy backfill features: official BTC 5m outcomes (Sigrex mirror of Gamma, cross-checked 100% against PMData)
joined with Binance BTCUSDT 1s closes. Same feature formulas as research_features.py (Chainlink), strictly causal:
a 1s candle's close is only known at open_time+1s. Output rows are tagged price_source=binance_1s and are meant
for training only, never for the program judge's validation/audit.
"""
from pathlib import Path
import argparse,glob,io,json,math,zipfile
import numpy as np
import polars as pl

ROOT=Path('D:/POLYMARKET/research/proxy-backfill')
OFFSETS=(60,120,210,240,270)
REF=['spot_open_bps','twap_open_bps','spot_twap_bps','return_5s_bps','return_15s_bps','return_60s_bps','vol_60s_bps']

def load_binance(folder):
    times,closes=[],[]
    for path in sorted(glob.glob(str(Path(folder)/'BTCUSDT-1s-*.zip'))):
        with zipfile.ZipFile(path) as z:
            d=pl.read_csv(io.BytesIO(z.read(z.namelist()[0])),has_header=False,columns=[0,4],schema_overrides={'column_1':pl.Int64,'column_5':pl.Float64})
        d.columns=['t','c'];t=d['t'].to_numpy();t=np.where(t>1e14,t//1_000_000,t//1000)  # microseconds since 2025, milliseconds before
        times.append(t+1);closes.append(d['c'].to_numpy())             # +1: close becomes known when the candle ends
    t=np.concatenate(times);c=np.concatenate(closes);order=np.argsort(t,kind='stable')
    t,c=t[order],c[order];keep=np.r_[True,np.diff(t)>0]
    return t[keep],c[keep]

def window(known,close,ts):
    """Last 61 closes known at or before ts (seconds); None when stale or gapped (>5s), never reads the future."""
    i=np.searchsorted(known,ts,side='right')-1
    if i<60 or ts-known[i]>5 or known[i]-known[i-60]>65:return None
    return close[i-60:i+1]

def build(labels_csv,binance_folder,out):
    known,close=load_binance(binance_folder)
    m=pl.read_csv(labels_csv,infer_schema_length=20000).select('slug','condition_id','start_ts','label_up','winning_outcome').filter(pl.col('label_up').is_in([0,1])).sort('start_ts')
    rows,audit=[],[]
    for slug,condition,start,label,outcome in m.iter_rows():
        o=window(known,close,start);e=window(known,close,start+300)
        if o is not None and e is not None:
            open_twap,end_twap=float(np.mean(o[1:])),float(np.mean(e[1:]))
            audit.append({'slug':slug,'start_s':start,'month':str(np.datetime64(start,'s'))[:7],'label_up':label,
                          'twap_rule_up':int(end_twap>=open_twap),'spot_rule_up':int(e[-1]>=o[-1]),'move_bps':(end_twap/open_twap-1)*1e4})
        else:audit.append({'slug':slug,'start_s':start,'month':str(np.datetime64(start,'s'))[:7],'label_up':label,'twap_rule_up':None,'spot_rule_up':None,'move_bps':None})
        if o is None:continue
        opening=float(np.mean(o[1:]))
        for offset in OFFSETS:
            s=window(known,close,start+offset)
            if s is None:continue
            spot=s[-1];twap=float(np.mean(s[1:]))
            rows.append({'market_slug':slug,'condition_id':condition,'start_s':start,'decision_us':(start+offset)*1_000_000,'offset':offset,'label_up':label,
                'spot_open_bps':(spot/opening-1)*1e4,'twap_open_bps':(twap/opening-1)*1e4,'spot_twap_bps':(spot/twap-1)*1e4,
                'return_5s_bps':math.log(s[-1]/s[-6])*1e4,'return_15s_bps':math.log(s[-1]/s[-16])*1e4,'return_60s_bps':math.log(s[-1]/s[0])*1e4,
                'vol_60s_bps':float(np.std(np.diff(np.log(s)),ddof=1)*1e4),'price_source':'binance_1s','label_source':'polymarket official outcome (Sigrex mirror)'})
    a=pl.DataFrame(audit,infer_schema_length=None)
    big=a.filter(pl.col('move_bps').abs()>=3)
    monthly=a.group_by('month').agg(pl.len().alias('markets'),pl.col('move_bps').is_not_null().sum().alias('with_prices'),
        (pl.col('twap_rule_up')==pl.col('label_up')).mean().alias('twap_rule_agreement'),(pl.col('spot_rule_up')==pl.col('label_up')).mean().alias('spot_rule_agreement')).join(
        big.group_by('month').agg((pl.col('twap_rule_up')==pl.col('label_up')).mean().alias('twap_agreement_move_ge_3bps'),(pl.col('spot_rule_up')==pl.col('label_up')).mean().alias('spot_agreement_move_ge_3bps')),on='month',how='left').sort('month')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    pl.DataFrame(rows).write_parquet(out/'proxy-features-all.parquet');a.write_csv(out/'proxy-label-audit.csv');monthly.write_csv(out/'proxy-monthly-agreement.csv')
    print(monthly);print('feature rows',len(rows),'markets',a.height)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--labels',default=str(ROOT/'raw/polymarket_btc_up_down_5m_251218_260909.csv'))
    p.add_argument('--binance',default=str(ROOT/'raw/binance-1s'));p.add_argument('--out',default=str(ROOT))
    a=p.parse_args();build(a.labels,a.binance,a.out)
