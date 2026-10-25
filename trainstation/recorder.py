"""Full order-book recorder for the live BTC 5m market (read-only market data, never orders).

Tables, written as parquet parts under root/orderbook/date=YYYY-MM-DD/<table>/part-<ms>.parquet (UTC date):
  books      every second, per outcome: top LEVELS bid/ask prices and sizes, book source/receive times, quality
  trades     every last_trade_price event: outcome, price, size, taker side, exchange and receive times
  bbo        every change of best bid/ask per outcome (sub-second dynamics), with exchange and receive times
  reference  every Chainlink spot / 60s TWAP point: observed and receive times, exact decimal value, snapshot flag
  markets    one row per market: slug, condition id, token ids, start/end
Buffers flush every FLUSH_S seconds (a crash loses at most that much). Recording pauses below MIN_FREE_GB free disk.
"""
from datetime import datetime,timezone
from pathlib import Path
import shutil
import time

LEVELS=20
FLUSH_S=300
MIN_FREE_GB=5
TABLES=('books','trades','bbo','reference','markets')

class OrderBookRecorder:
    def __init__(self,root,status=None,levels=LEVELS,flush_s=FLUSH_S,min_free_gb=MIN_FREE_GB):
        self.folder=Path(root)/'orderbook';self.status=status if status is not None else {}
        self.levels=levels;self.flush_s=flush_s;self.min_free=min_free_gb*1024**3
        self.buffers={t:[] for t in TABLES};self.last_flush=time.monotonic();self.last_second=None;self.last_bbo={}
        self.outcome={};self.counts={t:0 for t in TABLES};self.paused=False
    def on_market(self,market):
        fee=getattr(market,'fee',None)
        self.outcome={asset:side for side,asset in market.outcomes.items()}
        self.buffers['markets'].append({'slug':market.slug,'condition_id':market.condition_id,'up_token':market.outcomes.get('Up'),
            'down_token':market.outcomes.get('Down'),'start_s':int(market.start),'end_s':int(market.end),'tick':float(market.tick),
            'fee_rate':str(fee.rate) if fee else None,'fee_exponent':fee.exponent if fee else None})
    def snapshot(self,now_ms,market,books):
        """One row per outcome per wall-clock second (called often; records once per second)."""
        second=now_ms//1000
        if second==self.last_second or market is None:return
        self.last_second=second
        for side,asset in market.outcomes.items():
            b=books.get(asset)
            if b is None:continue
            bids=sorted(b.bids.items(),reverse=True)[:self.levels];asks=sorted(b.asks.items())[:self.levels]
            self.buffers['books'].append({'ts_ms':int(second*1000),'captured_ms':int(now_ms),'slug':market.slug,'outcome':side,'initialized':bool(b.initialized),'quality':b.quality,
                'source_ms':int(b.source_ms),'received_ms':int(b.received_ms),
                'bid_prices':[float(p) for p,_ in bids],'bid_sizes':[float(q) for _,q in bids],
                'ask_prices':[float(p) for p,_ in asks],'ask_sizes':[float(q) for _,q in asks]})
    def on_trade(self,item,now_ms,slug):
        asset=item.get('asset_id')
        try:price,size=float(item['price']),float(item['size'])
        except (KeyError,TypeError,ValueError):return
        self.buffers['trades'].append({'slug':slug,'outcome':self.outcome.get(asset,'?'),'price':price,'size':size,'side':str(item.get('side','')),
            'exchange_ms':int(item.get('timestamp') or 0),'received_ms':int(now_ms)})
    def on_book(self,asset,book,now_ms,slug,source_ms):
        """Record a best bid/ask change for this outcome (no row when nothing changed)."""
        bid=float(max(book.bids)) if book.bids else None;ask=float(min(book.asks)) if book.asks else None
        bid_size=float(book.bids[max(book.bids)]) if book.bids else None;ask_size=float(book.asks[min(book.asks)]) if book.asks else None
        key=(bid,ask,bid_size,ask_size)
        if self.last_bbo.get(asset)==key:return
        self.last_bbo[asset]=key
        self.buffers['bbo'].append({'slug':slug,'outcome':self.outcome.get(asset,'?'),'bid':bid,'ask':ask,'bid_size':bid_size,'ask_size':ask_size,
            'exchange_ms':int(source_ms),'received_ms':int(now_ms),'valid':bool(book.initialized and book.quality=='OK')})
    def on_reference(self,kind,observed_ms,value,now_ms,snapshot=False):
        # snapshot=True marks history replayed on (re)connect: old observed times, not live latency.
        self.buffers['reference'].append({'kind':kind,'observed_ms':int(observed_ms),'received_ms':int(now_ms),'value':str(value),'value_float':float(value),'snapshot':bool(snapshot)})
    def maybe_flush(self,force=False):
        if not force and time.monotonic()-self.last_flush<self.flush_s:return
        self.last_flush=time.monotonic()
        if shutil.disk_usage(self.folder.parent).free<self.min_free:
            # Never fill the disk: drop the buffers and say so (training data and models are kept).
            self.paused=True;self.buffers={t:[] for t in TABLES};self.status['recorder']=f'盘口记录已暂停：剩余磁盘不足 {self.min_free//1024**3}GB';return
        self.paused=False
        import polars as pl
        stamp=int(time.time()*1000)
        for table,rows in self.buffers.items():
            if not rows:continue
            frame=pl.DataFrame(rows,infer_schema_length=None)
            key='ts_ms' if table=='books' else 'received_ms' if table in ('trades','bbo','reference') else None
            days=frame[key].map_elements(lambda ms:datetime.fromtimestamp(ms/1000,timezone.utc).strftime('%Y-%m-%d'),return_dtype=pl.String) if key else pl.Series([datetime.now(timezone.utc).strftime('%Y-%m-%d')]*frame.height)
            for day in days.unique().to_list():
                part=frame.filter(days==day);target=self.folder/f'date={day}'/table;target.mkdir(parents=True,exist_ok=True)
                temp=target/f'part-{stamp}.tmp';part.write_parquet(temp,compression='zstd');temp.replace(target/f'part-{stamp}.parquet')
            self.counts[table]+=frame.height;self.buffers[table]=[]
        size=sum(p.stat().st_size for p in self.folder.rglob('*.parquet'))
        self.status['recorder']=f"盘口记录：快照 {self.counts['books']:,} · 成交 {self.counts['trades']:,} · 最优价变化 {self.counts['bbo']:,} · 累计 {size/1e6:,.0f} MB"
