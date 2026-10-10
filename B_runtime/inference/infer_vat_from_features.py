"""Feature-table VAT inference.  Example:
python infer_vat_from_features.py --input ../08_examples/vat_feature_input_example.csv --output vat_output.csv
"""
import argparse, hashlib, joblib, numpy as np, pandas as pd
from pathlib import Path

def sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(8*1024*1024), b""): h.update(b)
    return h.hexdigest()

p=argparse.ArgumentParser(); p.add_argument("--input",required=True); p.add_argument("--output",required=True); p.add_argument("--model",default="../04_models/vat_stacking_model.joblib"); p.add_argument("--input-version",default="A_input_unset"); p.add_argument("--input-hash",default="unprovided"); a=p.parse_args()
root=Path(__file__).resolve().parent; model_path=(root/a.model).resolve(); inp=Path(a.input)
df=pd.read_parquet(inp) if inp.suffix.lower()==".parquet" else pd.read_csv(inp)
b=joblib.load(model_path); [setattr(m,"n_jobs",1) for m in b["base_models"].values() if hasattr(m,"n_jobs")]; cols=b["feature_columns"]; missing=[c for c in cols if c not in df]
if missing: raise ValueError(f"Missing VAT feature columns: {missing}")
x=b["preprocessor"].transform(df[cols]); base=np.column_stack([m.predict(x) for m in b["base_models"].values()]); pred=b["meta_model"].predict(base)
time=pd.to_datetime(df["sample_time_utc"],utc=True); event=df.get("arr_event_id",pd.Series([pd.NA]*len(df))); pc=df.get("port_call_id",pd.Series([pd.NA]*len(df))); lead=df.get("remaining_time_to_arr_min",pd.Series([pd.NA]*len(df)))
out=pd.DataFrame({"task":"VAT","model_version":"vat_stacking_v1","prediction_time_utc":time,"data_available_until_utc":time,"event_id":event,"port_call_id":pc,"prediction":pred,"unit":"min_to_arrival_gate","lead_time_min":lead,"confidence":pd.NA,"uncertainty":pd.NA,"qc_flag":"ok","a_input_version":a.input_version,"a_input_sha256":a.input_hash,"model_sha256":sha256(model_path)})
out.to_parquet(a.output,index=False) if str(a.output).endswith(".parquet") else out.to_csv(a.output,index=False,encoding="utf-8-sig")
