# Probes

Templates in `templates/` are generic and reusable across target repos.
For each measured target repo, the conductor LLM instantiates a template's
placeholders (`{behavior}`, `{test_command}`, `{n}`, `{dir}`, `{url}`,
`{what}`, `{files}`, `{questions}`) into a concrete prompt and drafts an
answer key.

A human reviews and **freezes** that answer key before any measured run.
Frozen instances live in `probes/<repo>/` (one file per instantiated probe)
and never change after freeze - a probe with a changed answer key is a new
probe, not an edit to the old one.

Templates themselves may evolve over time; frozen instances under
`probes/<repo>/` must not.
