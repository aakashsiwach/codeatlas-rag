from codeatlas.graph import CodeGraph
from codeatlas.ingest.java import parse_java

SOURCES = {
    "x/Notifier.java": """package x;
public interface Notifier {
    void send(String to);
    default void sendAll(java.util.List<String> to) { to.forEach(this::send); send("ops"); }
}
""",
    "x/MailNotifier.java": """package x;
public class MailNotifier implements Notifier {
    @Override public void send(String to) { }
    @Override public void sendAll(java.util.List<String> to) { }
}
""",
    "x/Repo.java": """package x;
import java.util.List;
import java.util.Map;
public class Repo<T extends BaseEntity> implements Store<T, Long> {
    private Map<String, List<Owner>> byName;
    public <R extends Comparable<R>> List<T> find(Map<String, List<R>> filter) { return null; }
}
""",
    "x/Job.java": """package x;
public class Job {
    private Notifier notifier;
    public void run() {
        Runnable task = new Runnable() {
            @Override public void run() { notifier.send("anon"); }
        };
        class Local { void go() { notifier.send("local"); } }
        task.run();
    }
    public void after() { }
}
""",
    "x/Level.java": """package x;
public enum Level {
    LOW(1), HIGH(9);
    private final int weight;
    Level(int weight) { this.weight = weight; }
    public int weight() { return weight; }
}
""",
    "x/Audited.java": """package x;
public @interface Audited { String value() default ""; }
""",
}


def parsed():
    return {path: parse_java(path, src) for path, src in SOURCES.items()}


def types_by_name():
    return {t.name: t for facts, _ in parsed().values() for t in facts.types}


def test_interface_default_methods_are_methods_and_dispatch_to_overrides():
    types = types_by_name()
    assert [m.name for m in types["Notifier"].methods] == ["send", "sendAll"]
    graph = CodeGraph.build(facts for facts, _ in parsed().values())
    assert graph.g.edges["x.Notifier#sendAll", "x.MailNotifier#sendAll"]["relation"] == "implemented_by"
    assert graph.g.has_edge("x.Notifier#sendAll", "x.Notifier#send")  # call inside the default method


def test_generics_are_reduced_to_simple_type_names():
    repo = types_by_name()["Repo"]
    assert repo.implements == ["Store"] and repo.fields == {"byName": "Map"}
    assert {"Owner", "BaseEntity", "List"} <= set(repo.type_refs)
    assert [m.name for m in repo.methods] == ["find"]


def test_anonymous_and_local_classes_stay_inside_their_method():
    facts, chunks = parsed()["x/Job.java"]
    assert [t.name for t in facts.types] == ["Job"]  # no separate type for the anonymous or local class
    run = next(m for m in facts.types[0].methods if m.name == "run")
    assert ["Notifier", "send"] in run.calls  # calls made inside them belong to the enclosing method
    run_chunk = next(c for c in chunks if c.symbol == "x.Job#run")
    assert "class Local" in run_chunk.text and run_chunk.end_line < next(c for c in chunks if c.symbol == "x.Job#after").start_line


def test_enums_and_annotation_types():
    types = types_by_name()
    level = types["Level"]
    assert level.kind == "enum" and level.fields == {"weight": "int"}
    assert [m.name for m in level.methods] == ["Level", "weight"]  # constructor and method
    _, chunks = parsed()["x/Level.java"]
    outline = next(c for c in chunks if c.kind == "class")
    assert "LOW," in outline.text and "HIGH," in outline.text
    assert types["Audited"].kind == "annotation"
