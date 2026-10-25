# UAL 1 skeleton library

Models and animations by **Quaternius**. Source: https://quaternius.com/packs/universalanimationlibrary.html (Standard pack). CC0 1.0; the original license and README are in `sources/`.

One complete source pack: 43 biped motions covering locomotion, jump stages, idle, spell stages and combat references. Original `UAL1_Standard_RM.glb` is preserved as `sources/ual1-root-motion.glb`; its SHA-256 and clip inventory are in `sources/provenance.json`.

Derived JSON samples use glTF animation evaluation, quaternion SLERP and forward kinematics at 60 Hz. Long profiles retain the original proportions; short and round profiles adjust bone translations while retaining rotations and timing. These adaptations are drafts without foot IK or prop constraints, not finished skinned characters. UAL 2, animal and 100STYLE packs are not included.

The workbench reads the JSON clips directly; no engine, Node server or model download is required. The GLB is retained for future re-extraction with a glTF-capable tool. No separate legacy HTML viewer is bundled.
