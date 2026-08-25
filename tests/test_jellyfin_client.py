import pytest
import httpx
from unittest.mock import AsyncMock, patch
from app.services.jellyfin_client import JellyfinClient


@pytest.mark.asyncio
async def test_jellyfin_client_test_connection_success():
    client = JellyfinClient(
        base_url="http://127.0.0.1:8096",
        api_key="valid_token",
        username="admin_user",
        password="admin_password",
    )
    req = httpx.Request("GET", "http://127.0.0.1:8096")

    mock_info_resp = httpx.Response(200, json={"ServerName": "Test Jellyfin", "Version": "10.11.11"}, request=req)
    mock_users_resp = httpx.Response(200, json=[{"Id": "u1", "Name": "User1"}], request=req)
    mock_items_resp = httpx.Response(200, json={"TotalRecordCount": 150}, request=req)
    mock_pr_resp = httpx.Response(200, json=[{"1": 1}], request=req)
    mock_auth_resp = httpx.Response(200, json={"AccessToken": "sess_tok_999", "User": {"Id": "admin_id", "Name": "admin_user"}}, request=req)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        
        async def side_effect_get(url, **kwargs):
            if "System/Info" in url:
                return mock_info_resp
            if "Users" in url:
                return mock_users_resp
            if "Items" in url:
                return mock_items_resp
            return httpx.Response(404, request=req)

        async def side_effect_post(url, **kwargs):
            if "AuthenticateByName" in url:
                return mock_auth_resp
            if "submit_custom_query" in url:
                return mock_pr_resp
            return httpx.Response(200, request=req)

        mock_get.side_effect = side_effect_get
        mock_post.side_effect = side_effect_post

        res = await client.test_connection()
        assert res["connected"] is True
        assert res["server_name"] == "Test Jellyfin"
        assert res["user_count"] == 1
        assert res["audio_count"] == 150
        assert res["playback_reporting_available"] is True
        assert res["playback_reporting_mode"] == "plugin_api"
        assert res["admin_authenticated"] is True


@pytest.mark.asyncio
async def test_jellyfin_admin_session_auth():
    client = JellyfinClient(
        base_url="http://127.0.0.1:8096",
        api_key="api_key_123",
        username="admin",
        password="secretpassword",
    )
    req = httpx.Request("POST", "http://127.0.0.1:8096/Users/AuthenticateByName")
    mock_auth_resp = httpx.Response(200, json={"AccessToken": "my_admin_session_token"}, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_auth_resp

        tok = await client.get_session_token()
        assert tok == "my_admin_session_token"
        assert client._session_token == "my_admin_session_token"

        # Calling again uses cached token without firing a new POST
        mock_post.reset_mock()
        tok2 = await client.get_session_token()
        assert tok2 == "my_admin_session_token"
        mock_post.assert_not_called()

        # Calling with force_refresh=True triggers new POST
        tok3 = await client.get_session_token(force_refresh=True)
        assert tok3 == "my_admin_session_token"
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_jellyfin_create_and_update_playlist():
    client = JellyfinClient(
        base_url="http://127.0.0.1:8096",
        api_key="valid_token",
        username="admin",
        password="secretpassword",
    )
    req = httpx.Request("POST", "http://127.0.0.1:8096")

    mock_auth_resp = httpx.Response(200, json={"AccessToken": "sess_token_123"}, request=req)
    mock_create_resp = httpx.Response(200, json={"Id": "pl_123"}, request=req)
    mock_items_resp = httpx.Response(200, json={"Items": [{"Id": "item_old", "PlaylistItemId": "pl_entry_1"}]}, request=req)
    mock_del_resp = httpx.Response(200, request=req)
    mock_add_resp = httpx.Response(200, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_del:

        async def side_effect_post(url, **kwargs):
            if "AuthenticateByName" in url:
                return mock_auth_resp
            if "/Playlists" in url:
                return mock_create_resp
            return mock_add_resp

        mock_post.side_effect = side_effect_post
        mock_get.return_value = mock_items_resp
        mock_del.return_value = mock_del_resp

        pl_id = await client.create_playlist("Pop Mix", "user_1", ["t1", "t2"])
        assert pl_id == "pl_123"

        # Verify create_playlist payload sets IsPublic=False & OpenAccess=False
        create_calls = [c for c in mock_post.call_args_list if "/Playlists" in c[0][0]]
        assert len(create_calls) == 1
        payload = create_calls[0][1]["json"]
        assert payload["IsPublic"] is False
        assert payload["OpenAccess"] is False
        assert payload["UserId"] == "user_1"

        await client.update_playlist_items("pl_123", "user_1", ["t3", "t4"])
        mock_del.assert_called_once()


@pytest.mark.asyncio
async def test_jellyfin_set_playlist_access_and_get_playlist():
    client = JellyfinClient(
        base_url="http://127.0.0.1:8096",
        api_key="valid_token",
        username="admin",
        password="secretpassword",
    )
    req = httpx.Request("POST", "http://127.0.0.1:8096")
    mock_auth_resp = httpx.Response(200, json={"AccessToken": "sess_token_123"}, request=req)
    mock_post_resp = httpx.Response(204, request=req)
    mock_get_resp = httpx.Response(200, json={"OpenAccess": False, "Shares": [], "ItemIds": ["t1"]}, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:

        async def side_effect_post(url, **kwargs):
            if "AuthenticateByName" in url:
                return mock_auth_resp
            return mock_post_resp

        mock_post.side_effect = side_effect_post
        mock_get.return_value = mock_get_resp

        await client.set_playlist_access("pl_123", "u_456", is_public=False)

        post_calls = [c for c in mock_post.call_args_list if "Playlists/pl_123" in c[0][0]]
        assert len(post_calls) == 1
        assert post_calls[0][1]["params"] == {"userId": "u_456"}
        assert post_calls[0][1]["json"] == {"IsPublic": False, "OpenAccess": False}

        # Test get_playlist
        pl_data = await client.get_playlist("pl_123")
        assert pl_data["OpenAccess"] is False


@pytest.mark.asyncio
async def test_jellyfin_delete_playlist():
    client = JellyfinClient(
        base_url="http://127.0.0.1:8096",
        api_key="valid_token",
        username="admin",
        password="secretpassword",
    )
    req = httpx.Request("POST", "http://127.0.0.1:8096")
    mock_auth_resp = httpx.Response(200, json={"AccessToken": "sess_token_123"}, request=req)
    mock_del_resp = httpx.Response(204, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_del:

        mock_post.return_value = mock_auth_resp
        mock_del.return_value = mock_del_resp

        ok = await client.delete_playlist("pl_old_123")
        assert ok is True
        mock_del.assert_called_once()
        assert "Items/pl_old_123" in mock_del.call_args[0][0]
