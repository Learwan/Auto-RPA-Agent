from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.analyzer.preprocessor import NormalizedOperation

logger = logging.getLogger(__name__)

INTENT_MIN_OPERATIONS = 3
DATA_FLOW_CLIPBOARD_GAP_MS = 5000
VARIABLE_PATTERN_DATE = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?")
VARIABLE_PATTERN_AMOUNT = re.compile(r"[\d,]+\.?\d{0,2}")
VARIABLE_PATTERN_EMAIL = re.compile(r"[\w.-]+@[\w.-]+\.\w+")
VARIABLE_PATTERN_PHONE = re.compile(r"1[3-9]\d{9}")
VARIABLE_PATTERN_URL = re.compile(r"https?://[\w./\-?=&]+")


@dataclass
class SubGoal:
    description: str
    step_range: tuple[int, int]
    confidence: float
    intent_type: str = "unknown"
    operations_summary: str = ""


@dataclass
class IntentResult:
    overall_intent: str
    sub_goals: list[SubGoal]
    business_context: str
    confidence: float
    intent_hierarchy: dict = field(default_factory=dict)


@dataclass
class DataFlow:
    source_step: int
    target_step: int
    data_type: str
    value: str
    confidence: float = 1.0


@dataclass
class VariableCandidate:
    name: str
    value: str
    var_type: str
    step_index: int
    confidence: float = 0.8


@dataclass
class BusinessAnnotation:
    step_index: int
    business_term: str
    technical_op: str
    data_flow_role: str = "none"
    variable_name: str | None = None
    confidence: float = 0.0


class HierarchicalIntentRecognizer:
    def __init__(self):
        self._intent_templates = self._load_intent_templates()

    def recognize(self, operations: list[NormalizedOperation]) -> IntentResult:
        if len(operations) < INTENT_MIN_OPERATIONS:
            return IntentResult(
                overall_intent="操作序列过短",
                sub_goals=[],
                business_context="unknown",
                confidence=0.3,
            )

        sub_goals = self._identify_sub_goals(operations)
        overall_intent = self._infer_overall_intent(sub_goals, operations)
        business_context = self._infer_business_context(operations, sub_goals)
        confidence = self._compute_overall_confidence(sub_goals)

        hierarchy = {
            "level_workflow": overall_intent,
            "level_tasks": [sg.description for sg in sub_goals],
            "level_operations": [op.op_type for op in operations],
        }

        return IntentResult(
            overall_intent=overall_intent,
            sub_goals=sub_goals,
            business_context=business_context,
            confidence=confidence,
            intent_hierarchy=hierarchy,
        )

    def _identify_sub_goals(self, operations: list[NormalizedOperation]) -> list[SubGoal]:
        groups = self._group_by_context(operations)
        sub_goals = []

        for group_key, group_ops in groups.items():
            if not group_ops:
                continue

            start_idx = group_ops[0].seq_num if hasattr(group_ops[0], "seq_num") else 0
            end_idx = group_ops[-1].seq_num if hasattr(group_ops[-1], "seq_num") else len(group_ops) - 1

            description = self._describe_group(group_key, group_ops)
            intent_type = self._classify_intent(group_ops)
            summary = self._summarize_operations(group_ops)

            confidence = self._compute_group_confidence(group_ops)

            sub_goals.append(SubGoal(
                description=description,
                step_range=(start_idx, end_idx),
                confidence=confidence,
                intent_type=intent_type,
                operations_summary=summary,
            ))

        return sub_goals

    def _group_by_context(self, operations: list[NormalizedOperation]) -> dict[str, list[NormalizedOperation]]:
        groups: dict[str, list[NormalizedOperation]] = defaultdict(list)
        current_key = "initial"

        for op in operations:
            ctx = op.context or {}
            app_name = ctx.get("active_window", {}).get("app_name", "")
            window_title = ctx.get("active_window", {}).get("title", "")

            if app_name:
                new_key = f"app:{app_name}"
            elif window_title:
                new_key = f"win:{window_title[:30]}"
            else:
                new_key = current_key

            if new_key != current_key and groups.get(current_key):
                current_key = new_key

            groups[current_key].append(op)

        return dict(groups)

    def _describe_group(self, group_key: str, ops: list[NormalizedOperation]) -> str:
        op_types = [op.op_type for op in ops]
        type_counts: dict[str, int] = defaultdict(int)
        for t in op_types:
            type_counts[t] += 1

        dominant = max(type_counts, key=type_counts.get) if type_counts else "unknown"

        app_name = ""
        if group_key.startswith("app:") or group_key.startswith("win:"):
            app_name = group_key[4:]

        action_map = {
            "mouse_click": "点击交互",
            "type_text": "数据录入",
            "hotkey": "快捷操作",
            "mouse_scroll": "内容浏览",
            "mouse_drag": "拖拽移动",
            "clipboard": "数据复制",
            "navigation": "页面导航",
            "switch_window": "窗口切换",
            "upload_file": "文件上传",
            "download_file": "文件下载",
        }

        action = action_map.get(dominant, dominant)

        if app_name:
            return f"在{app_name}中{action}"
        return action

    def _classify_intent(self, ops: list[NormalizedOperation]) -> str:
        op_types = set(op.op_type for op in ops)

        has_input = "type_text" in op_types
        has_click = "mouse_click" in op_types
        has_navigation = "navigation" in op_types
        has_clipboard = "clipboard" in op_types

        if has_input and has_click and not has_navigation:
            return "data_entry"
        if has_navigation and has_click:
            return "browsing"
        if has_clipboard and has_input:
            return "data_transfer"
        if has_click and not has_input:
            return "interaction"
        if has_input and not has_click:
            return "text_input"
        return "mixed"

    def _summarize_operations(self, ops: list[NormalizedOperation]) -> str:
        parts = []
        for op in ops[:10]:
            if op.op_type == "type_text":
                text = op.data.get("text", "")[:30] if op.data else ""
                if text:
                    parts.append(f"输入'{text}'")
            elif op.op_type == "mouse_click":
                x = op.data.get("x", 0) if op.data else 0
                y = op.data.get("y", 0) if op.data else 0
                parts.append(f"点击({x},{y})")
            elif op.op_type == "hotkey":
                key = op.data.get("key", "") if op.data else ""
                parts.append(f"快捷键[{key}]")
            elif op.op_type == "navigation":
                url = op.data.get("url", "")[:50] if op.data else ""
                parts.append(f"导航→{url}")

        return "; ".join(parts[:5])

    def _compute_group_confidence(self, ops: list[NormalizedOperation]) -> float:
        if len(ops) < 2:
            return 0.4

        app_names = set()
        for op in ops:
            ctx = op.context or {}
            an = ctx.get("active_window", {}).get("app_name", "")
            if an:
                app_names.add(an)

        app_consistency = 1.0 if len(app_names) <= 1 else 0.6

        type_counts: dict[str, int] = defaultdict(int)
        for op in ops:
            type_counts[op.op_type] += 1
        dominant_ratio = max(type_counts.values()) / len(ops) if type_counts else 0

        return round(app_consistency * 0.5 + dominant_ratio * 0.3 + 0.2, 3)

    def _infer_overall_intent(self, sub_goals: list[SubGoal], operations: list[NormalizedOperation]) -> str:
        if not sub_goals:
            return "未知操作"

        intent_types = [sg.intent_type for sg in sub_goals]
        has_data_entry = "data_entry" in intent_types
        has_browsing = "browsing" in intent_types
        has_transfer = "data_transfer" in intent_types

        if has_data_entry and has_transfer:
            return "数据录入与传输"
        if has_data_entry and has_browsing:
            return "信息查询与录入"
        if has_data_entry:
            return "数据录入"
        if has_browsing:
            return "信息浏览"
        if has_transfer:
            return "数据搬运"
        if "text_input" in intent_types:
            return "文本编辑"
        if "interaction" in intent_types:
            return "界面操作"

        return "综合操作"

    def _infer_business_context(self, operations: list[NormalizedOperation], sub_goals: list[SubGoal]) -> str:
        texts = []
        for op in operations:
            if op.op_type == "type_text" and op.data:
                text = op.data.get("text", "")
                if text:
                    texts.append(text)

        urls = []
        for op in operations:
            if op.op_type == "navigation" and op.data:
                url = op.data.get("url", "")
                if url:
                    urls.append(url)

        window_titles = set()
        for op in operations:
            ctx = op.context or {}
            wt = ctx.get("active_window", {}).get("title", "")
            if wt:
                window_titles.add(wt)

        business_keywords = {
            "报表": "reporting",
            "订单": "order_management",
            "审批": "approval",
            "邮件": "email",
            "日程": "scheduling",
            "客户": "crm",
            "库存": "inventory",
            "财务": "finance",
            "销售": "sales",
            "采购": "procurement",
            "人事": "hr",
            "合同": "contract",
        }

        all_text = " ".join(texts + list(window_titles))
        for keyword, context in business_keywords.items():
            if keyword in all_text:
                return context

        if any("excel" in t.lower() or "sheet" in t.lower() for t in window_titles):
            return "spreadsheet"
        if any("mail" in t.lower() or "outlook" in t.lower() for t in window_titles):
            return "email"
        if any("chrome" in t.lower() or "browser" in t.lower() for t in window_titles):
            return "web_browsing"

        return "general"

    def _compute_overall_confidence(self, sub_goals: list[SubGoal]) -> float:
        if not sub_goals:
            return 0.3
        avg_conf = sum(sg.confidence for sg in sub_goals) / len(sub_goals)
        return round(min(avg_conf * 1.1, 0.95), 3)

    def _load_intent_templates(self) -> dict[str, list[str]]:
        return {
            "data_entry": ["type_text", "mouse_click", "tab"],
            "browsing": ["navigation", "mouse_click", "mouse_scroll"],
            "data_transfer": ["clipboard", "type_text", "mouse_click"],
            "interaction": ["mouse_click", "hotkey"],
        }


class DataFlowAnalyzer:
    def __init__(self):
        self._clipboard_buffer: str | None = None
        self._clipboard_time: int = 0
        self._download_buffer: str | None = None

    def analyze(self, operations: list[NormalizedOperation]) -> list[DataFlow]:
        flows = []
        self._clipboard_buffer = None
        self._download_buffer = None

        for i, op in enumerate(operations):
            flow = self._detect_data_flow(op, i, operations)
            if flow:
                flows.append(flow)

        return flows

    def _detect_data_flow(
        self, op: NormalizedOperation, idx: int, all_ops: list[NormalizedOperation]
    ) -> DataFlow | None:
        if op.op_type == "clipboard" and op.data:
            action = op.data.get("action", "")
            if action == "copy":
                self._clipboard_buffer = op.data.get("content", "")
                self._clipboard_time = op.timestamp
                return None

            if action == "paste" and self._clipboard_buffer:
                gap = op.timestamp - self._clipboard_time
                if gap < DATA_FLOW_CLIPBOARD_GAP_MS:
                    source_idx = self._find_copy_step(all_ops, idx)
                    flow = DataFlow(
                        source_step=source_idx,
                        target_step=idx,
                        data_type="clipboard",
                        value=self._clipboard_buffer[:100],
                        confidence=0.9 if gap < 2000 else 0.7,
                    )
                    self._clipboard_buffer = None
                    return flow

        if op.op_type == "upload_file" and op.data:
            path = op.data.get("path", "")
            if path:
                source_idx = self._find_download_step(all_ops, idx)
                if source_idx >= 0:
                    return DataFlow(
                        source_step=source_idx,
                        target_step=idx,
                        data_type="file",
                        value=path[:100],
                        confidence=0.6,
                    )

        return None

    def _find_copy_step(self, ops: list[NormalizedOperation], before_idx: int) -> int:
        for i in range(before_idx - 1, max(-1, before_idx - 10), -1):
            if ops[i].op_type == "clipboard" and ops[i].data and ops[i].data.get("action") == "copy":
                    return i
        return max(0, before_idx - 1)

    def _find_download_step(self, ops: list[NormalizedOperation], before_idx: int) -> int:
        for i in range(before_idx - 1, max(-1, before_idx - 20), -1):
            if ops[i].op_type == "download_file":
                return i
        return -1


class VariableIdentifier:
    def __init__(self):
        self._variable_counter: dict[str, int] = defaultdict(int)

    def identify(self, operations: list[NormalizedOperation]) -> list[VariableCandidate]:
        candidates = []

        for i, op in enumerate(operations):
            if op.op_type == "type_text" and op.data:
                text = op.data.get("text", "")
                if not text:
                    continue

                var = self._classify_text(text, i)
                if var:
                    candidates.append(var)

        return self._deduplicate(candidates)

    def _classify_text(self, text: str, step_index: int) -> VariableCandidate | None:
        if VARIABLE_PATTERN_DATE.search(text):
            self._variable_counter["date"] += 1
            return VariableCandidate(
                name=f"date_{self._variable_counter['date']}",
                value=text,
                var_type="date",
                step_index=step_index,
                confidence=0.9,
            )

        if VARIABLE_PATTERN_EMAIL.search(text):
            self._variable_counter["email"] += 1
            return VariableCandidate(
                name=f"email_{self._variable_counter['email']}",
                value=text,
                var_type="email",
                step_index=step_index,
                confidence=0.95,
            )

        if VARIABLE_PATTERN_PHONE.search(text):
            self._variable_counter["phone"] += 1
            return VariableCandidate(
                name=f"phone_{self._variable_counter['phone']}",
                value=text,
                var_type="phone",
                step_index=step_index,
                confidence=0.9,
            )

        if VARIABLE_PATTERN_URL.search(text):
            self._variable_counter["url"] += 1
            return VariableCandidate(
                name=f"url_{self._variable_counter['url']}",
                value=text,
                var_type="url",
                step_index=step_index,
                confidence=0.85,
            )

        if VARIABLE_PATTERN_AMOUNT.match(text) and len(text) <= 15:
            try:
                val = float(text.replace(",", ""))
                if 0 < val < 10000000:
                    self._variable_counter["amount"] += 1
                    return VariableCandidate(
                        name=f"amount_{self._variable_counter['amount']}",
                        value=text,
                        var_type="number",
                        step_index=step_index,
                        confidence=0.7,
                    )
            except ValueError:
                pass

        if len(text) >= 2 and len(text) <= 50 and not text.startswith("http"):
            self._variable_counter["text"] += 1
            return VariableCandidate(
                name=f"text_{self._variable_counter['text']}",
                value=text,
                var_type="string",
                step_index=step_index,
                confidence=0.5,
            )

        return None

    def _deduplicate(self, candidates: list[VariableCandidate]) -> list[VariableCandidate]:
        seen: dict[str, VariableCandidate] = {}
        for c in candidates:
            key = f"{c.var_type}:{c.value}"
            if key not in seen or c.confidence > seen[key].confidence:
                seen[key] = c
        return list(seen.values())


class BusinessSemanticExtractor:
    def __init__(self):
        self._intent_recognizer = HierarchicalIntentRecognizer()
        self._data_flow_analyzer = DataFlowAnalyzer()
        self._variable_identifier = VariableIdentifier()
        self._term_map = self._load_business_terms()

    def extract(self, operations: list[NormalizedOperation]) -> dict:
        intent_result = self._intent_recognizer.recognize(operations)
        data_flows = self._data_flow_analyzer.analyze(operations)
        variables = self._variable_identifier.identify(operations)
        annotations = self._annotate_operations(operations, data_flows, variables)

        return {
            "intent": {
                "overall": intent_result.overall_intent,
                "sub_goals": [
                    {
                        "description": sg.description,
                        "step_range": sg.step_range,
                        "confidence": sg.confidence,
                        "intent_type": sg.intent_type,
                    }
                    for sg in intent_result.sub_goals
                ],
                "business_context": intent_result.business_context,
                "confidence": intent_result.confidence,
            },
            "data_flows": [
                {
                    "source_step": df.source_step,
                    "target_step": df.target_step,
                    "data_type": df.data_type,
                    "value_preview": df.value[:50],
                    "confidence": df.confidence,
                }
                for df in data_flows
            ],
            "variables": [
                {
                    "name": v.name,
                    "value": v.value[:50],
                    "type": v.var_type,
                    "step_index": v.step_index,
                    "confidence": v.confidence,
                }
                for v in variables
            ],
            "annotations": [
                {
                    "step_index": a.step_index,
                    "business_term": a.business_term,
                    "technical_op": a.technical_op,
                    "data_flow_role": a.data_flow_role,
                    "variable_name": a.variable_name,
                    "confidence": a.confidence,
                }
                for a in annotations
            ],
        }

    def _annotate_operations(
        self,
        operations: list[NormalizedOperation],
        data_flows: list[DataFlow],
        variables: list[VariableCandidate],
    ) -> list[BusinessAnnotation]:
        annotations = []

        flow_source_map: dict[int, DataFlow] = {}
        flow_target_map: dict[int, DataFlow] = {}
        for df in data_flows:
            flow_source_map[df.source_step] = df
            flow_target_map[df.target_step] = df

        var_map: dict[int, VariableCandidate] = {}
        for v in variables:
            var_map[v.step_index] = v

        for i, op in enumerate(operations):
            business_term = self._map_to_business_term(op)
            data_role = "none"
            if i in flow_source_map:
                data_role = "source"
            elif i in flow_target_map:
                data_role = "target"

            var_name = None
            if i in var_map:
                var_name = var_map[i].name

            confidence = 0.6
            if data_role != "none":
                confidence += 0.15
            if var_name:
                confidence += 0.1
            if business_term != op.op_type:
                confidence += 0.1

            annotations.append(BusinessAnnotation(
                step_index=i,
                business_term=business_term,
                technical_op=op.op_type,
                data_flow_role=data_role,
                variable_name=var_name,
                confidence=min(confidence, 0.95),
            ))

        return annotations

    def _map_to_business_term(self, op: NormalizedOperation) -> str:
        data = op.data or {}
        ctx = op.context or {}
        window_title = ctx.get("active_window", {}).get("title", "").lower()

        if op.op_type == "type_text":
            text = data.get("text", "")
            for keyword, term in self._term_map.items():
                if keyword in text.lower() or keyword in window_title:
                    return term
            return "数据录入"

        if op.op_type == "mouse_click":
            for keyword, term in self._term_map.items():
                if keyword in window_title:
                    return term
            return "界面交互"

        if op.op_type == "clipboard":
            action = data.get("action", "")
            if action == "copy":
                return "数据复制"
            if action == "paste":
                return "数据粘贴"
            return "剪贴板操作"

        if op.op_type == "navigation":
            return "页面导航"

        if op.op_type == "hotkey":
            return "快捷操作"

        if op.op_type == "upload_file":
            return "文件上传"

        if op.op_type == "download_file":
            return "文件下载"

        return op.op_type

    def _load_business_terms(self) -> dict[str, str]:
        return {
            "提交": "提交表单",
            "保存": "保存数据",
            "搜索": "信息检索",
            "查询": "信息查询",
            "登录": "用户认证",
            "登出": "退出系统",
            "删除": "数据删除",
            "编辑": "数据修改",
            "新建": "创建记录",
            "添加": "添加数据",
            "导出": "数据导出",
            "导入": "数据导入",
            "发送": "消息发送",
            "回复": "消息回复",
            "审批": "流程审批",
            "确认": "操作确认",
            "取消": "操作取消",
            "刷新": "数据刷新",
            "下载": "文件下载",
            "上传": "文件上传",
        }
