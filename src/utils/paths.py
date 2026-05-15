def resolve_endpoint_path(path: str, site_id: str, **kwargs) -> str:
    formatted = path.format(site_id=site_id, **kwargs)
    if formatted.startswith("-/"):
        return f"/-/{formatted[2:]}"
    return f"/{formatted}" if not formatted.startswith("/") else formatted


def resolve_element_tag(ep_config: dict, ep_name: str) -> str:
    rk = ep_config.get("response_key")
    if rk and "." in rk:
        return rk.split(".")[-1]
    return rk or ep_name
