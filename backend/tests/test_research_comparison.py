"""Wortlautvergleich verwendet ausschließlich wörtlich belegte Quellenstellen."""

from jarvis.research import compare_verified_sources


def sources_and_claims():
    first = "Europa Clipper untersucht die eisige Oberfläche des Mondes Europa und sammelt Daten."
    second = "Europa Clipper erforscht den Mond Europa und untersucht seine Oberfläche mit Instrumenten."
    sources = [
        {"id": 1, "title": "PDF", "url": "https://example.com/a.pdf", "text": first,
         "retrieved_at": "2026-10-27T01:00:00Z", "media_type": "pdf"},
        {"id": 2, "title": "Seite", "url": "https://example.com/b", "text": second,
         "retrieved_at": "2026-10-27T01:01:00Z", "media_type": "web"},
    ]
    claims = [
        {"statement": "Erste", "evidence": [{"source_id": 1, "quote": first}]},
        {"statement": "Zweite", "evidence": [{"source_id": 2, "quote": second}]},
    ]
    return sources, claims


def test_two_verified_sources_show_quotes_and_word_overlap():
    sources, claims = sources_and_claims()
    comparison = compare_verified_sources(sources, claims)
    assert [entry["quote"] for entry in comparison["entries"]] == [source["text"] for source in sources]
    assert [entry["media_type"] for entry in comparison["entries"]] == ["pdf", "web"]
    assert "europa" in comparison["shared_words"]
    assert "untersucht" in comparison["shared_words"]
    assert "eisige" in comparison["first_only_words"]
    assert "instrumenten" in comparison["second_only_words"]
    assert "seine" not in comparison["shared_words"]
    assert comparison["shared_numbers"] == []


def test_numbers_are_derived_only_from_two_verified_quotes():
    sources, claims = sources_and_claims()
    sources[0]["text"] += " Im Jahr 2024 wurden 12,5 % auf 5 km gemessen."
    sources[1]["text"] += " Im Jahr 2024 wurden 14 % auf 5 km gemessen."
    claims[0]["evidence"][0]["quote"] = sources[0]["text"]
    claims[1]["evidence"][0]["quote"] = sources[1]["text"]
    comparison = compare_verified_sources(sources, claims)
    assert comparison["shared_numbers"] == ["2024", "5 km"]
    assert comparison["first_only_numbers"] == ["12,5 %"]
    assert comparison["second_only_numbers"] == ["14 %"]


def test_missing_source_or_unverified_quote_cannot_create_comparison():
    sources, claims = sources_and_claims()
    assert compare_verified_sources([], claims) is None
    assert compare_verified_sources(sources[:1], claims) is None
    assert compare_verified_sources(sources, claims[:1]) is None
    claims[1]["evidence"][0]["quote"] = "Eine völlig andere erfundene Aussage mit ausreichender Länge."
    assert compare_verified_sources(sources, claims) is None
    claims[1]["evidence"][0]["quote"] = sources[0]["text"]
    assert compare_verified_sources(sources, claims) is None
