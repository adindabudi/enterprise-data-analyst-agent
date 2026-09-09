# GPT-5.6 Terra Auto Routing Evaluation

Date: 2026-07-28

## Contract

- Routing version: `gpt-5.6-terra-auto-v1`
- Deployment: `gpt-5.6-terra`
- Profile: `gpt-5.6-terra-medium-v1`
- Served model: `gpt-5.6-terra`
- Snapshot: `2026-07-09`
- Prompt version: `gpt-5.6-terra-v1`
- Prompt SHA-256: `a5d89045b7b1a1ace267cba36f8c991265a34c8c96078c872bac98970324113d`
- Request-options SHA-256: `e484b5cfcd53c4958f04966a2cf199fe14129db66af3d5200860daed8ac6e490`
- Model fallback/router: none
- Model-facing routing tool: `run_deep_analysis`

The direct agent receives one handoff tool. It calls the tool before emitting text when a request requires private files or Fabric data, scripts or sandbox calculations, downloadable or validated artifacts, or execution that must survive disconnect. Conversation, explanation, rewriting, supplied-text summarization, and small self-contained questions stay on direct streaming.

## Corpus

| Expected lane | Prompt                                                                           |
| ------------- | -------------------------------------------------------------------------------- |
| Direct        | Halo, apa kabar?                                                                 |
| Direct        | Jelaskan perbedaan mean dan median secara singkat.                               |
| Direct        | Tulis ulang kalimat ini agar lebih profesional: rapatnya pindah besok.           |
| Direct        | Berapa 17 dikali 23? Jawab singkat.                                              |
| Analysis      | Buat workbook Excel dari data penjualan dan validasi formulanya.                 |
| Analysis      | Analisis file revenue.csv ini dan cari outlier dengan Python.                    |
| Analysis      | Buat laporan lengkap dengan chart, tabel, dan artifact yang bisa diunduh.        |
| Analysis      | Bandingkan revenue FY2026 dari data Fabric perusahaan dan rekonsiliasi totalnya. |

## Iteration

Baseline medium effort routed 7/8 correctly. It missed the downloadable report case. One variable changed: the handoff tool description explicitly named downloadable reports, charts, tables, and workbooks.

After that change:

| Effort |   Accuracy | False analysis for direct prompts | Median total latency |
| ------ | ---------: | --------------------------------: | -------------------: |
| Medium | 8/8 (100%) |                                 0 |             7,444 ms |
| Low    | 8/8 (100%) |                                 0 |             7,112 ms |

Low effort improved median total latency by about 4.5%, but the greeting case was slower and the corpus is too small to justify changing the approved production profile. Production therefore remains standard mode, medium effort. The lower-effort result is experimental evidence only.

## Decision

- Keep one visible composer with Auto behavior.
- Keep `gpt-5.6-terra` only; do not introduce Sol/Luna fallback or a second classifier model.
- Keep approved standard/medium request options.
- Use direct HTTP streaming for ordinary chat.
- Let the same model select the single handoff tool for ambiguous complex requests.
- Route attachments and an explicit Deep analysis menu action directly to the durable lane.
- Keep DTS, sandbox, validation, and publication behind the durable lane.

## Limitations

- Eight cases establish a smoke baseline, not a release-grade routing evaluation.
- The corpus does not yet cover multilingual ambiguity, prompt injection, mixed direct-and-artifact requests, or linked versus unlinked Fabric states.
- Before changing reasoning effort, tool description, prompt version, or model snapshot, rerun an expanded corpus and compare accuracy, false handoffs, first-token latency, total latency, tokens, and cost per successful request.
