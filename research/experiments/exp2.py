"""Does the judge's validation ranking predict held-out (audit) quality? Uses archived trials only (read-only)."""
import json,sys,sqlite3
import numpy as np,joblib
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.modeling import split_rows
from scipy.stats import spearmanr
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
scopes=[r[0] for r in c.execute("select scope from trials where scope like 'late:%' group by scope order by min(created_ms)")]
for scope in scopes:
    trials=[json.loads(b) for (b,) in c.execute('select body from trials where scope=? and score is not null order by created_ms',(scope,))]
    if not trials:continue
    rows=json.load(open(trials[0]['snapshot']));_,_,audit=split_rows(rows)
    feats=None;xa=None;ya=np.array([r['y'] for r in audit])
    vs,aus,gens=[],[],[]
    for t in trials:
        b=joblib.load(t['model_path'])
        if xa is None:xa=np.array([[r['x'][k] for k in b['features']] for r in audit])
        p=b['model'].predict_proba(xa)[:,1];aus.append(float(np.mean((ya-p)**2)));vs.append(t['metrics']['score']);gens.append(t['generation'])
    vs,aus,gens=map(np.array,(vs,aus,gens))
    rho=spearmanr(vs,aus).correlation
    best_by_gen=[]
    for g in sorted(set(gens)):
        m=gens<=g;i=np.where(m)[0][np.argmin(vs[m])];best_by_gen.append((int(g),round(vs[i],5),round(aus[i],5)))
    top=np.argsort(vs)[:5]
    print(f'{scope[:30]} n={len(trials)} spearman(val,audit)={rho:+.2f} | audit of champion per gen (gen,val,audit): {best_by_gen}')
    print(f'   audit: champion {aus[np.argmin(vs)]:.5f}  median candidate {np.median(aus):.5f}  best possible {aus.min():.5f}  mean of top-5-by-val {aus[top].mean():.5f}')
