"""Named capability identifiers Hermes can request.

Capabilities are plain strings, not a closed enum, so a new one can be
registered without a code change -- these constants exist only for
discoverability and typo-safety at call sites, mirroring how event types
are plain strings with named constants elsewhere in the OS.

ADR 0019 (Sprint 0) reconciliation:
The constants below are the **canonical 12-capability set** adopted by
ADR 0016 plus five additional constants marked **legacy** that the
runtime carried before the canonical taxonomy was ratified. The legacy
constants still work (they remain valid string identifiers) but new code
should prefer the canonical names. See ADR 0019 for the rationale.
"""

# -- Canonical 12 capabilities (ADR 0016) --------------------------------- #
REASONING = "reasoning"
PLANNING = "planning"
CODE_GENERATION = "code_generation"
DESKTOP_AUTOMATION = "desktop_automation"
BROWSER_AUTOMATION = "browser_automation"
IMAGE_GENERATION = "image_generation"
VIDEO_GENERATION = "video_generation"
VOICE_GENERATION = "voice_generation"
VISION = "vision"
MEMORY = "memory"
RETRIEVAL = "retrieval"
COMMUNICATION = "communication"

# -- Legacy aliases (deprecated; see ADR 0019) --------------------------- #
# `MEMORY_SEARCH` is the runtime's pre-canonical-taxonomy name for the
# `memory` capability. The role templates that previously used it have been
# migrated to `MEMORY` in the same Sprint 0 commit; the constant remains
# exported for any external caller still using the old name.
MEMORY_SEARCH = MEMORY  # legacy alias -- prefer `MEMORY`

# `FILE_STORAGE` was an implementation-internal capability name used in
# earlier manifests; it has no canonical equivalent in the 12-capability
# set (file-storage concerns are handled via tool-name or
# `communication`). Kept exported for backward compatibility only.
FILE_STORAGE = "file_storage"  # legacy -- not in the canonical 12

# `SPEECH` is the runtime's pre-canonical-taxonomy name for the
# `voice_generation` capability. The canonical name is `voice_generation`
# (parallel with `image_generation` / `video_generation` per the Glossary
# entry for the voice capability). Kept as an alias for backward
# compatibility.
SPEECH = VOICE_GENERATION  # legacy alias -- prefer `VOICE_GENERATION`
