from vibeos.apps import AppRegistry
from vibeos.clipboard import ClipboardAdapter
from vibeos.notifications import NotificationAdapter
from vibeos.portal import PortalAdapter
from vibeos.tool_protocol import ToolRegistry
from vibeos.tools.apps import app_tool_specs
from vibeos.tools.browser import browser_tool_specs
from vibeos.tools.clipboard import clipboard_tool_specs
from vibeos.tools.fixtures import fixture_tool_specs
from vibeos.tools.notifications import notification_tool_specs
from vibeos.tools.system import system_tool_specs
from vibeos.tools.windows import window_tool_specs
from vibeos.windows import WindowRegistry
from vibeos.verifiers import VerifierHarness


def test_extracted_domain_tool_specs_preserve_registered_tool_ids() -> None:
    registry = ToolRegistry(
        (
            *app_tool_specs(AppRegistry()),
            *window_tool_specs(WindowRegistry()),
            *clipboard_tool_specs(ClipboardAdapter()),
            *notification_tool_specs(NotificationAdapter()),
            *system_tool_specs(PortalAdapter(), lambda: {"capabilities": ["system.status"]}),
            *browser_tool_specs(PortalAdapter(), VerifierHarness()),
            *fixture_tool_specs(),
        )
    )

    assert set(registry.ids()) == {
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
