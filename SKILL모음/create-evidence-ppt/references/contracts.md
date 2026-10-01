# Evidence report contract
Per-slide manifest: slide identifier/index, section, source_key, item_key, version hash, analysis identifier, capture original/derivative hash, raw-note status and links. Summary slides must have an explicit set of contributors. A reported count must define distinct records versus keyword matches.

Raw notes verification: append a clear delimiter and exact body_raw to an existing notes placeholder or supported notes API, preserving useful metadata. Reopen the deck and extract the marked raw segment. CRLF, CR and vertical-tab mappings may be necessary for Office serialization, but must be documented and applied symmetrically. A successful save is insufficient proof.

PowerPoint COM: require Windows desktop PowerPoint and an interactive user environment; initialize/uninitialize COM on the owning thread. Track ownership of application and presentations. Close only resources opened by the job, never kill arbitrary Office instances. Use copies for summary extraction and verify outputs exist and are readable after COM returns. Do not use this skill to promise unattended server-side Office reliability.

Export previews preserving slide aspect ratio. Compute pixel height from slide width/height, not a fixed 16:9 assumption. Inspect output from the actual renderer: OOXML generation alone cannot prove visual appearance or font substitution.

Historical rebuilt reports must retain original period, source selection and analysis context. Expose changed presentation options and a new render identifier while retaining parent_run_id. If the source snapshot cannot be reconstructed, mark that limitation instead of silently using today's content.

Origin: V10 office.py, engine/v9/ppt_notes.py, PPT builders and PPT-only UI operation.
