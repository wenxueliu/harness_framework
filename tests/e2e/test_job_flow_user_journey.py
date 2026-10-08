"""Browser journey for the template -> instance -> task detail flow."""
from __future__ import annotations

import time

import pytest

from .conftest import api_json
from .helpers import wait_for_network_idle
from .webbridge import Page, expect


@pytest.mark.e2e
@pytest.mark.smoke
def test_template_instance_task_detail_and_theme(
    page: Page, dashboard_url: str,
) -> None:
    suffix = str(time.time_ns())
    template_id = "ui-template-" + suffix
    instance_name = "UI instance " + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI journey " + suffix,
            "tasks": [
                {"id": "build", "type": "backend", "depends_on": []},
                {"id": "test", "type": "test", "depends_on": ["build"]},
            ],
        })
    except RuntimeError as error:
        if ": 404 " in str(error):
            pytest.skip("当前 WEBAPI 进程尚未部署模板/实例 API")
        raise
    published = api_json(
        "POST", "/api/templates/" + template_id + "/publish", {},
    )
    api_json("POST", "/api/instances", {
        "template_id": template_id,
        "version_id": published["version"]["version_id"],
        "name": instance_name,
        "parameters": {"suite": "smoke"},
        "git": {"ref": "ui-test"},
        "workspace": {"workspace_id": "ui-workspace"},
    })

    page.goto(dashboard_url + "/#/instances")
    wait_for_network_idle(page)
    expect(page.get_by_text(instance_name)).to_be_visible(timeout=15000)
    page.get_by_text(instance_name).click()
    wait_for_network_idle(page)
    expect(page.get_by_text("Instance Detail")).to_be_visible(timeout=10000)

    page.locator('[data-testid="instance-task"]').first.click()
    wait_for_network_idle(page)
    expect(page.get_by_text("Task Detail")).to_be_visible(timeout=10000)
    page.locator('[data-testid="task-detail-back"]').click()
    wait_for_network_idle(page)
    expect(page.get_by_text("Instance Detail")).to_be_visible(timeout=10000)

    page.evaluate("""() => {
      const select = document.querySelector('select[aria-label="主题"]');
      if (!select) return false;
      select.value = 'light';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return document.documentElement.dataset.theme;
    }""")
    assert page.evaluate("() => document.documentElement.dataset.theme") == "light"


@pytest.mark.e2e
def test_template_detail_shows_version_diff(page: Page, dashboard_url: str) -> None:
    """Selecting a version renders the immutable version and its structural diff."""
    suffix = str(time.time_ns())
    template_id = "ui-version-diff-" + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI version diff " + suffix,
            "tasks": [
                {"id": "build", "type": "backend", "depends_on": []},
                {"id": "test", "type": "test", "depends_on": ["build"]},
            ],
        })
        first = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("PATCH", f"/api/templates/{template_id}/draft", {
            "expected_revision": 1,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        second = api_json("POST", f"/api/templates/{template_id}/publish", {})
        page.goto(dashboard_url + f"/#/templates/{template_id}")
        wait_for_network_idle(page)
        page.locator(f'[data-testid="version-tab-{second["version"]["version_id"]}"]').wait_for(timeout=10000)
        page.locator(f'[data-testid="version-tab-{second["version"]["version_id"]}"]').click()
        expect(page.get_by_text("版本差异")).to_be_visible(timeout=10000)
        expect(page.locator('[data-testid="version-diff"]')).to_be_visible()
        assert "1 个节点" in page.content()
        assert first["version"]["manifest_hash"] != second["version"]["manifest_hash"]
    finally:
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass


@pytest.mark.e2e
@pytest.mark.smoke
def test_home_shows_instances_and_template_create_button(
    page: Page, dashboard_url: str,
) -> None:
    """The homepage lists Job Flow instances and links to template creation."""
    suffix = str(time.time_ns())
    template_id = "ui-home-template-" + suffix
    instance_id = "ui-home-instance-" + suffix
    instance_name = "UI home instance " + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI home journey " + suffix,
            "tasks": [
                {"id": "build", "type": "backend", "depends_on": []},
                {"id": "test", "type": "test", "depends_on": ["build"]},
            ],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id,
            "template_id": template_id,
            "version_id": published["version"]["version_id"],
            "name": instance_name,
            "parameters": {}, "git": {}, "workspace": {},
        })

        page.goto(dashboard_url + "/#/")
        wait_for_network_idle(page)

        expect(page.get_by_text(instance_name)).to_be_visible(timeout=15000)

        page.locator("text=从模板创建").first.wait_for(timeout=10000)

        page.locator("text=从模板创建").first.click()
        wait_for_network_idle(page)
        expect(page.get_by_text("作业流模板")).to_be_visible(timeout=10000)
    finally:
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass


@pytest.mark.e2e
@pytest.mark.smoke
def test_queued_instance_can_be_deleted_from_instance_list(
    page: Page, dashboard_url: str,
) -> None:
    """A queued instance is removable from the list before execution starts."""
    suffix = str(time.time_ns())
    template_id = "ui-delete-template-" + suffix
    instance_name = "UI queued delete " + suffix
    instance_id = "ui-queued-delete-" + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI delete journey " + suffix,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id,
            "template_id": template_id,
            "version_id": published["version"]["version_id"],
            "name": instance_name,
            "parameters": {}, "git": {}, "workspace": {},
        })

        page.goto(dashboard_url + "/#/instances")
        wait_for_network_idle(page)
        instance_link = page.get_by_text(instance_name)
        expect(instance_link).to_be_visible(timeout=15000)
        page.evaluate("() => { window.confirm = () => true; return true; }")
        instance_link.locator("..").locator("..").locator(
            '[data-testid="delete-instance"]'
        ).click()
        expect(instance_link).to_be_hidden(timeout=15000)
    finally:
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass


@pytest.mark.e2e
def test_running_instance_delete_action_is_disabled(
    page: Page, dashboard_url: str,
) -> None:
    """The UI prevents deleting an instance after execution has started."""
    suffix = str(time.time_ns())
    template_id = "ui-running-delete-template-" + suffix
    instance_name = "UI running delete " + suffix
    instance_id = "ui-running-delete-" + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI running delete journey " + suffix,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id,
            "template_id": template_id,
            "version_id": published["version"]["version_id"],
            "name": instance_name,
            "parameters": {}, "git": {}, "workspace": {},
        })
        api_json("POST", f"/api/instances/{instance_id}/start", {})

        page.goto(dashboard_url + "/#/instances")
        wait_for_network_idle(page)
        instance_link = page.get_by_text(instance_name)
        expect(instance_link).to_be_visible(timeout=15000)
        is_disabled = page.evaluate("""(name) => {
          const link = Array.from(document.querySelectorAll('[data-testid="instance-card"] a'))
            .find((element) => (element.textContent || '').includes(name));
          return Boolean(link?.closest('[data-testid="instance-card"]')
            ?.querySelector('[data-testid="delete-instance"]')?.disabled);
        }""", instance_name)
        assert is_disabled is True
    finally:
        try:
            api_json("POST", f"/api/instances/{instance_id}/abort", {})
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass


@pytest.mark.e2e
def test_project_group_context_filters_templates_and_instances(
    page: Page, dashboard_url: str,
) -> None:
    """Template ownership is shown and both lists honor the project-group context."""
    suffix = str(time.time_ns())
    group_id = ""
    template_id = "ui-group-template-" + suffix
    template_name = "UI group template " + suffix
    instance_id = "ui-group-instance-" + suffix
    instance_name = "UI group instance " + suffix
    try:
        group = api_json("POST", "/api/project-groups", {
            "name": "UI group " + suffix, "description": "UI project-group journey",
        })["project_group"]
        group_id = group["group_id"]
        api_json("POST", "/api/templates", {
            "template_id": template_id, "name": template_name,
            "group_id": group_id,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id, "template_id": template_id,
            "version_id": published["version"]["version_id"], "name": instance_name,
            "parameters": {}, "git": {}, "workspace": {},
        })

        page.goto(dashboard_url + "/#/templates?groupId=" + group_id)
        wait_for_network_idle(page)
        expect(page.get_by_text(template_name)).to_be_visible(timeout=15000)
        expect(page.get_by_text(group_id)).to_be_visible(timeout=10000)

        page.goto(dashboard_url + "/#/instances?groupId=" + group_id)
        wait_for_network_idle(page)
        expect(page.get_by_text(instance_name)).to_be_visible(timeout=15000)
        expect(page.get_by_text(group_id)).to_be_visible(timeout=10000)
    finally:
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass
        if group_id:
            try:
                api_json("PATCH", f"/api/project-groups/{group_id}", {
                    "expected_revision": group["revision"], "status": "ARCHIVED",
                })
            except RuntimeError:
                pass


@pytest.mark.e2e
@pytest.mark.smoke
def test_template_detail_shows_instances_and_archive(
    page: Page, dashboard_url: str,
) -> None:
    """Template detail shows instance area, create-instance link, and archive."""
    suffix = str(time.time_ns())
    template_id = "ui-tmpl-detail-" + suffix
    instance_id = "ui-inst-detail-" + suffix
    instance_name = "UI detail instance " + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI template detail " + suffix,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id,
            "template_id": template_id,
            "version_id": published["version"]["version_id"],
            "name": instance_name,
            "parameters": {}, "git": {}, "workspace": {},
        })

        page.goto(dashboard_url + f"/#/templates/{template_id}")
        wait_for_network_idle(page)

        expect(page.locator('[data-testid="instance-area"]')).to_be_visible(timeout=15000)
        expect(page.get_by_text(instance_name)).to_be_visible(timeout=10000)
        expect(page.locator('[data-testid="create-instance"]')).to_be_visible(timeout=10000)
        expect(page.locator('[data-testid="archive-template"]')).to_be_visible(timeout=10000)
        expect(page.locator('[data-testid="template-status"]')).to_be_visible(timeout=10000)
        expect(page.locator('[data-testid="version-tabs"]')).to_be_visible(timeout=10000)

        status_text = page.evaluate(
            "() => document.querySelector('[data-testid=\"template-status\"]')?.textContent || ''"
        )
        assert status_text == "ACTIVE", f"Expected ACTIVE, got: {status_text}"
    finally:
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass


@pytest.mark.e2e
def test_instance_detail_shows_breadcrumb(
    page: Page, dashboard_url: str,
) -> None:
    """Instance detail shows breadcrumb: group > template > version > instance."""
    suffix = str(time.time_ns())
    template_id = "ui-breadcrumb-tmpl-" + suffix
    instance_id = "ui-breadcrumb-inst-" + suffix
    try:
        api_json("POST", "/api/templates", {
            "template_id": template_id,
            "name": "UI breadcrumb " + suffix,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        published = api_json("POST", f"/api/templates/{template_id}/publish", {})
        api_json("POST", "/api/instances", {
            "instance_id": instance_id,
            "template_id": template_id,
            "version_id": published["version"]["version_id"],
            "name": "UI breadcrumb instance " + suffix,
            "parameters": {}, "git": {}, "workspace": {},
        })

        page.goto(dashboard_url + f"/#/instances/{instance_id}")
        wait_for_network_idle(page)

        breadcrumb = page.locator('[data-testid="instance-breadcrumb"]')
        expect(breadcrumb).to_be_visible(timeout=15000)
        breadcrumb_text = page.evaluate(
            "() => document.querySelector('[data-testid=\"instance-breadcrumb\"]')?.textContent || ''"
        )
        assert template_id in breadcrumb_text, (
            f"breadcrumb should contain template_id, got: {breadcrumb_text}"
        )
        assert published["version"]["version_id"] in breadcrumb_text
    finally:
        try:
            api_json("DELETE", f"/api/instances/{instance_id}")
        except RuntimeError:
            pass
        try:
            api_json("DELETE", f"/api/templates/{template_id}")
        except RuntimeError:
            pass
