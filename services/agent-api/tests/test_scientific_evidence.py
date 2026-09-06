"""Network-bound scientific curation helpers use bounded, schema-checked results."""

import httpx
import pytest

from agent_api.knowledge.ontology import OntologyCandidate, resolve_ontology_terms
from agent_api.knowledge.pubmed import fetch_pubmed_abstract, search_pubmed


@pytest.mark.anyio
async def test_pubmed_search_and_abstract_parse_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            assert request.url.params["retmax"] == "10"
            return httpx.Response(
                200, content=b"<eSearchResult><IdList><Id>123</Id></IdList></eSearchResult>"
            )
        return httpx.Response(
            200,
            content=(
                b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>"
                b"<Article><ArticleTitle>Metabolic evidence</ArticleTitle>"
                b"<Journal><Title>JIMD</Title>"
                b"</Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue>"
                b"<AuthorList><Author><ForeName>A</ForeName><LastName>Author</LastName></Author>"
                b"</AuthorList><Abstract><AbstractText>Verified abstract.</AbstractText></Abstract>"
                b"</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidates = await search_pubmed("methylmalonic acidemia", client)
        article = await fetch_pubmed_abstract("123", client)
    assert candidates[0].pmid == "123"
    assert candidates[0].abstract is None
    assert article.abstract == "Verified abstract."


@pytest.mark.anyio
async def test_ontology_resolution_drops_obsolete_or_incomplete_terms() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "obo_id": "MONDO:0010975",
                            "label": "propionic acidemia",
                            "ontology_name": "mondo",
                        },
                        {
                            "obo_id": "MONDO:old",
                            "label": "old",
                            "ontology_name": "mondo",
                            "is_obsolete": True,
                        },
                        {"label": "missing ID", "ontology_name": "mondo"},
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        terms = await resolve_ontology_terms("propionic acidemia", "mondo", client)
    assert terms == [
        OntologyCandidate(
            curie="MONDO:0010975",
            label="propionic acidemia",
            ontology="mondo",
        ),
    ]
