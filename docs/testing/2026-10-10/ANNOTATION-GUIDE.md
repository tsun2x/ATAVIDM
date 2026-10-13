# TAVIDM annotation guide

An annotation tells the system **where an area is and what it means** in this camera view. You draw the road layout once; the system tracks vehicles moving through it. You do not draw a box around every vehicle.

Use real, known restrictions and the actual road layout. Drawing a No Parking Zone does not establish a legal restriction. If you do not know what an area means, ask the group leader before marking it.

This guide follows the current editor and detection rules inspected on 13 October 2026. Some objects can be saved even though their automatic detection is limited; those limitations are explained below.

Interactive references: [all annotation types](../2026-10-13/annotation-visual-guide/index.html) · [annotation combinations and overlaps](../2026-10-13/annotation-visual-guide/overlap-use-cases.html).

## Quick choice: what should I draw?

| What you want to check | Draw / supply |
|---|---|
| Vehicles staying in a no-parking area | Zones → No Parking Zone; a clip longer than the parking waiting time |
| Vehicles obstructing a travel lane | Lanes → Active Lane; the obstruction waiting time |
| Vehicles blocking a pedestrian crossing | Zones → Pedestrian Crossing; the crossing waiting time |
| Vehicles going against traffic | Lanes → Active Lane + Flow arrows → Lane flow pointing in the allowed direction |
| Trucks entering a ban area | Zones → Truck Ban Zone + actual recording date/time + ban schedule |
| Prohibited loading / terminal activity | Zones → No Loading/Unloading Zone + Activity region covering passenger activity; suitable vehicle/evidence |
| Vehicles crossing a solid road marking | Markings → supported solid type + prohibited crossing side |
| Vehicles entering against a No entry sign | Lane + Signs → No entry + Thresholds → No-entry threshold + matching allowed Flow arrow; attach all three to the same lane |
| Count vehicles crossing a point | Thresholds → Vehicle counting line, attached to the lane |
| A restricted lane/road segment | Zones → Restricted Lane; confirm the applicable vehicle restriction with the leader |
| Helmet, mirrors, rider count, or cargo passengers | Do not invent a special zone. These depend on clear vehicle/rider evidence and current detector capability. |

You do **not** need every object for every video. Draw only what exists and what your test needs.

## Open the editor and draw

1. Upload the MP4 in Live Monitor. Use its actual recording time when known.
2. Choose **Create New Zone Annotation**, or **Use Existing Zone Template** for the same camera view.
3. Click **Continue to Editor**. You will draw on the extracted first frame.
4. Choose the category tab, then its type. The seven tabs are **Zones, Lanes, Flow arrows, Thresholds, Markings, Signs, Activity**.
5. Click on the image to add points. For an area, click around its boundary in order. For a line or arrow, click the start and end.
6. Use **New object** before drawing a separate shape. Otherwise you may keep adding points to the selected object.
7. Where shown, select **Apply to lane** for the object. The dropdown currently shows lane IDs rather than friendly names; note which ID belongs to which lane.
8. Click **Save Annotation**, choose the save option, and click **Confirm Save**.
9. Reopen the annotation to check the saved shapes, lane links, and directions before processing.

### Drawing controls

| Action | Control |
|---|---|
| Add a corner or endpoint | Click on the image |
| Move a point | Drag the point |
| Remove a point | Double-click the point |
| Start another shape | New object |
| Remove the chosen shape | Delete selected |
| Move your view around the image | Shift + drag |
| Zoom in/out | Mouse wheel |

**Areas:** use at least three corners; four or more usually follow the road better. Click around the edge without crossing back over your own outline. Areas include zones, lanes, and activity regions.

**Lines/arrows:** use two clearly separated points. A two-point straight segment is easiest to check. For supported marking crossings, the engine checks the segment between the first and last points; adding bends does not make it follow a curved marking. Split a curve into separate straight objects if needed.

**Signs:** mark the visible sign's location, preferably with a small outline around it. The outline identifies the sign; it does not replace a lane or an entry threshold.

Draw on the **road surface**, excluding sidewalks/buildings unless they belong to the actual area. Follow the scene's perspective: a lane usually gets narrower farther from the camera. Do not use a vehicle's outline as the road boundary. If vehicles obscure the boundary, use a suitable same-view template or ask the leader; do not guess a restriction.

## 1. Zones — areas with a particular meaning

The six zone meanings below are supported by the scene format. In the current editor, **Active Lane is drawn under the Lanes tab**, so it is not another button under Zones.

### No Parking Zone

**What it means:** a road/curb area where parking is prohibited.

**When to use:** your video contains a real no-parking restriction and you want to check vehicles staying there.

**How:** outline only that prohibited area. Leave nearby legal parking outside it. Use footage long enough to exceed **Parking Dwell (s)**.

**Example:** draw the marked curbside strip where parking is prohibited, rather than the entire road.

**What to expect:** the rule looks for a vehicle remaining stationary in the area for the configured time. It produces evidence for review; it cannot fully determine parking intent or all exceptions. A vehicle merely passing through should not be treated as parked.

### Active Lane

**What it means:** a lane used by moving traffic.

**When to use:** obstruction, counterflow, or lane-linked lines/signs/counting.

**How:** use **Lanes → Active Lane**. Outline one direction of travel at a time. For counterflow, add an allowed Flow arrow for each relevant lane.

**Example:** draw the near-side lane separately from the opposite-direction lane.

**Avoid:** combining both directions into one lane with one arrow. Moving vehicles are normal; being inside the lane alone is not obstruction.

### Pedestrian Crossing

**What it means:** the road space pedestrians use to cross.

**When to use:** checking a vehicle remaining on and blocking the crossing.

**How:** outline the actual crossing footprint, including the relevant road width. Use the **Crossing Block (s)** setting for the waiting-time test.

**Example:** outline the zebra stripes, not the entire intersection.

**What to expect:** crossing blockage contributes to Obstruction. Driving across the crossing normally should not be treated as a sustained blockage. This is different from a vehicle counting line.

### Truck Ban Zone

**What it means:** the road area where a known truck-ban schedule applies.

**When to use:** checking applicable trucks during the configured ban hours.

**How:** outline the affected road segment. Supply the actual **Recording date/time** and check **Truck Ban Start / End** with the leader.

**Example:** annotate the restricted road through which trucks pass during the ban window.

**Avoid:** using today's upload time as the recording time. A zone without reliable time context cannot support the same scheduled decision. Do not assume every large vehicle or pickup is an applicable truck.

### No Loading/Unloading Zone

**What it means:** an area where loading/unloading is prohibited. Despite the stored name `loading_unloading`, the visible label is **No Loading/Unloading Zone**.

**When to use:** testing suspected prohibited terminal/passenger activity involving a relevant public-utility vehicle.

**How:** outline the prohibited stopping/activity location, then add an Activity region where boarding/alighting can be seen. Use a clip exceeding **Loading Zone (s)**.

**Example:** a prohibited roadside pickup spot where a jeepney stops and passengers visibly board.

**What to expect:** Illegal Terminal needs vehicle and passenger-activity context. A parked private car, an unidentified van, or any vehicle merely stopping is not enough by itself.

### Restricted Lane

**What it means:** a road/lane area with a known restriction on applicable vehicle classes.

**When to use:** the leader has established which vehicles are restricted and wants to inspect entry into that area.

**How:** outline the restricted road area accurately. Ask the leader to verify the applicable vehicle-class configuration; the drawing alone does not specify every class or exception.

**Example:** an established restricted road segment with a visible entry boundary.

**What to expect:** current support can produce a restricted-lane entry observation under pavement-marking review. It is not automatic legal confirmation. Use a Markings object for an actual painted solid-line crossing test.

## 2. Lanes — separate travel paths

The type is **Active Lane**. Create separate objects for separately directed lanes. A lane provides the area to which an arrow, threshold, marking, or sign applies.

**Best order:** draw the lane first, then create its linked objects. Select the correct **Apply to lane** value for each object. Reopen and verify the link; being physically drawn near a lane is not the same as being linked to it.

For obstruction, the lane area tells the system where a sustained stop matters. For counterflow, the lane also needs an unambiguous allowed direction. For No entry, the sign and threshold must share a lane link.

## 3. Flow arrows — allowed travel direction

Choose **Lane flow**. Click a start point and an endpoint **in the direction vehicles are allowed to travel**. Attach it to its lane using **Apply to lane**.

**Example:** if legal traffic moves from the bottom of the image toward the top, draw the arrow upward. A vehicle moving downward through that lane is the counterflow example.

Use one consistent direction per straight lane. Do not draw opposing arrows for the same lane. An arrow with identical start/end points gives no direction. Arrows can also provide the allowed-flow context for a lane-linked No entry threshold.

The Settings field **Lane Flow Direction (°)** uses image directions: `0° right`, `90° down`, `180° left`, `270° up`. It is a fallback for an older single-lane annotation; structured lanes drawn in this editor should have their own Flow arrows. Do not rely on one global angle for several opposite-direction lanes.

## 4. Thresholds — a line across a vehicle's path

Place the line across the actual path you want vehicles to cross. Its endpoints should span that path. Attach it to the relevant lane.

| Type | Meaning / when to use | Current behavior |
|---|---|---|
| **Threshold** | A general decision boundary, such as an entry boundary. | Can serve as a No entry decision boundary when linked correctly to a sign/lane and direction. Does not establish a violation on its own. |
| **Vehicle counting line** | Count vehicles passing a chosen road cross-section. | Used for vehicle crossing counts. Does not substitute for a No entry decision boundary. |
| **No-entry threshold** | The physical boundary where prohibited entry occurs. | Used with a No entry sign, matching lane link, and a clear allowed/prohibited travel direction. |

**Counting example:** draw a line across one straight lane at a visible point. Manually count vehicles that actually cross it, then compare the system's crossing results. Vehicles merely visible elsewhere are not crossing that line.

**No-entry example:** draw a line across the mouth of a restricted entrance. Link the No entry sign, this threshold, and the allowed Flow arrow to the same lane. Use one unambiguous applicable decision threshold per sign. Multiple matching thresholds can make the scene ambiguous and suppress candidates.

The current editor shows **Prohibited from** for markings, not thresholds. For a new No entry scene made through the UI, provide the matching lane Flow arrow so the system has direction context. A counting line or a sign outline alone is insufficient.

## 5. Markings — painted road boundaries

Draw along the actual road marking. For line types, use a straight two-point segment and choose the correct **Prohibited from** side. Link to the applicable lane where relevant.

| Type | Use it for | What the current system checks |
|---|---|---|
| **Double solid** | An actual double solid road marking. | Crossing the configured line from the prohibited side; commonly both sides, subject to the actual restriction. |
| **Single solid** | An actual single solid road marking. | A crossing observation when the prohibited side is explicitly configured. Do not guess that all crossings are prohibited. |
| **Solid/broken** | An actual adjacent solid and broken marking. | A crossing observation from the configured prohibited side. The system does not decide the legal side from the pixels for you. |
| **Restricted boundary** | A boundary associated with a restricted road area. | Not evaluated as a solid-line crossing type. A closed outline with at least three points can supply a restricted-area projection; review remains necessary. Prefer Zones → Restricted Lane for a clear area test. |
| **Generic marking** | A marking you want to document without a supported specific rule. | Saved scene context; no automatic generic-marking crossing rule. |

### Choosing “Prohibited from” correctly

**Both** means crossings from either side. **Left / right** means the side the vehicle **starts on before crossing**, relative to the marking's first point **A** and last point **B**. It does not mean the vehicle turns left/right.

The current engine uses image coordinates, which makes its side labels easy to misread. Use these concrete examples:

| You draw A → B | Engine's “left” approach side | Engine's “right” approach side |
|---|---|---|
| Horizontally from left to right | Below the line on screen | Above the line on screen |
| Vertically from top to bottom | To the right of the line on screen | To the left of the line on screen |

Reversing the point order swaps the sides. For a diagonal or one-sided restriction, have the leader verify the point order and test a crossing from each side before relying on it. If the editor's side wording is confusing, record that as a usability problem. Do not choose Both just to make the system emit an event.

**Example:** a solid/broken boundary allows crossing from the broken side but prohibits crossing from the solid side. Identify those sides from the actual marking, draw consistently, and select the engine's approach-side value corresponding to the prohibited side.

A vehicle traveling alongside the marking should not count as crossing. A vehicle that crosses outside the drawn segment should not be treated as crossing that segment. These are useful comparison clips.

## 6. Signs — the restriction shown by a real sign

Select the sign type, mark its location, and set **Apply to lane** to the lane it controls. A visible sign elsewhere in the image must not automatically apply to every road.

| Type | Meaning / when to draw it | Current automatic support |
|---|---|---|
| **No entry** | A real sign prohibiting entry along the affected approach. | Supported with a matching lane-linked threshold and clear direction; produces a review candidate. |
| **No left turn** | A real sign prohibiting a left turn. | A trajectory-based review-candidate path requires the sign, one associated lane, exactly one flow arrow, and exactly one lane-associated threshold. |
| **No right turn** | A real sign prohibiting a right turn. | A trajectory-based review-candidate path requires the sign, one associated lane, exactly one flow arrow, and exactly one lane-associated threshold. |
| **No U-turn** | A real sign prohibiting a U-turn. | A trajectory-based review-candidate path requires the sign, one associated lane, exactly one flow arrow, and exactly one lane-associated threshold. |
| **No overtaking** | A real sign prohibiting overtaking. | A review-candidate path requires the sign, one lane polygon and one flow arrow, plus two tracked vehicles whose order changes within the lane. |
| **Generic sign** | Other sign context without a supported specific type. | Saved for scene context; no generic sign-violation detection. |

**No entry setup:** draw the lane, draw its allowed Flow arrow, place the No-entry threshold across the entrance, and annotate the real No entry sign. Link the arrow, threshold, and sign to the same lane. Compare prohibited movement with permitted movement.

Do not label an unreadable sign by guessing. A sign alone is insufficient; the required lane geometry and usable track history must also be present. Turn-sign and overtaking candidates still require manual review.

## 7. Activity — where passenger activity happens

Both types are areas drawn with at least three corners. Use the **Activity** tab.

| Type | Meaning / when to use |
|---|---|
| **Passenger activity** | A region where relevant passenger movement/activity can be observed near a stopped public-utility vehicle. |
| **Boarding/alighting** | A more specifically described region where people visibly get on or off the vehicle. |

**How:** outline the relevant boarding location/vehicle approach, rather than every person in the frame. Combine it with the real No Loading/Unloading Zone when testing Illegal Terminal.

**Example:** mark the roadside area beside a jeepney door where passengers approach or leave while the jeepney remains stopped.

**Current limitation:** these regions provide spatial passenger/approach context. Drawing Boarding/alighting does not prove that a person actually boarded, and both types are currently used as activity context rather than separate complete action-recognition rules. A person on a distant sidewalk should not be assumed to be that vehicle's passenger. Inspect the clip manually.

## Saving and reusing annotations

| Save option | Use it when |
|---|---|
| **Use for This Video Only** | The drawing is specific to this clip or you are trying a test without changing a shared template. |
| **Save as New Template** | More videos will use the same stationary camera view. Give it a clear name, such as `Gate camera - northbound - test`. |
| **Update Existing Template** | You intentionally want to revise that existing template. Use a test template during group testing. |

Reuse only when the camera angle, crop, resolution, and road layout still align. If the camera moved or the video was cropped differently, check every shape and direction or create a new annotation. A template that saves successfully can still be geometrically wrong for another view.

Editing an annotation or setting does not change already-generated results. Start a new processing run to test the change.

## Final check before processing

- [ ] Areas follow the actual road, not the vehicles or whole image.
- [ ] Only real restrictions are marked; their meaning is known.
- [ ] Each lane has the correct direction where required.
- [ ] Arrows, signs, thresholds, and relevant markings are linked to the intended lane.
- [ ] Marking point order and prohibited side are correct.
- [ ] A counting line is separate from a No entry decision threshold.
- [ ] Truck-ban tests have the actual recording time and agreed schedule.
- [ ] Terminal tests include activity context and suitable visible evidence.
- [ ] Saved annotation was reopened and checked.
- [ ] You know which rules are available and which are limited.

If results are missing, first check the rule switch, saved annotation, required lane links/direction, video clarity, recording time, and waiting time. Record anything still unclear using [SIMPLE-RESULTS.md](SIMPLE-RESULTS.md). Missing evidence should lead to an uncertain result, not a forced violation.


\n