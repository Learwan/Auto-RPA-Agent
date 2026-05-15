from src.audit.logger import AuditAction, AuditLogger, AuditResource
from src.auth.rbac import Permission, RBACManager, Role
from src.llm.model_manager import ModelManager
from src.marketplace.service import MarketplaceService
from src.models.automation import AutomationFlow, AutomationStep, ErrorHandlingPolicy, StepTarget, StepType
from src.scheduler.service import SchedulerService, TriggerType
from src.vault.manager import Vault


class TestRBAC:
    def test_create_user(self):
        manager = RBACManager()
        user = manager.create_user("testuser", Role.EDITOR)
        assert user.username == "testuser"
        assert user.role == Role.EDITOR

    def test_check_permission_admin(self):
        manager = RBACManager()
        user = manager.create_user("admin", Role.ADMIN)
        assert manager.check_permission(user.user_id, Permission.MANAGE_USERS)

    def test_check_permission_viewer_denied(self):
        manager = RBACManager()
        user = manager.create_user("viewer", Role.VIEWER)
        assert not manager.check_permission(user.user_id, Permission.CREATE_SESSION)

    def test_check_permission_editor(self):
        manager = RBACManager()
        user = manager.create_user("editor", Role.EDITOR)
        assert manager.check_permission(user.user_id, Permission.CREATE_SESSION)
        assert not manager.check_permission(user.user_id, Permission.MANAGE_USERS)

    def test_deactivate_user(self):
        manager = RBACManager()
        user = manager.create_user("test", Role.EDITOR)
        manager.deactivate_user(user.user_id)
        assert not manager.check_permission(user.user_id, Permission.CREATE_SESSION)

    def test_find_user_by_username(self):
        manager = RBACManager()
        manager.create_user("alice", Role.ADMIN)
        found = manager.find_user_by_username("alice")
        assert found is not None
        assert found.role == Role.ADMIN


class TestAuditLogger:
    def test_log_entry(self):
        logger = AuditLogger()
        entry = logger.log("user1", AuditAction.CREATE, AuditResource.AUTOMATION, "auto-1")
        assert entry.user_id == "user1"
        assert entry.action == AuditAction.CREATE
        assert entry.success is True

    def test_query_entries(self):
        logger = AuditLogger()
        logger.log("user1", AuditAction.CREATE, AuditResource.AUTOMATION)
        logger.log("user2", AuditAction.EXECUTE, AuditResource.EXECUTION)
        logger.log("user1", AuditAction.DELETE, AuditResource.AUTOMATION)

        results = logger.query(user_id="user1")
        assert len(results) == 2

        results = logger.query(action=AuditAction.EXECUTE)
        assert len(results) == 1

    def test_export_entries(self):
        logger = AuditLogger()
        logger.log("user1", AuditAction.CREATE, AuditResource.AUTOMATION)
        entries = logger.export_entries()
        assert len(entries) == 1
        assert entries[0]["action"] == "create"


class TestVault:
    def test_store_and_retrieve(self):
        vault = Vault(vault_key="test-key-12345")
        cred = vault.store("db_password", "secret123", "password")
        value = vault.retrieve(cred.credential_id)
        assert value == "secret123"

    def test_retrieve_by_name(self):
        vault = Vault(vault_key="test-key-12345")
        vault.store("api_key", "ak-12345", "api_key")
        value = vault.retrieve_by_name("api_key")
        assert value == "ak-12345"

    def test_delete_credential(self):
        vault = Vault(vault_key="test-key-12345")
        cred = vault.store("temp", "value", "password")
        assert vault.delete(cred.credential_id)
        assert vault.retrieve(cred.credential_id) is None

    def test_list_credentials(self):
        vault = Vault(vault_key="test-key-12345")
        vault.store("key1", "val1", "password")
        vault.store("key2", "val2", "api_key")
        listing = vault.list_credentials()
        assert len(listing) == 2


class TestScheduler:
    def test_create_schedule(self):
        scheduler = SchedulerService()
        entry = scheduler.create_schedule("auto-1", "Daily Run", TriggerType.CRON, cron_expression="0 9 * * *")
        assert entry.automation_id == "auto-1"
        assert entry.trigger_type == TriggerType.CRON

    def test_pause_and_resume(self):
        scheduler = SchedulerService()
        entry = scheduler.create_schedule("auto-1", "Test")
        scheduler.pause_schedule(entry.schedule_id)
        assert scheduler.get_schedule(entry.schedule_id).status.value == "paused"
        scheduler.resume_schedule(entry.schedule_id)
        assert scheduler.get_schedule(entry.schedule_id).status.value == "active"

    def test_record_run(self):
        scheduler = SchedulerService()
        entry = scheduler.create_schedule("auto-1", "Test")
        scheduler.record_run(entry.schedule_id, success=True)
        assert scheduler.get_schedule(entry.schedule_id).run_count == 1


class TestMarketplace:
    def _make_flow(self) -> AutomationFlow:
        return AutomationFlow(
            id="flow-1",
            name="Test Flow",
            steps=[
                AutomationStep(id="s1", type=StepType.CLICK, action={"button": "left"}, target=StepTarget()),
            ],
            error_handling=ErrorHandlingPolicy(),
        )

    def test_publish_template(self):
        market = MarketplaceService()
        flow = self._make_flow()
        template = market.publish_template(flow, "My Template", "A test template", category="testing")
        assert template.name == "My Template"
        assert template.status.value == "published"

    def test_search_templates(self):
        market = MarketplaceService()
        flow = self._make_flow()
        market.publish_template(flow, "Click Template", "Click automation", category="ui", tags=["click"])
        results = market.search_templates(query="Click")
        assert len(results) == 1

    def test_rate_template(self):
        market = MarketplaceService()
        flow = self._make_flow()
        template = market.publish_template(flow, "Rated Template")
        assert market.rate_template(template.template_id, "user1", 4, "Great!")
        assert market.get_template(template.template_id)._avg_rating() == 4.0

    def test_download_increments(self):
        market = MarketplaceService()
        flow = self._make_flow()
        template = market.publish_template(flow, "Popular Template")
        market.download_template(template.template_id)
        market.download_template(template.template_id)
        assert market.get_template(template.template_id).downloads == 2


class TestModelManager:
    def test_list_models(self):
        manager = ModelManager()
        models = manager.list_models()
        assert len(models) > 0

    def test_get_model(self):
        manager = ModelManager()
        model = manager.get_model("qwen3.5-4b-4bit")
        assert model is not None
        assert model.model_type == "grounding"

    def test_scan_models(self):
        manager = ModelManager()
        models = manager.scan_models()
        assert len(models) > 0

    def test_register_custom_model(self):
        from src.llm.model_manager import ModelInfo

        manager = ModelManager()
        custom = ModelInfo(name="custom-model", model_type="grounding", path="/tmp/custom")
        manager.register_model(custom)
        assert manager.get_model("custom-model") is not None

    def test_unregister_model(self):
        from src.llm.model_manager import ModelInfo

        manager = ModelManager()
        custom = ModelInfo(name="temp-model", model_type="grounding", path="/tmp/temp")
        manager.register_model(custom)
        assert manager.unregister_model("temp-model")
        assert manager.get_model("temp-model") is None
