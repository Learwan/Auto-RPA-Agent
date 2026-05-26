from src.models.desktop import ElementCriteria, Rect, UIElement

MAX_SEARCH_DEPTH = 10
MAX_CHILDREN_PER_NODE = 100


def _get_pyatspi():
    import pyatspi

    return pyatspi


def _safe_name(element) -> str | None:
    text = getattr(element, "name", None)
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def _safe_description(element) -> str | None:
    text = getattr(element, "description", None)
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def _safe_value(element) -> str | None:
    try:
        text_iface = element.queryText()
        text = text_iface.getText(0, -1)
    except Exception:
        return None
    text = str(text).strip()
    return text or None


def _normalized_text(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _score_text(actual: str | None, expected: str | None, *, exact: int, contains: int) -> int:
    expected_text = _normalized_text(expected)
    actual_text = _normalized_text(actual)
    if not expected_text or not actual_text:
        return 0
    if actual_text == expected_text:
        return exact
    if expected_text in actual_text:
        return contains
    return 0


def _bounds_match(actual: Rect | None, expected: Rect | None, tolerance: int) -> bool:
    if actual is None or expected is None:
        return False
    return (
        abs(actual.x - expected.x) <= tolerance
        and abs(actual.y - expected.y) <= tolerance
        and abs(actual.width - expected.width) <= max(4, tolerance)
        and abs(actual.height - expected.height) <= max(4, tolerance)
    )


def _role_name(element) -> str:
    try:
        role_name = element.getRoleName()
        if role_name:
            return str(role_name).strip().lower().replace(" ", "_")
    except Exception:
        pass
    return "unknown"


def _bounds(element, pyatspi) -> Rect | None:
    try:
        component = element.queryComponent()
        extents = component.getExtents(pyatspi.DESKTOP_COORDS)
    except Exception:
        return None

    if extents.width <= 0 or extents.height <= 0:
        return None

    return Rect(x=int(extents.x), y=int(extents.y), width=int(extents.width), height=int(extents.height))


def _state_contains(element, pyatspi, state_name: str) -> bool:
    try:
        state = element.getState()
        return bool(state.contains(getattr(pyatspi, state_name)))
    except Exception:
        return False


def _element_to_ui(element, pyatspi) -> UIElement | None:
    role = _role_name(element)
    title = _safe_name(element)
    description = _safe_description(element)
    bounds = _bounds(element, pyatspi)
    identifier = None
    try:
        identifier = str(element.getAttributes().get("id", "")).strip() or None
    except Exception:
        identifier = None

    if role == "unknown" and title is None and identifier is None:
        return None

    try:
        children_count = int(getattr(element, "childCount", 0))
    except Exception:
        children_count = 0

    return UIElement(
        role=role,
        title=title,
        value=_safe_value(element),
        identifier=identifier,
        description=description,
        bounds=bounds,
        class_name=role,
        is_enabled=_state_contains(element, pyatspi, "STATE_ENABLED"),
        is_visible=_state_contains(element, pyatspi, "STATE_VISIBLE")
        or _state_contains(element, pyatspi, "STATE_SHOWING"),
        is_focusable=_state_contains(element, pyatspi, "STATE_FOCUSABLE"),
        is_focused=_state_contains(element, pyatspi, "STATE_FOCUSED"),
        children_count=children_count,
    )


def _matches_criteria(info: UIElement, criteria: ElementCriteria) -> int:
    score = 0

    if criteria.accessibility_id:
        if info.identifier == criteria.accessibility_id:
            score += 6
        elif info.identifier and criteria.accessibility_id.lower() in info.identifier.lower():
            score += 3

    if criteria.title:
        if info.title == criteria.title:
            score += 5
        elif info.title and criteria.title.lower() in info.title.lower():
            score += 2

    if criteria.text_contains and info.title and criteria.text_contains.lower() in info.title.lower():
        score += 4

    if criteria.role and info.role == criteria.role:
        score += 2

    if criteria.class_name and info.class_name == criteria.class_name:
        score += 2

    score += _score_text(info.value, criteria.value, exact=4, contains=2)
    score += _score_text(info.description, criteria.description, exact=3, contains=2)
    score += _score_text(getattr(info, "label", None), criteria.label, exact=3, contains=2)
    score += _score_text(getattr(info, "functional_label", None), criteria.functional_label, exact=3, contains=2)
    score += _score_text(getattr(info, "input_type", None), criteria.input_type, exact=2, contains=1)
    score += _score_text(getattr(info, "tag_name", None), criteria.tag_name, exact=2, contains=1)

    if criteria.bounds and _bounds_match(info.bounds, criteria.bounds, criteria.position_tolerance):
        score += 2

    if criteria.position and info.bounds:
        tolerance = criteria.position_tolerance
        if (
            info.bounds.x - tolerance <= criteria.position.x <= info.bounds.x + info.bounds.width + tolerance
            and info.bounds.y - tolerance <= criteria.position.y <= info.bounds.y + info.bounds.height + tolerance
        ):
            score += 2

    return score


def _iter_children(element):
    child_count = int(getattr(element, "childCount", 0))
    for index in range(min(child_count, MAX_CHILDREN_PER_NODE)):
        try:
            child = element.getChildAtIndex(index)
        except Exception:
            continue
        if child is not None:
            yield child


def _search(element, pyatspi, predicate, depth: int = 0):
    if depth > MAX_SEARCH_DEPTH or element is None:
        return None
    if predicate(element):
        return element
    for child in _iter_children(element):
        found = _search(child, pyatspi, predicate, depth + 1)
        if found is not None:
            return found
    return None


def _desktop_root(pyatspi):
    return pyatspi.Registry.getDesktop(0)


def _focused_accessible(pyatspi):
    desktop = _desktop_root(pyatspi)
    return _search(desktop, pyatspi, lambda element: _state_contains(element, pyatspi, "STATE_FOCUSED"))


def _active_application(pyatspi):
    focused = _focused_accessible(pyatspi)
    current = focused
    while current is not None:
        try:
            role = current.getRole()
        except Exception:
            break
        if role == pyatspi.ROLE_APPLICATION:
            return current
        current = getattr(current, "parent", None)
    return None


def _window_root_for_point(pyatspi, x: int, y: int):
    active_app = _active_application(pyatspi)
    if active_app is None:
        return None
    for child in _iter_children(active_app):
        try:
            component = child.queryComponent()
            target = component.getAccessibleAtPoint(x, y, pyatspi.DESKTOP_COORDS)
        except Exception:
            continue
        if target is not None:
            return target
    return None


async def get_focused_element() -> UIElement | None:
    try:
        pyatspi = _get_pyatspi()
    except Exception:
        return None

    focused = _focused_accessible(pyatspi)
    if focused is None:
        return None
    return _element_to_ui(focused, pyatspi)


async def get_element_at_position(x: int, y: int) -> UIElement | None:
    try:
        pyatspi = _get_pyatspi()
    except Exception:
        return None

    element = _window_root_for_point(pyatspi, x, y)
    if element is None:
        return None
    return _element_to_ui(element, pyatspi)


async def find_element_by_criteria(criteria: ElementCriteria) -> UIElement | None:
    try:
        pyatspi = _get_pyatspi()
    except Exception:
        return None

    active_app = _active_application(pyatspi)
    if active_app is None:
        return None

    best_match: UIElement | None = None
    best_score = 0

    def _visit(element, depth: int = 0) -> None:
        nonlocal best_match, best_score
        if depth > MAX_SEARCH_DEPTH or element is None:
            return

        info = _element_to_ui(element, pyatspi)
        if info is not None:
            score = _matches_criteria(info, criteria)
            if score > best_score:
                best_score = score
                best_match = info

        for child in _iter_children(element):
            _visit(child, depth + 1)

    _visit(active_app)
    return best_match if best_score > 0 else None
