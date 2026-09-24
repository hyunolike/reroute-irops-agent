from tests.conftest import SERVICE_DATE


async def test_flight_lookup_returns_cancellation(client):
    r = await client.get("/api/flights/KE123")
    assert r.status_code == 200
    f = r.json()
    assert (f["flight_no"], f["origin"], f["destination"], f["status"]) == ("KE123", "ICN", "NRT", "CANCELLED")
    assert f["disruption"]["type"] == "CANCELLATION"
    assert f["departure_time"].startswith(f"{SERVICE_DATE.isoformat()}T10:00")


async def test_unknown_flight_is_404(client):
    assert (await client.get("/api/flights/ZZ999")).status_code == 404


async def test_passenger_lookup(client):
    body = (await client.get("/api/flights/KE123/passengers")).json()
    pax = body["passengers"]
    assert body["count"] == 35
    assert sum(p["cabin"] == "BUSINESS" for p in pax) == 7
    assert sum(p["vip"] for p in pax) == 3
    assert sum(p["onward_flight_no"] is not None for p in pax) == 5
    assert {p["special_assistance"] for p in pax} >= {"WCHC", "UMNR"}


async def test_alternative_search_includes_coterminal_and_inventory(client):
    r = await client.get(
        "/api/flights/alternatives",
        params={"origin": "ICN", "destination": "NRT", "departure_date": SERVICE_DATE.isoformat(), "exclude": "KE123"},
    )
    flights = {f["flight_no"]: f for f in r.json()["flights"]}
    assert {"KE701", "OZ102", "KE703", "7C1102", "KE2101", "KE705"} <= set(flights)
    assert "KE123" not in flights
    assert flights["KE701"]["economy_available"] == 10 and flights["KE701"]["business_available"] == 2
    assert flights["KE2101"]["destination"] == "HND"


async def test_booking_write_requires_approval_token(client):
    body = {"plan_id": "p1", "items": [{"reservation_id": "R0001", "flight_no": "KE701", "cabin": "BUSINESS"}]}
    assert (await client.post("/api/bookings/rebookings", json=body)).status_code == 401
    r = await client.post("/api/bookings/rebookings", json=body, headers={"X-Approval-Token": "forged.token"})
    assert r.status_code == 403
    audit = (await client.get("/api/audit", params={"result": "DENY"})).json()["entries"]
    assert any(e["enforced_by"] == "airline-booking-api" for e in audit)


def test_additive_migration_adds_missing_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.db.migrations import migrate

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:  # a database created by an older ReRoute version
        c.execute(text("CREATE TABLE agent_tasks (id VARCHAR(32) PRIMARY KEY, command TEXT)"))
    assert migrate(engine) == ["agent_tasks.pending"]
    assert "pending" in {c["name"] for c in inspect(engine).get_columns("agent_tasks")}
    assert migrate(engine) == []  # idempotent
