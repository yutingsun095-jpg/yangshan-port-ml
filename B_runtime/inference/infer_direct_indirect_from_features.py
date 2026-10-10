"""Feature-table Direct/Indirect inference.  Example:
python infer_direct_indirect_from_features.py --input ../08_examples/direct_indirect_feature_input_example.csv --output direct_output.csv
"""
import argparse, hashlib, joblib, numpy as np, pandas as pd
from pathlib import Path
def sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(8*1024*1024), b""): h.update(b)
    return h.hexdigest()
p=argparse.ArgumentParser(); p.add_argument("--input",required=True); p.add_argument("--output",required=True); p.add_argument("--model",default="../04_models/direct_indirect_random_forest.joblib"); p.add_argument("--input-version",default="A_input_unset"); p.add_argument("--input-hash",default="unprovided"); a=p.parse_args()
root=Path(__file__).resolve().parent; model_path=(root/a.model).resolve(); inp=Path(a.input)
df=pd.read_parquet(inp) if inp.suffix.lower()==".parquet" else pd.read_csv(inp); model=joblib.load(model_path); estimator=model.named_steps.get("classifier",model.named_steps.get("model")); setattr(estimator,"n_jobs",1) if hasattr(estimator,"n_jobs") else None
pre=model.named_steps["preprocessor"]; cols=list(pre.transformers[0][2])+list(pre.transformers[1][2]); missing=[c for c in cols if c not in df]
if missing: raise ValueError(f"Missing Direct/Indirect feature columns: {missing}")
p_indirect=model.predict_proba(df[cols])[:, list(model.classes_).index(1)]; time=pd.to_datetime(df["sample_time_utc"],utc=True); arr=pd.to_datetime(df["arr_time_utc"],utc=True)
out=pd.DataFrame({"task":"DIRECT_INDIRECT","model_version":"direct_indirect_rf_v1","prediction_time_utc":time,"data_available_until_utc":time,"event_id":df.get("arr_event_id",pd.Series([pd.NA]*len(df))),"port_call_id":df.get("port_call_id",pd.Series([pd.NA]*len(df))),"prediction":p_indirect,"unit":"probability_indirect","lead_time_min":(arr-time).dt.total_seconds()/60,"confidence":np.maximum(p_indirect,1-p_indirect),"uncertainty":"uncalibrated_probability","qc_flag":"ok_uncalibrated","a_input_version":a.input_version,"a_input_sha256":a.input_hash,"model_sha256":sha256(model_path),"decision_threshold":0.50,"predicted_class":np.where(p_indirect>=0.50,"Indirect","Direct")})
out.to_parquet(a.output,index=False) if str(a.output).endswith(".parquet") else out.to_csv(a.output,index=False,encoding="utf-8-sig")
