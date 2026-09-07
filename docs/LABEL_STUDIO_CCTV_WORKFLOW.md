# Local Label Studio CCTV workflow

Label Studio Community Edition is the only annotation interface for this workflow. CCTV images and annotations remain local; do not upload them to Label Studio Cloud or another external service.

## Review contract

- Annotate every clearly visible vehicle in a selected frame using the ten classes in `config/training/class_schema.json`.
- `car` includes sedans, SUVs, and crossovers.
- Use `pickup_truck` only when the pickup body form or cargo bed is observable.
- Use `van` for a visually identifiable van body; for-hire status is not an object class.
- Select **Contains uncertain vehicle** when an object is too blurred, cropped, distant, or occluded to classify honestly. Explain it in the note and do not create a forced detector label for it.
- Existing boxes and future model outputs are suggestions. Correct, add, or remove them before selecting **Review complete**.
- Annotation does not confirm a traffic violation.

## Isolated local installation

The approved installation lives outside the repository at `D:\apps\label-studio-tavidm\venv`. It does not modify TAVIDM's Python environment.

PowerShell launch pattern:

```powershell
$env:LABEL_STUDIO_BASE_DATA_DIR='D:\apps\label-studio-tavidm\data'
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED='true'
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT='D:\tavidm-annotation-workspace'
& 'D:\apps\label-studio-tavidm\venv\Scripts\label-studio.exe' start --host 127.0.0.1 --port 8080 --no-browser
```

The convenience launcher is `scripts/start_label_studio_local.ps1`. Open `http://127.0.0.1:8080`, create the local owner login, create an object-detection project, and paste `config/label_studio/vehicle_detection.xml` into the labeling interface. Credentials stay with the owner and must not be written to the repository.

## Import and export

Run `scripts/build_label_studio_vehicle_tasks.py` to convert an attributed YOLO dataset into prediction-only tasks. Configure local storage with the dataset root as the document root, import the generated JSON, and review every task.

For newly selected CCTV frames, `scripts/preannotate_label_studio.py` can run a checkpoint whose class roster exactly matches the canonical ten-class order. It writes prediction-only tasks and refuses incompatible checkpoints. Use the best reliable predecessor checkpoint when the footage is available; model output remains a suggestion regardless of confidence.

Export reviewed image annotations as Label Studio JSON for validation. Do not treat a raw export as training-ready until the local validator passes and unresolved tasks are zero.

`scripts/validate_vehicle_annotations.py` compares that export with the immutable frame-selection manifests. It rejects missing reviews, uncertain frames, invalid boxes, unknown classes, altered provenance, and duplicate content. `scripts/build_pickup_video_gold.py` then checks whole-source split assignments and requires pickup examples in train, validation, and test before a future v6 build can be reviewed.
