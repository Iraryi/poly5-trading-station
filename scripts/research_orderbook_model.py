"""Fixed orderbook-only candidate for autonomous PAPER; no reference-feed substitution."""
import json,sys,hashlib
from pathlib import Path
import numpy as np,polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_squared_error
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from poly5.roi_model import FEATURES,ROIModel
from research_features import ROOT

OUT=ROOT.parent/'orderbook-roi-v1';OUT.mkdir(exist_ok=True)
def export(model):
    trees=[]
    for iteration in model._predictors:
        nodes=[]
        for n in iteration[0].nodes:
            assert not n['is_categorical']
            nodes.append({'leaf':bool(n['is_leaf']),'value':float(n['value']),'feature':int(n['feature_idx']),
                          'threshold':float(n['num_threshold']),'left':int(n['left']),'right':int(n['right']),
                          'missing_left':bool(n['missing_go_to_left'])})
        trees.append(nodes)
    return {'baseline':float(model._baseline_prediction[0,0]),'trees':trees}

def run():
    policy={'features':FEATURES,'horizons':[1,3,5,10,15,30],'mean_threshold':.02,'q20_threshold':.01,
            'entry_seconds':[60,120],'target_clip':[.01,.05],'stop_loss':-.05,'delay_s':.3,
            'model':'histogram gradient boosting, fixed parameters; mean + q20',
            'parameters':dict(max_iter=100,max_leaf_nodes=7,min_samples_leaf=50,learning_rate=.05,l2_regularization=10,early_stopping=False,random_state=507),
            'training':'2026-08-14 through 18; purged split excluded',
            'evaluation_note':'validation/test dates were previously scored for a different reference-feed model; not pristine prospective validation',
            'permission':'automatic PAPER research only; no live execution'}
    (OUT/'locked-policy.json').write_text(json.dumps(policy,indent=2),encoding='utf-8')
    data=pl.read_parquet(ROOT/'roi-dataset.parquet');frames={s:data.filter((pl.col('split')==s)&pl.col('entry_filled')) for s in ['train','validation','test']}
    X={s:f.select(FEATURES).to_numpy() for s,f in frames.items()}
    exported={'schema_version':1,'model_id':'orderbook-roi-v1','features':FEATURES,'paper_only':True,'horizons':{}}
    fitted={};metrics=[]
    for h in policy['horizons']:
        models={};fitted[h]={}
        for name in ['mean','q20']:
            kwargs={'loss':'squared_error'} if name=='mean' else {'loss':'quantile','quantile':.2}
            model=HistGradientBoostingRegressor(**kwargs,**policy['parameters']).fit(X['train'],frames['train'][f'roi_{h}s'].to_numpy())
            models[name]=export(model);fitted[h][name]=model
        exported['horizons'][str(h)]=models
        for s in ['validation','test']:
            p=fitted[h]['mean'].predict(X[s]);q=fitted[h]['q20'].predict(X[s]);y=frames[s][f'roi_{h}s'].to_numpy()
            metrics.append({'split':s,'horizon':h,'n':len(y),'mse':float(mean_squared_error(y,p)),
                            'q20_below_rate':float(np.mean(y<q)),'qualified_decision_count':int(np.sum((p>=.02)&(q>=.01)))})
    target=SOURCE/'assets/orderbook-roi-v1.json';target.write_text(json.dumps(exported,separators=(',',':')),encoding='utf-8')
    runtime=ROIModel(target);max_error=0.
    # Every evaluation vector must give the same prediction after portable export.
    for s in ['validation','test']:
        actual=[runtime.predict(dict(zip(FEATURES,row))) for row in X[s]]
        for h in policy['horizons']:
            for name in ['mean','q20']:
                values=np.array([next(r for r in a if r['horizon_s']==h)[name+'_roi'] for a in actual])
                max_error=max(max_error,float(np.max(np.abs(values-fitted[h][name].predict(X[s])))))
    assert max_error<1e-10
    report={'metrics':metrics,'portable_inference_max_abs_error':max_error,'model_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'paper_enabled':True,'live_enabled':False,'profitability_validated':False,'policy':policy}
    (OUT/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)

if __name__=='__main__':run()
