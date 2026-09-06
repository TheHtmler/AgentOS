from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    title: str
    content: str
    section_label: str | None = None
    tags: list[str] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class OntologyTermSpec:
    """A human-reviewed controlled-vocabulary term attached to a document."""

    curie: str
    label: str
    ontology: str


@dataclass
class DocumentSpec:
    slug: str
    title: str
    chunks: list[ChunkSpec]
    source_kind: str = "curated_summary"
    source_url: str | None = None
    source_label: str | None = None
    source_date: str | None = None
    version_label: str | None = None
    review_status: str = "curated"
    ontology_terms: list[OntologyTermSpec] = field(default_factory=lambda: [])
    ingestion: dict[str, object] = field(default_factory=lambda: {})
