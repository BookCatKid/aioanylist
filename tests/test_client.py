from __future__ import annotations

from types import SimpleNamespace

import pytest

from anylist_sdk.client import AnyListClient
from anylist_sdk.proto import PB
from anylist_sdk.realtime import RealtimeEvent
from anylist_sdk.types import AuthTokens, Domain


def tokens(user='user', locale='en-US'):
    return AuthTokens(user, 'access', 'refresh', True, locale)


def test_authenticated_constructor_installs_complete_service_surface() -> None:
    client=AnyListClient(tokens=tokens())
    assert client.user_id=='user'
    for name in ('lists','recipes','folders','categories','categorized_items','list_settings',
                 'starter_list_settings','mobile_settings','starter_lists','meal_plan','account',
                 'photos','sharing','alexa','web_state','raw'):
        assert getattr(client,name) is not None


@pytest.mark.asyncio
async def test_sign_in_to_different_account_replaces_all_state(monkeypatch) -> None:
    client=AnyListClient(tokens=tokens('old'))
    client.state.loaded_once=True
    client.state.shopping_lists['leak']=PB.ShoppingList(identifier='leak')
    async def signin(email,password):
        value=tokens('new','de-DE');client.transport.tokens=value;return value
    monkeypatch.setattr(client.transport,'sign_in',signin)
    result=await client.sign_in('x','y')
    assert result.user_id=='new' and client.state.user_id=='new'
    assert client.state.shopping_lists=={} and not client.state.loaded_once
    assert client.tag_data.locale=='de-DE'
    assert client.lists.user_id=='new'


@pytest.mark.asyncio
async def test_load_runs_sync_and_tag_data_then_replays_restored_operations(monkeypatch) -> None:
    client=AnyListClient(tokens=tokens())
    order=[]
    async def refresh(*,full=False): order.append(('sync',full));client.state.loaded_once=True;return PB.PBUserDataResponse()
    async def tags(): order.append(('tags',));return (None,None)
    monkeypatch.setattr(client.sync,'refresh',refresh)
    monkeypatch.setattr(client.tag_data,'active_and_english',tags)
    class Service:
        async def restore(self): order.append(('restore',));return 1
        async def flush(self): order.append(('flush',))
    service=Service()
    monkeypatch.setattr(client,'_operation_services',lambda:[service])
    await client.load(load_tag_data=True,restore_pending=True)
    assert ('sync',True) in order and ('tags',) in order
    assert order.count(('restore',))==1 and order.count(('flush',))==1
    assert client.ready.is_set()


@pytest.mark.asyncio
async def test_reconnect_does_user_data_and_account_catchup(monkeypatch) -> None:
    client=AnyListClient(tokens=tokens())
    calls=[]
    async def refresh(*args,**kwargs): calls.append('sync');return PB.PBUserDataResponse()
    async def account(): calls.append('account');return PB.PBAccountInfoResponse()
    monkeypatch.setattr(client.sync,'refresh',refresh)
    monkeypatch.setattr(client.account,'get',account)
    await client._on_reconnect()
    assert calls==['sync','account']


@pytest.mark.asyncio
async def test_account_invalidation_fetches_account_without_full_sync(monkeypatch) -> None:
    client=AnyListClient(tokens=tokens())
    calls=[]
    async def account(): calls.append('account')
    async def refresh(*args,**kwargs): calls.append('sync')
    monkeypatch.setattr(client.account,'get',account);monkeypatch.setattr(client.sync,'refresh',refresh)
    await client._on_realtime(RealtimeEvent('refresh-account-info',Domain.ACCOUNT))
    assert calls==['account']


@pytest.mark.asyncio
async def test_logout_clears_account_state_and_authenticated_services(monkeypatch) -> None:
    client=AnyListClient(tokens=tokens())
    client.state.shopping_lists['x']=PB.ShoppingList(identifier='x')
    async def stop(): pass
    async def logout(): client.transport.tokens=None
    monkeypatch.setattr(client.realtime,'stop',stop);monkeypatch.setattr(client.transport,'logout',logout)
    await client.logout()
    assert client.state.user_id is None and client.state.shopping_lists=={}
    assert client.lists is None and client.recipes is None and client.account is None
    assert client.raw is not None and not client.ready.is_set()
