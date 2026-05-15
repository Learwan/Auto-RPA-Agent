from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from src.models.automation import AutomationFlow

logger = logging.getLogger(__name__)


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


@dataclass
class TemplateRating:
    user_id: str
    score: int
    comment: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class WorkflowTemplate:
    template_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    author_id: str = ""
    status: TemplateStatus = TemplateStatus.DRAFT
    flow_definition: dict = field(default_factory=dict)
    version: str = "1.0.0"
    downloads: int = 0
    ratings: list[TemplateRating] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "author_id": self.author_id,
            "status": self.status.value,
            "version": self.version,
            "downloads": self.downloads,
            "avg_rating": self._avg_rating(),
            "ratings_count": len(self.ratings),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def _avg_rating(self) -> float:
        if not self.ratings:
            return 0.0
        return sum(r.score for r in self.ratings) / len(self.ratings)


class MarketplaceService:
    def __init__(self):
        self._templates: dict[str, WorkflowTemplate] = {}

    def publish_template(
        self,
        flow: AutomationFlow,
        name: str,
        description: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        author_id: str = "",
    ) -> WorkflowTemplate:
        template = WorkflowTemplate(
            name=name,
            description=description,
            category=category,
            tags=tags or [],
            author_id=author_id,
            status=TemplateStatus.PUBLISHED,
            flow_definition=flow.model_dump(),
        )
        self._templates[template.template_id] = template
        logger.info("Published template: %s (id=%s)", name, template.template_id)
        return template

    def import_template(self, template_json: str) -> WorkflowTemplate | None:
        try:
            data = json.loads(template_json)
            flow_data = data.get("flow_definition", {})
            flow = AutomationFlow.model_validate(flow_data)
            template = WorkflowTemplate(
                name=data.get("name", flow.name),
                description=data.get("description", flow.description),
                category=data.get("category", "general"),
                tags=data.get("tags", []),
                author_id=data.get("author_id", ""),
                status=TemplateStatus(data.get("status", "draft")),
                flow_definition=flow_data,
                version=data.get("version", "1.0.0"),
            )
            self._templates[template.template_id] = template
            return template
        except Exception as e:
            logger.error("Failed to import template: %s", e)
            return None

    def export_template(self, template_id: str) -> str | None:
        template = self._templates.get(template_id)
        if template is None:
            return None
        return json.dumps(
            template.to_dict() | {"flow_definition": template.flow_definition}, indent=2, ensure_ascii=False
        )

    def get_template(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def search_templates(
        self,
        query: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
        sort_by: str = "downloads",
        limit: int = 20,
    ) -> list[WorkflowTemplate]:
        results = [t for t in self._templates.values() if t.status == TemplateStatus.PUBLISHED]

        if query:
            q_lower = query.lower()
            results = [t for t in results if q_lower in t.name.lower() or q_lower in t.description.lower()]

        if category:
            results = [t for t in results if t.category == category]

        if tags:
            results = [t for t in results if any(tag in t.tags for tag in tags)]

        sort_key = {
            "downloads": lambda t: t.downloads,
            "rating": lambda t: t._avg_rating(),
            "newest": lambda t: t.created_at,
            "name": lambda t: t.name,
        }.get(sort_by, lambda t: t.downloads)

        results.sort(key=sort_key, reverse=(sort_by != "name"))
        return results[:limit]

    def rate_template(self, template_id: str, user_id: str, score: int, comment: str = "") -> bool:
        if score < 1 or score > 5:
            return False
        template = self._templates.get(template_id)
        if template is None:
            return False
        existing = [r for r in template.ratings if r.user_id != user_id]
        existing.append(TemplateRating(user_id=user_id, score=score, comment=comment))
        template.ratings = existing
        return True

    def download_template(self, template_id: str) -> WorkflowTemplate | None:
        template = self._templates.get(template_id)
        if template:
            template.downloads += 1
        return template

    def deprecate_template(self, template_id: str) -> bool:
        template = self._templates.get(template_id)
        if template:
            template.status = TemplateStatus.DEPRECATED
            return True
        return False

    def list_categories(self) -> list[str]:
        categories = set()
        for t in self._templates.values():
            if t.status == TemplateStatus.PUBLISHED:
                categories.add(t.category)
        return sorted(categories)
