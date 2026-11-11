from collections.abc import Callable


_pipeline: Callable[[str], object] | None = None


def load_pipeline(model_name: str | None) -> None:
    global _pipeline
    _pipeline = None
    if not model_name:
        return
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(
            "SCRAPZ_SPACY_MODEL is set, but spaCy is not installed. "
            "Install the optional NLP dependency before starting the app."
        ) from exc
    _pipeline = spacy.load(model_name)


def extract_entities(text: str) -> list[dict[str, str]]:
    if _pipeline is None:
        return []
    document = _pipeline(text[:20_000])
    return [
        {"text": entity.text, "label": entity.label_}
        for entity in document.ents
        if entity.label_ in {"ORG", "GPE", "LOC", "EVENT", "PRODUCT"}
    ]
