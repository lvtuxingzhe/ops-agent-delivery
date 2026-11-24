"""部署域服务包拆分回归：门面符号与 patch 语义。"""

from __future__ import annotations

from app.services import deployment_service
from app.services.deployment import lifecycle, model, plans, queries, templates


def test_facade_reexports_implementation_symbols() -> None:
    for name in (
        "NAMESPACE",
        "DEFAULT_TTL_MINUTES",
        "MAX_TTL_MINUTES",
        "MAX_OPS",
        "SCOPES",
        "ROLES",
        "READ_ROLES",
        "TEMPLATE_OS_FAMILIES",
        "TEMPLATE_PACKAGE_MANAGERS",
        "COMMAND_OS_FAMILY",
        "COMMAND_PACKAGE_MANAGER",
    ):
        assert getattr(deployment_service, name) is getattr(model, name)
    assert deployment_service.DeploymentError is model.DeploymentError
    assert deployment_service.canonical_hash is model.canonical_hash
    assert deployment_service.attach_policy_hash is model.attach_policy_hash
    assert deployment_service.deploy_type_of is model.deploy_type_of
    assert deployment_service._validate_template is templates._validate_template
    assert deployment_service._load_template_row is templates._load_template_row
    assert deployment_service.SEED_TEMPLATES is templates.SEED_TEMPLATES
    assert deployment_service.build_plan is plans.build_plan
    assert deployment_service.build_ai_plan is plans.build_ai_plan
    assert deployment_service.create_deployment is plans.create_deployment
    assert deployment_service.get_deployment is queries.get_deployment
    assert deployment_service.list_deployments is queries.list_deployments
    assert deployment_service.disable_template is lifecycle.disable_template


def test_approval_functions_stay_on_facade_for_patchability() -> None:
    for name in (
        "ensure_plan_executable",
        "confirm_plan",
        "approve_plan",
        "_consume_token",
    ):
        fn = getattr(deployment_service, name)
        assert fn.__module__ == "app.services.deployment_service"


def test_facade_public_surface_is_stable() -> None:
    assert set(deployment_service.__all__) == {
        "DeploymentError",
        "SEED_TEMPLATES",
        "approve_plan",
        "assign_approver",
        "canonical_hash",
        "confirm_plan",
        "create_deployment",
        "disable_template",
        "ensure_plan_executable",
        "get_deployment",
        "list_deployments",
        "list_templates",
        "load_template",
        "seed_templates",
    }
