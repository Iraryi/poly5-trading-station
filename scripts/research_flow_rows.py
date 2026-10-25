"""Order-flow features for the market-aware rows (PMData 2026-08-08..21), strictly before each decision moment.
Up-equivalent signed taker flow: BUY yes / SELL no = +shares, SELL yes / BUY no = -shares (taker side as recorded).
Adds flow_10s, flow_30s, trades_30s, imbalance5 (top-5 depth of the Up book), last_trade_gap (last Up-equivalent trade
price minus mid). Output: market-features-flow.parquet (same rows as market-features.parquet + these columns)."""
from pathlib import Path
import glob,json,sys,hashlib
import numpy as np,polars as pl
RESEARCH=Path('D:/POLYMARKET/research')

def build(out):
    base=pl.read_parquet(RESEARCH/'market-rows/market-features.parquet')
    tr=pl.concat([pl.read_parquet(p) for p in glob.glob(str(RESEARCH/'btc5m-v2-extended/raw/trades/**/*.parquet'),recursive=True)])
    tr=tr.filter(pl.col('event_type')=='last_trade_price').with_columns(pl.col('timestamp').dt.epoch('us').alias('ts'),
        (pl.when((pl.col('outcome')=='yes')==(pl.col('side')=='BUY')).then(1).otherwise(-1)*pl.col('size')).alias('signed'),
        pl.when(pl.col('outcome')=='yes').then(pl.col('price')).otherwise(1-pl.col('price')).alias('up_price')).sort('ts')
    trades={k[0]:(g['ts'].to_numpy(),g['signed'].to_numpy(),g['up_price'].to_numpy()) for k,g in tr.group_by(['market_slug'])}
    books={}
    for r in ('btc5m-v1','btc5m-v2-extended'):
        for p in glob.glob(str(RESEARCH/r/'checkpoint-books/*.parquet')):
            for row in pl.read_parquet(p,columns=['market_slug','at_us','bid_prices','bid_sizes','ask_prices','ask_sizes']).iter_rows(named=True):books[(row['market_slug'],row['at_us'])]=row
    extra=[]
    for r in base.iter_rows(named=True):
        t=r['decision_us'];ts,signed,price=trades.get(r['market_slug'],(np.array([]),np.array([]),np.array([])))
        k=np.searchsorted(ts,t,side='left')  # strictly before the decision
        def window(sec):
            j=np.searchsorted(ts,t-sec*1_000_000,side='left');return float(signed[j:k].sum()),k-j
        f10,_=window(10);f30,n30=window(30)
        st=books.get((r['market_slug'],t));bd=sum(sorted(st['bid_sizes'] and list(zip(st['bid_prices'],st['bid_sizes'])),reverse=True)[i][1] for i in range(min(5,len(st['bid_prices'] or [])))) if st else 0
        ad=sum(sorted(list(zip(st['ask_prices'],st['ask_sizes'])))[i][1] for i in range(min(5,len(st['ask_prices'] or [])))) if st else 0
        mid=(r['mkt_bid']+r['mkt_ask'])/2
        extra.append({'flow_10s':f10,'flow_30s':f30,'trades_30s':float(n30),'imbalance5':(bd-ad)/(bd+ad) if bd+ad>0 else 0.,'last_trade_gap':float(price[k-1]-mid) if k>0 else 0.})
    frame=pl.concat([base,pl.DataFrame(extra)],how='horizontal');out=Path(out);frame.write_parquet(out)
    meta={'rows':frame.height,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'columns_added':list(extra[0])}
    out.with_suffix('.json').write_text(json.dumps(meta,indent=1),encoding='utf-8');print(meta)
    print(frame.select(['flow_10s','flow_30s','trades_30s','imbalance5','last_trade_gap']).describe())

if __name__=='__main__':build(sys.argv[1] if len(sys.argv)>1 else RESEARCH/'market-rows/market-features-flow.parquet')
