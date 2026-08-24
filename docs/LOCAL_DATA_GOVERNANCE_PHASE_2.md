# Local Data Governance for Phase 2

No operational footage may be collected or admitted to the training package until the project owner approves the following record.

## Authorization record

- Data owner and approving authority
- Collection locations and camera identifiers
- Collection purpose and allowed project uses
- Collection start/end dates
- Whether faces, plates, audio, or other personal information may appear
- Restrictions on copying, publication, demonstrations, and model distribution
- Approval date and evidence location

## Privacy and retention procedure

- Store raw footage only in the approved restricted location; do not commit it to Git.
- Restrict access to named project roles and retain an access record.
- Minimize collection to footage necessary for the approved classes and conditions.
- De-identify or tightly restrict frames containing identifiable people or plates before sharing.
- Record retention duration, review date, deletion owner, and secure deletion method.
- Keep derived annotations and model artifacts only when the authorization permits them.
- Report accidental exposure through the institution's approved process.

## Collection coverage record

For each camera/location, record viewpoint, resolution, frame rate, time range, weather, lighting, congestion, occlusion, and visible target classes. Do not treat repeated frames or duplicate clips as additional coverage.

After approval, update `config/training/readiness_controls.json` only with evidence-backed states. `approved` must never mean merely discussed or planned.
