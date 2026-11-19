"""P3: согласие двух проходов и решение по составу.

Объединять модули один-к-одному нельзя: проходы группируют по-разному
(`2091×786` против `1870×785` — та же кухня). Поэтому сравниваем **итоги**
(двери, модули), считаем согласие и выбираем число правилом, а не суммой.

Правило:
- согласие по дверям ≥ 0,90 — берём меньшее число (перебор опаснее недобора
  для КП: лишняя дверь = лишние петли и ручки в смете);
- согласие < 0,90 — флаг «проходы расходятся», нужен переспрос;
- сильная разница в числе модулей при согласных дверях — флаг «разная
  детализация» (не блокирует, но видно менеджеру).
"""

from __future__ import annotations

from dataclasses import dataclass, field

AGREEMENT_MIN = 0.90
MODULE_RATIO_MIN = 0.7


@dataclass
class PassMetrics:
    """Итоги одного прохода."""

    doors: int
    modules: int
    confidence: str | None = None


@dataclass
class Consensus:
    """Согласованное решение по двум проходам."""

    doors: int
    modules: int
    agreement: float | None
    flags: list[str] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return self.agreement is not None and self.agreement >= AGREEMENT_MIN


def agreement_ratio(a: int, b: int) -> float | None:
    """Согласие по числу: 1.0 — совпало, 0.0 — один ноль, другой не ноль."""
    if a == 0 and b == 0:
        return 1.0
    if a + b == 0:
        return None
    return round(2 * min(a, b) / (a + b), 3)


def consensus(first: PassMetrics, second: PassMetrics) -> Consensus:
    """Свести два прохода в одно решение."""
    flags: list[str] = []
    ratio = agreement_ratio(first.doors, second.doors)

    if first.doors == 0 and second.doors == 0 or ratio is None:
        doors = 0
        flags.append("двери не найдены ни в одном проходе")
    elif ratio >= AGREEMENT_MIN:
        doors = min(first.doors, second.doors)
    else:
        doors = min(first.doors, second.doors)
        flags.append(
            f"проходы расходятся по дверям: {first.doors} и {second.doors} "
            f"(согласие {ratio:.0%}) — нужен переспрос"
        )

    modules = max(first.modules, second.modules)
    if first.modules and second.modules:
        module_ratio = min(first.modules, second.modules) / max(first.modules, second.modules)
        if module_ratio < MODULE_RATIO_MIN:
            flags.append(
                f"разная детализация: модулей {first.modules} и {second.modules}"
            )

    return Consensus(doors=doors, modules=modules, agreement=ratio, flags=flags)


def accept_reask(consensus_doors: int, reask_doors: int) -> tuple[bool, str]:
    """Принять ли результат переспроса.

    Переспрос нестабилен: на `0001` он исправил 4 → 2, на `0011` приблизил
    5 → 4, а на `0002` ушёл в 27 при согласии проходов на 16. Поэтому берём его
    только если он близок к согласию проходов, иначе оставляем флаг менеджеру.
    """
    tolerance = max(2, round(consensus_doors * 0.15))
    delta = abs(reask_doors - consensus_doors)
    if delta <= tolerance:
        return True, f"переспрос {reask_doors} в допуске ±{tolerance} от {consensus_doors}"
    return False, (
        f"переспрос {reask_doors} расходится с согласием {consensus_doors} "
        f"больше ±{tolerance} — оставлен флаг"
    )
