from __future__ import annotations

import pytest

from anylist_sdk.proto import PB
from anylist_sdk.services.meal_plan import MealPlanService
from anylist_sdk.state import AnyListState


@pytest.mark.asyncio
async def test_delete_label_sends_affected_event_ids_and_clears_labels(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    label = PB.PBCalendarLabel(identifier="label", calendarId="cal", name="Dinner")
    event = PB.PBCalendarEvent(
        identifier="event", calendarId="cal", labelId="label", eventType=0
    )
    template_event = PB.PBCalendarEvent(
        identifier="template-event",
        calendarId="cal",
        labelId="label",
        templateId="template",
        eventType=2,
    )
    untouched = PB.PBCalendarEvent(
        identifier="other", calendarId="cal", labelId="other-label", eventType=0
    )
    state.meal_plan_labels[label.identifier] = label
    state.meal_plan_events[event.identifier] = event
    state.meal_plan_events[untouched.identifier] = untouched
    state.meal_plan_template_events[template_event.identifier] = template_event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.delete_label("label")

    assert "label" not in state.meal_plan_labels
    assert not state.meal_plan_events["event"].HasField("labelId")
    assert not state.meal_plan_template_events["template-event"].HasField("labelId")
    assert state.meal_plan_events["other"].labelId == "other-label"

    endpoint, fields, response_type = fake_transport.calls[-1]
    assert endpoint == "/data/meal-planning-calendar/update"
    assert response_type == "PBEditOperationResponse"
    operation = fields["operations"].operations[0]
    assert operation.metadata.handlerId == "delete-label"
    assert operation.calendarId == "cal"
    assert operation.updatedLabel.identifier == "label"
    assert list(operation.eventIds) == ["event", "template-event"]


@pytest.mark.asyncio
async def test_delete_template_sends_parent_and_template_event_ids(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    template = PB.PBMealPlanTemplate(identifier="template", calendarId="cal", name="Week")
    group = PB.PBMealPlanTemplateGroup(identifier="group", calendarId="cal")
    group.items.add(identifier="template", itemType=0)
    group.items.add(identifier="other-template", itemType=0)
    template_event = PB.PBCalendarEvent(
        identifier="template-event",
        calendarId="cal",
        templateId="template",
        eventType=2,
    )
    other_event = PB.PBCalendarEvent(
        identifier="other-event",
        calendarId="cal",
        templateId="other-template",
        eventType=2,
    )
    state.meal_plan_templates[template.identifier] = template
    state.meal_plan_template_groups[group.identifier] = group
    state.meal_plan_template_events[template_event.identifier] = template_event
    state.meal_plan_template_events[other_event.identifier] = other_event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.delete_template("template")

    assert "template" not in state.meal_plan_templates
    assert [item.identifier for item in state.meal_plan_template_groups["group"].items] == [
        "other-template"
    ]
    assert "template-event" not in state.meal_plan_template_events
    assert "other-event" in state.meal_plan_template_events

    operation = fake_transport.calls[-1][1]["operations"].operations[0]
    assert operation.metadata.handlerId == "delete-template"
    assert operation.updatedTemplate.identifier == "template"
    assert operation.originalParentTemplateGroupId == "group"
    assert list(operation.eventIds) == ["template-event"]


@pytest.mark.asyncio
async def test_delete_template_requires_parent_group(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    state.meal_plan_templates["template"] = PB.PBMealPlanTemplate(
        identifier="template", calendarId="cal"
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    with pytest.raises(RuntimeError):
        await service.delete_template("template")
