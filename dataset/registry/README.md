# Dataset Registry Workflow

`datasets.json` is the evidence register for every candidate source. A link is not approval.

For each dataset, verify the official source, license, file integrity, sample quality, viewpoint, class coverage, annotation format, and leakage-safe split. Change `status` to `accepted` only when all required evidence fields pass. Foreign datasets must remain limited to `generic_pretraining`; final training, validation, and evaluation require Philippine-domain data.

Run the entry gate from the repository root:

```powershell
.\venv\Scripts\python.exe -m core.phase2_readiness
```

Exit code `0` means every dataset and project control passed. Exit code `1` is expected while preparation remains incomplete. Use `--output work/phase2-readiness.json` for a machine-readable report.

Never commit restricted raw footage, personal information, or credentials. Keep source permissions and privacy records in the approved project records location.
