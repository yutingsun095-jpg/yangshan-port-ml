"""Feature-table ARR→BER inference.  Example:
python infer_arr_to_ber_from_features.py --input ../08_examples/arr_to_ber_feature_input_example.csv --output waiting_output.csv
"""
import argparse, hashlib, joblib, numpy as np, pandas as pd
from pathlib import Path
def sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(8*1024*1024), b""): h.update(b)
    return h.hexdigest()
p=argparse.ArgumentParser(); p.add_argument("--input",required=True); p.add_argument("--output",required=True); p.add_argument("--model",default="../04_models/arr_to_ber_final_random_forest.joblib"); p.add_argument("--input-version",default="A_input_unset"); p.add_argument("--input-hash",default="unprovided"); a=p.parse_args()
root=Path(__file__).resolve().parent; model_path=(root/a.model).resolve(); inp=Path(a.input)
df=pd.read_parquet(inp) if inp.suffix.lower()==".parquet" else pd.read_csv(inp); model=joblib.load(model_path); setattr(model.named_steps["model"],"n_jobs",1) if hasattr(model.named_steps["model"],"n_jobs") else None
pre=model.named_steps["preprocessor"]; cols=list(pre.transformers[0][2])+list(pre.transformers[1][2]); missing=[c for c in cols if c not in df]
if missing: raise ValueError(f"Missing ARR→BER feature columns: {missing}")
pred=np.maximum(np.expm1(model.predict(df[cols])),0); time=pd.to_datetime(df["sample_time_utc"],utc=True); arr=pd.to_datetime(df["arr_time_utc"],utc=True)
out=pd.DataFrame({"task":"ARR_TO_BER","model_version":"arr_to_ber_rf_log1p_no_port_status_v1","prediction_time_utc":time,"data_available_until_utc":time,"event_id":df.get("arr_event_id",pd.Series([pd.NA]*len(df))),"port_call_id":df.get("port_call_id",pd.Series([pd.NA]*len(df))),"prediction":pred,"unit":"min_arrival_gate_to_first_stable_ber","lead_time_min":(arr-time).dt.total_seconds()/60,"confidence":pd.NA,"uncertainty":"long_tail_uncertainty_not_quantified","qc_flag":"ok","a_input_version":a.input_version,"a_input_sha256":a.input_hash,"model_sha256":sha256(model_path)})
out.to_parquet(a.output,index=False) if str(a.output).endswith(".parquet") else out.to_csv(a.output,index=False,encoding="utf-8-sig")
