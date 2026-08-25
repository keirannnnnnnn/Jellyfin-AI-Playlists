import pytest
import httpx
from unittest.mock import AsyncMock, patch
from app.services.jellyfin_client import JellyfinClient


@pytest.mark.asyncio
async def test_jellyfin_client_test_connection_success():
    client = JellyfinClient(base_url="http://127.0.0.1:8096", api_key="valid_token")
    req = httpx.Request("GET", "http://127.0.0.1:8096")

    mock_info_resp = httpx.Response(200, json={"ServerName": "Test Jellyfin", "Version": "10.9.1"}, request=req)
    mock_users_resp = httpx.Response(200, json=[{"Id": "u1", "Name": "User1"}], request=req)
    mock_items_resp = httpx.Response(200, json={"TotalRecordCount": 150}, request=req)
    mock_pr_resp = httpx.Response(200, json=[{"1": 1}], request=req)

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

        mock_get.side_effect = side_effect_get
        mock_post.return_value = mock_pr_resp

        res = await client.test_connection()
        assert res["connected"] is True
        assert res["server_name"] == "Test Jellyfin"
        assert res["user_count"] == 1
        assert res["audio_count"] == 150
        assert res["playback_reporting_available"] is True
        assert res["playback_reporting_mode"] == "plugin_api"


@pytest.mark.asyncio
async def test_jellyfin_create_and_update_playlist():
    client = JellyfinClient(base_url="http://127.0.0.1:8096", api_key="valid_token")
    req = httpx.Request("POST", "http://127.0.0.1:8096")

    mock_create_resp = httpx.Response(200, json={"Id": "pl_123"}, request=req)
    mock_items_resp = httpx.Response(200, json={"Items": [{"Id": "item_old", "PlaylistItemId": "pl_entry_1"}]}, request=req)
    mock_del_resp = httpx.Response(200, request=req)
    mock_add_resp = httpx.Response(200, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_del:

        mock_post.return_value = mock_create_resp
        pl_id = await client.create_playlist("Pop Mix", "user_1", ["t1", "t2"])
        assert pl_id == "pl_123"

        mock_get.return_value = mock_items_resp
        mock_del.return_value = mock_del_resp
        mock_post.return_value = mock_add_resp

        await client.update_playlist_items("pl_123", "user_1", ["t3", "t4"])
        mock_del.assert_called_once()


@pytest.mark.asyncio
async def test_jellyfin_set_playlist_access():
    client = JellyfinClient(base_url="http://127.0.0.1:8096", api_key="valid_token")
    req = httpx.Request("POST", "http://127.0.0.1:8096")
    mock_resp = httpx.Response(204, request=req)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        await client.set_playlist_access("pl_123", "u_456", is_public=False)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "pl_123" in call_args[0][0]
        assert call_args[1]["params"] == {"userId": "u_456"}
        assert call_args[1]["json"] == {"IsPublic": False}
