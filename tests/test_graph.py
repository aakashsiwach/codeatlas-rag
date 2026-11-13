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


def _facts(files: dict[str, str]):
    return [parse_java(path, source)[0] for path, source in files.items()]


def test_ambiguous_type_names_do_not_create_arbitrary_edges():
    graph = CodeGraph.build(_facts({
        "a/Config.java": "package a; public class Config { public void load() {} }",
        "b/Config.java": "package b; public class Config { public void load() {} }",
        "c/Uses.java": "package c; public class Uses { private Config config; void run() { config.load(); } }",
        "d/Imports.java": "package d; import b.Config; public class Imports { private Config config; void run() { config.load(); } }",
    }))
    assert not any(dst.endswith("Config") for dst in graph.g.successors("c.Uses"))  # unresolvable: no edge
    assert graph.g.has_edge("d.Imports", "b.Config")  # the import disambiguates
    assert graph.g.has_edge("d.Imports#run", "b.Config#load")


def test_lower_case_words_need_a_code_noun_to_match_a_type(graph):
    assert graph.resolve_mentions("What breaks if I change the customer entity?") == [f"{PKG}.customer.Customer"]
    assert graph.resolve_mentions("Who uses the customer's Redis cache?") == []
    assert graph.resolve_mentions("what about orders in general") == []


LOMBOK_ORDER = """package shop;
import lombok.Data;
@Data
public class Order {
    private static final long MAX = 10;
    private final String id;
    private long total;
    private boolean paid;
    public long getTotal() { return total * 100; }
}
"""
LOMBOK_LINE = """package shop;
import lombok.Getter;
import lombok.Setter;
public class Line {
    @Getter private String sku;
    @Getter @Setter private int qty;
    private String note;
}
"""
USES = """package shop;
public class Billing {
    record Charge(String customer, long amount) {}
    long bill(Order order, Line line, Charge charge) {
        order.setTotal(order.getTotal() + charge.amount());
        return line.getQty() + (order.isPaid() ? 1 : 0);
    }
}
"""


def test_generated_accessors_are_parsed():
    facts = {t.name: t for f in _facts({"shop/Order.java": LOMBOK_ORDER, "shop/Line.java": LOMBOK_LINE, "shop/Billing.java": USES}) for t in f.types}
    # @Data: getters for every non-static field (isX for primitive boolean), setters for non-final fields;
    # the explicit getTotal() replaces the generated one.
    assert facts["Order"].generated_methods == ["getId", "setTotal", "isPaid", "setPaid"]
    assert facts["Line"].generated_methods == ["getSku", "getQty", "setQty"]  # field-level only
    assert facts["Charge"].generated_methods == ["customer", "amount"]  # record accessors


def test_calls_to_generated_accessors_reach_the_type_in_impact_analysis():
    graph = CodeGraph.build(_facts({"shop/Order.java": LOMBOK_ORDER, "shop/Line.java": LOMBOK_LINE, "shop/Billing.java": USES}))
    for target in ("shop.Order#setTotal", "shop.Order#getTotal", "shop.Order#isPaid", "shop.Line#getQty", "shop.Billing.Charge#amount"):
        assert graph.g.has_edge("shop.Billing#bill", target), target
    assert graph.g.nodes["shop.Order#setTotal"]["generated"] is True
    dependents = {h.node for h in graph.impact(["shop.Order"], depth=1)}
    assert "shop.Billing#bill" in dependents


def test_fixture_record_accessors_link_the_controller(graph):
    assert graph.g.has_edge(f"{PKG}.order.OrderController#placeOrder", f"{PKG}.order.OrderController.PlaceOrderRequest#amountCents")
