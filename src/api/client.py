import xml.etree.ElementTree as ET
from typing import AsyncGenerator, Optional, List

from src.api.base import BaseTableauClient
from src.utils.exceptions import APIError
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_NS = {"t": "http://tableau.com/api"}


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_any(root: ET.Element, local_name: str) -> Optional[ET.Element]:
    result = root.find(f".//t:{local_name}", _NS)
    if result is not None:
        return result
    for elem in root.iter():
        if _strip_ns(elem.tag) == local_name:
            return elem
    return None


def _findall_any(root: ET.Element, local_name: str) -> List[ET.Element]:
    results = root.findall(f".//t:{local_name}", _NS)
    if results:
        return results
    return [e for e in root.iter() if _strip_ns(e.tag) == local_name]


class TableauAPIClient:
    def __init__(self, base_client: BaseTableauClient):
        self._base = base_client

    @property
    def auth(self):
        return self._base.auth

    @property
    def site_id(self) -> str:
        return self._base.auth.site_id

    def _parse_xml(self, text: str) -> ET.Element:
        return ET.fromstring(text)

    async def negotiate_api_version(self) -> str:
        fallback = self._base._settings.api.api_version
        url = f"{self._base._settings.api.server_url}/api/{fallback}/serverinfo"
        print_status("GET", f"Negotiating API version from {self._base._settings.api.server_url}...")

        try:
            response = await self._base._client.request("GET", url)
            root = self._parse_xml(response.text)

            api_elem = root.find(".//{http://tableau.com/api}restApiVersion")
            if api_elem is None:
                api_elem = _find_any(root, "restApiVersion")

            if api_elem is not None and api_elem.text:
                version = api_elem.text.strip()
                self._base._settings.api.api_version = version
                print_status("GET", f"Negotiated API version: {version}")
                return version

            print_status("WARN", f"Could not parse API version from serverinfo, using fallback: {fallback}")
            return fallback
        except Exception as e:
            print_status("WARN", f"API version negotiation failed ({e}), using fallback: {fallback}")
            return fallback

    async def get(self, endpoint: str) -> ET.Element:
        response = await self._base.request("GET", endpoint)
        return self._parse_xml(response.text)

    async def post(self, endpoint: str, data: str) -> ET.Element:
        response = await self._base.request("POST", endpoint, content=data)
        return self._parse_xml(response.text)

    async def put(self, endpoint: str, data: str) -> ET.Element:
        response = await self._base.request("PUT", endpoint, content=data)
        return self._parse_xml(response.text)

    async def delete(self, endpoint: str) -> None:
        await self._base.request("DELETE", endpoint)

    async def paginate(self, endpoint: str, page_size: int = 100) -> AsyncGenerator[ET.Element, None]:
        page_number = 1
        total_available = None
        page_size_actual = page_size

        while True:
            separator = "&" if "?" in endpoint else "?"
            paged_endpoint = f"{endpoint}{separator}pageSize={page_size}&pageNumber={page_number}"

            root = await self.get(paged_endpoint)

            pagination = _find_any(root, "pagination")
            if pagination is not None:
                total_available = int(pagination.get("totalAvailable", "0"))
                page_size_actual = int(pagination.get("pageSize", str(page_size)))
                page_number_actual = int(pagination.get("pageNumber", str(page_number)))
            else:
                total_available = 0

            for child in root.iter():
                tag = _strip_ns(child.tag)
                if tag in ("tsResponse", "pagination"):
                    continue
                if len(child) > 0 and all(_strip_ns(c.tag) != tag for c in child):
                    continue
                if child is root:
                    continue
                parent_tag = None
                for potential_parent in root.iter():
                    if child in list(potential_parent):
                        parent_tag = _strip_ns(potential_parent.tag)
                        break
                if parent_tag and parent_tag not in ("tsResponse",):
                    yield child

            if total_available is None or total_available == 0:
                break

            fetched_so_far = page_number * page_size_actual
            if fetched_so_far >= total_available:
                break

            page_number += 1

    async def paginate_items(self, endpoint: str, element_tag: str, page_size: int = 100) -> List[ET.Element]:
        items = []
        page_number = 1

        while True:
            separator = "&" if "?" in endpoint else "?"
            paged_endpoint = f"{endpoint}{separator}pageSize={page_size}&pageNumber={page_number}"

            root = await self.get(paged_endpoint)
            found = _findall_any(root, element_tag)
            items.extend(found)

            pagination = _find_any(root, "pagination")
            if pagination is None:
                break

            total_available = int(pagination.get("totalAvailable", "0"))
            page_size_actual = int(pagination.get("pageSize", str(page_size)))
            fetched_so_far = page_number * page_size_actual
            if fetched_so_far >= total_available:
                break

            page_number += 1

        return items
