# Реестр ADR: cc-bench

Это реестр решений cc-bench (бенчмарк-инструмента).
Реестр решений SDD-стека: /home/octrow/cybernet/sdd-kit/docs/ADR/

| № | Название | Суть |
|---|---|---|
| [0001](ADR-0001-tool-not-experiment.md) | Инструмент, не эксперимент | отдельный репозиторий-инструмент, sdd-kit - одна из измеряемых тулз |
| [0002](ADR-0002-v1-scope.md) | Скоуп v1 | свои репо (WBN/VA), Tier P автоматом, Tier F полуавто |
| [0003](ADR-0003-results-format.md) | Формат результатов | CSV по прогонам + markdown-отчёт (медиана/IQR), сырьё в logs/ |
| [0004](ADR-0004-stack-and-domain.md) | Стек и доменная модель | Python+uv, армы - YAML-спеки, пробы шаблон+LLM+заморозка человеком, судья Tier F - LLM + выборочная проверка |
| [0005](ADR-0005-active-time-and-report.md) | Активное время и отчёт | активное время - единственная временная метрика; отчёт обязан давать советы по тулзе |

Глоссарий: [glossary.md](glossary.md) - арма, тулза, проба, Tier P, Tier F, fired-check, bench-base, кондуктор, активное время, answer key, break-even.
