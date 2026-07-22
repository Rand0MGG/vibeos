from vibeos.apps import AppRegistry
from pathlib import Path

from vibeos.capabilities import CAPABILITIES, capability_payload, effect_policy_summary, executable_actions
from vibeos.clipboard import ClipboardAdapter
from vibeos.notifications import NotificationAdapter
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.composition import compose_foundation
from vibeos.portal import PortalAdapter
from vibeos.tool_protocol import ToolRegistry
from vibeos.tools.apps import app_tool_specs
from vibeos.tools.browser import browser_tool_specs
from vibeos.tools.clipboard import clipboard_tool_specs
from vibeos.tools.fixtures import fixture_tool_specs
from vibeos.tools.system import system_tool_specs
from vibeos.tools.registry import CapabilityRecipeRegistry, build_tool_registry
from vibeos.tools.windows import window_tool_specs
from vibeos.windows import WindowRegistry
from vibeos.verifiers import VerifierHarness
from vibeos.task_models import DisplayFields, TaskPlan, TaskRoute, TaskStep


def test_extracted_domain_tool_specs_preserve_registered_tool_ids(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path / "specs.sqlite3")
    registry = ToolRegistry(
        (
            *app_tool_specs(AppRegistry()),
            *window_tool_specs(WindowRegistry()),
            *clipboard_tool_specs(ClipboardAdapter()),
            *system_tool_specs(PortalAdapter()),
            *foundation.tool_specs,
            *browser_tool_specs(PortalAdapter(), VerifierHarness()),
            *fixture_tool_specs(),
        )
    )

    assert set(registry.ids()) == {
        "app.list",
        "app.open",
        "apps.resolve_installed",
        "clipboard.write",
        "browser.follow_search_result",
        "browser.observe_context",
        "browser.observe_search_results",
        "browser.open_resolved_target",
        "browser.open_site_search",
        "browser.open_url",
        "browser.resolve_named_target",
        "browser.search_web",
        "browser.verify_goal_page_identity",
        "browser.verify_query",
        "browser.verify_url_opened",
        "notification.send",
        "portal.open_uri",
        "system.status",
        "app.fixture.activate_search_shortcut",
        "app.fixture.enter_search_query",
        "app.fixture.locate_search_control",
        "app.fixture.observe_results",
        "app.fixture.verify_target_presence",
        "window.close",
        "window.focus",
        "window.list",
        "window.maximize",
        "window.minimize",
        "window.resolve",
    }


def test_capability_recipes_cover_every_registered_capability() -> None:
    recipes = CapabilityRecipeRegistry()
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_recipe_coverage",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id="app_structured_search_route",
        routes=(TaskRoute(id="app_structured_search_route", score=1.0, domain_id="apps"),),
    )
    generic_target = {
        "name": "example",
        "app": "example",
        "query": "example",
        "text": "example",
        "uri": "https://example.com",
        "title": "example",
        "body": "example",
    }

    uncovered = [
        action
        for action in CAPABILITIES
        if not recipes.calls_for(
            plan,
            TaskStep(id=f"step_{action.replace('.', '_')}", action=action, capability_id=action, target=generic_target),
        )
    ]

    assert uncovered == []


def test_composed_tool_registry_contains_every_recipe_tool(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path / "registry.sqlite3")
    registry = build_tool_registry(
        apps=AppRegistry(),
        windows=WindowRegistry(),
        portal=PortalAdapter(),
        clipboard=ClipboardAdapter(),
        verifiers=VerifierHarness(),
        foundation_specs=foundation.tool_specs,
    )
    recipes = CapabilityRecipeRegistry()
    plan = TaskPlan(
        schema_version="v2",
        plan_id="plan_recipe_registry",
        utterance="test",
        display=DisplayFields(goal="test"),
        selected_route_id="app_structured_search_route",
        routes=(TaskRoute(id="app_structured_search_route", score=1.0, domain_id="apps"),),
    )
    target = {"name": "example", "app": "example", "query": "example", "text": "example", "uri": "https://example.com"}
    recipe_tools = {
        call.tool_id
        for action in CAPABILITIES
        for call in recipes.calls_for(plan, TaskStep(id=f"step_{action.replace('.', '_')}", action=action, capability_id=action, target=target))
    }

    assert recipe_tools <= set(registry.ids())


def make_foundation(path: Path):
    database = CoreDatabase(path)
    database.upgrade()
    return compose_foundation(
        database=database,
        portal=PortalAdapter(),
        notifications=NotificationAdapter(),
        capabilities=lambda: {
            "schema_version": "v2",
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "effect_policy": effect_policy_summary(),
        },
    )
