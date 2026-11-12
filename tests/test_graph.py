import pytest
from conftest import FIXTURE_REPO

from codeatlas.graph import IMPACT_INTENT, CodeGraph
from codeatlas.ingest.java import parse_java

PKG = "com.example.shop"


@pytest.fixture(scope="module")
def graph() -> CodeGraph:
    facts = []
    for path in sorted(FIXTURE_REPO.rglob("*.java")):
        rel = path.relative_to(FIXTURE_REPO).as_posix()
        file_facts, _ = parse_java(rel, path.read_text(encoding="utf-8"))
        facts.append(file_facts)
    return CodeGraph.build(facts)


def relation(graph: CodeGraph, src: str, dst: str) -> str | None:
    if not graph.g.has_edge(src, dst):
        return None
    return graph.g.edges[src, dst]["relation"]


def test_calls_resolve_through_fields_locals_and_interfaces(graph):
    assert relation(graph, f"{PKG}.order.OrderController#getOrder", f"{PKG}.order.OrderService#findOrder") == "calls"
    assert (
        relation(graph, f"{PKG}.order.OrderService#findOrder", f"{PKG}.order.OrderServiceImpl#findOrder")
        == "implemented_by"
    )
    assert relation(graph, f"{PKG}.order.OrderServiceImpl#placeOrder", f"{PKG}.payment.PaymentService#charge") == "calls"
    assert relation(graph, f"{PKG}.order.OrderServiceImpl#placeOrder", f"{PKG}.order.Order#markPaid") == "calls"
    assert (
        relation(graph, f"{PKG}.payment.PaymentService#charge", f"{PKG}.payment.PaymentGatewayClient#createCharge")
        == "calls"
    )
    assert relation(graph, f"{PKG}.order.OrderServiceImpl", f"{PKG}.order.OrderService") == "implements"
    assert relation(graph, f"{PKG}.order.Order", f"{PKG}.customer.Customer") == "field"


def test_impact_of_an_entity(graph):
    targets = graph.resolve_mentions("What breaks if I change the Customer entity?")
    assert targets == [f"{PKG}.customer.Customer"]
    hits = {h.node: h for h in graph.impact(targets)}
    assert f"{PKG}.order.Order" in hits
    assert f"{PKG}.order.OrderServiceImpl#placeOrder" in hits
    assert f"{PKG}.payment.PaymentService#charge" in hits
    # reached through the OrderService interface and the Order return type
    assert f"{PKG}.order.OrderService#placeOrder" in hits
    assert f"{PKG}.order.OrderController#placeOrder" in hits

    report = graph.describe(targets, list(hits.values()))
    assert "Direct dependents" in report
    assert "PaymentService#charge" in report


def test_endpoint_roles_find_servers_and_clients(graph):
    roles = graph.endpoint_roles(["/orders/{id}"])
    assert roles["serves"] == [f"{PKG}.order.OrderController#getOrder"]
    assert roles["http_clients"] == [f"{PKG}.reporting.OrderLookupClient#fetchOrder"]
    assert roles["literal_callers"] == [f"{PKG}.billing.InvoiceJob#syncInvoices"]
    callers = {h.node for h in graph.impact(roles["http_clients"])}
    assert f"{PKG}.reporting.ReportingService#describeOrder" in callers


def test_mentions_and_neighbors(graph):
    assert graph.resolve_mentions("explain OrderServiceImpl.placeOrder") == [f"{PKG}.order.OrderServiceImpl#placeOrder"]
    assert f"{PKG}.payment.PaymentService#charge" in graph.neighbors(f"{PKG}.order.OrderServiceImpl#placeOrder", limit=10)
    assert graph.stats()["types"] >= 15


def test_impact_intent():
    assert IMPACT_INTENT.search("What breaks if I change the Customer entity?")
    assert IMPACT_INTENT.search("Which services call the /orders/{id} endpoint?")
    assert IMPACT_INTENT.search("who calls placeOrder")
    assert not IMPACT_INTENT.search("Explain the auth flow for a new joiner")
