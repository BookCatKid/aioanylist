from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from google.protobuf.message import Message

from uuid import UUID

from ..identifiers import uuid4_hex, uuid5_hex
from ..operations import QueueSpec
from ..proto import PB
from ..state import AnyListState, clone
from ..transport import AnyListTransport
from .base import OperationService, clone_message

_ROOT_TEMPLATE_GROUP_NAMESPACE = UUID(hex="3da9450f605a455ca3aadaf230998b4d")


class MealPlanService(OperationService):
    def __init__(self,transport:AnyListTransport,state:AnyListState,*,user_id:str,journal=None):
        super().__init__(transport,state,user_id=user_id,
            spec=QueueSpec(f"{user_id}:meal-plan","/data/meal-planning-calendar/update",
                           "PBCalendarOperation","PBCalendarOperationList"),journal=journal)
        self.queue.on_response=self._on_response
        self.on_event_updated: Callable[[Message, Message, bool], Awaitable[None] | None] | None = None
        self.on_event_removed: Callable[[Message, bool], Awaitable[None] | None] | None = None
    async def _notify_event_updated(self, new: Message, old: Message, flush: bool) -> None:
        if self.on_event_updated is None:
            return
        result = self.on_event_updated(new, old, flush)
        if result is not None:
            await result

    async def _notify_event_removed(self, event: Message, flush: bool) -> None:
        if self.on_event_removed is None:
            return
        result = self.on_event_removed(event, flush)
        if result is not None:
            await result

    async def _on_response(self,response:Message)->None:
        calendar_id=self.state.meal_plan_calendar_id
        if not calendar_id:return
        if calendar_id in {str(x) for x in response.fullRefreshTimestampIds}:
            self.state.meal_plan_logical_timestamp=0
            await self.refresh();return
        mismatch=False
        for original in response.originalLogicalTimestamps:
            if str(original.identifier)==calendar_id and int(original.logicalTimestamp)!=int(self.state.meal_plan_logical_timestamp):
                mismatch=True;break
        if mismatch:
            await self.refresh();return
        for current in response.currentLogicalTimestamps:
            if str(current.identifier)==calendar_id:
                if int(current.logicalTimestamp)==0:await self.refresh()
                else:self.state.meal_plan_logical_timestamp=int(current.logicalTimestamp)
                break
    async def operation(self,handler_id:str,*,flush:bool=True,operation_version:int|None=None,**fields):
        if self.state.meal_plan_calendar_id and "calendarId" not in fields:
            fields["calendarId"]=self.state.meal_plan_calendar_id
        return await super().operation(handler_id,flush=flush,operation_version=operation_version,**fields)
    def events(self):return list(self.state.meal_plan_events.values())
    def labels(self):return list(self.state.meal_plan_labels.values())
    def templates(self):return list(self.state.meal_plan_templates.values())
    def template_groups(self):return list(self.state.meal_plan_template_groups.values())

    async def refresh(self) -> Message | None:
        # CalendarManager.xp returns before issuing /get while calendar edits are queued.
        if self.queue.pending_count:
            return None
        fields: dict[str, Message | str] = {
            "client_info": self.state.user_data_client_info().mealPlanningCalendarClientInfo
        }
        if self.state.meal_plan_calendar_id:
            fields["calendar_timestamp"] = str(self.state.meal_plan_logical_timestamp)
            fields["calendar_id"] = self.state.meal_plan_calendar_id
        response = await self.transport.post_proto(
            "/data/meal-planning-calendar/get", fields=fields, response_type="PBCalendarResponse"
        )
        if response is None:
            return None
        assert isinstance(response, Message)
        self.state.apply_meal_plan(response)
        return response

    async def save_event(self,event:Message,*,event_type:int|None=None,is_new:bool|None=None,handler_id:str|None=None,flush:bool=True)->Message:
        e=clone_message(event)
        if not e.identifier:e.identifier=uuid4_hex()
        if event_type is not None:
            e.eventType=event_type
        if self.state.meal_plan_calendar_id:e.calendarId=self.state.meal_plan_calendar_id
        store = self._event_store(int(e.eventType))
        previous=store.get(e.identifier)
        old=clone_message(previous) if previous is not None else None
        existed=previous is not None
        self._refresh_event_sort_index(e, old)
        store[e.identifier]=clone(e)
        h=handler_id or ("new-event" if (is_new if is_new is not None else not existed) else "update-event")
        await self.operation(h,updatedEvent=e,eventType=int(e.eventType),flush=flush)
        if old is not None:
            await self._notify_event_updated(store[e.identifier],old,flush)
        return store[e.identifier]
    async def delete_event(self,event_id:str,*,flush:bool=True)->None:
        e=self.state.meal_plan_events.pop(event_id,None)
        if e is None:
            e=self.state.meal_plan_template_events.pop(event_id,None)
        if e is None:raise KeyError(event_id)
        fields: dict[str, Any] = {"updatedEvent": e}
        if int(e.eventType) == int(PB.PBCalendarEventType.MealPlanTemplateEvent):
            fields["eventType"] = int(e.eventType)
        await self.operation("delete-event",flush=flush,**fields)
        await self._notify_event_removed(e,flush)
    async def save_events(self,events:Sequence[Message],*,flush:bool=True)->None:
        vals=[]
        for e in events:
            x=clone_message(e)
            if not x.identifier:x.identifier=uuid4_hex()
            if self.state.meal_plan_calendar_id:x.calendarId=self.state.meal_plan_calendar_id
            self.state.meal_plan_events[x.identifier]=clone(x);vals.append(x)
        await self.operation("save-new-events",updatedEvents=vals,operation_version=1,flush=flush)
    async def set_event_date(self,event_ids:Sequence[str],date:str|None,*,flush:bool=True)->None:
        vals=[];changes=[]
        for eid in event_ids:
            e=self.state.meal_plan_events[eid];old=clone_message(e)
            if date is None:
                e.ClearField("date")
                e.ClearField("labelSortIndex")
                e.eventType=PB.PBCalendarEventType.MealPlanQueueEvent
            else:
                e.date=date
                e.ClearField("labelSortIndex")
                if int(e.eventType)==int(PB.PBCalendarEventType.MealPlanQueueEvent):
                    e.eventType=PB.PBCalendarEventType.MealPlanCalendarEvent
            self._refresh_event_sort_index(e, old);vals.append(clone_message(e));changes.append((e,old))
        await self.operation("set-date-for-events",updatedEvents=vals,flush=flush)
        for current,old in changes:
            await self._notify_event_updated(current,old,flush)
    async def add_event_list_item(self,event_id:str,item:Message,*,flush:bool=True)->Message:
        e=self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if e is None: raise KeyError(event_id)
        old_event=clone_message(e);x=clone_message(item)
        if not x.identifier:x.identifier=uuid4_hex()
        e.eventListItems.add().CopyFrom(x)
        await self.operation("add-event-list-item",updatedEventListItem=x,updatedEvent=clone_message(e),eventType=int(e.eventType),flush=flush)
        await self._notify_event_updated(e,old_event,flush)
        return e.eventListItems[-1]
    async def update_event_list_item(self,event_id:str,item_id:str,updated:Message,*,handler_id:str="set-event-list-item-name",flush:bool=True)->None:
        e=self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if e is None: raise KeyError(event_id)
        old_event=clone_message(e)
        original=None
        for i,x in enumerate(e.eventListItems):
            if x.identifier==item_id:
                original=clone_message(x);e.eventListItems[i].CopyFrom(updated);break
        if original is None:raise KeyError(item_id)
        await self.operation(handler_id,originalEventListItem=original,updatedEventListItem=updated,
                             updatedEvent=clone_message(e),eventType=int(e.eventType),flush=flush)
        await self._notify_event_updated(e,old_event,flush)
    async def remove_event_list_item(self,event_id:str,item_id:str,*,flush:bool=True)->None:
        e=self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if e is None: raise KeyError(event_id)
        old_event=clone_message(e);original=None
        for i,x in enumerate(e.eventListItems):
            if x.identifier==item_id:original=clone_message(x);del e.eventListItems[i];break
        if original is None:raise KeyError(item_id)
        await self.operation("remove-event-list-item",originalEventListItem=original,updatedEvent=clone_message(e),eventType=int(e.eventType),flush=flush)
        await self._notify_event_updated(e,old_event,flush)
    async def reorder_event_list_items(
        self, event_id: str, ids: Sequence[str], *, flush: bool = True
    ) -> None:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        by_id = {str(item.identifier): clone_message(item) for item in event.eventListItems}
        original_order = [str(item.identifier) for item in event.eventListItems]
        reordered: list[Message] = []
        used: set[str] = set()
        for item_id in ids:
            item = by_id.get(str(item_id))
            if item is not None and str(item_id) not in used:
                reordered.append(item)
                used.add(str(item_id))
        # The official event model appends any omitted items in their previous order.
        for item_id in original_order:
            if item_id not in used:
                reordered.append(by_id[item_id])
        del event.eventListItems[:]
        for item in reordered:
            event.eventListItems.add().CopyFrom(item)
        await self.operation(
            "set-ordered-event-list-item-ids",
            orderedEventListItemIds=list(ids),
            updatedEvent=clone_message(event),
            eventType=int(event.eventType),
            flush=flush,
        )

    async def save_label(
        self, label: Message, *, is_new: bool | None = None, flush: bool = True
    ) -> Message:
        x = clone_message(label)
        if not x.identifier:
            x.identifier = uuid4_hex()
        if self.state.meal_plan_calendar_id:
            x.calendarId = self.state.meal_plan_calendar_id
        existed = x.identifier in self.state.meal_plan_labels
        creating = is_new if is_new is not None else not existed
        if creating:
            # AnyList Web gives a newly created label max(existing sortIndex)+1.
            x.sortIndex = (
                max((int(v.sortIndex) for v in self.state.meal_plan_labels.values()), default=-1)
                + 1
            )
        self.state.meal_plan_labels[x.identifier] = clone(x)
        await self.operation(
            "new-label" if creating else "update-label", updatedLabel=x, flush=flush
        )
        return self.state.meal_plan_labels[x.identifier]
    async def delete_label(self, label_id: str, *, flush: bool = True) -> None:
        label = self.state.meal_plan_labels.pop(label_id, None)
        if label is None:
            raise KeyError(label_id)

        affected_ids: list[str] = []
        for store in (self.state.meal_plan_events, self.state.meal_plan_template_events):
            for event in store.values():
                if (getattr(event, "labelId", "") or "") != label_id:
                    continue
                affected_ids.append(str(event.identifier))
                event.ClearField("labelId")

        await self.operation(
            "delete-label",
            updatedLabel=clone_message(label),
            eventIds=affected_ids,
            flush=flush,
        )
    async def reorder_labels(
        self, ids: Sequence[str], *, flush: bool = True
    ) -> None:
        # jR rewrites each selected label's local sortIndex before queuing the IDs.
        for index, label_id in enumerate(ids):
            label = self.state.meal_plan_labels.get(label_id)
            if label is not None:
                label.sortIndex = index
        await self.operation(
            "set-sorted-label-ids", sortedLabelIds=list(ids), flush=flush
        )

    async def save_template(
        self,
        template: Message,
        *,
        parent_group_id: str | None = None,
        is_new: bool | None = None,
        flush: bool = True,
    ) -> Message:
        x = clone_message(template)
        if not x.identifier:
            x.identifier = uuid4_hex()
        if self.state.meal_plan_calendar_id:
            x.calendarId = self.state.meal_plan_calendar_id
        existed = x.identifier in self.state.meal_plan_templates
        creating = is_new if is_new is not None else not existed
        if creating:
            if parent_group_id is None:
                raise ValueError("parent_group_id is required when creating a meal-plan template")
            parent = self._template_group(parent_group_id)
            x.sortIndex = (
                max((int(v.sortIndex) for v in self.state.meal_plan_templates.values()), default=-1)
                + 1
            )
            if not any(item.identifier == x.identifier for item in parent.items):
                parent.items.add(
                    identifier=x.identifier,
                    itemType=PB.PBMealPlanTemplateGroupItem.Type.Template,
                )
        self.state.meal_plan_templates[x.identifier] = clone(x)
        fields: dict[str, Any] = {"updatedTemplate": x}
        if creating:
            fields["updatedParentTemplateGroupId"] = parent_group_id
        await self.operation(
            "new-template" if creating else "update-template", flush=flush, **fields
        )
        return self.state.meal_plan_templates[x.identifier]
    async def delete_template(self, template_id: str, *, flush: bool = True) -> None:
        template = self.state.meal_plan_templates.get(template_id)
        if template is None:
            raise KeyError(template_id)

        parent_group_id: str | None = None
        for group in self.state.meal_plan_template_groups.values():
            if any(item.identifier == template_id for item in group.items):
                parent_group_id = str(group.identifier)
                kept = [clone_message(item) for item in group.items if item.identifier != template_id]
                del group.items[:]
                for item in kept:
                    group.items.add().CopyFrom(item)
                break
        if parent_group_id is None:
            raise RuntimeError(f"Template {template_id!r} is not present in a template group")
        self.state.meal_plan_templates.pop(template_id, None)

        event_ids = [
            event_id
            for event_id, event in self.state.meal_plan_template_events.items()
            if (getattr(event, "templateId", "") or "") == template_id
        ]
        for event_id in event_ids:
            self.state.meal_plan_template_events.pop(event_id, None)

        await self.operation(
            "delete-template",
            updatedTemplate=clone_message(template),
            originalParentTemplateGroupId=parent_group_id,
            eventIds=event_ids,
            flush=flush,
        )

    async def _set_event_field(
        self, event_id: str, field: str, value, handler_id: str, *, flush: bool = True
    ) -> Message:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        old_event = clone_message(event)
        descriptor = event.DESCRIPTOR.fields_by_name[field]
        if descriptor.message_type:
            getattr(event, field).CopyFrom(value)
        else:
            setattr(event, field, value)
        await self.operation(
            handler_id,
            updatedEvent=clone_message(event),
            eventType=int(event.eventType),
            flush=flush,
        )
        await self._notify_event_updated(event, old_event, flush)
        return event

    async def set_event_title(self, event_id: str, title: str, *, flush: bool = True) -> Message:
        return await self._set_event_optional_text(
            event_id, "title", title, "set-event-title", flush=flush
        )

    async def set_event_details(self, event_id: str, details: str, *, flush: bool = True) -> Message:
        return await self._set_event_optional_text(
            event_id, "details", details, "set-event-details", flush=flush
        )

    async def _set_event_optional_text(
        self,
        event_id: str,
        field: str,
        value: str,
        handler_id: str,
        *,
        flush: bool = True,
    ) -> Message:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        old_event = clone_message(event)
        if value:
            setattr(event, field, value)
        else:
            event.ClearField(field)
        await self.operation(
            handler_id,
            updatedEvent=clone_message(event),
            eventType=int(event.eventType),
            flush=flush,
        )
        await self._notify_event_updated(event, old_event, flush)
        return event

    async def set_event_icon(
        self, event_id: str, icon: str | Message, *, flush: bool = True
    ) -> Message:
        value = icon if isinstance(icon, Message) else PB.PBIcon(iconName=icon)
        return await self._set_event_field(event_id, "icon", value, "set-event-icon", flush=flush)

    async def set_event_label(
        self, event_id: str, label_id: str, *, flush: bool = True
    ) -> Message:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        current = str(event.labelId or "")
        if current == label_id:
            return event
        old_event = clone_message(event)
        if label_id:
            event.labelId = label_id
        else:
            event.ClearField("labelId")
        # Event.ks() resets labelSortIndex for normal/template events. Queue and favorite
        # events keep their independent label ordering.
        if int(event.eventType) not in (
            int(PB.PBCalendarEventType.MealPlanQueueEvent),
            int(PB.PBCalendarEventType.MealPlanFavoriteEvent),
        ):
            event.ClearField("labelSortIndex")
        self._refresh_event_sort_index(event, old_event)
        await self.operation(
            "set-event-label",
            updatedEvent=clone_message(event),
            eventType=int(event.eventType),
            flush=flush,
        )
        await self._notify_event_updated(event, old_event, flush)
        return event

    async def set_event_label_sort_index(
        self, event_id: str, sort_index: int, *, flush: bool = True
    ) -> Message:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        if int(getattr(event, "labelSortIndex", 0)) == int(sort_index):
            return event
        return await self._set_event_field(
            event_id, "labelSortIndex", sort_index, "set-event-label-sort-index", flush=flush
        )

    async def set_event_list_item_name(
        self, event_id: str, item_id: str, name: str, *, flush: bool = True
    ) -> None:
        event, index = self._event_list_item(event_id, item_id)
        updated = clone_message(event.eventListItems[index])
        updated.name = name
        await self.update_event_list_item(
            event_id, item_id, updated, handler_id="set-event-list-item-name", flush=flush
        )

    async def set_event_list_item_details(
        self, event_id: str, item_id: str, details: str, *, flush: bool = True
    ) -> None:
        event, index = self._event_list_item(event_id, item_id)
        updated = clone_message(event.eventListItems[index])
        updated.details = details
        await self.update_event_list_item(
            event_id, item_id, updated, handler_id="set-event-list-item-details", flush=flush
        )

    async def set_event_list_item_quantity(
        self, event_id: str, item_id: str, quantity: Message, *, flush: bool = True
    ) -> None:
        event, index = self._event_list_item(event_id, item_id)
        updated = clone_message(event.eventListItems[index])
        updated.quantityPb.CopyFrom(quantity)
        await self.update_event_list_item(
            event_id, item_id, updated, handler_id="set-event-list-item-quantity", flush=flush
        )

    async def set_event_list_item_package_size(
        self, event_id: str, item_id: str, package_size: Message, *, flush: bool = True
    ) -> None:
        event, index = self._event_list_item(event_id, item_id)
        updated = clone_message(event.eventListItems[index])
        updated.packageSizePb.CopyFrom(package_size)
        await self.update_event_list_item(
            event_id,
            item_id,
            updated,
            handler_id="set-event-list-item-package-size",
            flush=flush,
        )

    def _event_list_item(self, event_id: str, item_id: str) -> tuple[Message, int]:
        event = self.state.meal_plan_events.get(event_id) or self.state.meal_plan_template_events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        for index, item in enumerate(event.eventListItems):
            if item.identifier == item_id:
                return event, index
        raise KeyError(item_id)

    async def delete_events_for_recipe_id(self, recipe_id: str, *, flush: bool = True) -> None:
        normal_ids = [
            event_id
            for event_id, event in self.state.meal_plan_events.items()
            if event.recipeId == recipe_id
        ]
        template_ids = [
            event_id
            for event_id, event in self.state.meal_plan_template_events.items()
            if event.recipeId == recipe_id
        ]
        for event_id in normal_ids:
            self.state.meal_plan_events.pop(event_id, None)
        for event_id in template_ids:
            self.state.meal_plan_template_events.pop(event_id, None)
        marker = PB.PBCalendarEvent(
            identifier=uuid4_hex(),
            calendarId=self.state.meal_plan_calendar_id or "",
            recipeId=recipe_id,
        )
        await self.operation(
            "delete-events-for-recipe-id",
            eventIds=normal_ids + template_ids,
            updatedEvent=marker,
            flush=flush,
        )

    async def set_template_name(
        self, template_id: str, name: str, *, flush: bool = True
    ) -> Message:
        template = self._template(template_id)
        template.name = name
        await self.operation("set-template-name", updatedTemplate=clone_message(template), flush=flush)
        return template

    async def set_template_icon(
        self, template_id: str, icon: str | Message, *, flush: bool = True
    ) -> Message:
        template = self._template(template_id)
        value = icon if isinstance(icon, Message) else PB.PBIcon(iconName=icon)
        template.icon.CopyFrom(value)
        await self.operation("set-template-icon", updatedTemplate=clone_message(template), flush=flush)
        return template

    async def add_template_day_ids(
        self, template_id: str, day_ids: Sequence[str], *, flush: bool = True
    ) -> Message:
        template = self._template(template_id)
        for day_id in day_ids:
            if day_id not in template.dayIds:
                template.dayIds.append(day_id)
        # Official operation carries only the added day IDs in updatedTemplate.dayIds.
        partial = clone_message(template)
        del partial.dayIds[:]
        partial.dayIds.extend(day_ids)
        await self.operation("add-template-day-ids", updatedTemplate=partial, flush=flush)
        return template

    async def remove_template_day_ids(
        self,
        template_id: str,
        day_ids: Sequence[str],
        *,
        event_ids: Sequence[str] = (),
        flush: bool = True,
    ) -> Message:
        template = self._template(template_id)
        remove = set(day_ids)
        kept = [x for x in template.dayIds if x not in remove]
        del template.dayIds[:]
        template.dayIds.extend(kept)
        affected = list(dict.fromkeys([
            *event_ids,
            *(
                event_id
                for event_id, event in self.state.meal_plan_template_events.items()
                if str(getattr(event, "templateDayId", "") or "") in remove
            ),
        ]))
        for event_id in affected:
            self.state.meal_plan_template_events.pop(event_id, None)
        partial = clone_message(template)
        del partial.dayIds[:]
        partial.dayIds.extend(day_ids)
        await self.operation(
            "remove-template-day-ids",
            updatedTemplate=partial,
            eventIds=affected,
            flush=flush,
        )
        return template

    async def set_template_day_ids(
        self, template_id: str, day_ids: Sequence[str], *, flush: bool = True
    ) -> Message:
        template = self._template(template_id)
        del template.dayIds[:]
        template.dayIds.extend(day_ids)
        await self.operation("set-template-day-ids", updatedTemplate=clone_message(template), flush=flush)
        return template

    async def set_template_day_id_for_events(
        self, event_ids: Sequence[str], day_id: str, *, flush: bool = True
    ) -> None:
        updated: list[Message] = []
        for event_id in event_ids:
            event = self.state.meal_plan_template_events.get(event_id)
            if event is None or event.templateDayId == day_id:
                continue
            event.templateDayId = day_id
            updated.append(clone_message(event))
        if not updated:
            return
        await self.operation(
            "set-template-day-id-for-events",
            eventType=PB.PBCalendarEventType.MealPlanTemplateEvent,
            updatedEvents=updated,
            flush=flush,
        )

    async def create_root_template_group(
        self, *, group_id: str | None = None, flush: bool = True
    ) -> Message:
        calendar_id = self.state.meal_plan_calendar_id or ""
        identifier = group_id or uuid5_hex(calendar_id, _ROOT_TEMPLATE_GROUP_NAMESPACE)
        group = PB.PBMealPlanTemplateGroup(
            identifier=identifier,
            calendarId=calendar_id,
        )
        self.state.meal_plan_template_groups[group.identifier] = clone(group)
        await self.operation("create-root-template-group", templateGroup=group, flush=flush)
        return self.state.meal_plan_template_groups[group.identifier]

    async def create_template_group(
        self,
        name: str,
        parent_group_id: str,
        *,
        icon: str | Message | None = None,
        group_id: str | None = None,
        flush: bool = True,
    ) -> Message:
        group = PB.PBMealPlanTemplateGroup(
            identifier=group_id or uuid4_hex(),
            calendarId=self.state.meal_plan_calendar_id or "",
            name=name,
        )
        if icon is not None:
            value = icon if isinstance(icon, Message) else PB.PBIcon(iconName=icon)
            group.groupSettings.icon.CopyFrom(value)
        self.state.meal_plan_template_groups[group.identifier] = clone(group)
        parent = self.state.meal_plan_template_groups.get(parent_group_id)
        if parent is not None:
            parent.items.add(
                identifier=group.identifier, itemType=PB.PBMealPlanTemplateGroupItem.Type.Group
            )
        await self.operation(
            "new-template-group",
            templateGroup=group,
            updatedParentTemplateGroupId=parent_group_id,
            flush=flush,
        )
        return self.state.meal_plan_template_groups[group.identifier]

    async def delete_template_group(
        self, group_id: str, parent_group_id: str, *, flush: bool = True
    ) -> None:
        group = self._template_group(group_id)
        # AnyList Web recursively deletes direct templates first, then child groups,
        # before removing the group from its parent. Keep all operations queued until
        # the outermost call finishes so the mutation is observed as one batch.
        template_ids = [
            str(item.identifier)
            for item in group.items
            if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Template)
        ]
        child_group_ids = [
            str(item.identifier)
            for item in group.items
            if int(item.itemType) == int(PB.PBMealPlanTemplateGroupItem.Type.Group)
        ]
        for template_id in template_ids:
            if template_id in self.state.meal_plan_templates:
                await self.delete_template(template_id, flush=False)
        for child_group_id in child_group_ids:
            if child_group_id in self.state.meal_plan_template_groups:
                await self.delete_template_group(child_group_id, group_id, flush=False)

        self.state.meal_plan_template_groups.pop(group_id, None)
        parent = self.state.meal_plan_template_groups.get(parent_group_id)
        if parent is not None:
            kept = [clone_message(x) for x in parent.items if x.identifier != group_id]
            del parent.items[:]
            for item in kept:
                parent.items.add().CopyFrom(item)
        partial = PB.PBMealPlanTemplateGroup(identifier=group_id)
        await self.operation(
            "delete-template-group",
            templateGroup=partial,
            originalParentTemplateGroupId=parent_group_id,
            flush=flush,
        )

    async def set_ordered_template_group_items(
        self, group_id: str, items: Sequence[Message], *, flush: bool = True
    ) -> None:
        group = self._template_group(group_id)
        del group.items[:]
        for item in items:
            group.items.add().CopyFrom(item)
        await self.operation(
            "set-ordered-template-group-items",
            originalParentTemplateGroupId=group_id,
            templateGroupItems=[clone_message(x) for x in items],
            flush=flush,
        )

    async def move_template_group_items(
        self,
        items: Sequence[Message],
        old_parent_id: str,
        new_parent_id: str,
        *,
        flush: bool = True,
    ) -> bool:
        old_parent = self._template_group(old_parent_id)
        new_parent = self._template_group(new_parent_id)
        for item in items:
            if int(item.itemType) != int(PB.PBMealPlanTemplateGroupItem.Type.Group):
                continue
            moved_id = str(item.identifier)
            if moved_id == new_parent_id or self._group_contains_group(moved_id, new_parent_id):
                return False
        ids = {x.identifier for x in items}
        old_kept = [clone_message(x) for x in old_parent.items if x.identifier not in ids]
        del old_parent.items[:]
        for item in old_kept:
            old_parent.items.add().CopyFrom(item)
        existing = {x.identifier for x in new_parent.items}
        for item in items:
            if item.identifier not in existing:
                new_parent.items.add().CopyFrom(item)
        await self.operation(
            "move-template-group-items",
            originalParentTemplateGroupId=old_parent_id,
            updatedParentTemplateGroupId=new_parent_id,
            templateGroupItems=[clone_message(x) for x in items],
            flush=flush,
        )
        return True

    def _group_contains_group(self, group_id: str, target_id: str) -> bool:
        group = self.state.meal_plan_template_groups.get(group_id)
        if group is None:
            return False
        for item in group.items:
            if int(item.itemType) != int(PB.PBMealPlanTemplateGroupItem.Type.Group):
                continue
            child_id = str(item.identifier)
            if child_id == target_id or self._group_contains_group(child_id, target_id):
                return True
        return False

    async def set_template_group_items_sort_order(
        self, group_id: str, sort_order: int, *, flush: bool = True
    ) -> Message:
        group = self._template_group(group_id)
        group.groupSettings.itemsSortOrder = sort_order
        partial = PB.PBMealPlanTemplateGroup(identifier=group_id)
        partial.groupSettings.CopyFrom(group.groupSettings)
        await self.operation(
            "set-template-group-items-sort-order", templateGroup=partial, flush=flush
        )
        return group

    async def set_template_group_groups_sort_position(
        self, group_id: str, position: int, *, flush: bool = True
    ) -> Message:
        group = self._template_group(group_id)
        group.groupSettings.groupsSortPosition = position
        partial = PB.PBMealPlanTemplateGroup(identifier=group_id)
        partial.groupSettings.CopyFrom(group.groupSettings)
        await self.operation(
            "set-template-group-groups-sort-position", templateGroup=partial, flush=flush
        )
        return group

    def _refresh_event_sort_index(
        self, event: Message, old_event: Message | None = None
    ) -> None:
        """Mirror CalendarOperationManager.dR orderAddedSortIndex assignment."""
        should_recompute = old_event is None
        event_type = int(event.eventType)
        if old_event is not None:
            if event_type == int(PB.PBCalendarEventType.MealPlanTemplateEvent):
                should_recompute = str(event.templateDayId) != str(old_event.templateDayId)
            else:
                should_recompute = str(event.date) != str(old_event.date)
            should_recompute = should_recompute or str(event.labelId) != str(old_event.labelId)
        if not should_recompute:
            return

        candidates: list[Message]
        if event_type == int(PB.PBCalendarEventType.MealPlanQueueEvent):
            candidates = [
                value
                for value in self.state.meal_plan_events.values()
                if int(value.eventType) == event_type
            ]
        elif event_type == int(PB.PBCalendarEventType.MealPlanFavoriteEvent):
            candidates = [
                value
                for value in self.state.meal_plan_events.values()
                if int(value.eventType) == event_type
            ]
        elif event_type == int(PB.PBCalendarEventType.MealPlanTemplateEvent):
            candidates = [
                value
                for value in self.state.meal_plan_template_events.values()
                if str(value.templateDayId) == str(event.templateDayId)
            ]
        else:
            candidates = [
                value
                for value in self.state.meal_plan_events.values()
                if int(value.eventType) == event_type and str(value.date) == str(event.date)
            ]
        maximum = max(
            (int(value.orderAddedSortIndex) for value in candidates if value.identifier != event.identifier),
            default=-1,
        )
        event.orderAddedSortIndex = maximum + 1

    def _event_store(self, event_type: int) -> dict[str, Message]:
        if int(event_type) == int(PB.PBCalendarEventType.MealPlanTemplateEvent):
            return self.state.meal_plan_template_events
        return self.state.meal_plan_events

    def _template(self, template_id: str) -> Message:
        template = self.state.meal_plan_templates.get(template_id)
        if template is None:
            raise KeyError(template_id)
        return template

    def _template_group(self, group_id: str) -> Message:
        group = self.state.meal_plan_template_groups.get(group_id)
        if group is None:
            raise KeyError(group_id)
        return group

    async def set_icalendar_enabled(self,enabled:bool)->Message:
        request=PB.PBMealPlanSetICalendarEnabledRequest(shouldEnableIcalendarGeneration=enabled)
        response = await self.transport.post_proto(
            "/data/meal-planning-calendar/set-icalendar-enabled",
            fields={"icalendar_request":request},
            response_type="PBMealPlanSetICalendarEnabledRequestResponse",
        )
        assert isinstance(response, Message)
        return response
    async def send_as_email(self,email:str,markup:str)->bytes:
        return await self.transport.request(
            "POST","/data/meal-planning-calendar/send-as-email",fields={"email":email,"markup":markup}
        )
