from __future__ import annotations

import pytest

from aioanylist.proto import PB
from aioanylist.services.meal_plan import MealPlanService
from aioanylist.state import AnyListState


@pytest.mark.asyncio
async def test_meal_plan_refresh_returns_before_http_while_edit_queue_pending(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="calendar")
    service = MealPlanService(fake_transport, state, user_id="user")
    await service.queue.enqueue(service.queue.new_operation("new-event"), flush=False)

    result = await service.refresh()

    assert result is None
    assert fake_transport.calls == []


@pytest.mark.asyncio
async def test_delete_label_sends_affected_event_ids_and_clears_labels(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    label = PB.PBCalendarLabel(identifier="label", calendarId="cal", name="Dinner")
    event = PB.PBCalendarEvent(identifier="event", calendarId="cal", labelId="label", eventType=0)
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
async def test_new_label_gets_next_sort_index_and_reorder_updates_local_indices(
    fake_transport,
) -> None:
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
async def test_reorder_event_list_items_matches_official_append_unmentioned_behavior(
    fake_transport,
) -> None:
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
async def test_new_template_requires_parent_and_updates_parent_membership_and_sort_index(
    fake_transport,
) -> None:
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
async def test_event_label_change_clears_normal_event_label_sort_and_recomputes_order(
    fake_transport,
) -> None:
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

    from aioanylist.identifiers import uuid5_hex

    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    state.meal_plan_calendar_id = "calendar"
    group = await service.create_root_template_group(flush=False)
    assert group.identifier == uuid5_hex("calendar", UUID(hex="3da9450f605a455ca3aadaf230998b4d"))


@pytest.mark.asyncio
async def test_delete_template_group_recursively_removes_descendants_and_template_events(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")
    state.meal_plan_calendar_id = "calendar"
    root = PB.PBMealPlanTemplateGroup(identifier="root", calendarId="calendar")
    child = PB.PBMealPlanTemplateGroup(identifier="child", calendarId="calendar")
    root.items.add(identifier="child", itemType=PB.PBMealPlanTemplateGroupItem.Type.Group)
    child.items.add(identifier="template", itemType=PB.PBMealPlanTemplateGroupItem.Type.Template)
    state.meal_plan_template_groups.update(root=root, child=child)
    state.meal_plan_templates["template"] = PB.PBMealPlanTemplate(
        identifier="template", calendarId="calendar"
    )
    state.meal_plan_template_events["event"] = PB.PBCalendarEvent(
        identifier="event",
        calendarId="calendar",
        templateId="template",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
    )

    await service.delete_template_group("child", "root", flush=False)

    assert "child" not in state.meal_plan_template_groups
    assert "template" not in state.meal_plan_templates
    assert "event" not in state.meal_plan_template_events
    assert list(root.items) == []
    assert [op.metadata.handlerId for op in service.queue._pending] == [
        "delete-template",
        "delete-template-group",
    ]


@pytest.mark.asyncio
async def test_move_template_group_rejects_cycle_without_mutating_or_queueing(
    fake_transport,
) -> None:
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
async def test_template_event_list_item_mutations_use_template_event_store(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(
        identifier="template-event",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
        templateDayId="day",
    )
    state.meal_plan_template_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    created = await service.add_event_list_item(
        event.identifier,
        PB.PBCalendarEventListItem(identifier="item", name="Milk"),
        flush=False,
    )
    assert created.identifier == "item"
    add = service.queue._pending[-1]
    assert add.metadata.handlerId == "add-event-list-item"
    assert add.eventType == PB.PBCalendarEventType.MealPlanTemplateEvent

    await service.set_event_list_item_name(event.identifier, "item", "Oat milk", flush=False)
    updated = state.meal_plan_template_events[event.identifier].eventListItems[0]
    assert updated.name == "Oat milk"
    rename = service.queue._pending[-1]
    assert rename.metadata.handlerId == "set-event-list-item-name"
    assert rename.eventType == PB.PBCalendarEventType.MealPlanTemplateEvent

    await service.remove_event_list_item(event.identifier, "item", flush=False)
    assert list(state.meal_plan_template_events[event.identifier].eventListItems) == []
    remove = service.queue._pending[-1]
    assert remove.metadata.handlerId == "remove-event-list-item"
    assert remove.eventType == PB.PBCalendarEventType.MealPlanTemplateEvent


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "field_name", "handler"),
    [
        ("set_event_title", "title", "set-event-title"),
        ("set_event_details", "details", "set-event-details"),
    ],
)
async def test_empty_event_text_clears_optional_field_like_official_wrapper(
    fake_transport, method_name: str, field_name: str, handler: str
) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(identifier="e", calendarId="cal", eventType=0)
    setattr(event, field_name, "old")
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await getattr(service, method_name)(event.identifier, "", flush=False)

    assert not state.meal_plan_events[event.identifier].HasField(field_name)
    operation = service.queue._pending[-1]
    assert operation.metadata.handlerId == handler
    assert not operation.updatedEvent.HasField(field_name)


@pytest.mark.asyncio
async def test_set_event_date_moves_queue_event_to_calendar_and_clears_label_sort(
    fake_transport,
) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(
        identifier="e",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanQueueEvent,
        labelSortIndex=7,
    )
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.set_event_date([event.identifier], "2026-09-10", flush=False)

    live = state.meal_plan_events[event.identifier]
    assert live.date == "2026-09-10"
    assert live.eventType == PB.PBCalendarEventType.MealPlanCalendarEvent
    assert not live.HasField("labelSortIndex")
    operation = service.queue._pending[-1]
    assert operation.metadata.handlerId == "set-date-for-events"
    assert operation.updatedEvents[0].eventType == PB.PBCalendarEventType.MealPlanCalendarEvent
    assert not operation.updatedEvents[0].HasField("labelSortIndex")


@pytest.mark.asyncio
async def test_clear_event_date_moves_calendar_event_to_queue(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(
        identifier="e",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanCalendarEvent,
        date="2026-09-10",
        labelSortIndex=7,
    )
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.set_event_date([event.identifier], None, flush=False)

    live = state.meal_plan_events[event.identifier]
    assert not live.HasField("date")
    assert live.eventType == PB.PBCalendarEventType.MealPlanQueueEvent
    assert not live.HasField("labelSortIndex")
    operation = service.queue._pending[-1]
    assert operation.metadata.handlerId == "set-date-for-events"
    assert operation.updatedEvents[0].eventType == PB.PBCalendarEventType.MealPlanQueueEvent


@pytest.mark.asyncio
async def test_event_and_label_operation_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    state.meal_plan_events["event"] = PB.PBCalendarEvent(
        identifier="event", calendarId="cal", eventType=PB.PBCalendarEventType.MealPlanCalendarEvent
    )
    state.meal_plan_labels["label"] = PB.PBCalendarLabel(
        identifier="label", calendarId="cal", name="Old", sortIndex=3
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.save_event(
        PB.PBCalendarEvent(
            identifier="event",
            calendarId="cal",
            eventType=PB.PBCalendarEventType.MealPlanCalendarEvent,
            title="Updated",
        ),
        flush=False,
    )
    update = service.queue._pending[-1]
    assert update.metadata.handlerId == "update-event"
    assert update.updatedEvent.title == "Updated"
    assert update.eventType == PB.PBCalendarEventType.MealPlanCalendarEvent

    await service.save_events(
        [
            PB.PBCalendarEvent(title="One", eventType=PB.PBCalendarEventType.MealPlanCalendarEvent),
            PB.PBCalendarEvent(title="Two", eventType=PB.PBCalendarEventType.MealPlanCalendarEvent),
        ],
        flush=False,
    )
    bulk = service.queue._pending[-1]
    assert bulk.metadata.handlerId == "save-new-events"
    assert bulk.metadata.operationVersion == 1
    assert len(bulk.updatedEvents) == 2
    assert all(event.identifier for event in bulk.updatedEvents)
    assert all(event.calendarId == "cal" for event in bulk.updatedEvents)

    await service.save_label(
        PB.PBCalendarLabel(identifier="label", calendarId="cal", name="New", sortIndex=3),
        is_new=False,
        flush=False,
    )
    label_update = service.queue._pending[-1]
    assert label_update.metadata.handlerId == "update-label"
    assert label_update.updatedLabel.name == "New"

    await service.reorder_labels(["label"], flush=False)
    reorder = service.queue._pending[-1]
    assert reorder.metadata.handlerId == "set-sorted-label-ids"
    assert list(reorder.sortedLabelIds) == ["label"]


@pytest.mark.asyncio
async def test_event_icon_label_sort_and_embedded_item_operation_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    event = PB.PBCalendarEvent(
        identifier="event",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanCalendarEvent,
    )
    event.eventListItems.add(identifier="item", name="Milk", details="old")
    state.meal_plan_events[event.identifier] = event
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.set_event_icon(event.identifier, "dinner", flush=False)
    icon = service.queue._pending[-1]
    assert icon.metadata.handlerId == "set-event-icon"
    assert icon.updatedEvent.icon.iconName == "dinner"

    await service.set_event_label_sort_index(event.identifier, 4, flush=False)
    label_sort = service.queue._pending[-1]
    assert label_sort.metadata.handlerId == "set-event-label-sort-index"
    assert label_sort.updatedEvent.labelSortIndex == 4

    await service.set_event_list_item_details(event.identifier, "item", "new", flush=False)
    details = service.queue._pending[-1]
    assert details.metadata.handlerId == "set-event-list-item-details"
    assert details.originalEventListItem.details == "old"
    assert details.updatedEventListItem.details == "new"
    assert details.eventType == PB.PBCalendarEventType.MealPlanCalendarEvent

    quantity = PB.PBItemQuantity(amount="2", unit="cup", rawQuantity="2 cups")
    await service.set_event_list_item_quantity(event.identifier, "item", quantity, flush=False)
    quantity_op = service.queue._pending[-1]
    assert quantity_op.metadata.handlerId == "set-event-list-item-quantity"
    assert quantity_op.updatedEventListItem.quantityPb == quantity

    package = PB.PBItemPackageSize(
        size="12", unit="oz", packageType="jar", rawPackageSize="12 oz jar"
    )
    await service.set_event_list_item_package_size(event.identifier, "item", package, flush=False)
    package_op = service.queue._pending[-1]
    assert package_op.metadata.handlerId == "set-event-list-item-package-size"
    assert package_op.updatedEventListItem.packageSizePb == package


@pytest.mark.asyncio
async def test_template_operation_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    template = PB.PBMealPlanTemplate(
        identifier="template", calendarId="cal", name="Old", dayIds=["d1", "d2"]
    )
    state.meal_plan_templates[template.identifier] = template
    state.meal_plan_template_events["event"] = PB.PBCalendarEvent(
        identifier="event",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
        templateDayId="d1",
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.set_template_name(template.identifier, "Week", flush=False)
    rename = service.queue._pending[-1]
    assert rename.metadata.handlerId == "set-template-name"
    assert rename.updatedTemplate.name == "Week"

    await service.set_template_icon(template.identifier, "calendar", flush=False)
    icon = service.queue._pending[-1]
    assert icon.metadata.handlerId == "set-template-icon"
    assert icon.updatedTemplate.icon.iconName == "calendar"

    await service.add_template_day_ids(template.identifier, ["d3"], flush=False)
    add_days = service.queue._pending[-1]
    assert add_days.metadata.handlerId == "add-template-day-ids"
    assert list(add_days.updatedTemplate.dayIds) == ["d3"]

    await service.remove_template_day_ids(template.identifier, ["d1"], flush=False)
    remove_days = service.queue._pending[-1]
    assert remove_days.metadata.handlerId == "remove-template-day-ids"
    assert list(remove_days.updatedTemplate.dayIds) == ["d1"]
    assert list(remove_days.eventIds) == ["event"]

    await service.set_template_day_ids(template.identifier, ["x", "y"], flush=False)
    set_days = service.queue._pending[-1]
    assert set_days.metadata.handlerId == "set-template-day-ids"
    assert list(set_days.updatedTemplate.dayIds) == ["x", "y"]

    moved_event = PB.PBCalendarEvent(
        identifier="move-event",
        calendarId="cal",
        eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
        templateDayId="x",
    )
    state.meal_plan_template_events[moved_event.identifier] = moved_event
    await service.set_template_day_id_for_events([moved_event.identifier], "y", flush=False)
    move_day = service.queue._pending[-1]
    assert move_day.metadata.handlerId == "set-template-day-id-for-events"
    assert move_day.eventType == PB.PBCalendarEventType.MealPlanTemplateEvent
    assert [event.templateDayId for event in move_day.updatedEvents] == ["y"]


@pytest.mark.asyncio
async def test_template_group_operation_contracts(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    service = MealPlanService(fake_transport, state, user_id="user")

    root = await service.create_root_template_group(group_id="root", flush=False)
    root_op = service.queue._pending[-1]
    assert root_op.metadata.handlerId == "create-root-template-group"
    assert root_op.templateGroup.identifier == "root"

    await service.create_template_group(
        "Child", root.identifier, icon="folder", group_id="child", flush=False
    )
    child_op = service.queue._pending[-1]
    assert child_op.metadata.handlerId == "new-template-group"
    assert child_op.updatedParentTemplateGroupId == "root"
    assert child_op.templateGroup.groupSettings.icon.iconName == "folder"

    template_item = PB.PBMealPlanTemplateGroupItem(
        identifier="template", itemType=PB.PBMealPlanTemplateGroupItem.Type.Template
    )
    await service.set_ordered_template_group_items(root.identifier, [template_item], flush=False)
    ordered = service.queue._pending[-1]
    assert ordered.metadata.handlerId == "set-ordered-template-group-items"
    assert ordered.originalParentTemplateGroupId == "root"
    assert [item.identifier for item in ordered.templateGroupItems] == ["template"]

    destination = PB.PBMealPlanTemplateGroup(identifier="destination", calendarId="cal")
    state.meal_plan_template_groups[destination.identifier] = destination
    moved = await service.move_template_group_items(
        [template_item], root.identifier, destination.identifier, flush=False
    )
    assert moved is True
    move = service.queue._pending[-1]
    assert move.metadata.handlerId == "move-template-group-items"
    assert move.originalParentTemplateGroupId == "root"
    assert move.updatedParentTemplateGroupId == "destination"

    await service.set_template_group_items_sort_order(destination.identifier, 2, flush=False)
    item_sort = service.queue._pending[-1]
    assert item_sort.metadata.handlerId == "set-template-group-items-sort-order"
    assert item_sort.templateGroup.identifier == "destination"
    assert item_sort.templateGroup.groupSettings.itemsSortOrder == 2

    await service.set_template_group_groups_sort_position(destination.identifier, 1, flush=False)
    group_sort = service.queue._pending[-1]
    assert group_sort.metadata.handlerId == "set-template-group-groups-sort-position"
    assert group_sort.templateGroup.groupSettings.groupsSortPosition == 1


@pytest.mark.asyncio
async def test_delete_events_for_recipe_operation_has_official_marker_shape(fake_transport) -> None:
    state = AnyListState(user_id="user", meal_plan_calendar_id="cal")
    state.meal_plan_events["event"] = PB.PBCalendarEvent(
        identifier="event", calendarId="cal", recipeId="recipe", eventType=0
    )
    state.meal_plan_template_events["template-event"] = PB.PBCalendarEvent(
        identifier="template-event", calendarId="cal", recipeId="recipe", eventType=2
    )
    service = MealPlanService(fake_transport, state, user_id="user")

    await service.delete_events_for_recipe_id("recipe", flush=False)

    operation = service.queue._pending[-1]
    assert operation.metadata.handlerId == "delete-events-for-recipe-id"
    # Intentional evidence-backed divergence: app.js maps the normal-event array twice
    # (84364-84373), despite fetching the template-event array in between.  The SDK sends
    # both real IDs so the recipe cleanup actually removes references from both stores.
    assert list(operation.eventIds) == ["event", "template-event"]
    assert operation.updatedEvent.calendarId == "cal"
    assert operation.updatedEvent.recipeId == "recipe"
    assert operation.updatedEvent.identifier
    assert "event" not in state.meal_plan_events
    assert "template-event" not in state.meal_plan_template_events
