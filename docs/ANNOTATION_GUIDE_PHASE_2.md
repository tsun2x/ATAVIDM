# Phase 2 Annotation Guide

Use `config/training/class_schema.json` (schema_version **1.1.0**) as the label source of truth.

The pilot contract freezes **26** labels: **17** object classes and **9** scene classes. Exactly **12** of the object classes are vehicle detector classes.

## General rules

- Draw tight boxes around visible objects; do not infer hidden extents excessively.
- Label the most specific supported vehicle type only when visible evidence supports it. An ordinary van is not automatically `uv_express_van`.
- Preserve `jeepney`, `tricycle`, `piaggio`, and `uv_express_van` as distinct operational types.
- Preserve `truck` and `pickup_truck` as distinct classes. Do not collapse them.
- Associate `rider` only with people actually on the motorcycle; adjacent pedestrians remain `person`.
- Use `helmet_nut_shell` only for visibly supported form. Other supported helmets use `helmet_acceptable`; uncertain type goes to review.
- A mirror that is occluded or outside useful resolution is unknown, not absent.
- Label only supported Philippine sign and marking families. Do not map foreign-looking signs into Philippine classes.
- Do not annotate `violation`; the rule engine derives candidates from objects, tracks, zones, time, and context.
- Do not annotate `collision_vehicle`, `private_vehicle`, `public_utility_vehicle`, or `unknown` as detector labels. Broad hierarchy keys are derived, not drawn.
- When evidence is insufficient, record `UNKNOWN` / `UNCERTAIN` as a review state. Do not force a class.

## Pickup truck (`pickup_truck`)

### Positive examples

- Conventional pickup with a clearly visible open cargo bed.
- Pickup with a covered / canopied cargo bed where the pickup body style remains identifiable.
- Utility pickup used for cargo or tools when the vehicle is still recognizably a pickup.

### Label as `truck` instead

- Large cargo trucks, dump trucks, flatbeds, box trucks, and other applicable large commercial cargo vehicles that are not conventional pickups.
- Vehicles whose body is clearly a heavy truck rather than a light pickup.

### Hard negatives / do not force `pickup_truck`

- Severely occluded or distant vehicles where the cargo bed cannot be confirmed.
- SUVs, crossovers, and passenger vans that only vaguely resemble a pickup silhouette.
- Ordinary `van` bodies without pickup cargo-bed evidence.
- Modified bodies where the annotator cannot honestly decide between `truck` and `pickup_truck` — leave as review/unknown rather than guessing.

### Edge cases

| Situation | Guidance |
|---|---|
| Covered bed / canopy | Still `pickup_truck` if the pickup form is clear |
| Utility / service pickup | `pickup_truck` when body style is a pickup |
| Small light truck of ambiguous form | Prefer review/unknown over forcing a class |
| SUV with open rear but no cargo bed | Not `pickup_truck` |
| Van with rear doors only | `van` (or `uv_express_van` only with PUV evidence) |

### Relationship to the cargo-passenger rule

The cargo bed is **contextual evidence** for Unauthorized Passenger in Applicable Truck/Pickup Cargo Area. Both `truck` and `pickup_truck` are applicable vehicle types for that rule.

A person whose box merely overlaps the vehicle box is **not** sufficient evidence of a cargo-area passenger. Cabin occupants are not cargo-area passengers. Do not invent geometry thresholds during annotation; only label observable objects and attributes.

## Gold-set QC

The gold set must be double-reviewed. Record disagreements, corrections, missing boxes, wrong classes, duplicates, and unresolved unknowns. Frames from the same video, camera, location, or nearby time interval must remain in one split.
