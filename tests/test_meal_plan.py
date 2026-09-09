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


@pytest.mark.asyncio
async def test_new_label_gets_next_sort_index_and_reorder_updates_local_indices(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    state.meal_plan_labels["a"] = PB.PBCalendarLabel(identifier="a", calendarId="cal", sortIndex=2)
    state.meal_plan_labels["b"] = PB.PBCalendarLabel(identifier="b", calendarId="cal", sortIndex=7)
    service = MealPlanService(fake_transport, state, user_id="user")

    created = await service.save_label(PB.PBCalendarLabel(name="Dinner"), flush=False)
    assert created.sortIndex == 8
    assert service.queue._pending[-1].metadata.handlerId == "new-label"
    assert service.queue._pending[-1].updatedLabel.sortIndex == 8

    await service.reorder_labels([created.identifier, "a", "b"], flush=False)
    assert created.sortIndex == 0
    assert state.meal_plan_labels["a"].sortIndex == 1
    assert state.meal_plan_labels["b"].sortIndex == 2
    assert list(service.queue._pending[-1].sortedLabelIds) == [created.identifier, "a", "b"]


@pytest.mark.asyncio
async def test_reorder_event_list_items_matches_official_append_unmentioned_behavior(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(identifier="event", calendarId="cal", eventType=0)
    for item_id in ["a", "b", "c"]:
        event.eventListItems.add(identifier=item_id, name=item_id)
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.reorder_event_list_items("event", ["c", "missing", "a"], flush=False)

    assert [x.identifier for x in event.eventListItems] == ["c", "a", "b"]
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "set-ordered-event-list-item-ids"
    assert list(op.orderedEventListItemIds) == ["c", "missing", "a"]
    assert [x.identifier for x in op.updatedEvent.eventListItems] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_new_template_requires_parent_and_updates_parent_membership_and_sort_index(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    parent = PB.PBMealPlanTemplateGroup(identifier="group", calendarId="cal")
    state.meal_plan_template_groups[parent.identifier] = parent
    state.meal_plan_templates["old"] = PB.PBMealPlanTemplate(
        identifier="old", calendarId="cal", sortIndex=4
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    with pytest.raises(ValueError):
        await service.save_template(PB.PBMealPlanTemplate(name="Week"), flush=False)

    created = await service.save_template(
        PB.PBMealPlanTemplate(name="Week"), parent_group_id="group", flush=False
    )
    assert created.sortIndex == 5
    assert any(
        x.identifier == created.identifier
        and x.itemType == PB.PBMealPlanTemplateGroupItem.Type.Template
        for x in parent.items
    )
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "new-template"
    assert op.updatedParentTemplateGroupId == "group"
    assert op.updatedTemplate.sortIndex == 5


@pytest.mark.asyncio
async def test_failed_template_delete_does_not_remove_local_template(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    state.meal_plan_templates["template"] = PB.PBMealPlanTemplate(
        identifier="template", calendarId="cal"
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    with pytest.raises(RuntimeError):
        await service.delete_template("template")

    assert "template" in state.meal_plan_templates


@pytest.mark.asyncio
async def test_event_label_change_clears_normal_event_label_sort_and_recomputes_order(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    first = PB.PBCalendarEvent(
        identifier="a", calendarId="cal", eventType=0, date="2026-09-08", orderAddedSortIndex=3
    )
    event = PB.PBCalendarEvent(
        identifier="b",
        calendarId="cal",
        eventType=0,
        date="2026-09-08",
        labelId="old",
        labelSortIndex=9,
        orderAddedSortIndex=1,
    )
    state.meal_plan_events[first.identifier] = first
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.set_event_label("b", "new", flush=False)

    assert event.labelId == "new"
    assert not event.HasField("labelSortIndex")
    assert event.orderAddedSortIndex == 4
    op = service.queue._pending[-1]
    assert op.metadata.handlerId == "set-event-label"
    assert op.updatedEvent.orderAddedSortIndex == 4
