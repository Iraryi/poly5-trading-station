"""Portable inference for audited numeric histogram trees, with no ML runtime."""
import json,math
from pathlib import Path

FEATURES=['stage_fraction','mid','spread','imbalance5','microprice_offset','mid_change_1s','mid_change_5s','book_age_s']

def predict_tree(nodes,x):
    index=0
    for _ in range(len(nodes)+1):
        node=nodes[index]
        if node['leaf']:return node['value']
        value=x[node['feature']]
        left=node['missing_left'] if math.isnan(value) else value<=node['threshold']
        index=node['left'] if left else node['right']
    raise ValueError('Invalid prediction tree')

def predict_ensemble(model,x):
    return model['baseline']+sum(predict_tree(tree,x) for tree in model['trees'])

class ROIModel:
    def __init__(self,path=None):
        path=Path(path) if path else Path(__file__).resolve().parents[1]/'assets/orderbook-roi-v1.json'
        self.data=json.loads(path.read_text(encoding='utf-8'))
        if self.data['features']!=FEATURES or self.data['schema_version']!=1:raise ValueError('ROI 模型格式不兼容')
        self.id=self.data['model_id']
    def predict(self,features):
        x=[float(features[name]) for name in FEATURES]
        if not all(math.isfinite(v) for v in x):raise ValueError('ROI 输入含无效值')
        return [{'horizon_s':int(h),'mean_roi':predict_ensemble(models['mean'],x),
                 'q20_roi':predict_ensemble(models['q20'],x)} for h,models in self.data['horizons'].items()]
