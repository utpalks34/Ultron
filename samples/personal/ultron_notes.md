# Ultron project notes (SAMPLE)

## Hardware plan

The router model runs on the CPU and stays resident. The worker model holds the GPU alone and is evicted when a run ends. Peak VRAM must stay under 2900 MB.

## Decisions

Push-to-talk comes before the wake word. Gestures are the first thing to cut if the schedule slips. The event bus, the VRAM manager and the confirmation gate are never cut.
