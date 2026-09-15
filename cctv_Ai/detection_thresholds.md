# Staged detection thresholds

The CCTV pipeline uses different confidence levels for different jobs. A low-confidence motorcycle can still be tracked and sent to helmet analysis, while a no-helmet violation still requires stronger repeated evidence.

## Recommended defaults

```env
TRACKING_CANDIDATE_CONFIDENCE=0.28
COUNT_MIN_CONFIDENCE=0.35

HELMET_CONFIDENCE=0.50
NO_HELMET_CONFIDENCE=0.68
HELMET_CONFIRM_FRAMES=3
HELMET_CONFIRM_WINDOW=5

FALLBACK_NO_HELMET_CONFIDENCE=0.72
FALLBACK_NO_HELMET_CONFIRM_FRAMES=3
FALLBACK_NO_HELMET_VOTE_RATIO=0.80
```

## Decision flow

1. YOLO + ByteTrack accepts person/motorcycle candidates from about 28% confidence so distant bikes are not discarded early.
2. The motorcycle track is used to locate the rider/head region.
3. `helmet.pt` makes an independent helmet/no-helmet decision.
4. No-helmet requires repeated agreement across frames before it becomes a violation.
5. Plate OCR and road-damage models use their own independent thresholds.

Motorcycle confidence and helmet confidence are therefore not expected to match. A motorcycle detected at 35% can still produce an 80% no-helmet result after the cropped rider/head image is enlarged and passed through the specialized helmet model.
