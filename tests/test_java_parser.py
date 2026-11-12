from conftest import FIXTURE_REPO, JAVA_ROOT

from codeatlas.ingest.java import parse_java, simple_type


def parse(rel: str):
    path = f"{JAVA_ROOT}/{rel}"
    return parse_java(path, (FIXTURE_REPO / path).read_text(encoding="utf-8"))


def test_simple_type():
    assert simple_type("java.util.List<Owner>") == "List"
    assert simple_type("Map<String, List<Pet>>") == "Map"
    assert simple_type("String[]") == "String"
    assert simple_type("Object...") == "Object"


def test_controller_types_endpoints_and_calls():
    facts, chunks = parse("order/OrderController.java")
    assert facts.package == "com.example.shop.order"
    assert "org.springframework.web.bind.annotation.GetMapping" in facts.imports

    types = {t.qualified_name: t for t in facts.types}
    controller = types["com.example.shop.order.OrderController"]
    assert controller.stereotype == "controller"
    assert controller.annotations == ["RestController", "RequestMapping"]
    assert controller.fields == {"orderService": "OrderService"}
    assert controller.start_line == 10  # includes the Javadoc

    methods = {m.name: m for m in controller.methods}
    assert methods["getOrder"].endpoints == ["GET /orders/{id}"]
    assert methods["placeOrder"].endpoints == ["POST /orders"]
    assert ["OrderService", "findOrder"] in methods["getOrder"].calls
    assert ["PlaceOrderRequest", "customerId"] in methods["placeOrder"].calls
    assert methods["OrderController"].is_constructor

    record = types["com.example.shop.order.OrderController.PlaceOrderRequest"]
    assert record.kind == "record"
    assert record.fields == {"customerId": "Long", "amountCents": "long"}

    method_chunks = {c.symbol: c for c in chunks if c.kind == "method"}
    get_order = method_chunks["com.example.shop.order.OrderController#getOrder"]
    assert (get_order.start_line, get_order.end_line) == (23, 27)
    assert "Returns a single order by id" in get_order.text
    assert "Endpoints: GET /orders/{id}" in get_order.embed_text
    assert "Calls: OrderService.findOrder" in get_order.embed_text
    assert get_order.metadata["stereotype"] == "controller"

    class_chunk = next(c for c in chunks if c.kind == "class" and c.symbol.endswith("OrderController"))
    assert "private final OrderService orderService;" in class_chunk.text
    assert "getOrder(@PathVariable Long id)" in class_chunk.text


def test_feign_clients_are_recorded_as_outgoing_calls():
    facts, _ = parse("payment/PaymentGatewayClient.java")
    client = facts.types[0]
    assert client.kind == "interface"
    assert client.stereotype == "http-client"
    assert client.methods[0].client_endpoints == ["POST /gateway/charges"]
    assert client.methods[0].endpoints == []

    facts, _ = parse("reporting/OrderLookupClient.java")
    assert facts.types[0].methods[0].client_endpoints == ["GET /orders/{orderId}"]


def test_string_literal_paths_and_static_calls():
    facts, _ = parse("billing/InvoiceJob.java")
    sync = next(m for m in facts.types[0].methods if m.name == "syncInvoices")
    assert sync.path_literals == ["/orders/{id}"]
    assert sync.annotations == ["Scheduled"]

    facts, _ = parse("payment/PaymentService.java")
    methods = {m.name: m for m in facts.types[0].methods}
    assert methods["charge"].annotations == ["Retryable"]
    assert ["PaymentGatewayClient", "createCharge"] in methods["charge"].calls
    assert ["Customer", "getEmail"] in methods["charge"].calls
    assert ["PaymentResult", "failed"] in methods["recover"].calls


def test_nested_enum_and_entity():
    facts, chunks = parse("order/Order.java")
    names = [t.qualified_name for t in facts.types]
    assert names == ["com.example.shop.order.Order", "com.example.shop.order.Order.OrderStatus"]
    order = facts.types[0]
    assert order.stereotype == "entity"
    assert order.fields["customer"] == "Customer"
    assert "Customer" in order.type_refs
    enum_chunk = next(c for c in chunks if c.symbol == "com.example.shop.order.Order.OrderStatus")
    assert "PENDING," in enum_chunk.text


def test_facts_round_trip():
    from codeatlas.ingest.java import JavaFileFacts

    facts, _ = parse("order/OrderServiceImpl.java")
    assert JavaFileFacts.from_dict(facts.to_dict()) == facts
