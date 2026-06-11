from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppSearchFixture:
    app_name: str
    fixture_id: str
    visible_controls: tuple[str, ...] = ()
    shortcut_search_enabled: bool = False
    search_results: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def has_control(self, control_id: str) -> bool:
        return control_id in self.visible_controls

    def results_for(self, query: str) -> tuple[str, ...]:
        return self.search_results.get(query.strip().lower(), ())
