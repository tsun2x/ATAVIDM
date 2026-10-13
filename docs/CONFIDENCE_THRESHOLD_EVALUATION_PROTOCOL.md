# TAVIDM Confidence Threshold Evaluation Protocol

## Purpose

Measure candidate detector confidence floors against reviewed footage before changing any operating defaults. A model score is an uncalibrated detector output; the rule evidence score describes the rule engine's available evidence. Neither score is a probability that a person committed an offence.

## Holdout design

1. Select footage with adjudicated vehicle and violation labels. Split by whole source recording, camera, or collection session so adjacent frames cannot appear in both tuning and evaluation sets.
2. Keep the holdout sources unseen while selecting candidate thresholds. Record the model/checkpoint hash, class schema, rule configuration, sampling settings, and source manifest.
3. Report per-class precision, recall, false positives, and false negatives. Include the number of labeled positive and negative examples so small denominators remain visible.
4. Stratify results by distance/vehicle size, lighting, occlusion, and small-motorcycle visibility. Report the same metrics for each stratum and identify strata with too little data for conclusions.
5. Compare the current detector floor with predeclared candidate floors, including 65% and 90% only as candidate values raised by the adviser. Consider class-specific floors only when a held-out result supports them and the rule/configuration contracts can preserve the canonical class roster.
6. Choose a candidate using a predeclared operational cost for false positives and false negatives. Do not optimize a single overall accuracy number or tune on the final holdout.
7. Repeat the evaluation on a separate untouched confirmation set before recommending a default change. Preserve the current operating thresholds until that review is complete and separately authorized.

## Required report

Include the source-group split, labels and adjudication method, checkpoint and config hashes, per-class and stratified counts/metrics, candidate thresholds, error examples, confidence intervals where sample sizes support them, and limitations. Record whether the evaluation used synthetic fixtures or actual reviewed footage; synthetic tests do not establish field accuracy.
