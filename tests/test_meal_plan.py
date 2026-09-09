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

@pytest.mark.asyncio
async def test_root_template_group_uses_official_deterministic_identifier(fake_transport) -> None:
    from uuid import UUID
    from anylist_sdk.identifiers import uuid5_hex

    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    state.meal_plan_calendar_id = "calendar"
    group = await service.create_root_template_group(flush=False)
    assert group.identifier == uuid5_hex(
        "calendar", UUID(hex="3da9450f605a455ca3aadaf230998b4d")
    )


@pytest.mark.asyncio
async def test_delete_template_group_recursively_removes_descendants_and_template_events(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    state.meal_plan_calendar_id = "calendar"
    root = PB.PBMealPlanTemplateGroup(identifier="root", calendarId="calendar")
    child = PB.PBMealPlanTemplateGroup(identifier="child", calendarId="calendar")
    root.items.add(identifier="child", itemType=PB.PBMealPlanTemplateGroupItem.Type.Group)
    child.items.add(identifier="template", itemType=PB.PBMealPlanTemplateGroupItem.Type.Template)
    state.meal_plan_template_groups.update(root=root, child=child)
    state.meal_plan_templates["template"] = PB.PBMealPlanTemplate(identifier="template", calendarId="calendar")
    state.meal_plan_template_events["event"] = PB.PBCalendarEvent(
        identifier="event", calendarId="calendar", templateId="template",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
    )

    await service.delete_template_group("child", "root", flush=False)

    assert "child" not in state.meal_plan_template_groups
    assert "template" not in state.meal_plan_templates
    assert "event" not in state.meal_plan_template_events
    assert list(root.items) == []
    assert [op.metadata.handlerId for op in service.queue._pending] == [
        "delete-template", "delete-template-group"
    ]


@pytest.mark.asyncio
async def test_move_template_group_rejects_cycle_without_mutating_or_queueing(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    root = PB.PBMealPlanTemplateGroup(identifier="root")
    parent = PB.PBMealPlanTemplateGroup(identifier="parent")
    child = PB.PBMealPlanTemplateGroup(identifier="child")
    root.items.add(identifier="parent", itemType=PB.PBMealPlanTemplateGroupItem.Type.Group)
    parent.items.add(identifier="child", itemType=PB.PBMealPlanTemplateGroupItem.Type.Group)
    state.meal_plan_template_groups.update(root=root, parent=parent, child=child)
    moved = PB.PBMealPlanTemplateGroupItem(
        identifier="parent", itemType=PB.PBMealPlanTemplateGroupItem.Type.Group
    )

    assert await service.move_template_group_items([moved], "root", "child", flush=False) is False
    assert [x.identifier for x in root.items] == ["parent"]
    assert service.queue._pending == []

@pytest.mark.asyncio
async def test_template_event_save_and_delete_use_template_event_store(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    event = PB.PBCalendarEvent(
        identifier="template-event",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
        templateDayId="day",
    )

    saved = await service.save_event(event, flush=False)
    assert saved.identifier in state.meal_plan_template_events
    assert saved.identifier not in state.meal_plan_events
    await service.delete_event(saved.identifier, flush=False)
    assert saved.identifier not in state.meal_plan_template_events
    assert service.queue._pending[-1].metadata.handlerId == "delete-event"
    assert service.queue._pending[-1].eventType == PB.PBCalendarEventType.MealPlanTemplateEvent


@pytest.mark.asyncio
async def test_remove_template_days_discovers_affected_events(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    state.meal_plan_templates["t"] = PB.PBMealPlanTemplate(
        identifier="t", calendarId="cal", dayIds=["d1", "d2"]
    )
    state.meal_plan_template_events["e1"] = PB.PBCalendarEvent(
        identifier="e1", eventType=PB.PBCalendarEventType.MealPlanTemplateEvent, templateDayId="d1"
    )
    state.meal_plan_template_events["e2"] = PB.PBCalendarEvent(
        identifier="e2", eventType=PB.PBCalendarEventType.MealPlanTemplateEvent, templateDayId="d2"
    )

    await service.remove_template_day_ids("t", ["d1"], flush=False)

    assert list(state.meal_plan_templates["t"].dayIds) == ["d2"]
    assert "e1" not in state.meal_plan_template_events and "e2" in state.meal_plan_template_events
    assert list(service.queue._pending[-1].eventIds) == ["e1"]


@pytest.mark.asyncio
async def test_event_label_sort_index_equal_value_is_noop(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(identifier="e", eventType=0, labelSortIndex=4)
    state.meal_plan_events["e"] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    result = await service.set_event_label_sort_index("e", 4, flush=False)
    assert result is event
    assert service.queue._pending == []
