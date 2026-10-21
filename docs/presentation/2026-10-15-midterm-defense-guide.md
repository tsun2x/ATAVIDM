# TAVIDM midterm defense: exact PowerPoint creation guide

Prepared 14 October 2026 for the presentation expected on 15 October 2026.

## Send this section to the groupmate making the slides

Your job is to edit the supplied template. Follow the steps below in order. The text to paste and the words to say are already provided.

### 1. Open and save a copy

1. Open `C:\Users\AMD\Downloads\Title-MidtermDefense-Date.pptx` in PowerPoint.
2. Click **File → Save As → Browse**.
3. Name the copy `TAVIDM-MidtermDefense-2026-10-15.pptx`.
4. Keep **PowerPoint Presentation (.pptx)** as the file type. Click **Save**.
5. Keep the existing **five slides**, WMSU logo, red banners, backgrounds, and page numbers.
6. Keep the existing **4:3 slide size**. Do not change it to widescreen.
7. Replace the sample rice-study text and sample people's names. Those names are examples, not our group members or our adviser.

**How to replace text:** click inside the correct text box, select its words, and paste the new words. Do not select every object on the slide. Do not delete the logo or banner. If pasted text looks different, use the paste option **Keep Text Only**, then match the surrounding font.

**Before typing:** get the exact approved thesis title, full group member names, and adviser name from the project owner. Use the approved manuscript's Statement of the Problem and Objectives if they differ from the proposed wording below. The text below is a source-grounded draft, not proof of adviser approval.

### 2. Slide 1 — Title

**Keep:** the red WMSU panel on the left and the white area on the right.

**Replace the sample rice title with:**

> TAVIDM: TRAFFIC VIOLATION DETECTION AND MONITORING SYSTEM USING COMPUTER VISION AND DEEP LEARNING

Use the exact approved title if it differs. Let the title wrap into several lines in the existing white area. If it does not fit, reduce the title font gradually; do not stretch the text box over the logo. Check that every word is visible in Slide Show mode.

**Replace the names with:**

> Proponents: [FULL GROUP MEMBER NAMES]
>
> Thesis Adviser: [EXACT ADVISER NAME AND TITLE]

Replace the brackets. Do not leave them in the submitted file. Do not keep the template's sample names.

**Presenter says, approximately 20 seconds:**

> Good day. We are presenting TAVIDM, our Traffic Violation Detection and Monitoring System. It uses computer vision to identify and track vehicles, then applies configured traffic rules to flag possible violations for human review.

### 3. Slide 2 — Introduction

Keep the heading **Introduction**. Replace the four sample paragraphs with these four short paragraphs, in this order:

> Reviewing traffic videos manually requires people to watch footage and identify possible violations.
>
> TAVIDM uses computer vision to detect vehicles and track their movement across video frames.
>
> Configured road zones and traffic rules help the system flag possible violations and save evidence for review.
>
> Authorized users can review the evidence, manage records, and generate reports through the web application.

Use the original paragraph layout. Keep the body text readable; aim for at least 22 points if it fits. Do not add a large paragraph about every technology.

**Presenter says, approximately 1 minute:**

> Think of the system as an assistant that watches traffic footage. First, it identifies vehicles. Next, it follows their movement. It then checks that movement against the road setup and configured rules. If it finds a possible violation, it saves evidence so a person can review it. The system supports review and reporting; a detection does not automatically prove that a driver committed a violation.

### 4. Slide 3 — Statement of the Problem

Keep the heading **Statement of the Problem** and the existing two-bullet layout.

**Paste these two bullets, unless the approved manuscript has different questions:**

> How can traffic videos be processed to identify vehicles, track their movement, and flag possible violations using configured road zones and rules?
>
> How can evidence, human review, violation records, and reports be managed in one web application?

These are proposed presentation questions. The project owner must check them against the approved thesis before submission.

**Presenter says, approximately 45 seconds:**

> Our first problem is how to turn traffic footage into useful information about vehicles and possible violations. Our second problem is how to organize the evidence and review process so authorized users can make decisions and prepare reports.

### 5. Slide 4 — Objectives

Keep the heading **Objectives** and the subheading **Specific Objectives**.

**Replace the general objective paragraph with:**

> To develop a web-based traffic violation detection and monitoring system that processes traffic videos, flags possible violations, and supports evidence review and reporting.

**Replace the four specific-objective bullets with:**

> Detect and track vehicles in traffic videos using YOLOv8m and ByteTrack.
>
> Apply configured road zones and traffic rules to flag possible violations.
>
> Provide evidence review and record management for authorized users.
>
> Generate reports and evaluate system performance using labeled data and defined test scenarios.

Check these against the approved objectives. The last objective describes evaluation work; do not claim it is completed unless you can show the actual evaluation results.

**Presenter says, approximately 1 minute:**

> YOLOv8m identifies objects in the video. ByteTrack helps follow the same vehicle across frames. The rule engine checks the vehicle's behavior using the configured road layout. Reviewers then inspect the evidence and decide what to do with the record. The application also supports reports. We distinguish software testing from measuring the model's accuracy on labeled data.

### 6. Slide 5 — Demo

Keep the large centered word **Demo**. This is the signal to switch from PowerPoint to the browser. Keep this slide simple.

**Presenter says:**

> We will now show the system, our dataset, and the source code repository.

The project owner should operate the system during the demonstration. The slide maker should prepare the PowerPoint and help with timing.

## Browser demo: prepare these three tabs before presenting

1. **System:** the actual working system URL, already logged in using an appropriate demonstration account.
2. **Dataset:** the actual Roboflow project and dataset version used or referenced by the team.
3. **GitHub:** https://github.com/tsun2x/ATAVIDM/tree/cursor/phase-1-foundation — after the project owner has published the latest presentation changes.

Do not put passwords or API keys on slides. Keep unrelated personal tabs closed while sharing the screen.

### Exact demonstration order — target 5 to 6 minutes

#### A. Show the system — about 3 minutes

1. Open the system tab. State whether it is hosted online or running on your laptop. If it is `localhost` or `127.0.0.1`, call it a local demonstration.
2. Show the main dashboard. Say: “This is where authorized users access the monitoring and review tools.”
3. Open a short, prepared traffic video or its completed processing result. Select this exact sample during rehearsal and write its name below.
4. Show its saved road zones or annotation view. Point to one configured zone and explain what rule it supports.
5. Show the processed result with vehicle boxes and tracking information. Say: “The detector identifies vehicles, and tracking follows their movement.”
6. Open **Review Queue** and the prepared candidate. Write its identifier below during rehearsal so you can find it quickly.
7. Open the available scene image, vehicle crop, or clip. Actually display the media. A button being present is not enough.
8. Explain the review choices shown by the application. If demonstrating a decision, use an authorized disposable demonstration record, then explain the saved result. Use the rehearsal's agreed decision rather than changing a real operational record.
9. Open **Violations** and show the resulting record or an existing approved demonstration record.
10. Open **Reports**, generate the report prepared during rehearsal, and open the downloaded PDF or Excel file. Point to its title, records, and branding. Show the file itself.

**Write these before rehearsal ends:**

- System URL: ____________________
- Sample video name: ____________________
- Prepared review record: ____________________
- Approved demonstration decision, if used: ____________________
- Prepared report/filter: ____________________

If upload or processing is slow, briefly show the upload controls and use the completed result for the remaining steps. Explain that it was processed beforehand. Run the exact path once before presentation day; do not discover missing media while presenting.

#### B. Show the dataset — about 1 minute

1. Switch to the actual **Roboflow project**, not its homepage.
2. Show the project name and the exact version.
3. Open an example image with annotation boxes and labels.
4. Show the class list and train/validation/test split for that version, if available.
5. Read counts from the page. Do not invent image counts, split percentages, or accuracy scores.

**Say:**

> This is our dataset project. The boxes mark the objects and the labels tell the model what each object is. Training data teaches the model; validation data helps us compare settings; test data measures performance on separate examples.

If the active model was trained using a locally modified dataset, explain that relationship accurately. Showing a Roboflow page does not establish that every local training image came from that exact version.

#### C. Show GitHub — about 45 seconds

1. Switch to the repository tab.
2. Make sure the branch selector says **cursor/phase-1-foundation**.
3. Show the latest commit. At the readiness check, the expected local commit was **eed689f4**, titled **fix: complete adviser alterations implementation**. If a newer authorized commit is published, show that instead.
4. Point to `app.py`, `core/`, `templates/`, and `tests/` in the repository.

**Say:**

> This repository contains our application source code. Flask serves the web application, the core modules handle video processing and rules, and the templates provide the web pages. We also maintain tests. Model weights and runtime data are managed separately from the source repository.

## What the slide maker must check before sending the PowerPoint

- [ ] Exactly five slides, in the supplied order.
- [ ] Original 4:3 size and WMSU design preserved.
- [ ] No rice-study text remains, including text in speaker notes.
- [ ] No sample names remain.
- [ ] Approved title, names, problem statement, and objectives checked by the project owner.
- [ ] No brackets or unfinished placeholders remain in the slides.
- [ ] Every slide fits on screen in **Slide Show → From Beginning**.
- [ ] Body text can be read from the back of a classroom.
- [ ] PPTX opens on the laptop that will be used to present.
- [ ] Save a PDF copy using **File → Export → Create PDF/XPS** for a slide backup.
- [ ] Send both the PPTX and PDF to the group before presentation day.

## Project owner's readiness check — current evidence, 14 October 2026

### Requirement 1: “Your system must already be online and working”

**Status: not established by the current checks.** No deployed URL was provided, and the checked common local server ports did not show a listener. Source code and test results do not prove the application is currently online.

Before claiming this requirement is satisfied:

1. Start or access the intended deployment.
2. Open its URL from a second device using the connection you will use tomorrow.
3. Log in and complete the exact demonstration path above.
4. Confirm the evidence images/clips and generated reports open successfully.
5. If the teacher requires internet access to the system, confirm an internet-facing URL. A laptop-only address does not establish that requirement. If the wording permits a local running application, get that interpretation from the teacher.

### Requirement 2: “Your datasets and your own internet connection”

**Status: showing your real Roboflow project is suitable dataset evidence, but the project/version and your personal connection have not been verified here.**

Prepare the exact project/version URL, annotated examples, labels, and available split information. Bring your own hotspot or other permitted connection and test both Roboflow and GitHub using it. Bring sufficient mobile data, your charger, and a charged phone. A screenshot is a useful backup, but cannot demonstrate a working internet connection.

### Requirement 3: “An updated GitHub repository with source code ready for presentation”

**Status: latest local changes were not yet on GitHub at the check.**

- Local branch: `cursor/phase-1-foundation`.
- Local latest commit: `eed689f45ec1653debb2bc66ade6e9d6b052a728`.
- Live remote branch: `e03bf20c7f90042064024dde8dd0547d7d9aabd2`.
- The local branch was one commit ahead.
- GitHub's default branch is `master`, so opening the repository homepage can show different code from your presentation branch.

The project owner needs to publish the already committed changes, then verify that GitHub shows the expected commit on the intended branch. No push was performed as part of this guide. Do not broadly stage unrelated local files or secrets to accomplish this.

If the owner chooses to publish this existing commit, the command is:

```powershell
git push origin cursor/phase-1-foundation
```

After publishing, reload the GitHub branch page and check the commit identifier. The repository excludes model weights and operational data; keep the local model, demo video, dependencies, and required configuration ready on the presentation machine.

## Honest answers to likely panel questions

**“How does it work?”**

> Video enters the system. YOLOv8m detects objects, ByteTrack follows vehicles, and the rule engine checks configured road zones and behavior. Possible violations are saved with evidence for human review.

**“Does it automatically punish drivers?”**

> It flags possible violations and supports authorized review. A detection alone is not a final enforcement decision.

**“Do all twelve violation types work fully?”**

> The system keeps twelve canonical violation types. The current registry marks four as implemented and eight as partial. Each rule still depends on the required scene setup and evidence.

The four marked implemented are Obstruction, Counterflow, Truck-Ban Violation, and Motorcycle Overloading. Do not describe all twelve as fully validated in real-world use.

**“What does confidence mean?”**

> Detection confidence expresses the model's confidence in an object prediction. Rule evidence strength is a separate measure. Neither is a probability that a driver is guilty.

**“How accurate is it?”**

> We should report accuracy using our documented evaluation on labeled data. Passing software tests checks application behavior; it does not establish detection accuracy.

Only quote evaluation numbers that the team can show with the dataset version and evaluation method. Do not use an invented percentage.

**“What are your limitations?”**

> Results depend on camera view, visibility, model capability, and correct road annotation. Some violation types remain partial, and human review is required. We are continuing to validate the system against defined scenarios.

Owner's technical note: the prior review found a remaining vehicle-grouping issue when a corrected violation type is later combined with another violation. Ordinary fusion was fixed, but that combined correction/fusion path still needs remediation and verification. Do not present that path as verified. If asked about it, explain the limitation directly.

## Final rehearsal — do this tonight

1. One person presents slides 1–4; the project owner operates the demo; another person watches the time and prepares the dataset/repository tabs. Combine roles if the group is small.
2. Run the entire presentation using the actual laptop and internet connection.
3. Target approximately 9–10 minutes: title 20 seconds, introduction 1 minute, problems 45 seconds, objectives 1 minute, demo 5–6 minutes. Follow your teacher's time limit if different.
4. Check projector compatibility or bring the required HDMI/USB-C adapter.
5. Save the slides, a short demo recording, and permitted sample screenshots locally as backups. Explain clearly if showing recorded results.
6. Confirm the application URL, Roboflow page, GitHub commit, evidence display, and report opening once more before presenting.

**Done means:** the five-slide deck is finished, the correct names and approved thesis wording are present, the actual demo works, the dataset page is ready, your own connection works, and GitHub displays the intended source commit.
