# Generalization scope and MMSI disclosure

## Formal scope of VAT and ARR→BER results

The reported VAT and ARR→BER evaluations are **historical Port Call / event
generalization** results for Yangshan January 2024. VAT is grouped by ARR event;
ARR→BER is grouped by Port Call. This prevents snapshots from one target event
or call appearing in more than one holdout subset.

They are not claimed to be strict unseen-vessel (MMSI-isolated) results. The
complete audit is `../01_split_manifests/mmsi_cross_split_audit.csv`; it shows
MMSIs that occur across split partitions because a vessel can make multiple,
distinct historical Port Calls.

## Required wording for paper/report

> Model performance is evaluated under a historical event/Port-Call holdout
> protocol. The results quantify generalization to held-out vessel visits in the
> same Yangshan January 2024 operational setting, rather than strict
> generalization to previously unseen MMSIs. Therefore, external vessel,
> seasonal and port-transfer generalization are outside the present claim.

## Why no additional MMSI-group sensitivity is reported here

The verified formal release keeps the already approved event/Port-Call protocol
for VAT and ARR→BER. A new MMSI-group split would materially change the
evaluation question, reduce the effective sample size, and require retraining
and independent re-approval of all models. It is deliberately presented as
future validation, not retroactively substituted for the approved holdout
metrics.

Direct/Indirect is different: its delivery includes a separate 5-fold MMSI
group OOF sensitivity file, but its very strong spatial separability should
still be interpreted as a Yangshan historical-replay result.
