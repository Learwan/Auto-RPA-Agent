from src.executor.element_locator import LocatedElement
from src.executor.step_verifier import COORDINATE_TOLERANCE_PX, CORE_ATTRIBUTES, FUZZY_ATTRIBUTES, StepVerifier
from src.models.automation import LocateStrategy, StepTarget
from src.models.desktop import Point, Rect, UIElement
from src.models.execution import VerificationLevel


def _make_ui_element(
    role: str = "button",
    title: str | None = "Test",
    identifier: str | None = None,
    class_name: str | None = None,
    selector: str | None = None,
    xpath: str | None = None,
    bounds: Rect | None = Rect(x=100, y=200, width=80, height=30),
) -> UIElement:
    return UIElement(
        role=role,
        title=title,
        identifier=identifier,
        class_name=class_name,
        selector=selector,
        xpath=xpath,
        bounds=bounds,
    )


def _make_located(
    element: UIElement | None = None,
    position: Point | None = None,
    strategy: LocateStrategy = LocateStrategy.ACCESSIBILITY_ID,
) -> LocatedElement:
    if element is None:
        element = _make_ui_element()
    return LocatedElement(element=element, strategy_used=strategy, position=position or Point(x=140, y=215))


def _make_verifier() -> StepVerifier:
    return StepVerifier(
        locator=None,
        platform_adapter=None,
        coordinate_tolerance_px=COORDINATE_TOLERANCE_PX,
    )


class TestStepVerifierVisualVerification:
    def test_element_with_bounds_passes(self):
        verifier = _make_verifier()
        element = _make_ui_element(bounds=Rect(x=100, y=200, width=80, height=30))
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn")

        result = verifier._verify_visual(located, target)
        assert result.visual_verification in (VerificationLevel.PASSED, VerificationLevel.WARNING)

    def test_element_without_bounds_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element(bounds=None)
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn")

        result = verifier._verify_visual(located, target)
        assert result.visual_verification == VerificationLevel.WARNING

    def test_non_interactive_role_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element(role="statictext", bounds=Rect(x=100, y=200, width=80, height=30))
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="label")

        result = verifier._verify_visual(located, target)
        assert result.visual_verification == VerificationLevel.WARNING

    def test_non_visible_element_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element(role="button", bounds=Rect(x=100, y=200, width=80, height=30))
        element.is_visible = False
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn")

        result = verifier._verify_visual(located, target)
        assert result.visual_verification in (VerificationLevel.PASSED, VerificationLevel.WARNING)


class TestStepVerifierCoordinateVerification:
    def test_close_coordinates_pass(self):
        verifier = _make_verifier()
        element = _make_ui_element()
        located = _make_located(element, position=Point(x=105, y=205))
        target = StepTarget(strategy=LocateStrategy.POSITION, position=Point(x=100, y=200))

        result = verifier._verify_coordinate(located, target)
        assert result.coordinate_verification == VerificationLevel.PASSED

    def test_moderate_offset_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element()
        located = _make_located(element, position=Point(x=150, y=250))
        target = StepTarget(strategy=LocateStrategy.POSITION, position=Point(x=100, y=200))

        result = verifier._verify_coordinate(located, target)
        assert result.coordinate_verification in (VerificationLevel.WARNING, VerificationLevel.FAILED)

    def test_large_offset_fails(self):
        verifier = _make_verifier()
        element = _make_ui_element()
        located = _make_located(element, position=Point(x=500, y=600))
        target = StepTarget(strategy=LocateStrategy.POSITION, position=Point(x=100, y=200))

        result = verifier._verify_coordinate(located, target)
        assert result.coordinate_verification == VerificationLevel.FAILED

    def test_no_expected_position_skips(self):
        verifier = _make_verifier()
        element = _make_ui_element()
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn")

        result = verifier._verify_coordinate(located, target)
        assert result.coordinate_verification == VerificationLevel.PASSED

    def test_coordinate_distance_calculated(self):
        verifier = _make_verifier()
        element = _make_ui_element()
        located = _make_located(element, position=Point(x=130, y=240))
        target = StepTarget(strategy=LocateStrategy.POSITION, position=Point(x=100, y=200))

        result = verifier._verify_coordinate(located, target)
        assert result.coordinate_distance_px > 0


class TestStepVerifierAttributeVerification:
    def test_matching_core_attribute_passes(self):
        verifier = _make_verifier()
        element = _make_ui_element(identifier="btn-submit")
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn-submit")

        result = verifier._verify_attributes(located, target)
        assert result.attribute_verification == VerificationLevel.PASSED

    def test_mismatching_core_attribute_fails(self):
        verifier = _make_verifier()
        element = _make_ui_element(identifier="btn-other")
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn-submit")

        result = verifier._verify_attributes(located, target)
        assert result.attribute_verification == VerificationLevel.FAILED

    def test_mismatching_auxiliary_attribute_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element(identifier="btn", title="Different Title")
        located = _make_located(element)
        target = StepTarget(strategy=LocateStrategy.ACCESSIBILITY_ID, accessibility_id="btn", title="Expected Title")

        result = verifier._verify_attributes(located, target)
        assert result.attribute_verification == VerificationLevel.WARNING

    def test_expected_attributes_checked(self):
        verifier = _make_verifier()
        element = _make_ui_element(identifier="btn", role="button", title="Submit")
        located = _make_located(element)
        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn",
            expected_attributes={"role": "button", "title": "Submit"},
        )

        result = verifier._verify_attributes(located, target)
        assert result.attribute_verification == VerificationLevel.PASSED

    def test_expected_attributes_mismatch_warns(self):
        verifier = _make_verifier()
        element = _make_ui_element(role="button", title="Cancel")
        located = _make_located(element)
        target = StepTarget(
            strategy=LocateStrategy.ACCESSIBILITY_ID,
            accessibility_id="btn",
            expected_attributes={"role": "button", "title": "Submit"},
        )

        result = verifier._verify_attributes(located, target)
        assert result.attribute_verification in (VerificationLevel.WARNING, VerificationLevel.FAILED)


class TestStepVerifierOverallLevel:
    def _make_state(self, all_passed: bool = True, score: float = 1.0):
        from src.executor.step_verifier import ActionabilityReport
        return ActionabilityReport(
            checks={"visible": True, "enabled": True, "interactive": True, "in_viewport": True, "stable": True, "not_obscured": True} if all_passed else {"visible": False},
            details="All passed" if all_passed else "Some failed",
            all_passed=all_passed,
            score=score,
        )

    def test_all_passed_returns_passed(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.PASSED,
            VerificationLevel.PASSED,
            VerificationLevel.PASSED,
            self._make_state(True),
            strict=False,
        )
        assert level == VerificationLevel.PASSED

    def test_any_failed_returns_failed(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.PASSED,
            VerificationLevel.FAILED,
            VerificationLevel.PASSED,
            self._make_state(True),
            strict=False,
        )
        assert level == VerificationLevel.FAILED

    def test_warning_in_non_strict_returns_warning(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.PASSED,
            VerificationLevel.WARNING,
            VerificationLevel.PASSED,
            self._make_state(True),
            strict=False,
        )
        assert level == VerificationLevel.WARNING

    def test_warning_in_strict_returns_warning(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.PASSED,
            VerificationLevel.WARNING,
            VerificationLevel.PASSED,
            self._make_state(True),
            strict=True,
        )
        assert level == VerificationLevel.WARNING

    def test_failed_and_warning_returns_failed(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.FAILED,
            VerificationLevel.WARNING,
            VerificationLevel.PASSED,
            self._make_state(True),
            strict=False,
        )
        assert level == VerificationLevel.FAILED

    def test_low_state_score_returns_failed(self):
        level = StepVerifier._compute_overall_level_v2(
            VerificationLevel.PASSED,
            VerificationLevel.PASSED,
            VerificationLevel.PASSED,
            self._make_state(False, 0.3),
            strict=False,
        )
        assert level == VerificationLevel.FAILED


class TestStepVerifierConstants:
    def test_core_attributes_defined(self):
        assert "identifier" in CORE_ATTRIBUTES
        assert "selector" in CORE_ATTRIBUTES
        assert "xpath" in CORE_ATTRIBUTES

    def test_fuzzy_attributes_defined(self):
        assert "title" in FUZZY_ATTRIBUTES
        assert "role" in FUZZY_ATTRIBUTES
        assert "class_name" in FUZZY_ATTRIBUTES

    def test_coordinate_tolerance_positive(self):
        assert COORDINATE_TOLERANCE_PX > 0
