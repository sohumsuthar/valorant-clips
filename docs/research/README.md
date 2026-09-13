# Research dossier — what to do with a 10TB Valorant archive

Generated 2026-09-13 by a 31-agent research workflow (run `wf_df45e754-ae5`), all 5 phases complete.
Total: 374,635 characters of primary research across 12 specialist agents.

## Purpose

Decide what to actually build with a 10TB+ personal archive of Valorant gameplay
(NVIDIA ShadowPlay DVR recordings, ~2023-2026), and what to do with the unfinished
`valclips` codebase in this repo.

## Sections

| Agent | Document | Visibility |
|---|---|---|
| `recon:adjacent-repos` | _(local only)_ | **PRIVATE** — quotes private repos + personal notes |
| `recon:ai-analyzers` | [recon-ai-analyzers.md](recon-ai-analyzers.md) | public |
| `recon:infra-and-data` | _(local only)_ | **PRIVATE** — network topology and host details |
| `recon:repo-core` | [recon-repo-core.md](recon-repo-core.md) | public |
| `recon:web-ui` | [recon-web-ui.md](recon-web-ui.md) | public |
| `research:10tb-pipeline-engineering` | [research-10tb-pipeline-engineering.md](research-10tb-pipeline-engineering.md) | public |
| `research:commercial-landscape` | [research-commercial-landscape.md](research-commercial-landscape.md) | public |
| `research:dgx-spark-capability` | [research-dgx-spark-capability.md](research-dgx-spark-capability.md) | public |
| `research:esports-academia` | [research-esports-academia.md](research-esports-academia.md) | public |
| `research:oss-prior-art` | [research-oss-prior-art.md](research-oss-prior-art.md) | public |
| `research:riot-data-sources` | [research-riot-data-sources.md](research-riot-data-sources.md) | public |
| `research:sota-video-models` | [research-sota-video-models.md](research-sota-video-models.md) | public |

## Method

Phase 1 (Recon) audited this repository and the surrounding infrastructure.
Phase 2 (Research) surveyed the 2026 landscape: video-understanding models,
esports-video literature, open-source prior art, Riot data sources, commercial
products, DGX Spark capability, and 10TB pipeline engineering.

Phase 3 produced four competing proposals from distinct lenses. Phase 4 scored
them on a three-judge panel. Phase 5 adversarially fact-checked every
load-bearing claim: of 12 checked, **3 were refuted and 9 only partially true** --
zero survived unqualified. The `verify-*.md` files carry those verdicts, and they
supersede any proposal or research claim they contradict.

Proposals and judge scorecards are withheld (`.private/`): every one of them
quotes infrastructure detail from the recon reports.

## Caveat

Agent output is research, not gospel. Claims marked UNVERIFIED were not confirmed.
Phase 5 adversarially re-checks the claims the recommendations depend on; prefer
those verdicts over any single agent's assertion.

## Publication policy

Two of the twelve reports are kept **out of version control** (`.private/`, gitignored)
because this repository is public:

- `recon:infra-and-data` — tailnet addresses, LAN topology, hardware identifiers.
- `recon:adjacent-repos` — quotes internals of private repositories and personal notes.

Everything published here is either general landscape research or an audit of this
repository's own code. Before adding a report, scan it for host addresses, hardware
identifiers, private-repository contents, and absolute home paths.
