"""Bounded, metadata-only PubMed retrieval for Ops evidence curation."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MAX_RESULTS = 10


@dataclass(frozen=True)
class PubMedArticle:
    pmid: str
    title: str
    journal: str | None
    publication_date: str | None
    authors: str | None
    abstract: str | None = None


def _text(node: ElementTree.Element | None) -> str | None:
    if node is None:
        return None
    value = " ".join(part.strip() for part in node.itertext() if part.strip())
    return value or None


def _article_from_xml(article: ElementTree.Element, *, include_abstract: bool) -> PubMedArticle:
    pmid = _text(article.find(".//PMID"))
    title = _text(article.find(".//ArticleTitle"))
    if not pmid or not title:
        raise ValueError("PubMed returned an article without PMID or title")
    author_nodes = article.findall(".//AuthorList/Author")
    authors = [
        " ".join(filter(None, (_text(author.find("ForeName")), _text(author.find("LastName")))))
        for author in author_nodes[:3]
    ]
    author_text = ", ".join(author for author in authors if author) or None
    if len(author_nodes) > 3 and author_text:
        author_text += " et al."
    return PubMedArticle(
        pmid=pmid,
        title=title,
        journal=_text(article.find(".//Journal/Title")),
        publication_date=_text(article.find(".//PubDate")),
        authors=author_text,
        abstract=_text(article.find(".//Abstract")) if include_abstract else None,
    )


async def search_pubmed(query: str, client: httpx.AsyncClient) -> list[PubMedArticle]:
    """Find a small, recent candidate set without retrieving patient data."""

    response = await client.get(
        f"{_EUTILS}/esearch.fcgi",
        params={"db": "pubmed", "term": query, "retmax": str(_MAX_RESULTS), "sort": "pub date"},
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    pmids = [node.text for node in root.findall(".//IdList/Id") if node.text]
    if not pmids:
        return []
    return await _fetch_articles(pmids, client=client, include_abstract=False)


async def fetch_pubmed_abstract(pmid: str, client: httpx.AsyncClient) -> PubMedArticle:
    articles = await _fetch_articles([pmid], client=client, include_abstract=True)
    if not articles:
        raise ValueError("PubMed 未找到该 PMID")
    if not articles[0].abstract:
        raise ValueError("该 PubMed 记录没有可导入的摘要")
    return articles[0]


async def _fetch_articles(
    pmids: list[str],
    *,
    client: httpx.AsyncClient,
    include_abstract: bool,
) -> list[PubMedArticle]:
    response = await client.get(
        f"{_EUTILS}/efetch.fcgi",
        params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return [
        _article_from_xml(article, include_abstract=include_abstract)
        for article in root.findall(".//PubmedArticle")
    ]
