from api.main import INSTANCE_ID


async def test_every_response_names_the_serving_instance(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-instance-id"] == INSTANCE_ID
    assert response.json()["instance"] == INSTANCE_ID


async def test_instance_header_present_on_error_responses(client):
    response = await client.get("/profiles/me")
    assert response.status_code in (401, 403)
    assert response.headers["x-instance-id"] == INSTANCE_ID
