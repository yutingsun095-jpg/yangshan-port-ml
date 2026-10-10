# 20-Port-Call integration test report

**Result: PASS (20 / 20).**  The checks are read-only and were run against the packaged A inputs and frozen B prediction tables.

## Coverage

The selected cases cover Direct, Indirect, Main, West, no BER, no UNB, no DEP, linked ANC, Review, not eligible, ARR→BER applicable/not-applicable, VAT missing review ARR, and the ASCII VAT bins `0-1h`, `1-2h`, `2-4h`, `4-6h`.

## Checks applied to every case

- `port_call_id`, MMSI and A formal-event links load consistently.
- AIS history contains no row after the test replay time.
- selected Port Status contains no snapshot after replay time.
- every B prediction has `data_available_until_utc <= prediction_time_utc <= replay_time_utc`.
- C export contains only observed formal events and has no future historical truth input.

## Case list

| category | port_call_id | mmsi | replay_time_utc | vat_available | direct_indirect_available | arr_to_ber_available | result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Direct-high | PC_202401_356407000_20240101T131425 | 356407000 | 2024-01-01T13:15:24+00:00 | True | True | True | PASS |
| Indirect-high | PC_202401_235098885_20240102T104402 | 235098885 | 2024-01-02T10:45:00+00:00 | True | True | True | PASS |
| Main | PC_202401_352002648_20240101T000403 | 352002648 | 2023-12-31T23:45:04+00:00 | True | False | False | PASS |
| West | PC_202401_371753000_20240102T085140 | 371753000 | 2024-01-02T08:52:39+00:00 | True | True | True | PASS |
| no_BER | PC_202401_373398000_20240101T032708 | 373398000 | 2024-01-01T03:18:06+00:00 | True | False | False | PASS |
| no_UNB | PC_202401_414688000_20240101T021420 | 414688000 | 2024-01-01T02:15:19+00:00 | True | False | True | PASS |
| no_DEP | PC_202401_636016177_20240101T030823 | 636016177 | 2024-01-01T03:09:03+00:00 | True | False | True | PASS |
| linked_ANC | PC_202401_563102400_20240103T140736 | 563102400 | 2024-01-03T14:08:26+00:00 | True | True | True | PASS |
| Review | PC_202401_477194100_20240101T025430 | 477194100 | 2024-01-01T02:45:27+00:00 | True | False | False | PASS |
| not_eligible_no_BER | PC_202401_412377430_20240101T055756 | 412377430 | 2024-01-01T05:48:54+00:00 | True | False | False | PASS |
| ARR_to_BER_applicable | PC_202401_211888820_20240101T055321 | 211888820 | 2024-01-01T05:54:21+00:00 | True | False | True | PASS |
| ARR_to_BER_not_applicable | PC_202401_215221000_20240101T055830 | 215221000 | 2024-01-01T05:57:30.239000+00:00 | False | False | False | PASS |
| VAT_prediction_missing_review_ARR | PC_202401_413463060_20240101T065359 | 413463060 | 2024-01-01T06:52:59.820000+00:00 | False | False | False | PASS |
| VAT_0-1h | PC_202401_412372760_20240101T060205 | 412372760 | 2024-01-01T06:02:57+00:00 | True | False | True | PASS |
| VAT_1-2h | PC_202401_413370650_20240101T060228 | 413370650 | 2024-01-01T05:53:15+00:00 | True | False | False | PASS |
| VAT_2-4h | PC_202401_533130753_20240101T060527 | 533130753 | 2024-01-01T05:56:24+00:00 | True | False | False | PASS |
| VAT_4-6h | PC_202401_477257100_20240101T083351 | 477257100 | 2024-01-01T08:34:42+00:00 | True | False | True | PASS |
| additional_B_linked_case_18 | PC_202401_636023397_20240101T101209 | 636023397 | 2024-01-01T10:12:54+00:00 | True | False | True | PASS |
| additional_B_linked_case_19 | PC_202401_636017577_20240101T103345 | 636017577 | 2024-01-01T10:34:38+00:00 | True | False | True | PASS |
| additional_B_linked_case_20 | PC_202401_477855900_20240101T133132 | 477855900 | 2024-01-01T13:32:14+00:00 | True | True | True | PASS |

The machine-readable equivalent is `docs/test_20_port_calls.csv`.
