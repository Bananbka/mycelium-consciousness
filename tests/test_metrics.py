async def test_metrics_endpoint_exposes_request_histogram_by_route_template(client):
    await client.get("/health")
    body = (await client.get("/metrics")).text

    assert "http_request_duration_seconds_bucket" in body
    assert 'path="/health"' in body
    assert 'status="200"' in body


async def test_unknown_paths_do_not_create_unbounded_label_values(client):
    await client.get("/no/such/path/12345")
    body = (await client.get("/metrics")).text

    assert "12345" not in body
    assert 'path="unmatched"' in body


async def test_metrics_endpoint_does_not_count_itself(client):
    await client.get("/metrics")
    body = (await client.get("/metrics")).text

    assert 'path="/metrics"' not in body
