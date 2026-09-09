# Document Skill Terms Acceptance

The Anthropic document skill content is source-available reference material and is not committed to this repository. Review the exact `skills.lock.json` before acquiring it, then accept that reviewed version locally:

```sh
export EDA_DOCUMENT_TERMS_ACCEPTED="$(sha256sum skills.lock.json | cut -d' ' -f1)"
```

Acquisition writes only to the ignored `services/worker/document-skills/` directory. Do not commit acquired content or use it with real customer document data unless the applicable terms and data handling approvals are in place.
