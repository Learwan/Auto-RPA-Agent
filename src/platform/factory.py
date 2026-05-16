import platform

from src.platform.base import BasePlatformAdapter


def get_platform_adapter() -> BasePlatformAdapter:
    system = platform.system().lower()
    if system == "darwin":
        from src.platform.macos.adapter import MacOSAdapter

        return MacOSAdapter()
    elif system == "windows":
        from src.platform.windows.adapter import WindowsAdapter

        return WindowsAdapter()
    elif system == "linux":
        from src.platform.linux.adapter import LinuxAdapter

        return LinuxAdapter()
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def get_current_platform() -> str:
    return platform.system().lower()


def is_web_target(target) -> bool:
    if target is None:
        return False

    from src.models.automation import LocateStrategy

    return bool(
        target.strategy in {LocateStrategy.CSS_SELECTOR, LocateStrategy.XPATH}
        or target.selector
        or target.xpath
        or target.url
        or target.frame
    )


def is_web_flow(flow) -> bool:
    return any(is_web_target(step.target) or bool(step.action.get("url")) for step in flow.steps)


def create_adapter_for_flow(flow) -> BasePlatformAdapter:
    if is_web_flow(flow):
        from src.config import settings

        if getattr(settings, "WEB_USE_MCP", False):
            from src.platform.web.mcp_adapter import PlaywrightMCPAdapter

            return PlaywrightMCPAdapter()

        from src.platform.web.adapter import WebAdapter

        return WebAdapter()
    return get_platform_adapter()


create_platform_adapter = get_platform_adapter
