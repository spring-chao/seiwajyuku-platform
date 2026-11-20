from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import current_user
from app.api.learning_plans import router


def test_admin_resources_require_plan_read_and_keep_operations_videos_separate():
    app = FastAPI()
    app.include_router(router)
    endpoint = "/api/v1/learning-resources"

    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        app.dependency_overrides[current_user] = lambda: {"permissions": []}
        assert client.get(endpoint).status_code == 403
        app.dependency_overrides[current_user] = lambda: {"permissions": ["plans:read"]}
        catalog = client.get(endpoint)
        assert catalog.status_code == 200
        assert catalog.headers["Cache-Control"] == "private, no-store"
        years = catalog.json()["data"]["years"]
        assert [len(year["months"]) for year in years] == [12, 12, 12]
        assert sum(month["qrCount"] for year in years for month in year["months"]) == 68
        assert client.get(endpoint, params={"year": 2}).status_code == 422
        assert client.get(endpoint, params={"year": 4, "month": 1}).status_code == 422
        month = client.get(endpoint, params={"year": 1, "month": 1}).json()["data"]
        assert month["classQrs"][0]["qrData"].startswith("data:image/")
        assert month["classQrs"][0]["url"].startswith("https://")
        for number in (11, 12):
            month = client.get(endpoint, params={"year": 3, "month": number}).json()["data"]
            assert all(not month[key] for key in ("classQrs", "groupQrs", "extraQrs"))

